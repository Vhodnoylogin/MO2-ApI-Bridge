"""Removal orchestration over disposable setup files, never a live manager."""
import importlib
import base64
import copy
import json
import os
import sys
import threading
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_mo2
fake_mo2.install()
import common

services = importlib.import_module(common.NAME + '.services')
base = importlib.import_module(common.NAME + '.base')
reading = importlib.import_module(common.NAME + '.reading')
mods = importlib.import_module(common.NAME + '.mods')
archives = importlib.import_module(common.NAME + '.archives')
try:
    from PyQt6.QtCore import QByteArray
except ImportError:
    QByteArray = None


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

    def selected_record(self):
        archive = self.fx.path('downloads', 'Alpha Mod-101-1-0.7z')
        with zipfile.ZipFile(archive, 'w') as output:
            output.writestr('00 Core/chosen.esp', b'chosen payload')
            output.writestr('01 Other Runtime/omitted.esp', b'wrong runtime')
            output.writestr('fomod/ModuleConfig.xml', b'<config/>')
        return {'schemaVersion': 1, 'archive': archive,
                'archiveSha256': archives.file_sha256(archive), 'archiveBytes': os.path.getsize(archive),
                'selection': {'options': ['Core'], 'runtime': 'fixture'},
                'fomod': [{'path': 'fomod/ModuleConfig.xml', 'sha256': 'f' * 64}],
                'files': [{'source': '00 Core/chosen.esp', 'destination': 'chosen.esp',
                           'sha256': 'a' * 64, 'bytes': 14}],
                'nativeMetadata': {'installationFile': os.path.basename(archive), 'version': '1.0',
                    'nexusId': 101, 'gameName': 'SkyrimVR',
                    'installedFiles': [{'modId': 101, 'fileId': 123}]}}

    def receipt_patches(self, record):
        raw = json.dumps(record, indent=2)
        return (raw, patch.object(fake_mo2.FakeMod, 'pluginSetting', return_value=raw, create=True),
                patch.object(reading, 'installed_file_metadata',
                             return_value=([{'modId': 101, 'fileId': 123}], None)))

    def test_selected_removal_retains_raw_receipt_assessment_and_exact_reinstall_inputs(self):
        record = self.selected_record()
        raw, native_setting, metadata = self.receipt_patches(record)
        with native_setting, metadata:
            code, result = self.call()
        self.assertEqual(code, 200, result)
        self.assertFalse(os.path.exists(self.fx.mod_path('Alpha Mod')))
        self.assertEqual(result['card']['installProvenanceRaw'], raw)
        self.assertEqual(result['card']['installProvenance'], record)
        self.assertEqual(result['card']['installProvenanceStatus']['state'], 'metadata-match')
        evidence = result['reversal']
        self.assertFalse(evidence['complete'])
        self.assertFalse(evidence['archiveOnlyUndoRestoresSelection'])
        self.assertFalse(evidence['archiveHashVerified'])
        self.assertFalse(evidence['payloadHashesVerified'])
        plan = evidence['nativeReinstall']
        self.assertTrue(plan['requiresRevalidation'])
        self.assertFalse(plan['restoresCurrentPayload'])
        self.assertEqual(plan['body']['archiveSha256'], record['archiveSha256'])
        self.assertEqual(plan['body']['selection'], record['selection'])
        self.assertEqual(plan['body']['files'],
                         [{'source': '00 Core/chosen.esp', 'destination': 'chosen.esp'}])
        # Keep the old archive/name undo contract while disclosing its limited scope.
        self.assertEqual(result['undo']['body'], {'archive': record['archive'], 'name': 'Alpha Mod'})
        self.assertEqual(result['undo']['scope'], 'archive-only')
        self.assertFalse(result['undo']['reconstructsRecordedSelection'])
        self.assertEqual(self.call()[1]['card']['installProvenanceRaw'], raw)

        def selected_native(body):
            target = self.o.createMod(fake_mo2.GuessedString(body['name']))
            with zipfile.ZipFile(body['archive']) as source:
                for row in body['files']:
                    destination = os.path.join(target.absolutePath(), *row['destination'].split('/'))
                    os.makedirs(os.path.dirname(destination), exist_ok=True)
                    with open(destination, 'wb') as output:
                        output.write(source.read(row['source']))
            return {'created': target.absolutePath(), 'backend': 'native'}
        # Mock only the host pipeline: exercise the returned request's backend choice
        # and selected-file reconstruction against a real archive containing alternatives.
        with patch.object(self.svc.installer, '_install_native', side_effect=selected_native), \
                patch.object(self.svc.installer, '_install') as legacy:
            restored = self.svc.install(plan['body'])
        legacy.assert_not_called()
        self.assertEqual(restored['backend'], 'native')
        self.assertEqual(fake_mo2._files_under(restored['created']), ['chosen.esp'])
        with open(os.path.join(restored['created'], 'chosen.esp'), 'rb') as payload:
            self.assertEqual(payload.read(), b'chosen payload')

    def test_stale_or_incomplete_selection_is_retained_without_native_reinstall_plan(self):
        original = self.selected_record()
        cases = [('stale', dict(original, nativeMetadata=dict(original['nativeMetadata'], version='old'))),
                 ('missing-hash', dict(original, archiveSha256=None)),
                 ('missing-files', dict(original, files=[])),
                 ('missing-installer-evidence', {key: value for key, value in original.items()
                                                 if key != 'fomod'}),
                 ('missing-fomod-choices', dict(original, selection={})),
                 ('unsafe-mapping', dict(original, files=[{'source': '../foreign', 'destination': 'x'}]))]
        for label, record in cases:
            with self.subTest(label=label):
                raw, native_setting, metadata = self.receipt_patches(record)
                with native_setting, metadata:
                    result = self.svc.mods_remove({'mod': 'Alpha Mod'})
                self.assertEqual(result['card']['installProvenanceRaw'], raw)
                self.assertEqual(result['card']['installProvenance'], record)
                self.assertIsNone(result['reversal']['nativeReinstall'])
                self.assertFalse(result['reversal']['complete'])
                self.assertTrue(os.path.isdir(self.fx.mod_path('Alpha Mod')))

    def test_malformed_raw_receipt_is_preserved_on_refusal_without_inventing_a_plan(self):
        for raw in ('{broken', '[]', 'false'):
            with self.subTest(raw=raw), patch.object(fake_mo2.FakeMod, 'pluginSetting',
                                                     return_value=raw, create=True):
                result = self.svc.mods_remove({'mod': 'Alpha Mod'})
            self.assertEqual(result['card']['installProvenanceRaw'], raw)
            self.assertEqual(result['card']['installProvenanceStatus']['state'], 'malformed')
            self.assertIsNone(result['reversal']['nativeReinstall'])

    def assert_pollable(self, result):
        json.dumps(result, ensure_ascii=False).encode('utf-8')
        status = self.svc.ctx.operations.status(result['operation']['id'])
        json.dumps(status, ensure_ascii=False).encode('utf-8')
        self.assertTrue(status['terminal'])
        return status

    def assert_binary_receipt_removal(self, raw, native_type):
        with patch.object(fake_mo2.FakeMod, 'pluginSetting', return_value=raw, create=True):
            code, result = self.call()
        self.assertEqual(code, 200, result)
        self.assertTrue(result['removalComplete'])
        self.assertFalse(os.path.isdir(self.fx.mod_path('Alpha Mod')))
        evidence = result['card']['installProvenanceRaw']
        self.assertEqual(evidence['nativeType'], native_type)
        self.assertEqual(evidence['encoding'], 'base64')
        self.assertEqual(base64.b64decode(evidence['data']), bytes(raw))
        self.assertEqual(result['card']['installProvenanceStatus']['state'], 'malformed')
        self.assertIsNone(result['reversal']['nativeReinstall'])
        status = self.assert_pollable(result)
        self.assertEqual(status['result']['card']['installProvenanceRaw'], evidence)
        self.assertEqual(self.call()[1]['card']['installProvenanceRaw'], evidence)

    def test_malformed_binary_receipt_survives_completed_removal_and_polling(self):
        self.assert_binary_receipt_removal(b'{broken\x00\xff', 'bytes')

    def test_mutable_binary_receipt_is_detached_losslessly_before_removal(self):
        self.assert_binary_receipt_removal(bytearray(b'{broken\x00\xff'), 'bytearray')

    @unittest.skipUnless(QByteArray, 'PyQt6 unavailable; no native QByteArray coverage')
    def test_native_qbytearray_receipt_survives_completed_removal_and_polling(self):
        self.assert_binary_receipt_removal(QByteArray(b'{broken\x00\xff'), 'QByteArray')

    def test_malformed_original_string_stays_unchanged_after_removal_and_polling(self):
        raw = '{broken original string'
        with patch.object(fake_mo2.FakeMod, 'pluginSetting', return_value=raw, create=True):
            code, result = self.call()
        self.assertEqual(code, 200, result)
        self.assertEqual(result['card']['installProvenanceRaw'], raw)
        self.assertEqual(result['card']['installProvenanceStatus']['state'], 'malformed')
        self.assertTrue(result['removalComplete'])
        self.assert_pollable(result)

    def test_unsupported_receipt_values_refuse_before_refresh_or_destruction_and_stay_pollable(self):
        for index, raw in enumerate((object(), {'foreign': object()}, ['native-value'], 42)):
            with self.subTest(index=index), \
                    patch.object(fake_mo2.FakeMod, 'pluginSetting', return_value=raw, create=True), \
                    patch.object(self.o, 'refresh') as refresh, \
                    patch.object(self.svc.modops, 'remove_native') as remove:
                code, result = self.call(idempotencyKey='unsupported-' + str(index))
            self.assertEqual(code, 400, result)
            self.assertEqual(result['code'], 'badRequest')
            self.assertTrue(os.path.isdir(self.fx.mod_path('Alpha Mod')))
            self.assertTrue(os.path.isfile(self.fx.path('downloads', 'Alpha Mod-101-1-0.7z')))
            refresh.assert_not_called()
            remove.assert_not_called()
            self.assert_pollable(result)

    def test_unencodable_string_refuses_before_destruction_and_stays_pollable(self):
        with patch.object(fake_mo2.FakeMod, 'pluginSetting', return_value='\ud800', create=True), \
                patch.object(self.o, 'refresh') as refresh, \
                patch.object(self.svc.modops, 'remove_native') as remove:
            code, result = self.call()
        self.assertEqual(code, 400, result)
        self.assertTrue(os.path.isdir(self.fx.mod_path('Alpha Mod')))
        refresh.assert_not_called()
        remove.assert_not_called()
        self.assert_pollable(result)

    def test_missing_or_recycled_archive_does_not_offer_native_reinstall(self):
        record = self.selected_record()
        raw, native_setting, metadata = self.receipt_patches(record)
        with native_setting, metadata:
            card = self.svc.mods_remove({'mod': 'Alpha Mod'})['card']
        self.assertIsNotNone(mods.removal_reversal(card)['nativeReinstall'])
        self.assertIsNone(mods.removal_reversal(card, archive_kept=False)['nativeReinstall'])
        missing = copy.deepcopy(card)
        missing['archivePath'] = ''
        self.assertIsNone(mods.removal_reversal(missing)['nativeReinstall'])

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
