# -*- coding: utf-8 -*-
"""Bound client input and worker admission on real ephemeral loopback sockets."""
import http.client
import json
import os
import queue
import socket
import sys
import threading
import time
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


def fake_pyqt():
    class Signal:
        def __init__(self, *_args):
            pass

        def connect(self, *_args):
            pass

    core = types.ModuleType('PyQt6.QtCore')
    core.QObject = type('QObject', (), {})
    core.QThread = type('QThread', (), {})
    core.pyqtSignal = Signal
    sys.modules.setdefault('PyQt6', types.ModuleType('PyQt6'))
    sys.modules.setdefault('PyQt6.QtCore', core)


fake_pyqt()
pkg = common.import_package()
runtime = __import__(pkg.__name__ + '.runtime', fromlist=['runtime'])
config = __import__(pkg.__name__ + '.config', fromlist=['config'])
T = common.T
r = common.Report(T('httpcheck.title'))
TOKEN = runtime.new_token()
LIMITS = {'headerTimeoutSec': 0.2, 'bodyTimeoutSec': 0.25,
          'maxBodyBytes': 64, 'maxHandlers': 2}
SETTINGS = config.Config({'http': LIMITS})
calls, admitted = [], queue.Queue()
started, release, completed = threading.Event(), threading.Event(), threading.Event()


def echo(body):
    calls.append(body)
    return {'body': body}


def slow(_body):
    threading.Event().wait(0.55)
    return {'ok': True}


def blocked(_body):
    started.set()
    release.wait(5)
    completed.set()
    return {'ok': True}


BaseHandler = runtime.make_handler(TOKEN, {'/slow': slow, '/blocked': blocked},
                                   {'/echo': echo, '/slow': slow})


class Handler(BaseHandler):
    def setup(self):
        super().setup()
        admitted.put(True)


def connect(port, wait=True):
    client = socket.create_connection(('127.0.0.1', port), timeout=3)
    client.settimeout(3)
    if wait:
        admitted.get(timeout=3)
    return client


def response(client):
    reply = http.client.HTTPResponse(client)
    reply.begin()
    return reply.status, json.loads(reply.read().decode('utf-8'))


def post_header(length=None, extra=b'', route=b'/echo'):
    header = b'POST ' + route + b' HTTP/1.1\r\nHost: localhost\r\nX-Token: ' + TOKEN.encode('ascii') + b'\r\n'
    if length is not None:
        header += b'Content-Length: ' + str(length).encode('ascii') + b'\r\n'
    return header + extra + b'\r\n'


def exchange(port, data, wait=True, half_close=False):
    with connect(port, wait) as client:
        client.sendall(data)
        if half_close:
            client.shutdown(socket.SHUT_WR)
        return response(client)


def drip(client, stop, data=b' '):
    while not stop.wait(0.04):
        try:
            client.sendall(data)
        except OSError:
            return


def timed_input(port, beginning, label, expected):
    with connect(port) as client:
        start = time.monotonic()
        client.sendall(beginning)
        stop = threading.Event()
        sender = threading.Thread(target=drip, args=(client, stop), daemon=True)
        sender.start()
        try:
            code, body = response(client)
            elapsed = time.monotonic() - start
        finally:
            stop.set()
            sender.join(timeout=1)
        r.case(T(label), (code, body.get('code')), (408, 'requestTimeout'))
        r.case(T('httpcheck.deadline'), elapsed < 1.5, True)
        r.case(T('httpcheck.localized'), body.get('error'), pkg.i18n.t(expected))


srv = None
try:
    srv, port = runtime.serve(0, Handler, settings=SETTINGS)
    r.case(T('httpcheck.ephemeral'), (port > 0, srv.server_address[0]), (True, '127.0.0.1'))
    r.case(T('httpcheck.invalidSettings'),
           runtime._http_limits({'http': {'headerTimeoutSec': 0, 'bodyTimeoutSec': 'nan',
                                          'maxBodyBytes': -1, 'maxHandlers': True}}),
           config.DEFAULTS['http'])

    malformed = [('-1', b'', 400), ('+1', b'', 400), ('1.5', b'', 400),
                 ('1, 1', b'', 400), ('2', b'Content-Length: 2\r\n', 400),
                 ('2', b'Content-Length: 3\r\n', 400), ('65', b'', 413),
                 ('9' * 5000, b'', 413),
                 ('0', b'Transfer-Encoding: chunked\r\n', 400)]
    for index, (length, extra, wanted) in enumerate(malformed):
        code, body = exchange(port, post_header(length, extra))
        r.case(T('httpcheck.framing', n=index + 1),
               (code, body.get('code'), 'trace' in body),
               (wanted, 'bodyTooLarge' if wanted == 413 else 'badRequest', False))
    defective = [b'Content-Length : 20\r\n', b'Content-Length\t: 20\r\n',
                 b'Transfer-Encoding : chunked\r\n']
    for index, field in enumerate(defective, len(malformed) + 1):
        code, body = exchange(port, post_header(extra=field) + b'{')
        r.case(T('httpcheck.framing', n=index),
               (code, body.get('code'), 'trace' in body, len(calls)),
               (400, 'badRequest', False, 0))
    r.case(T('httpcheck.invalidNotDispatched'), calls, [])
    r.case(T('httpcheck.emptyBody'), exchange(port, post_header()), (200, {'body': {}}))
    r.case(T('httpcheck.leadingZeros'),
           exchange(port, post_header('0002') + b'{}'), (200, {'body': {}}))
    r.case(T('httpcheck.exactLimit'),
           exchange(port, post_header(64) + b' ' * 62 + b'{}'), (200, {'body': {}}))
    r.case(T('httpcheck.shortBody'),
           exchange(port, post_header(10) + b'{', half_close=True)[0], 400)
    r.case(T('httpcheck.invalidJson'), exchange(port, post_header(1) + b'{')[0], 400)

    timed_input(port, b'GET /slow HTTP/1.1\r\nX-Token: ',
                'httpcheck.partialHeaders', 'http.headerTimeout')
    timed_input(port, post_header(64) + b'{', 'httpcheck.partialBody', 'http.bodyTimeout')
    r.case(T('httpcheck.longGet'),
           exchange(port, b'GET /slow HTTP/1.1\r\nX-Token: ' + TOKEN.encode('ascii') + b'\r\n\r\n'),
           (200, {'ok': True}))
    r.case(T('httpcheck.longPost'), exchange(port, post_header(2, route=b'/slow') + b'{}'),
           (200, {'ok': True}))

    # Two incomplete requests occupy exactly two slots; excess clients get a JSON 503
    # directly from the accept loop, and create no extra request handlers.
    with connect(port) as first, connect(port) as second:
        first.sendall(b'GET /slow HTTP/1.1\r\nX-Token: ')
        second.sendall(post_header(10) + b'{')
        for _ in range(3):
            code, body = exchange(port, b'GET /slow HTTP/1.1\r\n\r\n', wait=False)
            r.case(T('httpcheck.saturated'), (code, body.get('code')), (503, 'serverBusy'))
        refusals = [exchange(port, b'GET /slow HTTP/1.1\r\n\r\n', wait=False)
                    for _ in range(20)]
        r.case(T('httpcheck.repeatedRefusals'),
               all(code == 503 and body.get('code') == 'serverBusy' for code, body in refusals), True)
        r.case(T('httpcheck.boundedDrains'), len(srv._rejected) <= LIMITS['maxHandlers'], True)
        r.case(T('httpcheck.noExtraHandlers'), admitted.empty(), True)
        r.case(T('httpcheck.firstExpires'), response(first)[0], 408)
        r.case(T('httpcheck.secondExpires'), response(second)[0], 408)
    # Wait on the actual admission primitive rather than racing thread cleanup or polling.
    available = srv._slots.acquire(timeout=2)
    r.case(T('httpcheck.capacityReleased'), available, True)
    if available:
        srv._slots.release()
    r.case(T('httpcheck.recovered'), exchange(port, post_header(2) + b'{}')[0], 200)

    with connect(port) as native, connect(port) as partial:
        native.sendall(b'GET /blocked HTTP/1.1\r\nX-Token: ' + TOKEN.encode('ascii') + b'\r\n\r\n')
        r.case(T('httpcheck.operationStarted'), started.wait(2), True)
        partial.sendall(post_header(20) + b'{')
        start = time.monotonic()
        srv.shutdown()
        srv.server_close()
        srv = None
        r.case(T('httpcheck.shutdownBounded'), time.monotonic() - start < 1.5, True)
        r.case(T('httpcheck.nativeStillRunning'), completed.is_set(), False)
        r.case(T('httpcheck.activeClosed'), native.recv(1), b'')
        r.case(T('httpcheck.partialClosed'), partial.recv(1), b'')
        release.set()
        r.case(T('httpcheck.operationCompletes'), completed.wait(2), True)
    srv, port = runtime.serve(0, Handler, settings=SETTINGS)
    with connect(port) as partial:
        partial.sendall(b'GET /slow HTTP/1.1\r\nX-Token: ')
        srv.shutdown()
        srv.server_close()
        srv = None
        r.case(T('httpcheck.partialHeadersClosed'), partial.recv(1), b'')
finally:
    release.set()
    if srv is not None:
        srv.shutdown()
        srv.server_close()

r.done()
