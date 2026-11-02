"""Archive failure/resource contracts on disposable files; no manager is contacted."""
from importlib import import_module
import os
import subprocess
import sys
import tempfile
import unittest
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
    def test_same_source_can_target_two_unique_files(self):
        mappings = [{'source': 'Core/a.bin', 'destination': destination}
                    for destination in ('a.bin', 'sub/a.bin')]
        self.assertEqual(archives.selection_mappings(mappings), mappings)
        self.assertEqual(native._mappings(mappings), mappings)

    def test_aliases_metadata_and_file_directory_collisions_refuse_in_both_layers(self):
        invalid = [[{'source': 'Core/a.bin', 'destination': destination}]
                   for destination in ('CON', 'x/AUX.txt', 'x. ', 'x/meta.ini', 'x/FOMOD/a')]
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


if __name__ == '__main__':
    unittest.main()
