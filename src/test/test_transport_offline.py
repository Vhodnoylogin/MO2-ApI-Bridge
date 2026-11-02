# -*- coding: utf-8 -*-
"""The transport: the socket, the token, the reply codes - without a running MO2.

Why. The bridge's lowest layer was never checked at all: it pulls in PyQt6, and that lives
only inside the manager's process. So three properties that entirely determine what a client
sees rested on nothing but good intentions - and two of them turned out to be broken.

What is checked here, on a REAL loopback socket:

  binding  a taken port must refuse rather than succeed silently. HTTPServer declares
           allow_reuse_address = 1, and on Windows a second bridge bound an already-listening
           address without a single error - it logged "listening" and received no requests.
  codes    a mistake in the request (400) is distinguishable from a broken bridge (500).
           Both used to come back as a 500 with a traceback, and the caller could not decide
           whether to retry.
  token    without the X-Token header - 403, and the route is never reached.

PyQt6 is stubbed just enough for the module to import: the transport knows about Qt in one
place only, MainThreadRunner, and that plays no part here - the routes are stand-ins.
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
    """A PyQt6 stub: enough to import runtime, not enough to run the main thread.

    There is no point stubbing the whole thing. The transport needs Qt in one place - moving
    a call into the main thread - and here the routes are stand-ins with nothing to move.
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
i18n.set_language('en')
r = common.Report(T('transport.title'))


def ask(port, route, token=None, body=None):
    """A request to the raised bridge. Returns (code, parsed reply)."""
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


# The routes are stand-ins: the transport knows nothing of their meaning, and must not.
def route_ok(_arg):
    return {'ok': True}


def route_bad_request(_arg):
    raise ValueError(i18n.t('err.needMod'))


def route_broken(_arg):
    raise KeyError('an internal breakage')


GET = {'/ping': route_ok, '/bad': route_bad_request, '/broken': route_broken}
POST = {'/do': route_ok, '/badpost': route_bad_request}

TOKEN = runtime.new_token()
# The port is high and deliberately not 8930: the suite must not disturb the real bridge
# if MO2 happens to be open.
BASE_PORT = int(os.environ.get('MO2AIBRIDGE_TEST_PORT') or 18930)

srv = second = None
try:
    srv, port = runtime.serve(BASE_PORT, runtime.make_handler(TOKEN, GET, POST), tries=20)
    r.head(T('transport.binding'))
    r.case(T('transport.boundFreePort'), port >= BASE_PORT, True)
    r.case(T('transport.serveReturnsPair'), isinstance(port, int), True)

    # A taken port must refuse. This is exactly what was fixed: with the inherited
    # allow_reuse_address the second bind silently succeeded and the requests went to the
    # first one.
    try:
        runtime.Server(('127.0.0.1', port), runtime.make_handler(TOKEN, GET, POST))
        r.case(T('transport.takenPortRefusedFail'), 'the bind succeeded', 'OSError')
    except OSError:
        r.case(T('transport.takenPortRefused'), True, True)

    # And serve does not fail on a taken port: it steps to the next one and says which.
    second, port2 = runtime.serve(port, runtime.make_handler(TOKEN, GET, POST), tries=5)
    r.case(T('transport.secondOnOtherPort'), port2 != port, True)
    r.case(T('transport.secondIsNextFree'), port2 > port, True)

    r.head(T('transport.token'))
    r.case(T('transport.noHeader403'), ask(port, '/ping')[0], 403)
    # The token rides in a header, and HTTP headers are latin-1: deliberately wrong, but
    # ASCII.
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
                                 data='{this is not json'.encode('utf-8'),
                                 headers={'X-Token': TOKEN,
                                          'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(req, timeout=10)
        r.case(T('transport.badJson400Fail'), 'answered 200', 400)
    except urllib.error.HTTPError as exc:
        r.case(T('transport.badJson400'), exc.code, 400)
    # An empty body is not a mistake: a route without parameters is called exactly so.
    code, res = ask(port, '/do', TOKEN, body={})
    r.case(T('transport.emptyBodyOk'), (code, res), (200, {'ok': True}))

    r.head(T('transport.shutdown'))
    srv.shutdown()
    srv.server_close()
    srv = None
    try:
        ask(port, '/ping', TOKEN)
        r.case(T('transport.portSilentAfterStopFail'), 'answered', 'connection refused')
    except Exception:
        r.case(T('transport.portSilentAfterStop'), True, True)
    # The freed port can be bound again - which means the socket really was closed, not
    # left hanging as a daemon until the process ends.
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
