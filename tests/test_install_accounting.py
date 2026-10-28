# -*- coding: utf-8 -*-
"""Archive accounting over disposable files and a mocked native installer.

No manager, installed mod, token, remote service or game is accessed. Real
QSettings exercises MO2's metadata format; these tests are not host acceptance.
"""
import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from contextlib import ExitStack
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_mo2
fake_mo2.install()
import common

installer = importlib.import_module(common.NAME + '.install')
native = importlib.import_module(common.NAME + '.nativeinstall')
services = importlib.import_module(common.NAME + '.services')
try:
    from PyQt6.QtCore import QSettings
except ImportError:
    QSettings = None


def settings(path):
    return QSettings(path, QSettings.Format.IniFormat)


class PathContract(unittest.TestCase):
    def test_windows_aliases_traversal_and_streams_are_rejected(self):
        for path in ('', '.', '..', '/x', 'C:x', 'a/../b', 'a//b', 'a\\..\\b',
                     'CON', 'x/LPT1.txt', 'x. ', 'a:stream', 'a\x00b', 'x/aux',
                     'x*', 'x?y', 'x\ny'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                installer.archive_member(path)
        self.assertEqual(installer.archive_member('00 Core\\SKSE\\x.dll'),
                         '00 Core/SKSE/x.dll')

    def test_real_archive_extracts_only_selection_and_fomod_metadata(self):
        fx = fake_mo2.Fixture()
        try:
            svc = services.Services(fx.organizer, lambda f, timeout=None: f(), 'README.md')
            if not svc.cfg.seven_zip():
                self.skipTest('7-Zip unavailable')
            archive = fx.path('selection.zip')
            with zipfile.ZipFile(archive, 'w') as output:
                output.writestr('00 Core/a.txt', 'selected')
                output.writestr('01 Other Runtime/a.txt', 'not selected')
                output.writestr('Wrapped/FoMoD/ModuleConfig.xml', '<config/>')
                output.writestr('Wrapped/FoMoD/large-image.bin', 'not selected')
            root = svc.installer._unpack_selected(archive, ['00 Core/a.txt'])
            try:
                self.assertEqual(installer.tree_files(root),
                    {os.path.join('00 core', 'a.txt'),
                     os.path.join('wrapped', 'fomod', 'moduleconfig.xml')})
            finally:
                shutil.rmtree(root)
        finally:
            fx.cleanup()


@unittest.skipUnless(QSettings, 'PyQt6 unavailable; no host acceptance implied')
class Accounting(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.fx = fake_mo2.Fixture()
        self.stack.callback(self.fx.cleanup)
        self.svc = services.Services(self.fx.organizer, lambda f, timeout=None: f(), 'README.md')
        self.svc.guard.refusal = lambda *a: None
        self.archive = self.fx.path('downloads', 'Original-101-123.7z')
        with zipfile.ZipFile(self.archive, 'w') as archive:
            archive.writestr('00 Core/payload.esp', b'exact selected payload')
            archive.writestr('01 Wrong Runtime/payload.esp', b'must never install')
            archive.writestr('fomod/ModuleConfig.xml', b'<config/>')
        meta = settings(self.archive + '.meta')
        meta.setValue('installed', False)
        meta.setValue('uninstalled', False)
        meta.setValue('modID', 101)
        meta.setValue('fileID', 123)
        meta.setValue('version', '1.0')
        meta.setValue('signedURL', 'opaque-scratch-value-preserved')
        meta.sync()
        with open(self.archive, 'rb') as source:
            self.original = source.read()
        self.body = {'archive': self.archive, 'name': 'Native Selection', 'native': True,
                     'archiveSha256': installer.file_sha256(self.archive),
                     'files': [{'source': '00 Core/payload.esp', 'destination': 'payload.esp'}],
                     'selection': {'runtime': 'fixture', 'options': ['core']}}
        self.calls = []
        self.behavior = ''
        self.stack.enter_context(patch.object(native, 'check_available', return_value=True))
        self.stack.enter_context(patch.object(native, 'install', side_effect=self.native_install))
        self.stack.enter_context(patch.object(native, 'refresh_downloads', create=True,
            side_effect=lambda *a, **kw: {'verified': self.behavior != 'model-stale', 'found': True}))
        self.stack.enter_context(patch.object(self.svc.installer, '_unpack', side_effect=self.unpack))
        self.stack.enter_context(patch.object(self.svc.installer, '_unpack_selected',
            side_effect=lambda arc, sources: self.unpack(arc)))

    def tearDown(self):
        self.stack.close()

    def unpack(self, arc):
        root = tempfile.mkdtemp(dir=self.fx.root)
        with zipfile.ZipFile(arc) as archive:
            archive.extractall(root)
        return root

    def native_install(self, organizer, arc, name, mappings, **kwargs):
        self.calls.append((arc, name, mappings))
        mod = organizer.createMod(fake_mo2.GuessedString(name))
        with zipfile.ZipFile(arc) as archive:
            for row in mappings:
                target = os.path.join(mod.absolutePath(), *row['destination'].split('/'))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, 'wb') as output:
                    output.write(archive.read(row['source']))
        if self.behavior == 'extra':
            open(os.path.join(mod.absolutePath(), 'unselected.dll'), 'wb').close()
        if self.behavior == 'corrupt':
            with open(os.path.join(mod.absolutePath(), mappings[0]['destination']), 'wb') as output:
                output.write(b'wrong bytes')
        if self.behavior == 'raises':
            raise native.NativeInstallError('synthetic-native-failure', {'installerInvoked': True})
        metadata_path = os.path.join(mod.absolutePath(), 'meta.ini')
        metadata = settings(metadata_path)
        metadata.setValue('installationFile', os.path.basename(arc))
        metadata.setValue('modid', 101)
        metadata.setValue('version', '1.0')
        metadata.setValue('installedFiles/size', 0 if self.behavior == 'missing-file-id' else 1)
        metadata.setValue('installedFiles/1/modid', 101)
        metadata.setValue('installedFiles/1/fileid', 123)
        metadata.sync()
        download = settings(arc + '.meta')
        if self.behavior != 'not-marked':
            download.setValue('installed', True)
        if self.behavior == 'metadata-damage':
            download.remove('signedURL')
        download.sync()

        class NativeMod(fake_mo2.FakeMod):
            def installationFile(self):
                return settings(metadata_path).value('installationFile', '')

            def setPluginSetting(self, plugin, key, value):
                if self_behavior == 'provenance-held':
                    return False
                metadata = settings(metadata_path)
                metadata.setValue('Plugins/' + plugin + '/' + key, value)
                metadata.sync()
                return True

            def pluginSetting(self, plugin, key, default=None):
                return settings(metadata_path).value('Plugins/' + plugin + '/' + key, default)

        self_behavior = self.behavior
        native_mod = NativeMod(organizer, name)
        original_get = organizer.modList().getMod
        self.stack.enter_context(patch.object(organizer.modList(), 'getMod', side_effect=
            lambda target: native_mod if target == name else original_get(target)))
        return native_mod, {'selectionPrepared': True, 'installerInvoked': True}

    def test_completed_native_install_records_original_archive_selection_and_accounting(self):
        result = self.svc.install(self.body)
        self.assertTrue(result['applied'])
        for key in ('installationComplete', 'filesVerified', 'archiveInstalled',
                    'archiveMetadataPreserved', 'provenanceSaved', 'refreshRequested',
                    'downloadStatusVerified'):
            self.assertTrue(result[key], key)
        self.assertEqual(result['files'], 1)
        with open(self.archive, 'rb') as source:
            self.assertEqual(source.read(), self.original)
        self.assertEqual(settings(self.archive + '.meta').value('signedURL'),
                         'opaque-scratch-value-preserved')
        provenance = result['installProvenance']
        self.assertEqual(provenance['archiveSha256'], self.body['archiveSha256'])
        self.assertEqual(provenance['selection'], self.body['selection'])
        self.assertEqual(len(provenance['fomod']), 1)
        self.assertEqual(provenance['files'][0]['source'], '00 Core/payload.esp')
        self.assertEqual(self.svc.mod({'name': ['Native Selection']})['installProvenance'], provenance)
        self.assertNotIn('signedURL', json.dumps(result))

    def test_unicode_filename_inventory_uses_consistent_normalization(self):
        self.body['files'][0]['destination'] = 'Straße.esp'
        self.assertTrue(self.svc.install(self.body)['installationComplete'])

    def test_invalid_manifest_hash_and_options_refuse_before_any_native_effect(self):
        invalid = [dict(archiveSha256='0' * 64), dict(files=[]), dict(selection={}),
                   dict(files=[{'source': 'missing.esp', 'destination': 'x.esp'}]),
                   dict(files=[{'source': '00 Core/payload.esp', 'destination': 'meta.ini'}]),
                   dict(files=[{'source': '00 Core/payload.esp', 'destination': 'INI Tweaks/x.ini'}]),
                   dict(paths=[]), dict(mode='merge'),
                   dict(files=[{'source': '00 Core/payload.esp', 'destination': 'x'},
                               {'source': '00 Core/payload.esp', 'destination': 'X/y'}])]
        for update in invalid:
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.svc.install(dict(self.body, **update))
        self.assertEqual(self.calls, [])
        self.assertFalse(os.path.exists(self.fx.mod_path('Native Selection')))
        self.assertFalse(settings(self.archive + '.meta').value('installed', type=bool))

    def test_existing_target_refuses_before_native_effect(self):
        self.body['name'] = 'Alpha Mod'
        with self.assertRaises(ValueError):
            self.svc.install(self.body)
        self.assertEqual(self.calls, [])

    def test_concurrent_target_refusal_never_suggests_deleting_foreign_mod(self):
        def foreign_target(*args, **kwargs):
            target = self.fx.mod_path(self.body['name'])
            os.mkdir(target)
            with open(os.path.join(target, 'foreign.txt'), 'w') as output:
                output.write('belongs to another operation')
            raise native.NativeInstallError('target-exists')
        with patch.object(native, 'install', side_effect=foreign_target):
            result = self.svc.install(self.body)
        self.assertFalse(result['applied'])
        self.assertIsNone(result['created'])
        self.assertIsNone(result['undo'])
        self.assertTrue(result['targetExists'])
        self.assertTrue(os.path.isfile(self.fx.mod_path('Native Selection') + '/foreign.txt'))

    def test_archive_outside_downloads_refuses_before_native_effect(self):
        outside = self.fx.path('outside.7z')
        shutil.copy2(self.archive, outside)
        self.body['archive'] = outside
        with self.assertRaises(ValueError):
            self.svc.install(self.body)
        self.assertEqual(self.calls, [])

    def test_partial_or_unverified_native_results_are_never_complete(self):
        for behavior in ('extra', 'corrupt', 'not-marked', 'metadata-damage',
                         'provenance-held', 'raises', 'model-stale', 'missing-file-id'):
            with self.subTest(behavior=behavior):
                self.behavior = behavior
                download = settings(self.archive + '.meta')
                download.setValue('installed', False)
                download.sync()
                body = dict(self.body, name='Partial ' + behavior)
                result = self.svc.install(body)
                self.assertFalse(result['applied'])
                self.assertFalse(result['installationComplete'])
                self.assertTrue(result['partial'])
                self.assertTrue(result['reconciliationRequired'])
                if behavior == 'raises':
                    self.assertIsNone(result['undo'])
                    self.assertIsNone(result['created'])
                else:
                    self.assertIsNotNone(result['undo'])
                self.assertNotIn('opaque-scratch-value', json.dumps(result))
                if behavior == 'metadata-damage':
                    meta = settings(self.archive + '.meta')
                    meta.setValue('signedURL', 'opaque-scratch-value-preserved')
                    meta.sync()

    def test_successful_legacy_copy_discloses_incomplete_accounting(self):
        body = {'archive': self.archive, 'name': 'Legacy Selection', 'paths': ['00 Core']}
        result = self.svc.install(body)
        self.assertTrue(result['applied'])
        self.assertEqual(result['backend'], 'copy')
        self.assertFalse(result['installationComplete'])
        self.assertFalse(result['archiveInstalled'])
        self.assertTrue(result['accountingRequired'])
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
