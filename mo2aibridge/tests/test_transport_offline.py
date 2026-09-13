# -*- coding: utf-8 -*-
"""Транспорт: сокет, токен, коды ответов - без запущенной MO2.

Зачем. Нижний слой моста не проверялся вовсе: он тянет PyQt6, а тот живёт только внутри
процесса менеджера. Поэтому три свойства, которые целиком определяют, что видит клиент,
держались на честном слове - и два из них оказались сломаны.

Что здесь проверяется на НАСТОЯЩЕМ сокете петли:

  привязка   занятый порт обязан дать отказ, а не молчаливый успех. HTTPServer объявляет
             allow_reuse_address = 1, и на Windows второй мост вставал на уже слушаемый
             адрес без единой ошибки - писал в лог «поднят» и не получал ни одного запроса.
  коды       ошибка в запросе (400) отличима от поломки моста (500). Раньше и то, и другое
             выходило пятисоткой с трассировкой, и вызывающий не мог решить, повторять ли.
  токен      без заголовка X-Token - 403, и до маршрута дело не доходит.

PyQt6 подменён пустышкой ровно настолько, чтобы модуль импортировался: сам транспорт
про Qt знает только в MainThreadRunner, а он здесь не участвует - маршруты подставные.
"""
import json
import os
import sys
import types
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

T = common.T


def fake_pyqt():
    """Пустышка PyQt6: хватает на импорт runtime, но не на работу главного потока.

    Подменять целиком незачем. Транспорту Qt нужен в одном месте - переносе вызова в
    главный поток, - а здесь маршруты подставные и переносить нечего.
    """
    class Signal(object):
        def __init__(self, *_a):
            pass

        def __get__(self, obj, owner=None):
            return self

        def connect(self, *_a):
            pass

        def emit(self, *_a):
            pass

    qtcore = types.ModuleType('PyQt6.QtCore')
    qtcore.QObject = type('QObject', (object,), {'__init__': lambda self: None,
                                                 'thread': lambda self: None})
    qtcore.QThread = type('QThread', (object,), {'currentThread': staticmethod(lambda: None)})
    qtcore.pyqtSignal = Signal
    root = types.ModuleType('PyQt6')
    root.QtCore = qtcore
    sys.modules.setdefault('PyQt6', root)
    sys.modules.setdefault('PyQt6.QtCore', qtcore)


fake_pyqt()
pkg = common.import_package()
runtime = __import__(pkg.__name__ + '.runtime', fromlist=['runtime'])
i18n = pkg.i18n
i18n.set_language('ru')
r = common.Report('транспорт без MO2')


def ask(port, route, token=None, body=None):
    """Запрос к поднятому мосту. Возвращает (код, разобранный ответ)."""
    data = json.dumps(body).encode('utf-8') if body is not None else None
    headers = {'Content-Type': 'application/json'}
    if token is not None:
        headers['X-Token'] = token
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, route),
                                 data=data, method='POST' if data is not None else 'GET',
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode('utf-8'))


# Маршруты подставные: транспорт про их смысл ничего не знает и знать не должен.
def route_ok(_arg):
    return {'ok': True}


def route_bad_request(_arg):
    raise ValueError(i18n.t('err.needMod'))


def route_broken(_arg):
    raise KeyError('внутренняя поломка')


GET = {'/ping': route_ok, '/bad': route_bad_request, '/broken': route_broken}
POST = {'/do': route_ok, '/badpost': route_bad_request}

TOKEN = runtime.new_token()
# Порт нарочно высокий и не 8930: набор не должен мешать настоящему мосту, если MO2 открыта.
BASE_PORT = int(os.environ.get('MO2AIBRIDGE_TEST_PORT') or 18930)

srv = second = None
try:
    srv, port = runtime.serve(BASE_PORT, runtime.make_handler(TOKEN, GET, POST), tries=20)
    r.head('привязка')
    r.case('встал на свободный порт', port >= BASE_PORT, True)
    r.case('serve вернул пару, а не один сервер', isinstance(port, int), True)

    # Занятый порт обязан дать отказ. Это и есть то, что чинилось: с наследуемым
    # allow_reuse_address вторая привязка молча удавалась, и запросы шли первому.
    try:
        runtime.Server(('127.0.0.1', port), runtime.make_handler(TOKEN, GET, POST))
        r.case('занятый порт отвергнут', 'привязка удалась', 'OSError')
    except OSError:
        r.case('занятый порт отвергнут', True, True)

    # А serve при занятом порте не падает, а отходит на следующий - и говорит, на какой.
    second, port2 = runtime.serve(port, runtime.make_handler(TOKEN, GET, POST), tries=5)
    r.case('второй мост встал на другой порт', port2 != port, True)
    r.case('второй порт - следующий свободный', port2 > port, True)

    r.head('токен')
    r.case('без заголовка - 403', ask(port, '/ping')[0], 403)
    # Токен в заголовке, а заголовки HTTP - latin-1: нарочно чужой, но ASCII.
    r.case('чужой токен - 403', ask(port, '/ping', 'not-the-token')[0], 403)
    r.case('пустой токен - 403', ask(port, '/ping', '')[0], 403)
    code, res = ask(port, '/ping', TOKEN)
    r.case('свой токен - 200', (code, res), (200, {'ok': True}))
    r.case('до маршрута без токена дело не доходит',
           ask(port, '/broken')[0], 403)

    r.head('коды ответа')
    code, res = ask(port, '/bad', TOKEN)
    r.case('ошибка в запросе - 400', code, 400)
    r.case('400 помечен машиночитаемо', res.get('code'), 'badRequest')
    r.case('400 несёт текст i18n, а не трассировку', res.get('error'), i18n.t('err.needMod'))
    r.case('в 400 трассировки нет', 'trace' in res, False)

    code, res = ask(port, '/broken', TOKEN)
    r.case('поломка моста - 500', code, 500)
    r.case('500 помечен машиночитаемо', res.get('code'), 'bridgeFailure')
    r.case('в 500 трассировка есть', 'trace' in res, True)

    code, res = ask(port, '/badpost', TOKEN, body={})
    r.case('на POST ошибка запроса тоже 400', (code, res.get('code')), (400, 'badRequest'))

    r.head('маршрутов нет')
    code, res = ask(port, '/no-such-route', TOKEN)
    r.case('неизвестный путь - 404', code, 404)
    r.case('404 перечисляет чтение и изменения отдельно',
           (sorted(res.get('get') or []), sorted(res.get('post') or [])),
           (sorted(GET), sorted(POST)))
    r.case('чтение, посланное как POST, - 404 со списком',
           ask(port, '/ping', TOKEN, body={})[0], 404)

    r.head('тело запроса')
    req = urllib.request.Request('http://127.0.0.1:%d/do' % port, method='POST',
                                 data='{это не json'.encode('utf-8'),
                                 headers={'X-Token': TOKEN,
                                          'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(req, timeout=10)
        r.case('нечитаемый JSON - 400', 'ответил 200', 400)
    except urllib.error.HTTPError as exc:
        r.case('нечитаемый JSON - 400', exc.code, 400)
    # Пустое тело - не ошибка: маршрут без параметров зовут именно так.
    code, res = ask(port, '/do', TOKEN, body={})
    r.case('пустое тело - обычный вызов', (code, res), (200, {'ok': True}))

    r.head('остановка')
    srv.shutdown()
    srv.server_close()
    srv = None
    try:
        ask(port, '/ping', TOKEN)
        r.case('после остановки порт не отвечает', 'ответил', 'отказ соединения')
    except Exception:
        r.case('после остановки порт не отвечает', True, True)
    # Освободившийся порт снова занимается - значит сокет действительно закрыт, а не
    # оставлен висеть демоном до конца процесса.
    again, port3 = runtime.serve(port, runtime.make_handler(TOKEN, GET, POST), tries=1)
    r.case('освободившийся порт занимается снова', port3, port)
    again.shutdown()
    again.server_close()
finally:
    for s in (srv, second):
        if s is not None:
            try:
                s.shutdown()
                s.server_close()
            except Exception:
                pass

r.done()
