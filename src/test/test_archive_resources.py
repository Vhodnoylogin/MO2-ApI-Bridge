"""Archive failure/resource contracts on disposable files; no manager is contacted."""
from importlib import import_module
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

archives = import_module(common.NAME + '.archives')
config = import_module(common.NAME + '.config')
native = import_module(common.NAME + '.nativeinstall')

LISTING = '\n--\nType = zip\n\n----------\nPath = chosen.txt\nFolder = -\n\n'


class Resources(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.extractor = archives.ArchiveExtractor(config.Config())
        self.directories, self.lists = [], []
        make_dir, make_file = tempfile.mkdtemp, tempfile.mkstemp

        def directory(**kwargs):
            path = make_dir(dir=self.temp.name, **kwargs)
            self.directories.append(path)
            return path

        def listing(**kwargs):
            descriptor, path = make_file(dir=self.temp.name, **kwargs)
            self.lists.append(path)
            return descriptor, path

        for target, callback in (('mkdtemp', directory), ('mkstemp', listing)):
            replacement = patch.object(archives.tempfile, target, side_effect=callback)
            replacement.start()
            self.addCleanup(replacement.stop)
        replacement = patch.object(self.extractor.cfg, 'seven_zip', return_value='7z')
        replacement.start()
        self.addCleanup(replacement.stop)

    def test_subprocess_timeout_or_spawn_failure_removes_all_scratch(self):
        for selected in (False, True):
            for error in (subprocess.TimeoutExpired('7z', 1), OSError('cannot start')):
                with self.subTest(selected=selected, error=type(error)), \
                        patch.object(archives.subprocess, 'run', side_effect=[
                            SimpleNamespace(returncode=0, stdout=LISTING), error]):
                    with self.assertRaises(type(error)):
                        if selected:
                            self.extractor.unpack_selected('original.zip', ['chosen.txt'])
                        else:
                            self.extractor.unpack('original.zip')
                    self.assertTrue(self.directories)
                    self.assertFalse(any(os.path.exists(p) for p in self.directories + self.lists))

    def test_list_creation_failure_also_removes_allocated_directory(self):
        with patch.object(archives.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout=LISTING)), \
                patch.object(archives.tempfile, 'mkstemp', side_effect=PermissionError('held')):
            with self.assertRaises(PermissionError):
                self.extractor.unpack_selected('original.zip', ['chosen.txt'])
        self.assertTrue(self.directories)
        self.assertFalse(any(os.path.exists(p) for p in self.directories))

    def test_both_backends_reject_unsupported_volumes_before_output(self):
        listing = LISTING.replace('Type = zip', 'Type = Rar\nMultivolume = +')
        with patch.object(archives.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout=listing)):
            for selected in (False, True):
                with self.assertRaises(ValueError):
                    if selected:
                        self.extractor.unpack_selected('original.rar', ['chosen.txt'])
                    else:
                        self.extractor.unpack('original.rar')
        self.assertEqual(self.directories, [])

    def test_full_backend_validates_traversal_before_output(self):
        with patch.object(archives.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout=LISTING.replace('chosen.txt', '../outside.txt'))):
            with self.assertRaises(ValueError):
                self.extractor.unpack('original.zip')
        self.assertEqual(self.directories, [])


class SharedSelection(unittest.TestCase):
    def test_fomod_runtime_sources_keep_their_original_member_names(self):
        mappings = [
            {'source': 'FoMoD/Payload.esp', 'destination': 'Payload.esp'},
            {'source': 'fomod/f/npc/modular/Meshes/animations/config.json',
             'destination': 'Meshes/animations/config.json'},
            {'source': 'Wrapped/FoMoD/assets/Meshes/animation.hkx',
             'destination': 'Meshes/animation.hkx'}]
        self.assertEqual(archives.selection_mappings(mappings), mappings)
        self.assertEqual(native._mappings(mappings), mappings)

    def test_large_fomod_selection_keeps_exact_mapping_and_excludes_other_options(self):
        # Synthetic plugin, runtime JSON and animation inventory: no third-party data.
        mappings = [{'source': 'fomod/assets/core/Data/Payload.esp',
                     'destination': 'Payload.esp'}]
        for index in range(36):
            destination = 'Meshes/animations/group-{}/config.json'.format(index)
            mappings.append({'source': 'fomod/assets/npc/modular/' + destination,
                             'destination': destination})
        for index in range(1796):
            destination = 'Meshes/animations/group-{}/animation-{}.hkx'.format(index % 36, index)
            mappings.append({'source': 'fomod/assets/npc/modular/' + destination,
                             'destination': destination})
        self.assertEqual(len(mappings), 1833)
        result = archives.selection_mappings(mappings)
        self.assertEqual(result, mappings)
        self.assertIsNot(result, mappings)
        self.assertTrue(all(row is not original for row, original in zip(result, mappings)))
        self.assertEqual(native._mappings(mappings), mappings)
        sources = [row['source'] for row in result]
        extra = ['fomod/ModuleConfig.xml', 'fomod/info.xml', 'fomod/script.cs',
                 'fomod/assets/player/modular/Meshes/animations/config.json',
                 'fomod/assets/npc/simple/Meshes/animations/config.json',
                 'fomod/assets/wrong-runtime/SKSE/Plugins/fixture.dll']
        members = {path.lower(): path for path in sources + extra}
        self.assertEqual(archives.selected_members(members, sources),
                         sorted(sources + ['fomod/ModuleConfig.xml']))

    def test_same_source_can_target_two_unique_files(self):
        mappings = [{'source': 'fomod/assets/a.bin', 'destination': destination}
                    for destination in ('a.bin', 'sub/a.bin')]
        self.assertEqual(archives.selection_mappings(mappings), mappings)
        self.assertEqual(native._mappings(mappings), mappings)

    def test_installer_controls_and_metadata_cannot_be_mapped_as_payload(self):
        sources = ['meta.ini', 'Core/META.INI', 'fomod', 'Wrapper/FoMoD']
        for prefix in ('fomod/', 'Wrapped/FoMoD/', 'fomod/assets/nested/'):
            sources.extend(prefix + name for name in
                           ('ModuleConfig.xml', 'INFO.XML', 'Script.cs', 'meta.ini/part'))
        for source in sources:
            mappings = [{'source': source, 'destination': 'payload.bin'}]
            with self.subTest(source=source):
                with self.assertRaises(archives.SelectionError) as error:
                    archives.selection_mappings(mappings)
                self.assertEqual((error.exception.index, error.exception.reason),
                                 (0, 'metadata-source'))
                with self.assertRaises(native.NativeInstallError):
                    native._mappings(mappings)

    def test_mapping_diagnostics_are_indexed_path_free_value_errors(self):
        first = {'source': 'core/a.bin', 'destination': 'a.bin'}
        cases = [(None, None, 'invalid-files'), ([], None, 'invalid-files'),
                 ([first, {}], 1, 'invalid-mapping'),
                 ([first, {'source': '../private-input', 'destination': 'b.bin'}],
                  1, 'unsafe-source'),
                 ([first, {'source': 'core/b.bin', 'destination': 'b:stream'}],
                  1, 'unsafe-destination'),
                 ([first, {'source': 'fomod/info.xml', 'destination': 'b.bin'}],
                  1, 'metadata-source'),
                 ([first, {'source': 'core/b.bin', 'destination': 'b/meta.ini'}],
                  1, 'metadata-destination'),
                 ([first, {'source': 'core/b.bin', 'destination': 'INI Tweaks/b.ini'}],
                  1, 'ini-tweaks-destination'),
                 ([first, {'source': 'core/b.bin', 'destination': 'A.BIN'}],
                  1, 'duplicate-destination'),
                 ([first, {'source': 'core/b.bin', 'destination': 'A.BIN/b'}],
                  1, 'destination-file-directory-collision')]
        for mappings, index, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(ValueError) as error:
                    archives.selection_mappings(mappings)
                self.assertIsInstance(error.exception, archives.SelectionError)
                self.assertEqual((error.exception.index, error.exception.reason), (index, reason))
                self.assertEqual(str(error.exception), archives.i18n.t('err.installFiles'))

    def test_fomod_sources_still_reject_unsafe_windows_paths(self):
        for source in ('/fomod/a', '//server/fomod/a', 'C:/fomod/a', 'C:fomod/a',
                       'fomod/../a', 'fomod/./a', 'fomod//a', 'fomod/a:stream',
                       'fomod/CON', 'fomod/a/LPT1.txt', 'fomod/a. ', 'fomod/a\x00b',
                       'fomod/a\nb', 'fomod/a*b', 'fomod/a?b', 'fomod/a"b'):
            mappings = [{'source': source, 'destination': 'payload.bin'}]
            with self.subTest(source=source):
                with self.assertRaises(archives.SelectionError) as error:
                    archives.selection_mappings(mappings)
                self.assertEqual(error.exception.reason, 'unsafe-source')
                with self.assertRaises(native.NativeInstallError):
                    native._mappings(mappings)

    def test_aliases_metadata_and_file_directory_collisions_refuse_in_both_layers(self):
        invalid = [[{'source': 'Core/a.bin', 'destination': destination}]
                   for destination in ('CON', 'x/AUX.txt', 'x. ', 'x/meta.ini', 'x/FOMOD/a',
                                       'INI Tweaks/x.ini', 'meta.ini/part')]
        invalid.extend([[{'source': 'a', 'destination': 'A'},
                         {'source': 'b', 'destination': 'a/child'}],
                        [{'source': 'a', 'destination': 'a'},
                         {'source': 'b', 'destination': 'A'}]])
        for mappings in invalid:
            with self.subTest(mappings=mappings):
                with self.assertRaises(ValueError):
                    archives.selection_mappings(mappings)
                with self.assertRaises(native.NativeInstallError):
                    native._mappings(mappings)

    def test_fomod_listing_still_rejects_links_and_case_collisions(self):
        member = 'Path = fomod/assets/a.bin\nFolder = -\n'
        listings = [member + '\n' + member.replace('/a.bin', '/A.BIN')]
        listings.extend(member + name + ' = target\n' for name in
                        ('Symbolic Link', 'Hard Link', 'Copy Link'))
        for listing in listings:
            with self.subTest(listing=listing), self.assertRaises(ValueError):
                archives.archive_files(listing)


class RealFomodSelection(unittest.TestCase):
    def test_original_archive_extracts_only_selected_payload_and_config(self):
        extractor = archives.ArchiveExtractor(config.Config())
        if not extractor.cfg.seven_zip():
            self.skipTest('7-Zip unavailable')
        payload = {'FoMoD/assets/core/Data/Payload.esp': b'synthetic plugin',
                   'FoMoD/assets/npc/modular/Meshes/animations/config.json': b'{"npc": true}',
                   'FoMoD/assets/npc/modular/Meshes/animations/idle.hkx': b'synthetic animation'}
        controls = {'FoMoD/ModuleConfig.xml': b'<config/>', 'FoMoD/info.xml': b'<fomod/>',
                    'FoMoD/script.cs': b'installer only'}
        alternatives = {'FoMoD/assets/player/Meshes/animations/config.json': b'{"player": true}',
                        'FoMoD/assets/npc/simple/Meshes/animations/config.json': b'{"simple": true}'}
        with tempfile.TemporaryDirectory() as temporary:
            original = os.path.join(temporary, 'original.zip')
            with zipfile.ZipFile(original, 'w') as output:
                for path, data in {**payload, **controls, **alternatives}.items():
                    output.writestr(path, data)
            original_hash = archives.file_sha256(original)
            mappings = [{'source': source, 'destination': source.split('/Data/')[-1]
                         if '/Data/' in source else source.split('/modular/')[1]}
                        for source in payload]
            files = archives.selection_mappings(mappings)
            root = extractor.unpack_selected(original, [row['source'] for row in files])
            try:
                self.assertEqual(archives.tree_files(root),
                                 {path.replace('/', os.sep).lower()
                                  for path in list(payload) + ['FoMoD/ModuleConfig.xml']})
                for path, data in payload.items():
                    with open(os.path.join(root, *path.split('/')), 'rb') as source:
                        self.assertEqual(source.read(), data)
                self.assertEqual(archives.file_sha256(original), original_hash)
            finally:
                shutil.rmtree(root)


if __name__ == '__main__':
    unittest.main()
