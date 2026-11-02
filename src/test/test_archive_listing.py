# -*- coding: utf-8 -*-
"""Pure technical-listing regressions and refusal before extraction side effects."""
import importlib
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_mo2
fake_mo2.install()
import common

installer = importlib.import_module(common.NAME + '.install')
services = importlib.import_module(common.NAME + '.services')

FILE = 'Path = options/a.bin\nSize = 3\nAttributes = A\nEncrypted = -\nBlock = 0'
DIRECTORY = ('Path = options/empty\nSize = 0\nPacked Size = 0\nAttributes = D\n'
             'CRC = \nEncrypted = -\nMethod = \nBlock = ')


class TechnicalListing(unittest.TestCase):
    def test_final_empty_directory_values_survive_every_line_ending_and_position(self):
        for ending in ('\n', '\r\n'):
            for records in ((FILE, DIRECTORY), (DIRECTORY, FILE),
                            (DIRECTORY, FILE, DIRECTORY.replace('empty', 'other'))):
                for suffix in ('', '\n', '\n\n', '\n\n\n'):
                    text = ('\n\n'.join(records) + suffix).replace('\n', ending)
                    with self.subTest(ending=repr(ending), records=records, suffix=repr(suffix)):
                        self.assertEqual(installer.archive_files(text),
                                         {'options/a.bin': 'options/a.bin'})

    def test_empty_file_values_and_embedded_delimiter_are_preserved(self):
        listing = FILE.replace('options/a.bin', 'options/a = b.bin') + '\nComment = '
        self.assertEqual(installer.archive_files(listing),
                         {'options/a = b.bin': 'options/a = b.bin'})
        self.assertEqual(installer.archive_files(''), {})
        self.assertEqual(installer.archive_files(DIRECTORY), {})

    def test_malformed_fields_missing_paths_duplicates_encryption_and_unsafe_paths_refused(self):
        invalid = [FILE + '\nBlock =', FILE + '\nBlock = 1',
                   FILE.replace('Path = options/a.bin\n', ''), FILE + '\nbroken',
                   FILE + '\n = value', FILE.replace('Encrypted = -', 'Encrypted = +'),
                   FILE + '\n\n' + FILE.upper().replace('PATH', 'Path').replace('SIZE', 'Size'),
                   FILE + '\n\n' + FILE,
                   FILE + '\n\n' + DIRECTORY.replace('options/empty', 'options/a.bin')]
        invalid.extend(FILE.replace('options/a.bin', path) for path in
                       ('../escape', 'C:/absolute', 'a:stream', 'a/CON', 'a/../x'))
        invalid.extend(DIRECTORY.replace('options/empty', path) for path in
                       ('../escape', 'C:/absolute', 'a:stream'))
        for listing in invalid:
            with self.subTest(listing=listing), self.assertRaises(ValueError):
                installer.archive_files(listing)

    def test_duplicate_case_variants_refused(self):
        for second in (FILE.replace('options/a.bin', 'OPTIONS/A.BIN'),
                       DIRECTORY.replace('options/empty', 'OPTIONS/A.BIN')):
            with self.assertRaises(ValueError):
                installer.archive_files(FILE + '\n\n' + second)

    def test_refusal_before_extraction_or_temporary_output(self):
        fx = fake_mo2.Fixture()
        try:
            svc = services.Services(fx.organizer, lambda f, timeout=None: f(), 'README.md')
            for listing, sources in ((FILE + '\nBlock =', ['options/a.bin']),
                                     (FILE + '\n\n' + DIRECTORY, ['missing.bin'])):
                with patch.object(svc.cfg, 'seven_zip', return_value='7z'), \
                        patch.object(installer.subprocess, 'run', return_value=SimpleNamespace(
                            returncode=0, stdout=listing)) as run, \
                        patch.object(installer.tempfile, 'mkdtemp') as mkdtemp:
                    with self.assertRaises(ValueError):
                        svc.installer._unpack_selected('original.7z', sources)
                    self.assertEqual(run.call_count, 1)
                    mkdtemp.assert_not_called()
        finally:
            fx.cleanup()


if __name__ == '__main__':
    unittest.main()
