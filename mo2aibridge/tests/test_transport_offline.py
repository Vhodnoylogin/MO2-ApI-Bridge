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
r = common.Report(T('transport.title'))


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
    r.head(T('transport.binding'))
    r.case(T('transport.boundFreePort'), port >= BASE_PORT, True)
    r.case(T('transport.serveReturnsPair'), isinstance(port, int), True)

    # Занятый порт обязан дать отказ. Это и есть то, что чинилось: с наследуемым
    # allow_reuse_address вторая привязка молча удавалась, и запросы шли первому.
    try:
        runtime.Server(('127.0.0.1', port), runtime.make_handler(TOKEN, GET, POST))
        r.case(T('transport.takenPortRefusedFail'), 'привязка удалась', 'OSError')
    except OSError:
        r.case(T('transport.takenPortRefused'), True, True)

    # А serve при занятом порте не падает, а отходит на следующий - и говорит, на какой.
    second, port2 = runtime.serve(port, runtime.make_handler(TOKEN, GET, POST), tries=5)
    r.case(T('transport.secondOnOtherPort'), port2 != port, True)
    r.case(T('transport.secondIsNextFree'), port2 > port, True)

    r.head(T('transport.token'))
    r.case(T('transport.noHeader403'), ask(port, '/ping')[0], 403)
    # Токен в заголовке, а заголовки HTTP - latin-1: нарочно чужой, но ASCII.
    r.case(T('transport.wrongToken403'), ask(port, '/ping', 'not-the-token')[0], 403)
    r.case(T('transport.emptyToken403'), ask(port, '/ping', '')[0], 403)
    code, res = ask(port, '/ping', TOKEN)
    r.case(T('transport.ownToken200'), (code, res), (200, {'ok': True}))
    r.case(T('transport.routeNotReachedWithoutToken'),
           ask(port, '/broken')[0], 403)

    r.head(T('transport.replyCodes'))
    code, res = ask(port, '/bad', TOKEN)
    r.case(T('transport.badRequest400'), code, 400)
    r.case(T('transport.badRequestMarked'), res.get('code'), 'badRequest')
    r.case(T('transport.badRequestCarriesI18n'), res.get('error'), i18n.t('err.needMod'))
    r.case(T('transport.badRequestNoTrace'), 'trace' in res, False)

    code, res = ask(port, '/broken', TOKEN)
    r.case(T('transport.bridgeFailure500'), code, 500)
    r.case(T('transport.bridgeFailureMarked'), res.get('code'), 'bridgeFailure')
    r.case(T('transport.bridgeFailureHasTrace'), 'trace' in res, True)

    code, res = ask(port, '/badpost', TOKEN, body={})
    r.case(T('transport.postBadRequestToo'), (code, res.get('code')), (400, 'badRequest'))

    r.head(T('transport.noSuchRoute'))
    code, res = ask(port, '/no-such-route', TOKEN)
    r.case(T('transport.unknownPath404'), code, 404)
    r.case(T('transport.404ListsBothTables'),
           (sorted(res.get('get') or []), sorted(res.get('post') or [])),
           (sorted(GET), sorted(POST)))
    r.case(T('transport.readSentAsPost404'),
           ask(port, '/ping', TOKEN, body={})[0], 404)

    r.head(T('transport.requestBody'))
    req = urllib.request.Request('http://127.0.0.1:%d/do' % port, method='POST',
                                 data='{это не json'.encode('utf-8'),
                                 headers={'X-Token': TOKEN,
                                          'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(req, timeout=10)
        r.case(T('transport.badJson400Fail'), 'ответил 200', 400)
    except urllib.error.HTTPError as exc:
        r.case(T('transport.badJson400'), exc.code, 400)
    # Пустое тело - не ошибка: маршрут без параметров зовут именно так.
    code, res = ask(port, '/do', TOKEN, body={})
    r.case(T('transport.emptyBodyOk'), (code, res), (200, {'ok': True}))

    r.head(T('transport.shutdown'))
    srv.shutdown()
    srv.server_close()
    srv = None
    try:
        ask(port, '/ping', TOKEN)
        r.case(T('transport.portSilentAfterStopFail'), 'ответил', 'отказ соединения')
    except Exception:
        r.case(T('transport.portSilentAfterStop'), True, True)
    # Освободившийся порт снова занимается - значит сокет действительно закрыт, а не
    # оставлен висеть демоном до конца процесса.
    again, port3 = runtime.serve(port, runtime.make_handler(TOKEN, GET, POST), tries=1)
    r.case(T('transport.freedPortBindableAgain'), port3, port)
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
