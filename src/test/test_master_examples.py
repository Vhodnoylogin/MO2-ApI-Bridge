"""Strict pure client examples over synthetic data; no manager/files/network are read."""
import copy
import unittest

from examples.master_checks import (active_master_issues, compare_master_membership,
                                    compare_priority_order)


class MasterExamples(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {'plugin': 'Base.esm', 'active': True, 'priority': 0, 'loadOrder': 0},
            {'plugin': 'Disabled.esp', 'active': False, 'priority': 1, 'loadOrder': -1},
            {'plugin': 'DLC.esm', 'active': True, 'priority': 2, 'loadOrder': 1},
            {'plugin': 'Mod.esp', 'active': True, 'priority': 3, 'loadOrder': 2}]
        self.headers = {'Base.esm': [], 'DLC.esm': ['Base.esm'],
                        'Mod.esp': ['Base.esm', 'DLC.esm'],
                        'Disabled.esp': ['Missing.esm']}

    def test_order_only_master_difference_matches_without_rewriting_evidence(self):
        api, file = ['DLC.esm', 'Base.esm'], ['Base.esm', 'DLC.esm']
        before = copy.deepcopy((api, file))
        result = compare_master_membership(api, file)
        self.assertTrue(result['membersMatch'])
        self.assertFalse(result['sameSequence'])
        self.assertEqual((api, file), before)
        self.assertTrue(compare_master_membership(['BASE.esm'], ['Base.esm'])['membersMatch'])

    def test_duplicates_are_not_erased_by_set_comparison(self):
        for api, file in [(['A.esm', 'a.esm'], ['A.esm']),
                          (['A.esm'], ['A.esm', 'A.esm']),
                          (['A.esm', 'A.esm'], ['A.esm', 'A.esm'])]:
            with self.subTest(api=api, file=file):
                self.assertFalse(compare_master_membership(api, file)['membersMatch'])

    def test_real_membership_mismatch_still_fails(self):
        result = compare_master_membership(['Base.esm', 'Extra.esm'], ['Base.esm', 'DLC.esm'])
        self.assertFalse(result['membersMatch'])
        self.assertEqual(result['missingFromApi'], ['dlc.esm'])
        self.assertEqual(result['unexpectedInApi'], ['extra.esm'])

    def test_active_edges_use_actual_order_and_allow_disabled_dependents(self):
        before = copy.deepcopy((self.rows, self.headers))
        self.assertEqual(active_master_issues(self.rows, self.headers), [])
        self.headers['Mod.esp'].reverse()  # Physical MAST sequence is not load order.
        self.assertEqual(active_master_issues(self.rows, self.headers), [])
        self.headers['Mod.esp'].reverse()
        self.assertEqual((self.rows, self.headers), before)

    def test_missing_inactive_and_late_active_masters_fail(self):
        self.headers['Mod.esp'] = ['Missing.esm', 'Disabled.esp', 'DLC.esm']
        self.rows[2]['loadOrder'] = 3  # Priority still says DLC before Mod: not sufficient.
        issues = active_master_issues(self.rows, self.headers)
        self.assertEqual([row['reason'] for row in issues],
                         ['missing-master', 'inactive-master', 'late-master'])
        del self.headers['Mod.esp']
        self.assertEqual(active_master_issues(self.rows, self.headers),
                         [{'plugin': 'Mod.esp', 'reason': 'missing-file-header'}])

    def test_full_priority_includes_disabled_plugins_and_preserves_rows(self):
        before = copy.deepcopy(self.rows)
        expected = ['Base.esm', 'Disabled.esp', 'DLC.esm', 'Mod.esp']
        self.assertTrue(compare_priority_order(list(reversed(self.rows)), expected)['orderMatch'])
        self.assertFalse(compare_priority_order(self.rows,
                         ['Base.esm', 'DLC.esm', 'Disabled.esp', 'Mod.esp'])['orderMatch'])
        self.assertFalse(compare_priority_order(self.rows,
                         ['Base.esm', 'DLC.esm', 'Mod.esp'])['membersMatch'])
        self.assertEqual(self.rows, before)

    def test_malformed_or_ambiguous_input_never_verifies(self):
        with self.assertRaises(ValueError):
            compare_master_membership('Base.esm', ['Base.esm'])
        with self.assertRaises(ValueError):
            compare_priority_order(self.rows, ['Base.esm', 'base.esm'])
        with self.assertRaises(ValueError):
            active_master_issues(self.rows + [self.rows[0]], self.headers)
        self.rows[2]['loadOrder'] = 0
        with self.assertRaises(ValueError):
            active_master_issues(self.rows, self.headers)


if __name__ == '__main__':
    unittest.main()
