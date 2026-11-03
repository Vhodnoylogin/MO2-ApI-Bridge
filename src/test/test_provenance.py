# -*- coding: utf-8 -*-
"""Historical receipt binding over synthetic metadata; never contacts a running MO2.

Matching native metadata is deliberately weaker than checking installed bytes or an
archive hash. All files here belong to a disposable fake organizer.
"""
import copy
import importlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_mo2
fake_mo2.install()
import common

reading = importlib.import_module(common.NAME + '.reading')
services = importlib.import_module(common.NAME + '.services')
try:
    from PyQt6.QtCore import QSettings
except ImportError:
    QSettings = None


def current_metadata():
    return {'installationFile': 'Original-101-123.7z', 'version': '1.0',
            'nexusId': 101, 'gameName': 'SkyrimVR'}


def historical_record():
    snapshot = current_metadata()
    snapshot['installedFiles'] = [{'modId': 101, 'fileId': 123}]
    return {'schemaVersion': 1, 'archive': 'downloads/' + snapshot['installationFile'],
            'archiveSha256': 'a' * 64, 'archiveBytes': 100, 'selection': {'options': ['old']},
            'files': [{'source': 'old/a.txt', 'destination': 'a.txt',
                       'sha256': 'b' * 64, 'bytes': 1}], 'nativeMetadata': snapshot}


class Binding(unittest.TestCase):
    def test_each_native_identity_change_is_stale_and_does_not_mutate_evidence(self):
        record = historical_record()
        untouched = copy.deepcopy(record)
        changed = {'installationFile': 'Replacement-101-456.7z', 'version': '2.0',
                   'nexusId': 202, 'gameName': 'SkyrimSE'}
        for field, value in changed.items():
            with self.subTest(field=field):
                current = dict(current_metadata(), **{field: value})
                status = reading.provenance_status(record, current,
                    record['nativeMetadata']['installedFiles'])
                self.assertEqual(status['state'], 'stale')
                self.assertTrue(any(row['field'] == field and row['state'] == 'mismatch'
                                    for row in status['checks']))
                self.assertFalse(status['archiveHashVerified'])
                self.assertFalse(status['payloadHashesVerified'])
        self.assertEqual(record, untouched)

    def test_changed_native_file_id_is_stale(self):
        status = reading.provenance_status(historical_record(), current_metadata(),
                                           [{'modId': 101, 'fileId': 456}])
        self.assertEqual(status['state'], 'stale')
        self.assertEqual(status['checks'][-1]['field'], 'installedFiles')
        self.assertEqual(status['checks'][-1]['state'], 'mismatch')

    def test_legacy_archive_mismatch_is_stale_but_same_archive_is_unknown(self):
        record = historical_record()
        del record['nativeMetadata']
        self.assertEqual(reading.provenance_status(record, current_metadata())['state'],
                         'unknown')
        current = dict(current_metadata(), installationFile='New-101-456.7z')
        self.assertEqual(reading.provenance_status(record, current)['state'], 'stale')

    def test_archive_names_use_windows_case_and_separator_semantics(self):
        record = historical_record()
        record['archive'] = 'X:' + chr(92) + 'Downloads' + chr(92) + 'ORIGINAL-101-123.7z'
        record['nativeMetadata']['installationFile'] = 'moved/Original-101-123.7z'
        status = reading.provenance_status(record, current_metadata(),
                                           [{'modId': '101', 'fileId': '123'}])
        self.assertEqual(status['state'], 'metadata-match')

    def test_incomplete_and_unsupported_records_never_match(self):
        for update in ({'schemaVersion': 2}, {'schemaVersion': True},
                       {'nativeMetadata': []}, {'nativeMetadata': {}},
                       {'nativeMetadata': dict(current_metadata(), installedFiles=None)}):
            with self.subTest(update=update):
                status = reading.provenance_status(dict(historical_record(), **update),
                    current_metadata(), [{'modId': 101, 'fileId': 123}])
                self.assertEqual(status['state'], 'unknown')

    def test_unavailable_current_getter_is_unknown_instead_of_a_false_match(self):
        current = dict(current_metadata(), version=None)
        status = reading.provenance_status(historical_record(), current,
                                           [{'modId': 101, 'fileId': 123}])
        self.assertEqual(status['state'], 'unknown')

    def test_invalid_and_oversized_identity_arrays_are_bounded_unknown(self):
        for rows in ([{'modId': True, 'fileId': 123}], [{'modId': 101, 'fileId': -1}],
                     [{'modId': 101}], ['not-a-row'],
                     [{'modId': 101, 'fileId': 123}] * 3):
            with self.subTest(rows=rows):
                record = historical_record()
                record['nativeMetadata']['installedFiles'] = rows
                status = reading.provenance_status(record, current_metadata(), rows, limit=2)
                self.assertEqual(status['state'], 'unknown')
                self.assertNotIn('recorded', status['checks'][-1])

    def test_empty_local_identity_array_matches_only_when_explicitly_observed(self):
        record = historical_record()
        record['nativeMetadata']['installedFiles'] = []
        self.assertEqual(reading.provenance_status(record, current_metadata(), [])['state'],
                         'metadata-match')
        self.assertEqual(reading.provenance_status(record, current_metadata(), None)['state'],
                         'unknown')

    def test_nonfinite_receipt_numbers_are_malformed_including_exponent_overflow(self):
        for value in ('NaN', 'Infinity', '-Infinity', '1e309', '-1e309'):
            for raw in (value, '{"schemaVersion":1,"nested":[{"value":' + value + '}]}'):
                with self.subTest(raw=raw):
                    record, status = reading.read_provenance(raw, current_metadata(), '', 2)
                    self.assertIsNone(record)
                    self.assertEqual(status['state'], 'malformed')
                    self.assertEqual(status['reasons'], ['invalid-json'])
        raw = '{"schemaVersion":1,"nested":[1.5,1e100,"NaN","Infinity"]}'
        record, _ = reading.read_provenance(raw, current_metadata(), '', 2)
        self.assertEqual(record['nested'], [1.5, 1e100, 'NaN', 'Infinity'])

    def test_json_and_shape_handling_preserves_existing_decoded_contract(self):
        for raw, want_record, state in (('null', None, 'absent'), (None, None, 'absent'),
                                        ('{broken', None, 'malformed'), ('[]', [], 'malformed'),
                                        ('false', False, 'malformed'), ('42', 42, 'malformed')):
            with self.subTest(raw=raw):
                record, status = reading.read_provenance(raw, current_metadata(), '', 2)
                self.assertEqual(record, want_record)
                self.assertEqual(status['state'], state)
        record, status = reading.read_provenance(None, current_metadata(), '', 2, unavailable=True)
        self.assertIsNone(record)
        self.assertEqual(status['state'], 'unknown')
        self.assertEqual(status['reasons'], ['setting-unavailable'])


@unittest.skipUnless(QSettings, 'PyQt6 unavailable; no host acceptance implied')
class ReaderMetadata(unittest.TestCase):
    def setUp(self):
        self.fx = fake_mo2.Fixture()
        self.addCleanup(self.fx.cleanup)
        self.path = self.fx.mod_path('Alpha Mod')
        self.meta_path = os.path.join(self.path, 'meta.ini')
        self.record = historical_record()
        self.set_current(**current_metadata())
        self.set_pairs([{'modId': 101, 'fileId': 123}])
        self.save_record(self.record)
        test = self

        class NativeMod(fake_mo2.FakeMod):
            def pluginSetting(self, plugin, key, default=None):
                return test.settings().value('Plugins/' + plugin + '/' + key, default)

            def installationFile(self):
                return test.settings().value('installationFile', '')

            def version(self):
                return fake_mo2._Version(test.settings().value('version', ''))

            def nexusId(self):
                return int(test.settings().value('modid', 0))

            def gameName(self):
                return test.settings().value('gameName', '')

        self.mod = NativeMod(self.fx.organizer, 'Alpha Mod')
        self.addCleanup(patch.stopall)
        original_get = self.fx.organizer.modList().getMod
        patch.object(self.fx.organizer.modList(), 'getMod', side_effect=
            lambda name: self.mod if name == 'Alpha Mod' else original_get(name)).start()
        self.in_main = False

        def run_main(fn, timeout=None):
            self.in_main = True
            try:
                return fn()
            finally:
                self.in_main = False
        self.svc = services.Services(self.fx.organizer, run_main, 'README.md')

    def settings(self):
        return QSettings(self.meta_path, QSettings.Format.IniFormat)

    def set_current(self, **values):
        settings = self.settings()
        for key, value in values.items():
            settings.setValue('modid' if key == 'nexusId' else key, value)
        settings.sync()

    def set_pairs(self, rows):
        settings = self.settings()
        settings.remove('installedFiles')
        settings.setValue('installedFiles/size', len(rows))
        for index, row in enumerate(rows, 1):
            for key, value in row.items():
                settings.setValue('installedFiles/%s/%s' % (index, key.lower()), value)
        settings.sync()

    def save_record(self, record):
        settings = self.settings()
        settings.setValue('Plugins/mo2aibridge/installProvenance', json.dumps(record))
        settings.sync()

    def card(self):
        return self.svc.mod({'name': ['Alpha Mod']})

    def test_current_metadata_match_retains_record_and_never_verifies_bytes(self):
        before = Path(self.meta_path).read_bytes()
        self.assertFalse(os.path.exists(self.record['archive']))
        with patch('hashlib.sha256', side_effect=AssertionError('unexpected hashing')), \
                patch.object(os, 'walk', side_effect=AssertionError('unexpected traversal')):
            card = self.card()
        self.assertEqual(card['installProvenance'], self.record)
        status = card['installProvenanceStatus']
        self.assertEqual(status['state'], 'metadata-match')
        self.assertEqual(status['scope'], 'native-metadata')
        self.assertFalse(status['archiveHashVerified'])
        self.assertFalse(status['payloadHashesVerified'])
        self.assertEqual(Path(self.meta_path).read_bytes(), before)

    def test_ordinary_replace_preserves_historical_record_but_reports_stale(self):
        self.set_current(installationFile='Replacement-101-456.7z', version='2.0')
        self.set_pairs([{'modId': 101, 'fileId': 456}])
        before = Path(self.meta_path).read_bytes()
        card = self.card()
        self.assertEqual(card['version'], '2.0')
        self.assertEqual(card['installationFile'], 'Replacement-101-456.7z')
        self.assertEqual(card['installProvenance'], self.record)
        self.assertEqual(card['installProvenanceStatus']['state'], 'stale')
        self.assertEqual(Path(self.meta_path).read_bytes(), before)

    def test_json_parsing_and_ini_reads_are_outside_native_main_thread_job(self):
        native_read, parse = reading.installed_file_metadata, json.loads

        def assert_outside_native(*args, **kwargs):
            self.assertFalse(self.in_main)
            return native_read(*args, **kwargs)

        def assert_outside_parse(*args, **kwargs):
            self.assertFalse(self.in_main)
            return parse(*args, **kwargs)

        with patch.object(reading, 'installed_file_metadata', side_effect=assert_outside_native), \
                patch.object(reading.json, 'loads', side_effect=assert_outside_parse):
            self.assertEqual(self.card()['installProvenanceStatus']['state'], 'metadata-match')

    def test_absent_and_malformed_settings_do_not_write_or_invent_a_receipt(self):
        for raw, state in ((None, 'absent'), ('{broken', 'malformed')):
            with self.subTest(raw=raw):
                settings = self.settings()
                if raw is None:
                    settings.remove('Plugins/mo2aibridge/installProvenance')
                else:
                    settings.setValue('Plugins/mo2aibridge/installProvenance', raw)
                settings.sync()
                before = Path(self.meta_path).read_bytes()
                card = self.card()
                self.assertIsNone(card['installProvenance'])
                self.assertEqual(card['installProvenanceStatus']['state'], state)
                self.assertEqual(Path(self.meta_path).read_bytes(), before)

    def test_missing_or_unbounded_installed_file_array_is_unknown(self):
        for count in (None, -1, 'invalid', 1000000000):
            with self.subTest(count=count):
                settings = self.settings()
                settings.remove('installedFiles')
                if count is not None:
                    settings.setValue('installedFiles/size', count)
                settings.sync()
                self.assertEqual(self.card()['installProvenanceStatus']['state'], 'unknown')

    def test_unavailable_qsettings_evidence_is_unknown(self):
        with patch.object(reading, 'installed_file_metadata',
                          return_value=(None, 'native-metadata-unavailable')):
            self.assertEqual(self.card()['installProvenanceStatus']['state'], 'unknown')


if __name__ == '__main__':
    unittest.main()
