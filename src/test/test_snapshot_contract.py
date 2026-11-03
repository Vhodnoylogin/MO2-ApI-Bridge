"""Optional mutation targets and observed read consistency over a disposable fake MO2."""
import importlib
import os
import sys
import unittest
from contextlib import ExitStack
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_mo2
fake_mo2.install()
import common

services = importlib.import_module(common.NAME + '.services')
base = importlib.import_module(common.NAME + '.base')
reading = importlib.import_module(common.NAME + '.reading')


class Targets(unittest.TestCase):
    def setUp(self):
        self.fx = fake_mo2.Fixture()
        self.addCleanup(self.fx.cleanup)
        self.o = self.fx.organizer
        self.svc = services.Services(self.o, lambda f, timeout=None: f(), 'README.md')
        self.svc.guard.refusal = lambda *_: None

    def commands(self):
        key = {base.DANGER_KEY: base.DANGER_VALUE}
        return [(self.svc.toggle, {'mod': 'Alpha Mod', 'active': False}),
                (self.svc.mods_priority, dict(key, mod='Alpha Mod', priority=2)),
                (self.svc.mods_rename, dict(key, mod='Alpha Mod', newName='Renamed')),
                (self.svc.refresh, {})]

    def test_all_four_routes_reject_supplied_stale_identity_without_native_writes(self):
        for command, body in self.commands():
            for field, value in (('expectedProfile', 'Second'), ('expectedInstance', 'foreign'),
                                 ('expectedServerBootId', 'retired')):
                with self.subTest(command=command.__name__, field=field), ExitStack() as stack:
                    writes = [stack.enter_context(patch.object(obj, method)) for obj, method in
                              ((self.o.modList(), 'setActive'), (self.o.modList(), 'setPriority'),
                               (self.o.modList(), 'renameMod'), (self.o, 'refresh'))]
                    with self.assertRaises(ValueError):
                        command(dict(body, **{field: value}))
                    for write in writes:
                        write.assert_not_called()

    def test_profile_switch_after_admission_is_checked_inside_qt_job(self):
        for command, body in self.commands():
            with self.subTest(command=command.__name__):
                def delayed(fn, timeout=None):
                    self.o._profile = 'Second'
                    return fn()
                domain = self.svc.modops
                with patch.object(domain, 'run_main', side_effect=delayed), \
                        patch.object(self.o, 'refresh') as refresh:
                    with self.assertRaises(ValueError):
                        command(dict(body, expectedProfile='Claude'))
                    refresh.assert_not_called()
                self.o._profile = 'Claude'
                self.assertTrue(self.o.modList().state('Alpha Mod') & fake_mo2.ModState.ACTIVE)
                self.assertEqual(self.o.modList().priority('Alpha Mod'), 0)
                self.assertIsNotNone(self.o.modList().getMod('Alpha Mod'))

    def test_profile_a_b_a_checks_current_identity_and_omission_keeps_behavior(self):
        self.o._profile = 'Second'
        with self.assertRaises(ValueError):
            self.svc.toggle({'mod': 'Alpha Mod', 'active': False, 'expectedProfile': 'Claude'})
        self.o._profile = 'Claude'
        result = self.svc.toggle({'mod': 'Alpha Mod', 'active': False, 'expectedProfile': 'Claude',
                                  'expectedInstance': self.svc.ctx.instance_id})
        self.assertTrue(result['applied'])
        self.assertFalse(result['active'])
        self.assertTrue(self.svc.toggle({'mod': 'Alpha Mod', 'active': True})['active'])
        self.assertTrue(self.svc.refresh()['refreshed'])
        self.assertTrue(self.svc.mods_priority(dict(self.commands()[1][1]))['applied'])
        self.assertTrue(self.svc.mods_rename(dict(self.commands()[2][1]))['applied'])


class Snapshots(unittest.TestCase):
    def setUp(self):
        self.fx = fake_mo2.Fixture()
        self.addCleanup(self.fx.cleanup)
        self.o = self.fx.organizer
        self.svc = services.Services(self.o, lambda f, timeout=None: f(), 'README.md')
        self.svc.guard.refusal = lambda *_: None

    def test_unchanged_snapshot_is_observed_evidence_and_disk_is_never_atomic(self):
        result = self.svc.analyze({'name': 'Alpha Mod'})
        self.assertTrue(result['complete'])
        snapshot = result['snapshot']
        self.assertEqual(snapshot['state'], 'no-observed-divergence')
        self.assertFalse(snapshot['nativeAtomic'])
        self.assertFalse(snapshot['diskAtomic'])
        self.assertFalse(snapshot['externalWritersTracked'])
        self.assertTrue(snapshot['unobservedIntermediateChangesPossible'])
        self.assertEqual(snapshot['observations']['collectionBefore']['profile'], 'Claude')

    def test_standalone_card_does_not_enumerate_or_hash_the_entire_mod_list(self):
        with patch.object(self.o.modList(), 'allModsByProfilePriority',
                          side_effect=AssertionError('unexpected whole-order walk')):
            card = self.svc.mod({'name': 'Alpha Mod'})
        self.assertEqual(card['snapshot']['state'], 'no-observed-divergence')
        self.assertNotIn('modOrderSha256', card['snapshot']['observations']['after'])

    def test_profile_change_between_collection_and_card_is_explicit_divergence(self):
        original = self.svc.reader.mod
        def switched(query, **kwargs):
            self.o._profile = 'Second'
            return original(query, **kwargs)
        with patch.object(self.svc.reader, 'mod', side_effect=switched):
            result = self.svc.analyze({'name': 'Alpha Mod'})
        # complete retains its original collection-error meaning.
        self.assertTrue(result['complete'])
        self.assertEqual(result['snapshot']['state'], 'diverged')
        observed = result['snapshot']['observations']
        self.assertEqual(observed['collectionBefore']['profile'], 'Claude')
        self.assertEqual(observed['cardBefore']['profile'], 'Second')

    def test_reindex_without_profile_change_is_detected_between_phases(self):
        original = self.svc.reader.mod
        def reordered(query, **kwargs):
            self.o.modList().setPriority('Alpha Mod', 3)
            return original(query, **kwargs)
        with patch.object(self.svc.reader, 'mod', side_effect=reordered):
            result = self.svc.analyze({'name': 'Alpha Mod'})
        self.assertEqual(result['snapshot']['state'], 'diverged')
        self.assertEqual(result['card']['priority'], 3)

    def test_profile_switch_inside_origin_collection_is_detected(self):
        original = self.o.getFileOrigins
        def switched(path):
            self.o._profile = 'Second'
            return original(path)
        with patch.object(self.o, 'getFileOrigins', side_effect=switched):
            result = self.svc.analyze({'name': 'Alpha Mod'})
        self.assertEqual(result['snapshot']['state'], 'diverged')
        self.assertTrue(result['conflicts']['complete'])

    def test_unobserved_a_b_a_does_not_claim_atomicity_or_history_detection(self):
        original = self.svc.reader.mod
        def returned(query, **kwargs):
            self.o._profile = 'Second'
            self.o._profile = 'Claude'
            return original(query, **kwargs)
        with patch.object(self.svc.reader, 'mod', side_effect=returned):
            result = self.svc.analyze({'name': 'Alpha Mod'})
        self.assertEqual(result['snapshot']['state'], 'no-observed-divergence')
        self.assertTrue(result['snapshot']['unobservedIntermediateChangesPossible'])
        self.assertFalse(result['snapshot']['nativeAtomic'])

    def test_card_failure_and_partial_disk_inventory_keep_collection_contract(self):
        with patch.object(self.svc.reader, 'mod', side_effect=RuntimeError('unavailable')):
            result = self.svc.analyze({'name': 'Alpha Mod'})
        self.assertFalse(result['complete'])
        self.assertEqual(result['snapshot']['state'], 'unknown')
        def unreadable(root, onerror=None):
            onerror(PermissionError('held'))
            return iter([])
        with patch.object(reading.os, 'walk', side_effect=unreadable):
            result = self.svc.analyze({'name': 'Alpha Mod', 'conflicts': '0'})
        self.assertFalse(result['inventoryComplete'])
        self.assertFalse(result['complete'])
        self.assertEqual(result['errorCount'], 1)
        self.assertEqual(result['snapshot']['state'], 'no-observed-divergence')

    def test_known_divergence_is_retained_even_when_card_observation_is_missing(self):
        original = self.o.getFileOrigins
        def switched(path):
            self.o._profile = 'Second'
            return original(path)
        with patch.object(self.o, 'getFileOrigins', side_effect=switched), \
                patch.object(self.svc.reader, 'mod', side_effect=RuntimeError('unavailable')):
            result = self.svc.analyze({'name': 'Alpha Mod'})
        self.assertFalse(result['complete'])
        self.assertEqual(result['snapshot']['state'], 'diverged')

    def test_native_getters_remain_in_native_jobs(self):
        in_main = False
        def run_main(fn, timeout=None):
            nonlocal in_main
            previous, in_main = in_main, True
            try:
                return fn()
            finally:
                in_main = previous
        def checked(fn):
            def call(*args, **kwargs):
                self.assertTrue(in_main)
                return fn(*args, **kwargs)
            return call
        with ExitStack() as stack:
            stack.enter_context(patch.object(self.svc.reader, 'run_main', side_effect=run_main))
            for method in ('profile', 'modList', 'modsPath', 'getFileOrigins'):
                stack.enter_context(patch.object(self.o, method, side_effect=checked(getattr(self.o, method))))
            self.assertTrue(self.svc.analyze({'name': 'Alpha Mod'})['complete'])


if __name__ == '__main__':
    unittest.main()
