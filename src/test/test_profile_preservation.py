"""Synthetic acceptance counterexamples; no installed profiles are accessed."""
import codecs
import copy
import unittest

from profile_preservation import compare_modlist, compare_profiles


class ProfilePreservationTests(unittest.TestCase):
    def setUp(self):
        self.raw = b'# native profile\r\n-Zeta\r\n+Group_separator\r\n*A.esm\r\n-Basic\r\n'

    def test_exact_and_explicit_disabled_insertions_preserve_original_bytes(self):
        after = self.raw.replace(b'-Zeta', '-Новый\r\n-Zeta'.encode()) + b'-New\r\n'
        self.assertTrue(compare_modlist(self.raw, self.raw)['exactBytes'])
        result = compare_modlist(self.raw, after, ['Новый', 'New'])
        self.assertTrue(result['preserved'])
        self.assertFalse(result['exactBytes'])
        self.assertEqual(result['permittedDisabledAdditions'], ['Новый', 'New'])

    def test_flag_reassignment_and_missing_disabled_tail_are_material(self):
        after = self.raw.replace(b'-Zeta', b'+Zeta').replace(b'+Group_', b'-Group_')
        after = after.replace(b'-Basic\r\n', b'')
        result = compare_modlist(self.raw, after)
        self.assertFalse(result['preserved'])
        self.assertEqual(result['missingOriginal'], ['Basic'])
        self.assertEqual(result['changedOriginal'], ['Zeta', 'Group_separator'])

    def test_identical_membership_and_flags_with_reordered_rows_still_fails(self):
        after = self.raw.replace(b'-Zeta\r\n+Group_separator', b'+Group_separator\r\n-Zeta')
        result = compare_modlist(self.raw, after)
        self.assertFalse(result['preserved'])
        self.assertEqual(result['changedOriginal'], [])

    def test_active_foreign_unknown_and_renamed_additions_fail(self):
        for row in (b'+New', b'*New', b'-Unknown', b'-new'):
            with self.subTest(row=row):
                result = compare_modlist(self.raw, self.raw + row + b'\r\n', ['New'])
                self.assertFalse(result['preserved'])
                self.assertEqual(len(result['unexpectedAdditions']), 1)

    def test_bom_eol_comments_case_and_final_newline_are_not_normalized(self):
        for after in (codecs.BOM_UTF8 + self.raw, self.raw.replace(b'\r\n', b'\n'),
                      self.raw.replace(b'native', b'changed'), self.raw[:-2],
                      self.raw.replace(b'-Zeta', b'-zeta')):
            with self.subTest(after=after):
                self.assertFalse(compare_modlist(self.raw, after)['preserved'])
        with_bom = codecs.BOM_UTF8 + self.raw
        self.assertTrue(compare_modlist(with_bom, with_bom)['preserved'])

    def test_malformed_duplicate_and_ambiguous_permission_inputs_raise(self):
        for data in (b'Zeta\r\n', b'-\r\n', b'-Zeta \r\n', b'-A\r\n+a\r\n', b'-\xff'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                compare_modlist(self.raw, data)
        for permission in ('New', ['New', 'new'], ['Zeta'], [None]):
            with self.subTest(permission=permission), self.assertRaises(ValueError):
                compare_modlist(self.raw, self.raw, permission)

    def test_all_profiles_and_other_files_are_compared_without_input_mutation(self):
        before = {'Selected/modlist.txt': self.raw, 'Inactive/modlist.txt': self.raw,
                  'Inactive/plugins.txt': b'# plugins\r\n', 'Inactive/saves/one.ess': b'\x00\xff'}
        after = dict(before)
        after['Selected/modlist.txt'] += b'-New\r\n'
        frozen = copy.deepcopy((before, after))
        policy = {'Selected/modlist.txt': ['New']}
        self.assertTrue(compare_profiles(before, after, policy)['preserved'])
        self.assertEqual((before, after), frozen)
        after['Inactive/modlist.txt'] = self.raw.replace(b'-Basic\r\n', b'')
        self.assertFalse(compare_profiles(before, after, policy)['preserved'])
        after['Inactive/modlist.txt'] = self.raw
        after['Inactive/saves/one.ess'] = b'changed'
        self.assertFalse(compare_profiles(before, after, policy)['preserved'])

    def test_deleted_added_and_empty_snapshot_never_pass(self):
        before = {'P/modlist.txt': self.raw}
        self.assertFalse(compare_profiles(before, {})['preserved'])
        self.assertFalse(compare_profiles(before, {**before, 'P/new.ini': b''})['preserved'])
        with self.assertRaises(ValueError):
            compare_profiles({}, {})
        with self.assertRaises(ValueError):
            compare_profiles(before, before, {'Q/modlist.txt': ['New']})


if __name__ == '__main__':
    unittest.main()
