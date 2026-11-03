# -*- coding: utf-8 -*-
"""Selected-install audits over disposable files and passive native-shaped models.

No manager, token, remote service or game is accessed. These are source regression
checks, not live MO2 qualification. Real QSettings checks native INI readback.
"""
import importlib
import json
import os
import sys
import tempfile
import unittest
import zipfile
from contextlib import ExitStack
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_mo2
fake_mo2.install()
import common

audit_module = importlib.import_module(common.NAME + '.installationaudit')
archives = importlib.import_module(common.NAME + '.archives')
native = importlib.import_module(common.NAME + '.nativeinstall')
services = importlib.import_module(common.NAME + '.services')
try:
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QApplication
except ImportError:
    QSettings = None
    QApplication = None


def settings(path):
    return QSettings(path, QSettings.Format.IniFormat)


def tree_bytes(root):
    result = {}
    for directory, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(directory, name)
            with open(path, 'rb') as stream:
                result[os.path.relpath(path, root)] = stream.read()
    return result


def check(result, name):
    return next(row for row in result['checks'] if row['check'] == name)


@unittest.skipUnless(QApplication, 'PyQt6 unavailable; no host qualification implied')
class PreviewSelection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def test_real_7zip_preview_hashes_original_root_fomod_payload_and_cleans_scratch(self):
        fx = fake_mo2.Fixture()
        try:
            svc = services.Services(fx.organizer, lambda f, timeout=None: f(), 'README.md')
            svc.guard.refusal = lambda *_args: None
            if not svc.cfg.seven_zip():
                self.skipTest('7-Zip unavailable; no host qualification implied')
            archive = fx.path('downloads', 'Original.zip')
            with zipfile.ZipFile(archive, 'w') as output:
                output.writestr('fomod/f/Core.esp', b'original selected payload')
                output.writestr('fomod/ModuleConfig.xml', b'<config/>')
                output.writestr('fomod/images/large.bin', b'not selected')
                output.writestr('Other Runtime/Core.esp', b'not selected')
            body = {'native': True, 'archive': archive, 'name': 'Advisory Target',
                    'archiveSha256': archives.file_sha256(archive),
                    'files': [{'source': 'fomod/f/Core.esp', 'destination': 'Core.esp'}],
                    'selection': {'runtime': 'fixture', 'options': ['core']}}
            scratch = []
            unpack = svc.installer._unpack_selected

            def extract(*args):
                root = unpack(*args)
                scratch.append(root)
                self.assertEqual(archives.tree_files(root), {
                    os.path.join('fomod', 'f', 'core.esp'),
                    os.path.join('fomod', 'moduleconfig.xml')})
                return root

            before = tree_bytes(fx.root)
            with patch.object(native, 'check_available', return_value=True), \
                 patch.object(svc.installer, '_unpack_selected', side_effect=extract), \
                 patch.object(native, 'install', side_effect=AssertionError('native install')), \
                 patch.object(fx.organizer, 'refresh', side_effect=AssertionError('refresh')):
                result = svc.install_audit.preview(body)
            self.assertTrue(result['ready'], result)
            self.assertTrue(result['readOnly'])
            self.assertTrue(result['scratchWrites'])
            self.assertFalse(result['verified'])
            self.assertFalse(result['reservationCreated'])
            self.assertFalse(result['applyGuaranteed'])
            self.assertEqual(result['selectionEvidence']['files'][0]['bytes'], 25)
            self.assertEqual(len(result['selectionEvidence']['fomod']), 1)
            self.assertEqual(tree_bytes(fx.root), before)
            self.assertTrue(scratch)
            self.assertTrue(all(not os.path.exists(root) for root in scratch))
        finally:
            fx.cleanup()


@unittest.skipUnless(QSettings, 'PyQt6 unavailable; no host qualification implied')
class InstallAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.stack = ExitStack()
        self.fx = fake_mo2.Fixture()
        self.stack.callback(self.fx.cleanup)
        self.svc = services.Services(self.fx.organizer, lambda f, timeout=None: f(), 'README.md')
        self.svc.guard.refusal = lambda *_args: None
        self.audit = self.svc.install_audit
        self.arc = self.fx.path('downloads', 'Original-101-123.zip')
        self.target = self.fx.mod_path('Audited Selection')
        self.payload = b'exact selected payload'
        with zipfile.ZipFile(self.arc, 'w') as output:
            output.writestr('00 Core/payload.esp', self.payload)
            output.writestr('01 Other/payload.esp', b'unselected')
            output.writestr('fomod/ModuleConfig.xml', b'<config/>')
        download = settings(self.arc + '.meta')
        for key, value in {'modID': 101, 'fileID': 123, 'gameName': 'SkyrimSE',
                           'version': '1.0', 'installed': True, 'uninstalled': False,
                           'signedURL': 'private-scratch-value-never-returned'}.items():
            download.setValue(key, value)
        download.sync()
        self.body = {'native': True, 'archive': self.arc, 'name': 'Audited Selection',
                     'archiveSha256': archives.file_sha256(self.arc),
                     'files': [{'source': '00 Core/payload.esp', 'destination': 'payload.esp'}],
                     'selection': {'runtime': 'fixture', 'options': ['core']}}
        self.scratch = []

        def extract(archive, sources):
            root = tempfile.mkdtemp(dir=self.fx.root)
            self.scratch.append(root)
            with zipfile.ZipFile(archive) as source:
                for path in list(sources) + ['fomod/ModuleConfig.xml']:
                    source.extract(path, root)
            return root

        self.stack.enter_context(patch.object(self.svc.installer, '_unpack_selected', side_effect=extract))
        self.stack.enter_context(patch.object(native, 'check_available', return_value=True))
        self.observe = self.stack.enter_context(patch.object(native, 'observe_downloads', return_value={
            'verified': True, 'found': True, 'installed': True, 'nativeRefreshInvoked': False,
            'sourceRow': 1, 'model': 'DownloadList', 'identity': 'IDownloadManager.downloadPath(sourceRow)'}))

    def tearDown(self):
        self.stack.close()

    def make_target(self, file_id=123):
        self.fx.organizer.createMod(fake_mo2.GuessedString(self.body['name']))
        with open(os.path.join(self.target, 'payload.esp'), 'wb') as stream:
            stream.write(self.payload)
        metadata = settings(os.path.join(self.target, 'meta.ini'))
        for key, value in {'installationFile': os.path.basename(self.arc), 'modid': 101,
                           'gameName': 'SkyrimSE', 'version': '1.0', 'installedFiles/size': 1,
                           'installedFiles/1/modid': 101,
                           'installedFiles/1/fileid': file_id}.items():
            metadata.setValue(key, value)
        metadata.sync()

    def assert_no_writes(self, fn):
        before = tree_bytes(self.fx.root)
        with patch.object(native, 'install', side_effect=AssertionError('native install')), \
             patch.object(native, 'refresh_downloads', side_effect=AssertionError('native refresh')), \
             patch.object(self.fx.organizer, 'createMod', side_effect=AssertionError('create mod')), \
             patch.object(self.fx.organizer, 'refresh', side_effect=AssertionError('refresh')), \
             patch.object(self.svc.ctx.mutations, 'acquire', side_effect=AssertionError('lease')), \
             patch.object(QSettings, 'setValue', side_effect=AssertionError('settings write')), \
             patch.object(QSettings, 'allKeys', side_effect=AssertionError('private enumeration')), \
             patch.object(QSettings, 'sync', side_effect=AssertionError('settings sync')):
            result = fn()
        self.assertEqual(tree_bytes(self.fx.root), before)
        self.assertTrue(all(not os.path.exists(path) for path in self.scratch))
        self.assertNotIn('private-scratch-value', json.dumps(result))
        self.assertNotIn('signedURL', json.dumps(result))
        return result

    def test_settled_current_payload_after_original_inventory_failure_does_not_rewrite_failure(self):
        # The synthetic operation records the original failure, while a separate
        # current observation sees the payload which has since settled.
        body = dict(self.body, operationId='original-install',
                    expectedServerBootId=self.svc.ctx.boot_id)
        code, failed = self.svc.ctx.operations.invoke('/install', body, lambda _body: {
            'failed': True, 'partial': True, 'installationComplete': False,
            'reconciliationRequired': True, 'failureStage': 'payload-inventory'})
        self.assertEqual(code, 500)
        identifier = failed['operation']['id']
        original = self.svc.ctx.operations.status(identifier)
        self.make_target()
        result = self.assert_no_writes(lambda: self.audit.verify(
            dict(self.body, originalOperationId=identifier)))
        self.assertTrue(result['verified'], result)
        self.assertFalse(result['installationComplete'])
        self.assertFalse(result['provenanceCreated'])
        self.assertFalse(result['sourceMetadataPreservationVerified'])
        self.assertTrue(result['originalOperation']['referenceAvailable'])
        self.assertFalse(result['originalOperation']['suppliedRequestBoundToOriginal'])
        self.assertEqual(result['originalOperation']['outcome']['httpStatus'], 500)
        self.assertEqual(self.svc.ctx.operations.status(identifier), original)
        self.assertFalse(settings(os.path.join(self.target, 'meta.ini')).contains(
            'Plugins/mo2aibridge/installProvenance'))

    def test_persistent_extra_file_and_corrupt_payload_are_not_currently_verified(self):
        self.make_target()
        extra = os.path.join(self.target, 'unselected.dll')
        with open(extra, 'wb') as stream:
            stream.write(b'unselected')
        result = self.assert_no_writes(lambda: self.audit.verify(self.body))
        self.assertFalse(result['verified'])
        self.assertEqual(check(result, 'payload-inventory')['extra'], ['unselected.dll'])
        self.assertEqual(check(result, 'payload-hashes')['state'], 'not-run')
        os.remove(extra)
        with open(os.path.join(self.target, 'payload.esp'), 'wb') as stream:
            stream.write(b'corrupt selected payload')
        result = self.assert_no_writes(lambda: self.audit.verify(self.body))
        self.assertFalse(result['verified'])
        self.assertEqual(check(result, 'payload-hashes')['state'], 'mismatch')

    def test_wrong_or_missing_installed_file_id_fails_without_creating_provenance(self):
        self.make_target(file_id=999)
        result = self.assert_no_writes(lambda: self.audit.verify(self.body))
        self.assertFalse(result['verified'])
        self.assertEqual(check(result, 'native-installed-files')['state'], 'mismatch')
        metadata = settings(os.path.join(self.target, 'meta.ini'))
        metadata.remove('installedFiles')
        metadata.sync()
        result = self.assert_no_writes(lambda: self.audit.verify(self.body))
        self.assertFalse(result['verified'])
        self.assertEqual(check(result, 'native-installed-files')['state'], 'unavailable')

    def test_missing_source_id_and_native_row_are_explicitly_unavailable(self):
        self.make_target()
        download = settings(self.arc + '.meta')
        download.remove('fileID')
        download.sync()
        self.observe.side_effect = native.NativeInstallError('download-row-not-unique', {
            'nativeRefreshInvoked': False, 'found': False, 'verified': False})
        result = self.assert_no_writes(lambda: self.audit.verify(self.body))
        self.assertFalse(result['verified'])
        self.assertEqual(check(result, 'source-identity')['state'], 'unavailable')
        self.assertEqual(check(result, 'native-download-row')['state'], 'unavailable')

    def test_other_positive_native_pairs_are_allowed_but_malformed_pairs_are_not(self):
        self.make_target()
        metadata = settings(os.path.join(self.target, 'meta.ini'))
        metadata.setValue('installedFiles/size', 2)
        metadata.setValue('installedFiles/2/modid', 202)
        metadata.setValue('installedFiles/2/fileid', 456)
        metadata.sync()
        result = self.assert_no_writes(lambda: self.audit.verify(self.body))
        self.assertTrue(result['verified'], result)
        metadata.setValue('installedFiles/2/fileid', 0)
        metadata.sync()
        result = self.assert_no_writes(lambda: self.audit.verify(self.body))
        self.assertFalse(result['verified'])
        self.assertEqual(check(result, 'native-installed-files')['state'], 'mismatch')

    def test_unknown_operation_reference_does_not_claim_original_request_binding(self):
        self.make_target()
        result = self.assert_no_writes(lambda: self.audit.verify(dict(
            self.body, originalOperationId='unknown-operation')))
        self.assertTrue(result['verified'], result)
        self.assertFalse(result['originalOperation']['referenceAvailable'])
        self.assertEqual(result['originalOperation']['requestBinding'], 'not-verified')

    def test_negative_sample_limit_never_returns_nearly_unbounded_payload_samples(self):
        self.make_target()
        for name in ('extra-one.dll', 'extra-two.dll'):
            with open(os.path.join(self.target, name), 'wb') as stream:
                stream.write(b'unselected')
        with patch.object(self.audit, 'limit', return_value=-1):
            result = self.audit.verify(self.body)
        inventory = check(result, 'payload-inventory')
        self.assertEqual(inventory['extraCount'], 2)
        self.assertEqual(inventory['extra'], [])

    def test_failed_extraction_discloses_attempt_without_inventing_scratch_write_proof(self):
        with patch.object(self.svc.installer, '_unpack_selected', side_effect=RuntimeError(
                'private exception must not escape')):
            result = self.audit.preview(self.body)
        self.assertFalse(result['ready'])
        self.assertTrue(result['scratchExtractionAttempted'])
        self.assertTrue(result['scratchWritesPossible'])
        self.assertIsNone(result['scratchWrites'])
        self.assertNotIn('private exception', json.dumps(result))

    def test_preview_refuses_busy_or_modal_native_readiness_without_scratch(self):
        with patch.object(self.svc.guard, 'refusal', return_value={'why': 'busy'}):
            result = self.audit.preview(self.body)
        self.assertFalse(result['ready'])
        self.assertEqual(result['refusal']['reason'], 'manager-busy')
        self.assertFalse(result['scratchExtractionAttempted'])
        with patch.object(QApplication, 'activeModalWidget', return_value=object()):
            result = self.audit.preview(self.body)
        self.assertFalse(result['ready'])
        self.assertEqual(result['refusal']['reason'], 'preexisting-modal')
        self.assertFalse(result['scratchExtractionAttempted'])

    def test_preview_refuses_existing_target_and_structured_selection_error_before_scratch(self):
        self.make_target()
        result = self.assert_no_writes(lambda: self.audit.preview(self.body))
        self.assertFalse(result['ready'])
        self.assertEqual(result['refusal']['phase'], 'native-preflight')
        self.assertFalse(result['scratchWrites'])
        bad = dict(self.body, files=[{'source': '00 Core/payload.esp', 'destination': 'meta.ini'}])
        result = self.assert_no_writes(lambda: self.audit.preview(bad))
        self.assertFalse(result['accepted'])
        self.assertEqual(result['refusal']['index'], 0)
        self.assertEqual(result['refusal']['reason'], 'metadata-destination')
        self.assertFalse(result['scratchWrites'])

    def test_initial_and_final_expected_context_are_checked(self):
        result = self.audit.preview(dict(self.body, expectedServerBootId='other-boot'))
        self.assertFalse(result['accepted'])
        self.assertEqual(result['refusal']['reason'], 'expected-context-mismatch')
        self.assertFalse(self.scratch)
        calls = []

        def expected(_body):
            calls.append(True)
            if len(calls) > 1:
                raise ValueError('private exception text must not be returned')

        with patch.object(self.svc.ctx, 'expected', side_effect=expected):
            result = self.audit.preview(self.body)
        self.assertFalse(result['accepted'])
        self.assertEqual(result['refusal']['phase'], 'final-observation')
        self.assertEqual(result['refusal']['reason'], 'expected-context-mismatch')
        self.assertNotIn('private exception', json.dumps(result))

    def test_mutation_activity_and_observed_identity_divergence_prevent_acceptance(self):
        lease = self.svc.ctx.mutations.acquire('synthetic-write', self.svc.ctx.boot_id)
        try:
            result = self.audit.preview(self.body)
            self.assertFalse(result['accepted'])
            self.assertEqual(result['refusal']['reason'], 'bridge-mutation-active')
            self.assertFalse(self.scratch)
        finally:
            lease.release()
        self.make_target()
        original = self.svc.reader.native_identity
        calls = []

        def changed(*args):
            observed = original(*args)
            calls.append(True)
            if len(calls) > 1:
                observed['profile'] = 'changed-during-observation'
            return observed

        with patch.object(self.svc.reader, 'native_identity', side_effect=changed):
            result = self.audit.verify(self.body)
        self.assertFalse(result['verified'])
        self.assertEqual(result['snapshot']['state'], 'diverged')
        self.assertFalse(result['snapshot']['nativeAtomic'])
        self.assertFalse(result['snapshot']['externalWritersTracked'])

    def test_final_archive_hash_detects_change_during_native_accounting(self):
        self.make_target()

        def observe(*_args):
            with open(self.arc, 'ab') as stream:
                stream.write(b'changed-after-selection-hash')
            return {'verified': True, 'installed': True, 'nativeRefreshInvoked': False}

        self.observe.side_effect = observe
        result = self.audit.verify(self.body)
        self.assertFalse(result['verified'])
        self.assertEqual(check(result, 'archive-final-hash')['state'], 'mismatch')

    def test_mutation_that_starts_during_scratch_work_is_refused_at_final_boundary(self):
        evidence = self.svc.installer.selection_evidence
        leases = []

        def interrupted(*args):
            manifest = evidence(*args)
            leases.append(self.svc.ctx.mutations.acquire('later-write', self.svc.ctx.boot_id))
            return manifest

        try:
            with patch.object(self.svc.installer, 'selection_evidence', side_effect=interrupted):
                result = self.audit.preview(self.body)
            self.assertFalse(result['ready'])
            self.assertEqual(result['refusal']['phase'], 'final-observation')
            self.assertEqual(result['refusal']['reason'], 'bridge-mutation-active')
            self.assertTrue(all(not os.path.exists(path) for path in self.scratch))
        finally:
            for lease in leases:
                lease.release()


@unittest.skipUnless(QSettings, 'PyQt6 unavailable; no host qualification implied')
class PassiveDownloads(unittest.TestCase):
    def test_exact_native_installed_row_is_observed_without_refresh_or_filter_writes(self):
        # Reuse the existing native-shaped widget fixture, not a live manager.
        from test_native_adapter import NativeDownloadTests
        NativeDownloadTests.setUpClass()
        fixture = NativeDownloadTests()
        fixture.setUp()
        try:
            fixture.model.status = 'Installed'
            fixture.button.clicked.connect(lambda: self.fail('passive observation clicked Refresh'))
            fixture.box.setChecked(True)
            result = native.observe_downloads(fixture.organizer, fixture.archive)
            self.assertTrue(result['verified'])
            self.assertTrue(result['installed'])
            self.assertFalse(result['nativeRefreshInvoked'])
            self.assertEqual(fixture.refresh_count, 0)
            self.assertTrue(fixture.box.isChecked())
            fixture.model.status = 'Downloaded'
            with self.assertRaises(native.NativeInstallError) as error:
                native.observe_downloads(fixture.organizer, fixture.archive)
            self.assertFalse(error.exception.receipt['nativeRefreshInvoked'])
            self.assertEqual(fixture.refresh_count, 0)
        finally:
            fixture.tearDown()


if __name__ == '__main__':
    unittest.main()
