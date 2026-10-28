# -*- coding: utf-8 -*-
"""Plugin-order persistence over disposable files and a native-semantic model.

No manager, token, socket, installed profile or game is accessed. The synthetic
Qt thread must remain free to deliver refresh completion. Unlike the broad fake,
setLoadOrder changes priority only; loadOrder is an active-only cache and refresh
really reloads the profile files. Native writes return None, including silent
failure, so accepting the call alone cannot establish persistence.
"""
import copy
from importlib import import_module
import os
import queue
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_mo2
mobase = fake_mo2.install()
if not hasattr(mobase, 'GamePlugins'):
    mobase.GamePlugins = type('GamePlugins', (), {})
import common

services = import_module(common.NAME + '.services')
config = import_module(common.NAME + '.config')


class Dispatch:
    """A disposable event thread, independent from the request worker."""
    def __init__(self):
        self.jobs = queue.Queue()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        while True:
            item = self.jobs.get()
            if item is None:
                return
            fn, done, result = item
            try:
                result.append((True, fn()))
            except BaseException as exc:
                result.append((False, exc))
            finally:
                done.set()

    def call(self, fn, timeout=None):
        if threading.current_thread() is self.thread:
            return fn()
        done, result = threading.Event(), []
        self.jobs.put((fn, done, result))
        if not done.wait(timeout if timeout is not None else 2):
            raise TimeoutError('synthetic Qt dispatch timed out')
        good, value = result[0]
        if not good:
            raise value
        return value

    def post(self, fn):
        self.jobs.put((fn, threading.Event(), []))

    def close(self):
        self.jobs.put(None)
        self.thread.join(2)


class NativePluginList:
    """Distinct enumeration, full priority and cached active-only load order."""
    NAMES = ['Gamma.esl', 'Alpha.esp', 'Beta.esp']

    def __init__(self):
        self.order = ['Alpha.esp', 'Beta.esp', 'Gamma.esl']
        self.active = {'Alpha.esp': True, 'Beta.esp': False, 'Gamma.esl': True}
        self.cached = ['Alpha.esp', 'Gamma.esl']
        self.set_calls = 0
        self.constraint = None
        self.after_set = None
        self.refreshed_callbacks = []

    def pluginNames(self):
        return list(self.NAMES)

    def priority(self, name):
        return self.order.index(name)

    def loadOrder(self, name):
        return self.cached.index(name) if name in self.cached else -1

    def state(self, name):
        return mobase.PluginState.ACTIVE if self.active[name] else mobase.PluginState.INACTIVE

    def setState(self, name, state):
        self.active[name] = bool(state & mobase.PluginState.ACTIVE)

    def setLoadOrder(self, order):
        self.set_calls += 1
        self.order = list(self.constraint(order) if self.constraint else order)
        if self.after_set:
            self.after_set()

    def masters(self, name):
        return ['Alpha.esp'] if self.constraint and name == 'Gamma.esl' else []

    def onRefreshed(self, callback):
        self.refreshed_callbacks.append(callback)
        return True

    def emit_refreshed(self):
        for callback in list(self.refreshed_callbacks):
            callback()


class NativeFiles:
    """GamePlugins writes both native files and reads them into the list."""
    def __init__(self, organizer, plugins):
        self.o, self.plugins = organizer, plugins
        self.mode = ''
        self.writes = []
        self.reads = 0
        self.after_write = None
        self.primary = set()

    def paths(self):
        root = self.o.profile().absolutePath()
        return [os.path.join(root, name) for name in ('plugins.txt', 'loadorder.txt')]

    def render(self, plugins):
        order = sorted(plugins.pluginNames(), key=plugins.priority)
        header = '# Native fixture\r\n'
        return [(header + '\r\n'.join(('*' if plugins.active[n] else '') + n
                                     for n in order if n not in self.primary) + '\r\n').encode('utf-8'),
                (header + '\r\n'.join(order) + '\r\n').encode('utf-8')]

    def writePluginLists(self, plugins):
        self.writes.append(self.o.profile().absolutePath())
        if self.mode == 'locked':
            raise PermissionError('native fixture file held')
        if self.mode == 'silent':
            return None
        for index, (path, contents) in enumerate(zip(self.paths(), self.render(plugins))):
            with open(path, 'wb') as stream:
                stream.write(contents)
            if self.mode == 'partial' and index == 0:
                raise PermissionError('native fixture second file held')
        if self.after_write:
            self.after_write()
        return None  # The actual native API returns void.

    def readPluginLists(self, plugins):
        self.reads += 1
        state_path, order_path = self.paths()
        with open(order_path, encoding='utf-8-sig') as stream:
            order = [line.strip() for line in stream
                     if line.strip() and not line.startswith('#')]
        with open(state_path, encoding='utf-8-sig') as stream:
            state = [line.strip() for line in stream
                     if line.strip() and not line.startswith('#')]
        plugins.order = order
        active = {line.lstrip('*'): line.startswith('*') for line in state}
        plugins.active = {name: active.get(name, name in self.primary)
                          for name in plugins.pluginNames()}


class OrderHarness:
    def setUp(self):
        self.fx = fake_mo2.Fixture()
        self.addCleanup(self.fx.cleanup)
        self.dispatch = Dispatch()
        self.addCleanup(self.dispatch.close)
        self.o = self.fx.organizer
        self.o._game.primaryPlugins = lambda: []
        self.o._game.DLCPlugins = lambda: []
        self.pl = NativePluginList()
        self.o._plugins = self.pl
        self.files = NativeFiles(self.o, self.pl)
        self.o.gameFeatures = lambda: self
        self.callbacks = []
        self.refreshes = []
        self.completed_refreshes = 0
        self.timers = []
        self.refresh_mode = 'immediate'
        self.o.onNextRefresh = self.on_next_refresh
        self.o.refresh = self.refresh
        for path, contents in zip(self.files.paths(), self.files.render(self.pl)):
            with open(path, 'wb') as stream:
                stream.write(contents)
        self.initial = self.disk()
        self.before = list(self.pl.order)
        self.states = dict(self.pl.active)
        self.wanted = ['Gamma.esl', 'Beta.esp', 'Alpha.esp']
        self.cfg = config.Config({'timeouts': {'pluginOrderRefresh': 0.15}})
        self.svc = services.Services(self.o, self.dispatch.call, 'README.md', cfg=self.cfg)
        self.svc.guard.refusal = lambda *args: None
        self.body = {'order': self.wanted, 'apply': True,
                     'expectedProfile': self.o.profile().name(),
                     'expectedInstance': self.svc.ctx.instance_id,
                     'expectedServerBootId': self.svc.ctx.boot_id}
        self.addCleanup(self.cancel_timers)

    def gameFeature(self, kind):
        if kind is not mobase.GamePlugins:
            raise AssertionError('Only the native GamePlugins feature is needed')
        return self.files

    def on_next_refresh(self, callback, immediate_if_possible=True):
        if immediate_if_possible:
            callback()
        else:
            self.callbacks.append(callback)
        return True

    def complete_refresh(self):
        self.completed_refreshes += 1
        self.files.readPluginLists(self.pl)
        callbacks, self.callbacks = self.callbacks, []
        for callback in callbacks:
            callback()
        # Native CORE invokes external callbacks before the final refreshLists.
        # Only a subsequently queued Qt read may rely on this derived cache.
        self.pl.cached = [name for name in self.pl.order if self.pl.active[name]]
        self.pl.emit_refreshed()

    def refresh(self, save_changes=True):
        self.refreshes.append(self.o.profile().name())
        if self.refresh_mode == 'hold':
            return
        if self.refresh_mode == 'revert':
            for path, contents in zip(self.files.paths(), self.initial):
                with open(path, 'wb') as stream:
                    stream.write(contents)
        if self.refresh_mode == 'async':
            timer = threading.Timer(0.03, lambda: self.dispatch.post(self.complete_refresh))
            self.timers.append(timer)
            timer.start()
        else:
            self.complete_refresh()

    def cancel_timers(self):
        for timer in self.timers:
            timer.cancel()

    def disk(self):
        values = []
        for path in self.files.paths():
            with open(path, 'rb') as stream:
                values.append(stream.read())
        return values

    def apply(self, **changes):
        return self.svc.plugins_order(dict(self.body, **changes))

    def assert_failure(self, result):
        self.assertFalse(result['applied'], result)
        self.assertTrue(result.get('failed'), result)
        self.assertTrue(result.get('reconciliationRequired'), result)


class OrderPersistence(OrderHarness, unittest.TestCase):
    def test_native_priority_persists_across_actual_disk_reload_and_preserves_states(self):
        result = self.apply()
        self.assertTrue(result['applied'], result)
        self.assertTrue(result['persisted'], result)
        self.assertTrue(result['refreshed'], result)
        self.assertEqual(result['before'], self.before)
        self.assertEqual(result['undo']['body']['order'], self.before)
        self.assertEqual((result['count'], result['notListed'], result['unknown']),
                         (len(self.wanted), [], []))
        self.assertEqual(self.pl.order, self.wanted)
        self.assertEqual(self.pl.active, self.states)
        self.assertEqual(self.disk(), self.files.render(self.pl))
        self.assertTrue(self.files.writes)
        self.assertTrue(self.refreshes)
        self.assertEqual(self.pl.loadOrder('Beta.esp'), -1)
        # Read once more from bytes, independently from the route's receipt.
        self.pl.order = list(self.before)
        self.files.readPluginLists(self.pl)
        self.assertEqual(self.pl.order, self.wanted)

    def test_failed_or_silent_native_writes_cannot_be_acknowledged_as_applied(self):
        for mode in ('locked', 'silent'):
            with self.subTest(mode=mode):
                self.files.mode = mode
                result = self.apply()
                self.assert_failure(result)
                self.assertFalse(result.get('persisted'), result)
                self.assertTrue(result.get('memoryApplied'), result)
                self.assertEqual(self.disk(), self.initial)
                self.assertEqual(result['undo']['body']['order'], self.before
                                 if mode == 'locked' else result['before'])

    def test_refresh_that_reverts_the_disk_is_detected(self):
        self.refresh_mode = 'revert'
        result = self.apply()
        self.assert_failure(result)
        self.assertEqual(self.disk(), self.initial)
        self.assertEqual(self.pl.order, self.before)

    def test_partial_native_write_retains_exact_recovery_bytes_and_previous_backups(self):
        for path in self.files.paths():
            with open(path + '.bak', 'wb') as stream:
                stream.write(b'previous successful recovery\r\n')
        self.files.mode = 'partial'
        result = self.apply()
        self.assert_failure(result)
        for path in self.files.paths():
            with open(path + '.bak', 'rb') as stream:
                self.assertEqual(stream.read(), b'previous successful recovery\r\n')
        # The reply must expose exact reversal data even when native writes tear.
        declared = []
        def collect(value):
            if isinstance(value, dict):
                for child in value.values():
                    collect(child)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    collect(child)
            elif isinstance(value, str) and os.path.isfile(value):
                declared.append(value)
        collect(result)
        recovered = []
        for path in declared:
            with open(path, 'rb') as stream:
                recovered.append(stream.read())
        self.assertTrue(all(original in recovered for original in self.initial), result)

    def test_same_operation_replays_failure_and_a_fresh_retry_repairs_disk_drift(self):
        body = dict(self.body, idempotencyKey='native-order-failure')
        self.files.mode = 'silent'
        first_code, first = self.svc.ctx.operations.invoke('/plugins/order', body,
                                                         self.svc.plugins_order)
        writes = len(self.files.writes)
        self.files.mode = ''
        same_code, same = self.svc.ctx.operations.invoke('/plugins/order', body,
                                                       self.svc.plugins_order)
        self.assertEqual((first_code, same_code), (500, 500))
        self.assertEqual(first['operation']['id'], same['operation']['id'])
        self.assertEqual(len(self.files.writes), writes)
        # Memory may already match after failure; disk is still the old order.
        code, healed = self.svc.ctx.operations.invoke('/plugins/order',
            dict(body, idempotencyKey='native-order-repair'), self.svc.plugins_order)
        self.assertEqual(code, 200, healed)
        self.assertTrue(healed['applied'], healed)
        self.assertEqual(self.disk(), self.files.render(self.pl))
        self.assertEqual(self.pl.order, self.wanted)

    def test_noop_memory_still_repairs_old_disk(self):
        self.pl.setLoadOrder(self.wanted)
        result = self.apply()
        self.assertTrue(result['applied'], result)
        self.assertEqual(self.disk(), self.files.render(self.pl))
        self.files.readPluginLists(self.pl)
        self.assertEqual(self.pl.order, self.wanted)

    def test_native_primary_omission_is_accepted_without_losing_full_priority(self):
        self.files.primary = {'Alpha.esp'}
        self.o._game.primaryPlugins = lambda: ['Alpha.esp']
        self.wanted = ['Alpha.esp', 'Gamma.esl', 'Beta.esp']
        result = self.apply(order=self.wanted)
        self.assertTrue(result['applied'], result)
        self.assertEqual(self.pl.order, self.wanted)
        self.assertEqual(self.pl.active, self.states)
        self.assertNotIn(b'Alpha.esp', self.disk()[0])
        self.assertIn(b'Alpha.esp', self.disk()[1])

    def test_native_master_constraints_are_a_mismatch_not_false_success(self):
        self.pl.constraint = lambda order: ['Alpha.esp'] + [n for n in order if n != 'Alpha.esp']
        result = self.apply()
        self.assert_failure(result)
        self.assertNotEqual(self.pl.order, self.wanted)

    def test_invalid_orders_and_previews_have_no_memory_or_disk_effect(self):
        for order in ([], 'Alpha.esp', [None, 'Beta.esp', 'Gamma.esl'],
                      ['Alpha.esp', 'Alpha.esp', 'Beta.esp', 'Gamma.esl'],
                      [[], 'Alpha.esp', 'Beta.esp', 'Gamma.esl']):
            with self.subTest(order=order):
                try:
                    result = self.apply(order=order)
                except ValueError:
                    pass
                else:
                    self.assertFalse(result['applied'], result)
                self.assertEqual(self.pl.set_calls, 0)
                self.assertEqual(self.files.writes, [])
                self.assertEqual(self.disk(), self.initial)
        preview = self.apply(apply=False)
        self.assertFalse(preview['applied'])
        self.assertEqual(preview['before'], self.before)
        self.assertEqual(self.pl.set_calls, 0)
        self.assertEqual(self.files.writes, [])
        self.assertEqual(self.refreshes, [])
        incomplete = self.apply(order=['Alpha.esp', 'Beta.esp', 'Unknown.esp'])
        self.assertFalse(incomplete['applied'])
        self.assertEqual(incomplete['notListed'], ['Gamma.esl'])
        self.assertEqual(incomplete['unknown'], ['Unknown.esp'])
        self.assertEqual(self.pl.set_calls, 0)
        self.assertEqual(self.disk(), self.initial)

    def test_expected_identity_is_checked_before_writing_and_before_refreshing(self):
        for field in ('expectedProfile', 'expectedInstance'):
            with self.subTest(field=field):
                try:
                    result = self.apply(**{field: 'different'})
                except ValueError:
                    pass
                else:
                    self.assertFalse(result['applied'], result)
        self.assertEqual(self.pl.set_calls, 0)
        self.assertEqual(self.files.writes, [])
        self.assertEqual(self.disk(), self.initial)
        self.pl.after_set = lambda: setattr(self.o, '_profile', 'Second')
        result = self.apply()
        self.assert_failure(result)
        self.assertEqual(self.files.writes, [])
        self.assertEqual(self.refreshes, [])
        self.o._profile = 'Claude'
        self.pl.after_set = None
        self.files.after_write = lambda: setattr(self.o, '_profile', 'Second')
        result = self.apply()
        self.assert_failure(result)
        self.assertEqual(self.refreshes, [])

    def test_async_refresh_completes_on_free_event_thread_before_success(self):
        self.refresh_mode = 'async'
        result = self.apply()
        self.assertTrue(result['applied'], result)
        self.assertTrue(result['refreshed'], result)
        self.assertEqual(self.pl.order, self.wanted)
        self.assertTrue(self.files.reads)
        self.assertEqual(self.completed_refreshes, 1)

    def test_busy_state_is_rechecked_before_each_native_mutation(self):
        busy = [False]
        self.svc.guard.refusal = lambda *args: {'busy': True} if busy[0] else None
        original_feature = self.gameFeature
        def became_busy_during_lookup(kind):
            busy[0] = True
            return original_feature(kind)
        self.gameFeature = became_busy_during_lookup
        result = self.apply()
        self.assertFalse(result['applied'], result)
        self.assertEqual(self.pl.set_calls, 0)
        self.assertEqual(self.files.writes, [])
        self.assertEqual(self.disk(), self.initial)
        self.gameFeature = original_feature
        busy[0] = False
        self.files.after_write = lambda: busy.__setitem__(0, True)
        result = self.apply()
        self.assert_failure(result)
        self.assertEqual(self.files.reads, 0, 'native read also mutates the plugin list')
        self.assertEqual(self.refreshes, [])

    def test_refresh_timeout_is_terminal_and_late_callback_never_writes_again(self):
        self.refresh_mode = 'hold'
        result = self.apply()
        self.assert_failure(result)
        writes, contents = len(self.files.writes), self.disk()
        previous = copy.deepcopy(result)
        self.dispatch.call(self.complete_refresh)
        self.assertEqual(len(self.files.writes), writes)
        self.assertEqual(self.disk(), contents)
        self.assertFalse(result['applied'])
        self.assertEqual(result.get('failed'), previous.get('failed'))

    def test_host_tracker_reuses_one_native_hook_after_service_restart_and_timeout(self):
        self.assertTrue(self.apply()['applied'])
        tracker = self.svc.loadorder.order_refresh
        self.assertEqual(len(self.pl.refreshed_callbacks), 1)
        self.assertEqual(tracker.waits, [])
        # An HTTP stop/start replaces Services while keeping the same native host.
        self.svc = services.Services(self.o, self.dispatch.call, 'README.md', cfg=self.cfg,
                                     order_refresh=tracker)
        self.svc.guard.refusal = lambda *args: None
        self.body.update(expectedInstance=self.svc.ctx.instance_id,
                         expectedServerBootId=self.svc.ctx.boot_id)
        self.assertTrue(self.apply(order=self.before)['applied'])
        self.assertEqual(len(self.pl.refreshed_callbacks), 1)
        self.refresh_mode = 'hold'
        timed_out = self.apply()
        self.assert_failure(timed_out)
        self.assertEqual(tracker.waits, [])
        self.dispatch.call(self.complete_refresh)
        self.assertFalse(timed_out['applied'])
        self.refresh_mode = 'immediate'
        self.assertTrue(self.apply(order=self.before)['applied'])
        self.assertEqual(len(self.pl.refreshed_callbacks), 1)
        self.assertEqual(tracker.waits, [])


try:
    from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer
except ImportError:
    QCoreApplication = None


@unittest.skipUnless(QCoreApplication, 'standalone PyQt6 unavailable; no host acceptance implied')
class RealQtOrder(OrderHarness, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])
        cls.runtime = import_module(common.NAME + '.runtime')

    def setUp(self):
        super().setUp()
        self.runner = self.runtime.MainThreadRunner()
        self.addCleanup(self.runner.close)
        self.svc = services.Services(self.o, self.runner.call, 'README.md', cfg=self.cfg)
        self.svc.guard.refusal = lambda *args: None
        self.body.update(expectedInstance=self.svc.ctx.instance_id,
                         expectedServerBootId=self.svc.ctx.boot_id)

    def refresh(self, save_changes=True):
        self.refreshes.append(self.o.profile().name())
        QTimer.singleShot(15, self.complete_refresh)

    def request_in_worker(self):
        outcomes, errors = [], []
        def request():
            try:
                outcomes.append(self.apply())
            except BaseException as exc:
                errors.append(exc)
        worker = threading.Thread(target=request, daemon=True)
        worker.start()
        deadline = time.monotonic() + 2
        while worker.is_alive() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.001)
        worker.join(0.1)
        self.assertFalse(worker.is_alive(), 'request worker did not complete')
        self.assertEqual(errors, [])
        return outcomes[0]

    def test_request_worker_completes_through_actual_qt_runner(self):
        result = self.request_in_worker()
        self.assertTrue(result['applied'], result)
        self.assertEqual(self.completed_refreshes, 1)
        self.assertEqual(self.pl.order, self.wanted)
        self.assertEqual(self.pl.cached, ['Gamma.esl', 'Alpha.esp'])
        self.assertEqual(self.disk(), self.files.render(self.pl))

    def test_external_refresh_modal_does_not_allow_success_before_core_constraints(self):
        # Moving only disabled Beta leaves the old active loadOrder cache looking
        # correct. A later external callback's native modal can process queued Qt
        # reads before CORE applies relationships and locked-order constraints.
        self.body['order'] = ['Alpha.esp', 'Gamma.esl', 'Beta.esp']
        def core_refresh_with_external_modal():
            self.completed_refreshes += 1
            self.files.readPluginLists(self.pl)
            callbacks, self.callbacks = self.callbacks, []
            for callback in callbacks:
                callback()
            nested = QEventLoop()
            QTimer.singleShot(40, nested.quit)
            nested.exec()
            self.pl.order = list(self.before)  # Native relationship enforcement.
            self.pl.cached = [n for n in self.pl.order if self.pl.active[n]]
            self.pl.emit_refreshed()
        self.complete_refresh = core_refresh_with_external_modal
        result = self.request_in_worker()
        self.assertFalse(result['applied'], result)
        self.assertEqual(self.pl.order, self.before)

    def test_direct_qt_call_refuses_before_native_or_file_effects(self):
        with self.assertRaises(ValueError):
            self.apply()
        self.assertEqual(self.pl.set_calls, 0)
        self.assertEqual(self.files.writes, [])
        self.assertEqual(self.refreshes, [])
        self.assertEqual(self.disk(), self.initial)


if __name__ == '__main__':
    unittest.main()
