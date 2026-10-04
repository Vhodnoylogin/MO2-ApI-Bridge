# -*- coding: utf-8 -*-
"""The transport layer: Qt's main thread and the HTTP server.

It knows threads, sockets, JSON and the token. It knows nothing of MO2 or of what the routes
actually do - it is handed a ready "path -> function" table. So the logic can change without
touching the transport, and the other way round.
"""
import json
import secrets
import socket
import threading
import traceback
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from . import i18n, operations


class MainThreadJob:
    def __init__(self, identifier, fn, timeout):
        self.identifier, self.fn = identifier, fn
        self.deadline = time.monotonic() + max(0, float(timeout))
        self.lock, self.done = threading.RLock(), threading.Event()
        self.state, self.value, self.error = 'queued', None, None

    def cancel_queued(self):
        with self.lock:
            if self.state != 'queued':
                return False
            self.state = 'cancelled'
            self.fn = None
            self.done.set()
            return True

    def snapshot(self, include_result=True):
        with self.lock:
            out = {'id': self.identifier, 'state': self.state,
                   'terminal': self.done.is_set()}
            if self.error is not None:
                out['error'] = str(self.error)
            elif include_result and self.state == 'succeeded':
                try:
                    json.dumps(self.value)
                    out['result'] = self.value
                except (TypeError, ValueError):
                    out['resultAvailable'] = False
            return out


class MainThreadRunner(QObject):
    """Carries a call from the server thread into Qt's main thread and waits for the result.

    There is no way around it: mobase and Qt live on the main thread, and touching
    IOrganizer from another one brings MO2 down later and elsewhere. The price is
    serialisation: while a long call runs, the manager's window does not respond.
    """
    _fire = pyqtSignal(object)

    def __init__(self, limit=4096):
        super().__init__()
        self.boot_id = uuid.uuid4().hex
        self.jobs, self.lock, self.limit, self.closed = {}, threading.RLock(), int(limit), False
        self._fire.connect(self._run)

    def _run(self, job):
        with job.lock:
            if job.state != 'queued':
                return
            if self.closed or time.monotonic() >= job.deadline:
                job.cancel_queued()
                return
            job.state = 'running'
            fn, job.fn = job.fn, None
        try:
            value = fn()
        except Exception as exc:
            # The exception is carried over whole, not as a string. This used to produce a
            # RuntimeError("ValueError: mod is required"), and the transport lost the one
            # marker that tells a mistake in the request from a broken bridge.
            with job.lock:
                job.error, job.state = exc, 'failed'
        else:
            with job.lock:
                job.value, job.state = value, 'succeeded'
        finally:
            job.done.set()

    def call(self, fn, timeout=120.0):
        if self.closed:
            raise RuntimeError(i18n.t('err.runnerClosed'))
        # From the main thread the job runs in place. Otherwise the thread would queue a job
        # for itself and wait for itself to run it - a deadlock until the timeout.
        if QThread.currentThread() is self.thread():
            return fn()
        with self.lock:
            if self.closed:
                raise RuntimeError(i18n.t('err.runnerClosed'))
            # Completed native jobs may be discarded; POST outcomes/retry keys live
            # independently in Operations and are never evicted during a boot.
            if len(self.jobs) >= self.limit:
                for identifier, prior in list(self.jobs.items()):
                    if prior.done.is_set():
                        del self.jobs[identifier]
                        break
                else:
                    raise RuntimeError(i18n.t('err.operationCapacity'))
            job = MainThreadJob(self.boot_id + ':job:' + uuid.uuid4().hex, fn, timeout)
            self.jobs[job.identifier] = job
        record = operations.active()
        if record is not None:
            with record.lock:
                record.job = job
        self._fire.emit(job)
        if not job.done.wait(max(0, float(timeout))):
            if job.cancel_queued():
                raise operations.QueueExpired(job)
            if record is None:
                raise operations.JobPending(job)
            # The HTTP operation has its own bounded wait and pollable outcome.
            # Keep this continuation alive so native completion can finish the domain
            # operation (launch bookkeeping, verification and reversal data).
            job.done.wait()
        if job.state == 'cancelled':
            raise operations.QueueExpired(job)
        if job.error is not None:
            raise job.error
        return job.value

    def status(self, identifier):
        with self.lock:
            job = self.jobs.get(identifier)
        if job is None:
            raise ValueError(i18n.t('err.noOperation', id=identifier))
        return job.snapshot()

    def close(self):
        with self.lock:
            self.closed = True
            for job in self.jobs.values():
                job.cancel_queued()


def new_token():
    return secrets.token_hex(16)


def make_handler(token, routes_get, routes_post, operation_store=None):
    """Build the HTTP handler on top of ready route tables."""

    class Handler(BaseHTTPRequestHandler):
        # stay quiet on the console: MO2's log must not drown in "GET /mods 200" lines
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
            # compare_digest, not '!=': an ordinary string comparison returns on the first
            # mismatched character, and the token could be guessed one character at a time
            # from the response timing.
            given = self.headers.get('X-Token') or ''
            if not secrets.compare_digest(given, token):
                self._send(403, {'error': i18n.t('err.token')})
                return False
            return True

        def _dispatch(self, table, arg):
            u = urlparse(self.path)
            fn = table.get(u.path)
            if fn is None:
                # The list is returned split in two: half of all mistakes are a read sent as
                # a POST, or the other way round. A single combined list hints at none of it.
                return self._send(404, {'error': i18n.t('err.noRoute'),
                                        'get': sorted(routes_get),
                                        'post': sorted(routes_post)})
            try:
                if self.command == 'POST' and operation_store is not None:
                    code, result = operation_store.invoke(u.path, arg, fn)
                    self._send(code, result)
                else:
                    self._send(200, fn(arg))
            except operations.QueueExpired as exc:
                self._send(504, {'error': str(exc), 'code': 'queueExpired',
                                 'job': exc.job.snapshot()})
            except operations.JobPending as exc:
                self._send(202, {'pending': True, 'job': exc.job.snapshot()})
            except ValueError as exc:
                # A mistake in the request, not a broken bridge: a forgotten parameter, an
                # unknown mode, an unknown mod, a bad boolean. Both used to come back as a
                # 500 with a traceback, and the caller could not tell "you asked wrong" from
                # "the bridge broke" - that is, could not decide whether to retry.
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
    """Our own binding instead of the inherited one.

    `http.server.HTTPServer` declares `allow_reuse_address = 1`, and on Windows that means
    binding to an ALREADY LISTENING address is allowed. A second MO2 instance raised a second
    bridge without a single error, logged "listening", showed "running" in the menu - and
    received no requests at all: they all went to the first one. An honest refusal matters
    more here than the convenience of rebinding, so it goes back off.
    """
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        # The absence of SO_REUSEADDR is not enough on Windows: SO_EXCLUSIVEADDRUSE also
        # closes off an address being taken over by a foreign process that binds first.
        opt = getattr(socket, 'SO_EXCLUSIVEADDRUSE', None)
        if opt is not None:
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, opt, 1)
            except OSError:
                pass
        ThreadingHTTPServer.server_bind(self)


def serve(port, handler, tries=1):
    """Raise the server in the background. Loopback only - the port is never exposed outside.

    Returns the pair (server, port). The port comes back because it need not be the one
    asked for: when it is taken we try the next ones, and the caller must learn where the
    bridge actually ended up - otherwise it writes the wrong number into the token file.
    """
    last = None
    for step in range(max(1, int(tries))):
        try:
            srv = Server(('127.0.0.1', port + step), handler)
        except OSError as exc:
            last = exc
            continue
        threading.Thread(target=srv.serve_forever, name='MO2ApIBridge', daemon=True).start()
        return srv, port + step
    raise last
