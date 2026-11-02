"""Threaded bookkeeping regressions with mocked process enumeration; no live MO2."""
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
import os
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

busy = import_module(common.NAME + '.busy')


class BusyState(unittest.TestCase):
    def setUp(self):
        self.state = busy.LaunchState(lambda message: None)
        context = SimpleNamespace(o=None, run_main=lambda fn, **kwargs: fn(),
                                  note=lambda message: None)
        self.guard = busy.BusyGuard(context, self.state)
        self.guard.game_exe = 'game.exe'
        self.guard.window_locked = lambda: False

    def own(self, path):
        with self.guard.starting():
            self.state.on_about_to_run(path)

    def during_enumeration(self, callback):
        entered, release = threading.Event(), threading.Event()

        def enumerate_processes(names):
            entered.set()
            if not release.wait(2):
                raise AssertionError('callback could not finish while process lookup waited')
            return {}

        with patch.object(busy.winapi, 'pids_by_exe', side_effect=enumerate_processes), \
                ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.guard.busy)
            try:
                self.assertTrue(entered.wait(2))
                callback()
            finally:
                release.set()
            return future.result(timeout=2)

    def test_callback_during_name_iteration_cannot_invalidate_iterator(self):
        self.state.on_about_to_run('First.exe')
        entered, release = threading.Event(), threading.Event()
        basename = os.path.basename

        def scheduled(path):
            if path == 'First.exe':
                entered.set()
                if not release.wait(2):
                    raise AssertionError('callback blocked while names were inspected')
            return basename(path)

        with patch.object(busy.os.path, 'basename', side_effect=scheduled), \
                patch.object(busy.winapi, 'pids_by_exe', return_value={}), \
                ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.guard.busy)
            try:
                self.assertTrue(entered.wait(2))
                self.state.on_about_to_run('Second.exe')
            finally:
                release.set()
            result = future.result(timeout=2)
        self.assertEqual(result['mo2Run'], ['First.exe', 'Second.exe'])
        self.assertTrue(result['heldByUnknown'])

    def test_stale_empty_process_snapshot_cannot_remove_new_foreign_run(self):
        self.own('Tool.exe')

        def restart():
            self.state.on_finished_run('Tool.exe')
            self.state.on_about_to_run('Tool.exe')

        result = self.during_enumeration(restart)
        self.assertEqual(result['mo2Run'], ['Tool.exe'])
        self.assertEqual(self.state.snapshot(), {'Tool.exe': {'n': 1, 'mine': False}})

    def test_same_own_record_values_after_restart_do_not_authorize_stale_removal(self):
        self.own('Tool.exe')

        def restart():
            self.state.on_finished_run('Tool.exe')
            self.own('Tool.exe')

        result = self.during_enumeration(restart)
        self.assertEqual(result['mo2Run'], ['Tool.exe'])
        self.assertEqual(self.state.snapshot(), {'Tool.exe': {'n': 1, 'mine': True}})
        with patch.object(busy.winapi, 'pids_by_exe', return_value={}):
            self.assertIsNone(self.guard.busy())

    def test_own_dead_runs_retire_and_foreign_runs_wait_for_native_finish(self):
        self.own('Own.exe')
        self.state.on_about_to_run('Foreign.exe')
        self.state.on_about_to_run('Foreign.exe')
        with patch.object(busy.winapi, 'pids_by_exe', return_value={}):
            self.assertEqual(self.guard.busy()['mo2Run'], ['Foreign.exe'])
            self.state.on_finished_run('Foreign.exe')
            self.assertEqual(self.state.snapshot(), {'Foreign.exe': {'n': 1, 'mine': False}})
            self.state.on_finished_run('Foreign.exe')
            self.assertIsNone(self.guard.busy())

    def assert_mixed_launches_hold_until_native_finish(self):
        self.assertEqual(self.state.snapshot(), {'Tool.exe': {'n': 2, 'mine': False}})
        with patch.object(busy.winapi, 'pids_by_exe', return_value={}):
            self.assertEqual(self.guard.busy()['mo2Run'], ['Tool.exe'])
            self.state.on_finished_run('Tool.exe')
            self.assertEqual(self.guard.busy()['mo2Run'], ['Tool.exe'])
            self.assertEqual(self.state.snapshot(), {'Tool.exe': {'n': 1, 'mine': False}})
            self.state.on_finished_run('Tool.exe')
            self.assertIsNone(self.guard.busy())

    def test_foreign_then_own_at_same_path_cannot_auto_retire_foreign_evidence(self):
        self.state.on_about_to_run('Tool.exe')
        self.own('Tool.exe')
        self.assert_mixed_launches_hold_until_native_finish()

    def test_own_then_foreign_at_same_path_cannot_auto_retire_foreign_evidence(self):
        self.own('Tool.exe')
        self.state.on_about_to_run('Tool.exe')
        self.assert_mixed_launches_hold_until_native_finish()

    def test_legacy_assignment_and_read_snapshots_do_not_share_mutable_records(self):
        records = {'Tool.exe': {'n': 1, 'mine': False}}
        self.guard.launched = records
        records['Tool.exe']['n'] = 99
        snapshot = self.guard.launched
        snapshot['Tool.exe']['n'] = 77
        snapshot.clear()
        self.assertEqual(self.state.snapshot(), {'Tool.exe': {'n': 1, 'mine': False}})

    def test_reset_during_process_lookup_cannot_resurrect_old_bookkeeping(self):
        self.own('Tool.exe')
        self.assertIsNone(self.during_enumeration(self.state.reset))
        self.assertEqual(self.state.snapshot(), {})

    def test_nested_starting_scope_restores_prior_ownership_flag(self):
        with self.guard.starting():
            with self.guard.starting():
                self.own('First.exe')
            self.state.on_about_to_run('Second.exe')
        self.state.on_about_to_run('Foreign.exe')
        self.assertEqual(self.state.snapshot(), {
            'First.exe': {'n': 1, 'mine': True}, 'Second.exe': {'n': 1, 'mine': True},
            'Foreign.exe': {'n': 1, 'mine': False}})
        self.assertFalse(self.state.is_starting())


if __name__ == '__main__':
    unittest.main()
