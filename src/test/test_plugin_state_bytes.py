"""Preserve every unrelated profile byte while changing activation markers."""
from importlib import import_module
from pathlib import Path
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_mo2
fake_mo2.install()
import common

loadorder = import_module(common.NAME + '.loadorder')
services = import_module(common.NAME + '.services')


class StateBytes(unittest.TestCase):
    def test_only_markers_change_with_bom_mixed_endings_and_blank_lines(self):
        original = b'\xef\xbb\xbf*Alpha.esp\r\n# comment\nBeta.esp\r\n\r\n'
        changed, done, missing, matches = loadorder.plugin_state_bytes(
            original, {'alpha.esp': False, 'beta.esp': True}, lambda b: b.decode('utf-8'))
        self.assertEqual(changed, b'\xef\xbb\xbfAlpha.esp\r\n# comment\n*Beta.esp\r\n\r\n')
        self.assertEqual(done, {'alpha.esp', 'beta.esp'})
        self.assertEqual(missing, [])
        self.assertFalse(matches)

    def test_unknown_undecodable_names_and_missing_final_newline_are_preserved(self):
        original = b'*Alpha.esp\r\n' + 'Тест.esp'.encode('cp1251')
        changed, _, _, _ = loadorder.plugin_state_bytes(
            original, {'alpha.esp': False}, lambda b: b.decode('utf-8'))
        self.assertEqual(changed, original[1:])

    def test_host_codec_matches_non_ascii_requested_name(self):
        original = 'Тест.esp\r\n'.encode('cp1251')
        changed, done, missing, matches = loadorder.plugin_state_bytes(
            original, {'тест.esp': True}, lambda b: b.decode('cp1251'))
        self.assertEqual(changed, b'*' + original)
        self.assertEqual(done, {'тест.esp'})
        self.assertEqual(missing, [])
        self.assertFalse(matches)

    def test_utf8_bom_overrides_host_codec_for_all_names(self):
        original = b'\xef\xbb\xbf' + 'Тест.esp\r\nДругой.esp\n'.encode('utf-8')
        changed, done, missing, _ = loadorder.plugin_state_bytes(
            original, {'тест.esp': True, 'другой.esp': True}, lambda b: b.decode('cp1251'))
        self.assertEqual(changed, b'\xef\xbb\xbf' + '*Тест.esp\r\n*Другой.esp\n'.encode('utf-8'))
        self.assertEqual(done, {'тест.esp', 'другой.esp'})
        self.assertEqual(missing, [])

    def test_native_decoder_is_used_once_for_a_whole_valid_file(self):
        decode = unittest.mock.Mock(side_effect=lambda raw: raw.decode('utf-8'))
        original = b'*Alpha.esp\r\nBeta.esp\r\n'
        changed, _, _, _ = loadorder.plugin_state_bytes(original, {'beta.esp': True}, decode)
        self.assertEqual(changed, b'*Alpha.esp\r\n*Beta.esp\r\n')
        self.assertEqual(decode.call_count, 1)

    def test_atomic_writer_keeps_exact_original_in_backup_and_other_names(self):
        fx = fake_mo2.Fixture()
        self.addCleanup(fx.cleanup)
        svc = services.Services(fx.organizer, lambda fn, timeout=None: fn(), 'README.md')
        path = Path(fx.organizer.profile().absolutePath()) / 'plugins.txt'
        original = b'*Alpha.esp\r\n' + 'Тест.esp\r\n'.encode('cp1251') + b'\r\n'
        path.write_bytes(original)
        with patch.object(svc.loadorder, '_order_text', side_effect=lambda raw, system=False:
                          raw.decode('cp1251')):
            result = svc.loadorder.write_plugins_txt({'alpha.esp': False})
            self.assertTrue(result['written'])
            self.assertEqual(path.read_bytes(), original[1:])
            self.assertEqual(Path(result['backup']).read_bytes(), original)
            retry = svc.loadorder.write_plugins_txt({'alpha.esp': False})
            self.assertTrue(retry['unchanged'])
            self.assertEqual(Path(result['backup']).read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
