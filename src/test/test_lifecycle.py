# -*- coding: utf-8 -*-
"""Real Qt plugin lifecycle with a fake MO2 and mocked native process/window APIs.

No installed MO2, game, tool or user profile is accessed. HTTP binding is replaced:
the companion transport suite exercises real sockets and shutdown.
"""
import gc
import importlib
import os
import sys
import tempfile
import weakref
from contextlib import ExitStack
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_mo2
fake_mo2.install()
import common
try:
    from PyQt6.QtCore import QCoreApplication
except ImportError:
    print(common.T('lifecycle.skipped'))
    raise SystemExit(77)

plugin = importlib.import_module(common.NAME + '.plugin')
runtime = importlib.import_module(common.NAME + '.runtime')
config = importlib.import_module(common.NAME + '.config')
winapi = importlib.import_module(common.NAME + '.winapi')
base = importlib.import_module(common.NAME + '.base')
app = QCoreApplication.instance() or QCoreApplication([])
r = common.Report(common.T('lifecycle.title'))


def check(key, actual, expected=True):
    r.case(common.T('lifecycle.' + key), actual, expected)


def raises(kind, fn):
    try:
        fn()
    except kind:
        return True
    return False


class Server:
    def __init__(self, handler):
        self.handler = handler
        self.stopped = self.closed = False

    def shutdown(self):
        self.stopped = True

    def server_close(self):
        self.closed = True
        self.handler = None


class Harness:
    def __enter__(self):
        self.stack = ExitStack()
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.fx = fake_mo2.Fixture()
        self.stack.callback(self.fx.cleanup)
        self.alive, self.closed_handles, self.servers = set(), [], []
        self.fail_bind = False
        self.cfg = config.Config({'port': 19400, 'operationWaitSec': 0.2})
        self.stack.enter_context(patch.object(plugin, 'config', return_value=self.cfg))
        self.stack.enter_context(patch.object(plugin, 'log', lambda *args: None))
        self.stack.enter_context(patch.object(plugin, 'token_file',
            lambda port, wanted: os.path.join(self.temp, 'token.txt')))
        self.stack.enter_context(patch.object(runtime, 'serve', self.serve))
        # A valid enabled host window matters: an invalid HWND would hide the defect.
        self.stack.enter_context(patch.object(winapi, 'main_window', return_value=12345))
        self.stack.enter_context(patch.object(winapi, 'is_window', side_effect=lambda hwnd: hwnd == 12345))
        self.stack.enter_context(patch.object(winapi, 'is_enabled', return_value=True))
        self.stack.enter_context(patch.object(winapi, 'pids_by_exe', self.pids))
        self.stack.enter_context(patch.object(winapi, 'duplicate_process_handle', return_value=900))
        self.stack.enter_context(patch.object(winapi, 'process_id', return_value=700))
        self.stack.enter_context(patch.object(winapi, 'process_identity', return_value={
            'pid': 700, 'creationTime': 123, 'path': 'FakeTool.exe', 'exitTime': None}))
        self.stack.enter_context(patch.object(winapi, 'identity_alive',
            side_effect=lambda identity: 'faketool.exe' in self.alive))
        self.stack.enter_context(patch.object(winapi, 'close_process_handle',
                                             self.closed_handles.append))
        self.bridge = plugin.MO2ApIBridge()
        self.bridge.init(self.fx.organizer)
        return self

    def __exit__(self, *args):
        self.bridge._on_quit()
        if self.bridge._quit_hooked:
            app.aboutToQuit.disconnect(self.bridge._on_quit)
        self.stack.close()

    def pids(self, names):
        return {700 + i: name for i, name in enumerate(sorted(self.alive & set(names)))}

    def serve(self, port, handler, tries=1, settings=None):
        if self.fail_bind:
            raise OSError('test bind failure')
        server = Server(handler)
        self.servers.append(server)
        return server, port

    def start(self):
        check('start', self.bridge.start(), None)
        return self.bridge._svc

    def foreign(self, name):
        return self.fx.organizer.startApplication(name)

    def counts(self):
        return (len(self.fx.organizer._about_to_run), len(self.fx.organizer._finished_run))


# A bridge-launched non-game tool remains live with MO2's valid window enabled.
with Harness() as h:
    svc = h.start()
    h.fx.organizer.start_result = 800
    result = svc.run({base.DANGER_KEY: base.DANGER_VALUE, 'binary': 'FakeTool.exe'})
    h.alive.add('faketool.exe')
    check('ownMarked', svc.launched['FakeTool.exe'], {'n': 1, 'mine': True})
    check('ownedHandle', result['processHandleOwned'])
    old_boot, old_key = svc.ctx.boot_id, result['key']
    old_operations = svc.ctx.operations
    retry = {'idempotencyKey': 'same', 'expectedServerBootId': old_boot}
    code, outcome = old_operations.invoke('/test', retry, lambda body: {'applied': True})
    operation_id = outcome['operation']['id']
    old_runner = h.bridge._runner
    h.bridge.stop()
    check('closedHandleOnce', h.closed_handles, [900])
    check('oldOperationsClosed', old_operations.closed)
    check('closedRunner', raises(RuntimeError, lambda: old_runner.call(lambda: None)))
    check('socketClosed', h.servers[-1].stopped and h.servers[-1].closed)
    check('tokenRemoved', os.path.exists(os.path.join(h.temp, 'token.txt')), False)
    check('hostRecordRetained', h.bridge._launches.launched['FakeTool.exe']['mine'])
    del old_operations, old_runner
    previous = weakref.ref(svc)
    previous_context = weakref.ref(svc.ctx)
    previous_operations = weakref.ref(svc.ctx.operations)
    del svc
    app.processEvents()
    gc.collect()
    check('serviceCollectible', previous() is None)
    check('contextCollectible', previous_context() is None)
    check('operationsCollectible', previous_operations() is None)
    svc = h.start()
    check('bootChanged', svc.ctx.boot_id != old_boot)
    check('oldLaunchKeyUnknown', raises(ValueError, lambda: svc.windows({'key': [old_key]})))
    check('oldOperationUnknown', raises(ValueError, lambda: svc.ctx.operations.status(operation_id)))
    check('oldRetryBootRefused', raises(ValueError, lambda: svc.ctx.operations.invoke('/test', retry, lambda b: {})))
    code, fresh = svc.ctx.operations.invoke('/test', dict(retry, expectedServerBootId=svc.ctx.boot_id),
                                            lambda body: {'applied': True, 'fresh': True})
    check('retryScopeFresh', code == 200 and fresh['fresh'] and fresh['operation']['id'] != operation_id)
    check('oldProcessRegistryEmpty', svc.procs, {})
    busy = svc._busy()
    check('nonGameBusy', busy['viaMO2'] and not busy['isGame'] and not busy['windowLocked'])
    refused = svc.toggle({'mod': 'Alpha Mod', 'active': False})
    check('restartMutationRefused', refused.get('reason'), 'busy')
    check('modUnchanged', bool(h.fx.organizer.modList().state('Alpha Mod') & 2))
    h.alive.clear()
    check('ownRetiresAfterExit', svc._busy(), None)
    h.bridge.stop()
    check('noHandleDoubleClose', h.closed_handles, [900])

# Callbacks remain active before first start and throughout a stopped interval.
with Harness() as h:
    h.foreign('BeforeHttp.exe')
    svc = h.start()
    check('beforeFirstStartBusy', svc._busy()['mo2Run'], ['BeforeHttp.exe'])
    h.bridge.stop()
    h.fx.organizer.finish_run('BeforeHttp.exe')
    h.foreign('DuringStop.exe')
    check('stoppedCallbackNoRunner', h.bridge._runner, None)
    check('stoppedForeignRecorded', h.bridge._launches.launched,
          {'DuringStop.exe': {'n': 1, 'mine': False}})
    svc = h.start()
    check('stoppedLaunchRefuses', svc.toggle({'mod': 'Alpha Mod', 'active': False})['reason'], 'busy')
    check('foreignSurvivesNoProcess', svc._busy()['heldByUnknown'])
    h.bridge.stop()
    h.fx.organizer.finish_run('DuringStop.exe')
    svc = h.start()
    check('stoppedFinishClears', svc._busy(), None)

# Failed and repeated HTTP starts never register extra native callbacks.
with Harness() as h:
    for _ in range(3):
        h.start()
        h.bridge.stop()
    h.foreign('SurvivesFailure.exe')
    for _ in range(3):
        h.fail_bind = True
        check('failedStart', bool(h.bridge.start()))
        check('failedStartReleased', (h.bridge._svc, h.bridge._runner, h.bridge._server),
              (None, None, None))
    check('constantSubscriptions', h.counts(), (1, 1))
    check('stableCallbackOwner', all(cb.__self__ is h.bridge for cb in
          h.fx.organizer._about_to_run + h.fx.organizer._finished_run))
    h.fail_bind = False
    svc = h.start()
    check('recordAfterFailures', svc.launched['SurvivesFailure.exe']['n'], 1)
    h.fx.organizer.finish_run('SurvivesFailure.exe')
    check('oneFinishClears', svc._busy(), None)
    h.foreign('Quit.exe')
    app.aboutToQuit.emit()
    check('quitClosesHttp', (h.bridge._svc, h.bridge._runner, h.bridge._server), (None, None, None))
    check('quitClearsEvidence', h.bridge._launches.launched, {})
    h.foreign('LateShutdownCallback.exe')
    check('lateQuitCallbacksIgnored', h.bridge._launches.launched, {})

# A native registration can retain its callback and then raise. It is not retried.
with Harness() as h:
    def register_then_fail(callback):
        h.fx.organizer._about_to_run.append(callback)
        raise RuntimeError('test hook failure after registration')
    h.bridge._runs_hooked = False
    h.fx.organizer._about_to_run.clear()
    h.fx.organizer._finished_run.clear()
    with patch.object(h.fx.organizer, 'onAboutToRun', register_then_fail):
        h.bridge._subscribe_runs()
        for _ in range(3):
            h.start()
            h.bridge.stop()
            h.bridge._subscribe_runs()
    check('failedHookNotDuplicated', h.counts(), (1, 1))
    h.foreign('Single.exe')
    check('failedHookRecordsOnce', h.bridge._launches.launched['Single.exe']['n'], 1)

r.done()
