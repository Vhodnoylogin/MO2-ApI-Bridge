# -*- coding: utf-8 -*-
"""Транспортный слой: главный поток Qt и HTTP-сервер.

Знает про потоки, сокеты, JSON и токен. Не знает ни про MO2, ни про то, что именно делают
маршруты - ему передают готовую таблицу «путь -> функция». Поэтому логику можно менять,
не трогая транспорт, и наоборот.
"""
import json
import secrets
import socket
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from . import i18n


class MainThreadRunner(QObject):
    """Переносит вызов из потока сервера в главный поток Qt и ждёт результата.

    Без этого никак: mobase и Qt живут в главном потоке, и обращение к IOrganizer из чужого
    потока роняет MO2 не сразу и непонятно где. Плата - последовательность: пока идёт долгий
    вызов, окно менеджера не отвечает.
    """
    _fire = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self._fire.connect(self._run)

    def _run(self, job):
        fn, box, done = job
        try:
            box.append(('ok', fn()))
        except Exception as exc:
            # Исключение переносится целиком, а не строкой. Раньше здесь получался
            # RuntimeError("ValueError: нужен mod"), и транспорт терял единственный
            # признак, по которому ошибку в запросе можно отличить от поломки моста.
            box.append(('err', exc))
        done.set()

    def call(self, fn, timeout=120.0):
        # Из главного потока задание выполняется на месте. Иначе получилось бы, что поток
        # ставит задание сам себе и ждёт, пока сам же его выполнит, - тупик до таймаута.
        if QThread.currentThread() is self.thread():
            return fn()
        box, done = [], threading.Event()
        self._fire.emit((fn, box, done))
        if not done.wait(timeout):
            raise RuntimeError(i18n.t('err.mainThread', sec=timeout))
        kind, val = box[0]
        if kind == 'err':
            raise val
        return val


def new_token():
    return secrets.token_hex(16)


def make_handler(token, routes_get, routes_post):
    """Собрать обработчик HTTP поверх готовых таблиц маршрутов."""

    class Handler(BaseHTTPRequestHandler):
        # молчим в консоль: логи MO2 не должны тонуть в строчках вида "GET /mods 200"
        def log_message(self, *a):
            pass

        def _send(self, code, payload):
            body = json.dumps(payload, ensure_ascii=False, indent=1).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _auth(self):
            # compare_digest, а не '!=': обычное сравнение строк выходит на первом
            # несовпавшем символе, и по времени ответа токен подбирается посимвольно.
            given = self.headers.get('X-Token') or ''
            if not secrets.compare_digest(given, token):
                self._send(403, {'error': i18n.t('err.token')})
                return False
            return True

        def _dispatch(self, table, arg):
            u = urlparse(self.path)
            fn = table.get(u.path)
            if fn is None:
                # список отдаём разделённым: половина ошибок - это чтение, посланное как
                # POST, и наоборот. Общий список путей на такое не намекает никак.
                return self._send(404, {'error': i18n.t('err.noRoute'),
                                        'get': sorted(routes_get),
                                        'post': sorted(routes_post)})
            try:
                self._send(200, fn(arg))
            except ValueError as exc:
                # Ошибка в запросе, а не поломка моста: забытый параметр, чужой режим,
                # неизвестный мод, негодное булево. Раньше и то, и другое выходило
                # пятисоткой с трассировкой, и вызывающий не мог отличить «спросил
                # неверно» от «мост сломался» - то есть не мог решить, повторять ли.
                self._send(400, {'error': str(exc), 'code': 'badRequest'})
            except Exception as exc:
                self._send(500, {'error': str(exc), 'code': 'bridgeFailure',
                                 'trace': traceback.format_exc()[-800:]})

        def do_GET(self):
            if not self._auth():
                return
            self._dispatch(routes_get, parse_qs(urlparse(self.path).query))

        def do_POST(self):
            if not self._auth():
                return
            try:
                n = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(n) or b'{}')
            except Exception as exc:
                return self._send(400, {'error': str(exc)})
            self._dispatch(routes_post, body)

    return Handler


class Server(ThreadingHTTPServer):
    """Своя привязка вместо наследуемой.

    `http.server.HTTPServer` объявляет `allow_reuse_address = 1`, а на Windows это значит,
    что встать на УЖЕ СЛУШАЕМЫЙ адрес можно. Второй экземпляр MO2 поднимал второй мост без
    единой ошибки, писал в лог «поднят», показывал в меню «работает» - и не получал ни
    одного запроса: все они доставались первому. Честный отказ здесь нужнее, чем удобство
    повторной привязки, поэтому обратно выключаем.
    """
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        # Отсутствия SO_REUSEADDR на Windows мало: SO_EXCLUSIVEADDRUSE закрывает и перехват
        # адреса чужим процессом, который встаёт первым.
        opt = getattr(socket, 'SO_EXCLUSIVEADDRUSE', None)
        if opt is not None:
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, opt, 1)
            except OSError:
                pass
        ThreadingHTTPServer.server_bind(self)


def serve(port, handler, tries=1):
    """Поднять сервер в фоне. Слушаем только петлю - наружу порт не выставляется никогда.

    Возвращает пару (сервер, порт). Порт возвращается потому, что он не обязан совпасть
    с заказанным: при занятом пробуем следующие, и вызывающий обязан узнать, где мост в
    итоге встал, - иначе он напишет в файл токена неверное число.
    """
    last = None
    for step in range(max(1, int(tries))):
        try:
            srv = Server(('127.0.0.1', port + step), handler)
        except OSError as exc:
            last = exc
            continue
        threading.Thread(target=srv.serve_forever, name='MO2AIBridge', daemon=True).start()
        return srv, port + step
    raise last
