# -*- coding: utf-8 -*-
"""Standalone real Qt, HTTP and process regression tests for safe operations.

No MO2, game or user profile is opened. Standalone PyQt6 is required.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_mo2
fake_mo2.install()
import common
try:
    from PyQt6.QtCore import QCoreApplication
except ImportError:
    print(common.T('protocol.skipped'))
    raise SystemExit(77)
from importlib import import_module
runtime = import_module(common.NAME + '.runtime')
operations = import_module(common.NAME + '.operations')
services = import_module(common.NAME + '.services')
config = import_module(common.NAME + '.config')
winapi = import_module(common.NAME + '.winapi')
base = import_module(common.NAME + '.base')
app = QCoreApplication.instance() or QCoreApplication([])
r = common.Report(common.T('protocol.title'))


def check(label, actual, expected=True):
    r.case(common.T('protocol.' + label), actual, expected)


def error(fn):
    try:
        fn()
    except Exception as exc:
        return exc
    return None


def drain_until(test, seconds=2):
    deadline = time.monotonic() + seconds
    while not test() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.002)
    return test()


runner = runtime.MainThreadRunner()
marks, outcome = [], []
def queued():
    outcome.append(error(lambda: runner.call(lambda: marks.append('late'), timeout=0.02)))
t = threading.Thread(target=queued)
t.start(); t.join(2)
check('expiredError', isinstance(outcome[0], operations.QueueExpired))
app.processEvents()
check('expiredNeverRuns', marks, [])
check('expiredStatus', outcome[0].job.snapshot()['state'], 'cancelled')
# Expiry is checked on dequeue too, independent of a delayed waiting thread.
job = runtime.MainThreadJob('dequeue', lambda: marks.append('expired'), 0)
runner._run(job)
check('dequeueExpired', (job.state, marks), ('cancelled', []))

store = operations.Operations(runner.boot_id, wait=0.01)
release, entered = threading.Event(), threading.Event()
counts = []
def native():
    entered.set()
    release.wait(2)
    counts.append('once')
    return {'applied': True, 'value': 7}
def route(body):
    return runner.call(native, timeout=0.15)
body = {'idempotencyKey': 'one', 'operationId': 'caller-op', 'callerRunId': 'run',
        'expectedServerBootId': runner.boot_id}
replies = []
for _ in range(5):
    replies.append(store.invoke('/run', body, route))
identifier = replies[0][1]['operation']['id']
check('duplicatesPending', [code for code, _ in replies], [202] * 5)
check('sameOperation', len({reply['operation']['id'] for _, reply in replies}), 1)
# The Qt callback blocks here; a separate observer proves that polling remains
# available after its native wait expires, then lets the native work complete.
observed = []
def observer():
    entered.wait(1)
    time.sleep(0.2)
    observed.append(store.status(identifier)['terminal'])
    release.set()
observer_thread = threading.Thread(target=observer)
observer_thread.start()
app.processEvents()
observer_thread.join(2)
check('startedStillPending', observed, [False])
check('completeEventually', drain_until(lambda: store.status(identifier)['terminal']))
check('nativeRunsOnce', counts, ['once'])
code, replay = store.invoke('/run', body, route)
check('replaySuccess', (code, replay['value']), (200, 7))
check('replayNoLaunch', counts, ['once'])
check('conflictingBody', isinstance(error(lambda: store.invoke('/run', dict(body, args=['different']), route)), ValueError))
check('staleBoot', isinstance(error(lambda: store.invoke('/run', dict(body, expectedServerBootId='old'), route)), ValueError))
check('bootRequired', isinstance(error(lambda: store.invoke('/run', {'idempotencyKey': 'x'}, route)), ValueError))
check('unknownOperation', isinstance(error(lambda: operations.Operations('new').status(identifier)), ValueError))
tiny = operations.Operations('tiny', limit=1, wait=1)
tiny.invoke('/x', {}, lambda b: {'applied': True})
check('capacityRefuses', isinstance(error(lambda: tiny.invoke('/x', {}, lambda b: counts.append('extra'))), RuntimeError))
check('capacityNoWork', counts, ['once'])

# Real HTTP response loss/pending and terminal polling use the same record.
http_store = operations.Operations('http', wait=0.01)
gate, http_calls = threading.Event(), []
def http_work(body):
    gate.wait(2)
    http_calls.append(1)
    return {'started': True, 'pid': 123, 'applied': True}
handler = runtime.make_handler('test-token',
    {'/operations': lambda q: http_store.status(q['id'][0])}, {'/run': http_work}, http_store)
srv, port = runtime.serve(0, handler)
port = srv.server_address[1]
def request(method, path, body=None):
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path), method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={'X-Token': 'test-token'})
    with urllib.request.urlopen(req, timeout=2) as response:
        return response.status, json.load(response)
try:
    http_body = {'idempotencyKey': 'retry', 'expectedServerBootId': 'http'}
    code, first = request('POST', '/run', http_body)
    code2, second = request('POST', '/run', http_body)
    check('httpPending', (code, code2), (202, 202))
    check('httpDeduplicated', first['operation']['id'], second['operation']['id'])
    gate.set()
    http_id = first['operation']['id']
    check('httpTerminal', drain_until(lambda: http_store.status(http_id)['terminal']))
    check('httpPollResult', request('GET', '/operations?id=' + http_id)[1]['result']['started'])
    check('httpOnlyOnce', http_calls, [1])
finally:
    gate.set(); srv.shutdown(); srv.server_close()

fx = fake_mo2.Fixture()
svc = services.Services(fx.organizer, lambda fn, timeout=None: fn(), 'README.md')
svc.game_exe, svc._self_hwnd = 'nosuch.exe', 0
key = {base.DANGER_KEY: base.DANGER_VALUE}
try:
    check('profileMismatch', isinstance(error(lambda: svc.run(dict(key, binary='FakeTool', expectedProfile='wrong'))), ValueError))
    check('instanceMismatch', isinstance(error(lambda: svc.run(dict(key, binary='FakeTool', expectedInstance='wrong'))), ValueError))
    check('targetNoLaunch', fx.organizer.started, [])
    launch = svc.run(dict(key, binary='FakeTool', expectedProfile='Claude', expectedInstance=svc.ctx.instance_id))
    check('bootScopedKey', launch['key'].startswith(svc.ctx.boot_id + ':p'))
    other = services.Services(fx.organizer, lambda fn, timeout=None: fn(), 'README.md')
    check('restartDifferentBoot', svc.ctx.boot_id != other.ctx.boot_id)
    check('oldKeyRejected', isinstance(error(lambda: other.windows({'key': [launch['key']]})), ValueError))
    svc.profileops.clone_native = lambda old, new, *args: shutil.copytree(fx.path('profiles', old), fx.path('profiles', new))
    svc.profileops.select_native = lambda name, *args: setattr(fx.organizer, '_profile', name)
    cloned = svc.profiles_clone(dict(key, profile='Second', newName='Copied'))
    check('cloneApplied', cloned['applied'])
    check('cloneCurrentKept', fx.organizer.profile().name(), 'Claude')
    check('cloneReversal', cloned['reversal']['createdPath'], fx.path('profiles', 'Copied'))
    selected = svc.profiles_select(dict(key, profile='Copied'))
    check('selectApplied', selected['current'], 'Copied')
    check('selectUndo', svc.profiles_select(dict(key, **selected['undo']['body']))['current'], 'Claude')
    check('localFlagsRefused', svc.profiles_local({'profile': 'Second'})['reason'], 'unsupported')
finally:
    fx.cleanup()

if os.name == 'nt':
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)'],
                             creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        identity = winapi.process_identity(child._handle)
        check('processPid', identity['pid'], child.pid)
        check('processBirth', identity['creationTime'] > 0)
        check('processPath', os.path.normcase(identity['path']), os.path.normcase(sys.executable))
        check('processAlive', winapi.identity_alive(identity))
        check('pidReuseRefused', winapi.identity_alive(dict(identity, creationTime=identity['creationTime'] - 1)), False)
        parent = winapi.identity_by_pid(os.getpid())
        descendants = winapi.game_descendants(parent, os.path.basename(sys.executable).lower())
        check('childCausalMatch', child.pid in [c['pid'] for c in descendants])
    finally:
        child.terminate(); child.wait(timeout=3)
    check('processDead', winapi.identity_alive(identity), False)
runner.close()
check('shutdownRejects', isinstance(error(lambda: runner.call(lambda: marks.append('closed'))), RuntimeError))
check('shutdownNoMutation', marks, [])

# Closing the runner cancels jobs already accepted into Qt's queue.
shutdown_runner = runtime.MainThreadRunner()
shutdown_errors = []
t = threading.Thread(target=lambda: shutdown_errors.append(error(
    lambda: shutdown_runner.call(lambda: marks.append('shutdown-late'), timeout=1))))
t.start()
deadline = time.monotonic() + 1
while not shutdown_runner.jobs and time.monotonic() < deadline:
    time.sleep(0.002)
shutdown_runner.close()
t.join(2)
app.processEvents()
check('shutdownQueuedCancelled', isinstance(shutdown_errors[0], operations.QueueExpired))
check('shutdownQueuedNoWork', marks, [])

# Simultaneous retries reserve one record atomically; terminal failures replay too.
concurrent = operations.Operations('concurrent', wait=0.01)
barrier, gate = threading.Barrier(8), threading.Event()
concurrent_calls, concurrent_replies = [], []
concurrent_body = {'idempotencyKey': 'same', 'expectedServerBootId': 'concurrent'}
def failing_work(body):
    gate.wait(2)
    concurrent_calls.append(1)
    raise ValueError('test-failure')
def retry():
    barrier.wait(timeout=2)
    concurrent_replies.append(concurrent.invoke('/run', concurrent_body, failing_work))
threads = [threading.Thread(target=retry) for _ in range(8)]
for thread in threads: thread.start()
for thread in threads: thread.join(3)
check('concurrentOneRecord', len({reply['operation']['id'] for _, reply in concurrent_replies}), 1)
check('concurrentAllPending', [code for code, _ in concurrent_replies], [202] * 8)
gate.set()
concurrent_id = concurrent_replies[0][1]['operation']['id']
check('concurrentTerminal', drain_until(lambda: concurrent.status(concurrent_id)['terminal']))
code, replay = concurrent.invoke('/run', concurrent_body, failing_work)
check('failureReplays', (code, replay['error']), (400, 'test-failure'))
check('failureRunsOnce', concurrent_calls, [1])

# A read caller also receives a pollable result if its native job has started.
read_runner = runtime.MainThreadRunner()
read_entered, read_release = threading.Event(), threading.Event()
read_errors = []
def read_native():
    read_entered.set()
    read_release.wait(2)
    return {'read': 42}
t = threading.Thread(target=lambda: read_errors.append(error(
    lambda: read_runner.call(read_native, timeout=0.1))))
t.start()
def read_observer():
    read_entered.wait(1)
    t.join(1)
    read_release.set()
observer_thread = threading.Thread(target=read_observer)
observer_thread.start()
app.processEvents()
observer_thread.join(2)
check('readStartedPending', isinstance(read_errors[0], operations.JobPending))
check('readEventuallyPolls', read_runner.status(read_errors[0].job.identifier)['result'], {'read': 42})
r.done()
