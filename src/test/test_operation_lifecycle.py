"""Failure injection and real Qt reentry; no installed manager or network service."""
import importlib
import os
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import fake_mo2
fake_mo2.install()
import common
try:
    from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer
except ImportError:
    print(common.T('protocol.skipped'))
    raise SystemExit(77)

operations = importlib.import_module(common.NAME + '.operations')
runtime = importlib.import_module(common.NAME + '.runtime')
base = importlib.import_module(common.NAME + '.base')
config = importlib.import_module(common.NAME + '.config')
plugin = importlib.import_module(common.NAME + '.plugin')
launch = importlib.import_module(common.NAME + '.launch')
winapi = importlib.import_module(common.NAME + '.winapi')
app = QCoreApplication.instance() or QCoreApplication([])


def drain(predicate, seconds=3):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.001)
    return predicate()


class Guard:
    def refusal(self, _op):
        return None


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.fx = fake_mo2.Fixture()
        self.addCleanup(self.fx.cleanup)
        self.runner = runtime.MainThreadRunner()
        self.addCleanup(self.runner.close)
        self.ctx = base.Context(self.fx.organizer, self.runner.call, '', lambda msg: None,
                                config.Config({'operationWaitSec': 0.01}))
        self.domain = base.Domain(self.ctx, Guard())

    def test_failed_scheduling_is_terminal_and_replayable(self):
        for target in ('start', '__init__'):
            store = operations.Operations('boot', wait=0.01)
            body = {'expectedServerBootId': 'boot', 'idempotencyKey': 'once'}
            called = []
            with patch.object(threading.Thread, target, side_effect=RuntimeError('injected')):
                code, reply = store.invoke('/test', body, lambda b: called.append(b))
            self.assertEqual(code, 500)
            self.assertEqual(reply['code'], 'workerStartFailed')
            self.assertTrue(reply['operation']['terminal'])
            self.assertEqual(store.invoke('/test', body, lambda b: called.append(b)), (code, reply))
            self.assertEqual(called, [])

    def test_pending_retries_do_not_wait_again(self):
        store = operations.Operations('boot', wait=0.01)
        release = threading.Event()
        body = {'expectedServerBootId': 'boot', 'idempotencyKey': 'once'}
        try:
            _, first = store.invoke('/test', body, lambda b: release.wait(3))
            store.wait = 1
            started = time.monotonic()
            for _ in range(32):
                code, reply = store.invoke('/test', body, lambda b: self.fail('duplicate'))
                self.assertEqual(code, 202)
                self.assertEqual(reply['operation']['id'], first['operation']['id'])
                self.assertFalse(store.status(reply['operation']['id'])['terminal'])
            self.assertLess(time.monotonic() - started, 0.3)
        finally:
            release.set()
            self.assertTrue(drain(lambda: not store.pending()))

    def test_bounded_stage_audit_does_not_log_caller_data(self):
        notes = []
        store = operations.Operations('boot', wait=1, stage_limit=3, note=notes.append)
        body = {'expectedServerBootId': 'boot', 'idempotencyKey': 'secret-retry-key',
                'archive': 'https://example.invalid/signed?secret=credential'}
        code, reply = store.invoke('/test', body, lambda b: {'applied': True})
        self.assertEqual(code, 200)
        record = store.records[reply['operation']['id']]
        for _ in range(20):
            record.stage('nativeQueued')
        self.assertEqual(len(record.snapshot()['stages']), 3)
        self.assertTrue(all('monotonicSec' in e for e in record.snapshot()['stages']))
        self.assertEqual(len(notes), 2)
        self.assertNotIn('secret', ' '.join(notes))
        self.assertNotIn('credential', ' '.join(notes))

    def test_whole_worker_change_owns_mutation_and_failure_releases_it(self):
        entered, release = threading.Event(), threading.Event()
        result = []
        def work():
            entered.set()
            release.wait(3)
            raise ValueError('injected IO failure')
        def worker():
            try:
                self.domain.change('install', work, on_main=False)
            except ValueError:
                result.append('failed')
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            for op in ('remove', 'rename', 'run'):
                called = []
                reply = self.domain.change(op, lambda: called.append(1), on_main=False)
                self.assertEqual(reply['reason'], 'mutationBusy')
                self.assertEqual(called, [])
        finally:
            release.set()
            thread.join(2)
        self.assertEqual(result, ['failed'])
        self.assertIsNone(self.ctx.mutations.snapshot())

    def test_started_native_job_keeps_lease_after_direct_caller_times_out(self):
        result, reentry = [], []
        def native():
            loop = QEventLoop()
            QTimer.singleShot(60, lambda: reentry.append(
                self.domain.change('toggle', lambda: {'applied': True}, on_main=False)))
            QTimer.singleShot(110, loop.quit)
            loop.exec()
            return {'applied': True}
        def caller():
            try:
                self.domain.change('install', native, timeout=0.02)
            except operations.JobPending:
                result.append('pending')
        thread = threading.Thread(target=caller)
        thread.start()
        self.assertTrue(drain(lambda: not thread.is_alive()))
        thread.join(1)
        self.assertEqual(result, ['pending'])
        self.assertEqual(reentry[0]['reason'], 'mutationBusy')
        self.assertIsNone(self.ctx.mutations.snapshot())

    def test_stop_refuses_restart_until_worker_finishes_and_retains_outcome(self):
        bridge = plugin.MO2ApIBridge()
        bridge._organizer = self.fx.organizer
        entered, release = threading.Event(), threading.Event()
        class Server:
            def shutdown(self): pass
            def server_close(self): pass
        with patch.object(plugin, 'config', return_value=config.Config()), \
                patch.object(plugin, 'log', lambda *args: None), \
                patch.object(runtime, 'serve', return_value=(Server(), 18940)), \
                patch.object(plugin.Services, 'prime', lambda svc: None), \
                patch.object(plugin.MO2ApIBridge, '_hook_quit', lambda svc: None), \
                patch.object(plugin.MO2ApIBridge, 'isActive', return_value=True):
            # Wire services directly so the test never creates a production token.
            svc = plugin.Services(self.fx.organizer, self.runner.call, '',
                                  cfg=config.Config({'operationWaitSec': 0.01}),
                                  mutations=bridge._mutations, retired=bridge._retired)
            bridge._svc, bridge._runner = svc, self.runner
            domain = base.Domain(svc.ctx, Guard())
            def work():
                entered.set()
                release.wait(3)
                return svc.run_main(lambda: {'applied': True, 'verified': 'terminal'})
            body = {'idempotencyKey': 'old', 'expectedServerBootId': svc.ctx.boot_id}
            _, first = svc.ctx.operations.invoke('/test', body,
                lambda b: domain.change('install', work, on_main=False))
            self.assertTrue(entered.wait(1))
            bridge.stop()
            try:
                self.assertIsNotNone(bridge.start())
                self.assertIsNone(bridge._svc)
                self.assertTrue(bridge._retired.draining())
            finally:
                release.set()
            self.assertTrue(drain(lambda: not bridge._retired.draining()))
            fresh = plugin.Services(self.fx.organizer, lambda fn, **kw: fn(), '',
                                    mutations=bridge._mutations, retired=bridge._retired)
            outcome = fresh.operation_status({'id': [first['operation']['id']]})
            self.assertTrue(outcome['terminal'])
            self.assertEqual(outcome['result']['verified'], 'terminal')
            self.assertEqual(outcome['serverBootId'], svc.ctx.boot_id)
            self.assertNotEqual(fresh.ctx.boot_id, svc.ctx.boot_id)
            self.assertLess(fresh.ctx.operations.limit, 4096)

    def test_shutdown_defers_handle_close_until_native_users_leave(self):
        launcher = launch.Launcher(self.ctx, Guard())
        launcher.owned_handles.add(900)
        entered, release = threading.Event(), threading.Event()
        closed = []
        def wait():
            with launcher._using_handles() as allowed:
                self.assertTrue(allowed)
                entered.set()
                release.wait(3)
        with patch.object(winapi, 'close_process_handle', closed.append):
            thread = threading.Thread(target=wait)
            thread.start()
            self.assertTrue(entered.wait(1))
            launcher.close()
            launcher.close()
            self.assertEqual(closed, [])
            with launcher._using_handles() as allowed:
                self.assertFalse(allowed)
            release.set()
            thread.join(2)
            self.assertEqual(closed, [900])
            launcher.close()
            self.assertEqual(closed, [900])


if __name__ == '__main__':
    unittest.main()
