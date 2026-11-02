"""Removal orchestration over disposable setup files, never a live manager."""
import importlib
import os
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_mo2
fake_mo2.install()
import common

services = importlib.import_module(common.NAME + '.services')
base = importlib.import_module(common.NAME + '.base')


class Removal(unittest.TestCase):
    def setUp(self):
        self.fx = fake_mo2.Fixture()
        self.addCleanup(self.fx.cleanup)
        self.o = self.fx.organizer
        self.o._native_plugins.writePluginLists(self.o.pluginList())
        self.svc = services.Services(self.o, lambda fn, timeout=None: fn(), 'README.md',
            cfg=services.config.Config({'timeouts': {'modRemoveRefresh': 0.1}}))
        self.svc.guard.refusal = lambda *_: None
        self.svc.modops.remove_native = fake_mo2.remove_via_model
        self.body = {'mod': 'Alpha Mod', base.DANGER_KEY: base.DANGER_VALUE,
                     'expectedServerBootId': self.svc.ctx.boot_id,
                     'idempotencyKey': 'one-remove'}

    def call(self, **update):
        return self.svc.ctx.operations.invoke('/mods/remove', dict(self.body, **update),
                                              self.svc.mods_remove)

    def test_complete_removal_preserves_other_mods_archive_and_original_retry(self):
        before = self.svc.modops._setup()[1]
        archive = self.fx.path('downloads', 'Alpha Mod-101-1-0.7z')
        code, result = self.call()
        self.assertEqual(code, 200, result)
        self.assertTrue(result['removalComplete'])
        self.assertTrue(result['profilePersisted'])
        self.assertTrue(result['otherModsPreserved'])
        self.assertEqual(self.svc.modops._setup()[1], [row for row in before if row[0] != 'Alpha Mod'])
        self.assertTrue(os.path.isfile(archive))
        refreshes = self.o.refreshed
        again_code, again = self.call()
        self.assertEqual(again_code, 200)
        self.assertEqual(again['operation']['id'], result['operation']['id'])
        self.assertEqual(self.o.refreshed, refreshes)

    def test_preservation_finishes_off_native_thread_before_removal(self):
        events = []
        original = self.o.refresh
        def refresh(save=True):
            events.append('preserve' if len(events) == 0 else 'final-refresh')
            # Simulate the native callback being delivered after refresh returns.
            timer = threading.Timer(0.01, lambda: original(save))
            timer.start()
            self.addCleanup(timer.join)
        self.o.refresh = refresh
        def remove(*args, **kwargs):
            events.append('remove')
            return fake_mo2.remove_via_model(*args, **kwargs)
        self.svc.modops.remove_native = remove
        code, result = self.call()
        self.assertEqual(code, 200, result)
        self.assertEqual(events, ['preserve', 'remove', 'final-refresh'])

    def test_preservation_timeout_never_calls_delete_or_recycles_archive(self):
        with patch.object(self.o, 'refresh'), patch.object(self.svc.modops, 'remove_native') as remove:
            code, result = self.call(withArchive=True)
        self.assertEqual(code, 500)
        self.assertTrue(result['partial'])
        self.assertEqual(result['phase'], 'preserve')
        remove.assert_not_called()
        self.assertTrue(os.path.isdir(self.fx.mod_path('Alpha Mod')))
        self.assertFalse(self.svc.modops.refresh_tracker.waits)

    def test_intervening_setup_change_refuses_before_native_remove(self):
        original = self.o.refresh
        def changed(save=True):
            original(save)
            self.o.modList().setActive('Beta Mod', False)
        with patch.object(self.o, 'refresh', side_effect=changed), \
                patch.object(self.svc.modops, 'remove_native') as remove:
            code, result = self.call()
        self.assertEqual(code, 500)
        self.assertTrue(result['partial'])
        remove.assert_not_called()
        self.assertTrue(os.path.isdir(self.fx.mod_path('Alpha Mod')))

    def test_pending_unrelated_mod_edits_refuse_before_loss_or_refresh(self):
        # Unlike the broad fake setters, this is deliberately not on disk yet.
        self.o.modList()._active['Beta Mod'] = False
        with patch.object(self.o, 'refresh') as refresh, \
                patch.object(self.svc.modops, 'remove_native') as remove:
            code, result = self.call()
        self.assertEqual(code, 500, result)
        self.assertFalse(result['partial'])
        refresh.assert_not_called()
        remove.assert_not_called()
        self.assertFalse(self.o.modList().state('Beta Mod') & fake_mo2.ModState.ACTIVE)
        self.assertIn(['Beta Mod', False], [list(row) for row in result['setupBefore']['mods']])

    def test_pending_unrelated_plugin_edit_refuses_before_loss_or_refresh(self):
        self.o.pluginList().setState('Beta.esp', fake_mo2.PluginState.ACTIVE)
        before = self.fx.read_bytes('profiles', 'Claude', 'plugins.txt')
        with patch.object(self.o, 'refresh') as refresh, \
                patch.object(self.svc.modops, 'remove_native') as remove:
            code, result = self.call()
        self.assertEqual(code, 500, result)
        self.assertFalse(result['partial'])
        self.assertTrue(result['pluginsBefore']['states']['Beta.esp'])
        self.assertTrue(self.o.pluginList().state('Beta.esp') & fake_mo2.PluginState.ACTIVE)
        self.assertEqual(self.fx.read_bytes('profiles', 'Claude', 'plugins.txt'), before)
        refresh.assert_not_called()
        remove.assert_not_called()

    def test_known_modlist_lock_refuses_before_native_refresh(self):
        with patch(common.NAME + '.mods.winapi.check_file_replaceable', side_effect=PermissionError('held')), \
                patch.object(self.o, 'refresh') as refresh:
            code, result = self.call()
        self.assertEqual(code, 500)
        self.assertFalse(result['partial'])
        refresh.assert_not_called()

    def test_launch_during_native_confirmation_refuses_before_deletion(self):
        for running in ({'windowLocked': True, 'pids': [123], 'mo2Run': []},
                        {'windowLocked': True, 'pids': [], 'mo2Run': ['tool.exe']},
                        {}):
            with self.subTest(running=running):
                self.svc.guard.refusal = lambda *_: None
                def remove(*args, **kwargs):
                    self.svc.guard.refusal = lambda *_: {'running': running}
                    return fake_mo2.remove_via_model(*args, **kwargs)
                with patch.object(self.svc.modops, 'remove_native', side_effect=remove):
                    # Separate operation keys; each refused attempt leaves the target intact.
                    code, result = self.call(idempotencyKey='launch-' + str(running))
                self.assertEqual(code, 500)
                self.assertFalse(result['removalComplete'])
                self.assertTrue(os.path.isdir(self.fx.mod_path('Alpha Mod')))

    def test_exact_own_confirmation_ignores_only_its_window_lock(self):
        def remove(*args, **kwargs):
            self.svc.guard.refusal = lambda *_: {'running': {
                'windowLocked': True, 'heldByUnknown': True, 'app': None,
                'pids': [], 'mo2Run': [], 'viaMO2': False, 'isGame': False}}
            try:
                return fake_mo2.remove_via_model(*args, **kwargs)
            finally:
                self.svc.guard.refusal = lambda *_: None
        with patch.object(self.svc.modops, 'remove_native', side_effect=remove):
            code, result = self.call()
        self.assertEqual(code, 200, result)
        self.assertTrue(result['removalComplete'])

    def test_failed_modlist_persistence_never_claims_completion(self):
        def remove_without_save(*args, **kwargs):
            with patch.object(self.o, '_save_modlist'):
                return fake_mo2.remove_via_model(*args, **kwargs)
        with patch.object(self.svc.modops, 'remove_native', side_effect=remove_without_save):
            code, result = self.call()
        self.assertEqual(code, 500)
        self.assertTrue(result['filesystemRemoved'])
        self.assertFalse(result['profilePersisted'])
        self.assertFalse(result['removalComplete'])

    def test_native_true_without_deletion_never_recycles_archive_or_succeeds(self):
        receipt = {'invoked': True, 'nativeReturned': True, 'removed': False}
        with patch.object(self.svc.modops, 'remove_native', return_value=receipt), \
                patch(common.NAME + '.mods.winapi.recycle') as recycle:
            code, result = self.call(withArchive=True)
        self.assertEqual(code, 500)
        self.assertTrue(result['partial'])
        self.assertFalse(result['removalComplete'])
        self.assertTrue(result['archiveKept'])
        recycle.assert_not_called()

    def test_readback_failure_keeps_archive_and_reports_actual_partial_effect(self):
        original = self.o.refresh
        def changed_after_delete(save=True):
            original(save)
            if self.o.refreshed == 2:
                self.o.modList().setActive('Beta Mod', False)
        with patch.object(self.o, 'refresh', side_effect=changed_after_delete), \
                patch(common.NAME + '.mods.winapi.recycle') as recycle:
            code, result = self.call(withArchive=True)
        self.assertEqual(code, 500)
        self.assertTrue(result['partial'])
        self.assertFalse(result['otherModsPreserved'])
        self.assertTrue(result['filesystemRemoved'])
        self.assertFalse(result['removalComplete'])
        recycle.assert_not_called()


if __name__ == '__main__':
    unittest.main()
