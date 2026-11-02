# -*- coding: utf-8 -*-
"""Update-source identity and sweep reuse over synthetic files; no network or MO2."""
import importlib
import json
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_mo2
fake_mo2.install()
import common

services = importlib.import_module(common.NAME + '.services')
updates = importlib.import_module(common.NAME + '.updates')
base = importlib.import_module(common.NAME + '.base')


class UpdatePolicyPurityTests(unittest.TestCase):
    def test_domain_resolution_separates_managed_game_and_explicit_foreign_game(self):
        resolve = base.resolve_nexus_domain
        self.assertEqual(resolve('skyrimvr', {}, 'SkyrimVR', 'skyrimspecialedition'),
                         'skyrimspecialedition')
        self.assertEqual(resolve(None, {}, 'SkyrimVR', 'skyrimspecialedition'),
                         'skyrimspecialedition')
        self.assertEqual(resolve('ForeignGame', {}, 'SkyrimVR', 'skyrimspecialedition'), '')
        self.assertEqual(resolve('foreigngame', {'ForeignGame': 'foreignsection'},
                                 'SkyrimVR', 'skyrimspecialedition'), 'foreignsection')
        self.assertEqual(resolve('ForeignGame', {}, 'SkyrimVR', 'skyrimspecialedition',
                                 'explicit-fallback'), 'explicit-fallback')

    def test_decision_does_not_annotate_caller_file_records(self):
        files = [{'name': 'Example', 'fileName': 'Example.7z', 'fileId': 1,
                  'version': '1', 'category': 'main', 'time': 100, 'got': False}]
        original = [dict(record) for record in files]
        result = updates.decide(files, {1}, {'Example.7z'}, 100)
        self.assertEqual(result['verdict'], 'UP-TO-DATE')
        self.assertEqual(files, original)


class UpdateTargetsTests(unittest.TestCase):
    def setUp(self):
        self.fx = fake_mo2.Fixture()
        self.svc = services.Services(self.fx.organizer,
                                    run_main=lambda f, timeout=None: f(), docs_path='README.md')
        self.updater = self.svc.updater

    def tearDown(self):
        self.fx.cleanup()

    def download(self, archive, mod_id=101, file_id=1002, game='SkyrimSE', present=True):
        path = self.fx.path('downloads', archive)
        if present:
            with open(path, 'wb') as stream:
                stream.write(b'synthetic archive')
        with open(path + '.meta', 'w', encoding='utf-8') as stream:
            # gameName intentionally follows both IDs, as sidecar ordering is not fixed.
            stream.write('[General]\nmodID=%d\nfileID=%d\ngameName=%s\n' %
                         (mod_id, file_id, game))
        return path

    def page_files(self):
        return [
            {'name': 'Alpha Mod', 'fileName': 'Alpha Mod-101-1-0.7z',
             'fileId': 1001, 'version': '1.0', 'category': 'old', 'time': fake_mo2.T0},
            {'name': 'Alpha Mod', 'fileName': 'New-101-1-1.7z',
             'fileId': 1002, 'version': '1.1', 'category': 'main',
             'time': fake_mo2.T0 + 30 * fake_mo2.DAY}]

    def mod_metadata(self, name, **fields):
        with open(os.path.join(self.fx.mod_path(name), 'meta.ini'), 'w', encoding='utf-8') as stream:
            stream.write('[General]\n')
            for key, value in fields.items():
                stream.write('%s=%s\n' % (key, value))

    def test_page_identity_resolves_aliases_without_merging_games(self):
        domains = self.svc.cfg.get('nexusDomains')
        self.assertEqual(updates._page_key('SkyrimVR', 101, domains),
                         updates._page_key('SKYRIMSPECIALEDITION', 101, domains))
        self.assertNotEqual(updates._page_key('Skyrim', 101, domains),
                            updates._page_key('SkyrimSE', 101, domains))
        self.assertEqual(updates._page_key('UnknownGame', 101, domains), ('unknowngame', 101))
        self.assertEqual(updates._page_key(' SkyrimVR ', 101, domains),
                         updates._page_key('skyrimspecialedition', 101, domains))

    def test_blank_aliases_cannot_reuse_managed_page_for_foreign_game(self):
        self.svc.cfg.values['nexusDomains'] = {'SkyrimVR': '', 'ForeignGame': ''}
        self.svc.cfg.values['nexusDomainDefault'] = ''
        self.svc.cfg.values['updates']['delaySec'] = 0
        self.svc.o.managedGame().nexus_name = 'managedsection'
        self.mod_metadata('Beta Mod', modid=101, gamename='ForeignGame')
        self.updater._mode = 'api'
        self.updater._api_key = 'synthetic-key'
        requests = []

        def http(url, _headers, _timeout):
            requests.append(url)
            return 200, {}, json.dumps({'files': []})

        self.updater._http_get = http
        result = self.updater.updates({'mod': ['Alpha Mod', 'Beta Mod']})
        self.assertEqual(result['checked'], 1)
        self.assertEqual([row['checked'] for row in result['mods']], [True, False])
        self.assertEqual(result['mods'][1]['error'],
                         updates.i18n.t('upd.noDomain', game='ForeignGame'))
        self.assertEqual(len(requests), 1)
        self.assertIn('/games/managedsection/mods/101/', requests[0])

    def test_foreign_game_is_refused_before_bridge_or_api_request(self):
        self.svc.cfg.values['nexusDomains'] = {}
        self.svc.cfg.values['nexusDomainDefault'] = ''
        calls = []

        def forbidden(*_args):
            calls.append(True)
            self.fail('foreign game must not access managed domain, credentials or transport')

        self.svc.o.managedGame().gameNexusName = forbidden
        self.updater._choose_mode = forbidden
        self.updater._key = forbidden
        self.updater._http_get = forbidden
        expected = updates.i18n.t('upd.noDomain', game='ForeignGame')
        self.assertEqual(self.updater._fetch('ForeignGame', 7, 30), (None, expected))
        self.assertEqual(self.updater._api_files('ForeignGame', 7, 30), (None, expected))
        self.assertEqual(calls, [])

    def test_managed_game_and_case_insensitive_configured_override_remain_usable(self):
        self.svc.cfg.values['nexusDomains'] = {'ForeignGame': 'foreignsection'}
        self.svc.o.managedGame().nexus_name = 'managedsection'
        self.assertEqual(self.updater.nexus_domain('skyrimvr'), 'managedsection')
        self.assertEqual(self.updater.nexus_domain('FOREIGNGAME'), 'foreignsection')
        self.assertEqual(self.updater.nexus_domain(''), 'managedsection')

    def test_downloads_require_archive_and_match_game_after_ids(self):
        self.download('Missing.7z', present=False)
        self.download('Directory.7z', present=False)
        os.mkdir(self.fx.path('downloads', 'Directory.7z'))
        self.download('Foreign.7z', game='Skyrim')
        got, _root = self.updater._downloads_meta()
        own = got[self.updater._page_key('SkyrimVR', 101)]
        self.assertEqual(own, {1001: 'Alpha Mod-101-1-0.7z'})
        self.assertEqual(got[self.updater._page_key('Skyrim', 101)], {1002: 'Foreign.7z'})

    def test_downloads_read_host_paths_on_main_thread(self):
        inside_main = []
        native_downloads = self.fx.organizer.downloadsPath

        def runner(fn, timeout=None):
            inside_main.append(True)
            try:
                return fn()
            finally:
                inside_main.pop()

        def downloads_path():
            self.assertTrue(inside_main)
            return native_downloads()

        self.updater.run_main = runner
        self.fx.organizer.downloadsPath = downloads_path
        self.assertTrue(self.updater._downloads_meta()[0])

    def test_other_game_installation_does_not_satisfy_current_page(self):
        newest_time = fake_mo2.T0 + 30 * fake_mo2.DAY
        newest = self.download('New-101-1-1.7z')
        os.utime(newest, (newest_time, newest_time))
        self.mod_metadata('Beta Mod', modid=101, gamename='Skyrim',
                          installationFile='New-101-1-1.7z')
        targets, _total, pages = self.updater._targets(['Beta Mod'], False, 0, 50)
        self.assertEqual((targets[0]['game'], targets[0]['nexusId']), ('Skyrim', 101))
        self.assertEqual(pages[self.updater._page_key('Skyrim', 101)], ['New-101-1-1.7z'])
        self.updater._fetch = lambda *_args: (self.page_files(), '')
        result = self.updater.updates({'mod': ['Alpha Mod']})
        row = result['mods'][0]
        self.assertEqual(row['verdict'], 'DOWNLOADED-NOT-INSTALLED')
        self.assertEqual(row['installed']['pageTime'], fake_mo2.T0)

    def test_other_game_download_does_not_hide_available_update(self):
        self.download('New-101-1-1.7z', game='Skyrim')
        self.updater._fetch = lambda *_args: (self.page_files(), '')
        row = self.updater.updates({'mod': ['Alpha Mod']})['mods'][0]
        self.assertEqual(row['verdict'], 'UPDATE-AVAILABLE')
        self.assertEqual(row['downloaded'], ['Alpha Mod-101-1-0.7z'])

    def test_successful_pages_are_reused_only_within_one_sweep(self):
        calls, files = [], self.page_files()

        def fetch(game, mod_id, timeout):
            calls.append((game, mod_id))
            return files, ''

        self.updater._fetch = fetch
        result = self.updater.updates({'mod': ['Alpha Mod', 'Alpha Mod']})
        self.assertEqual((result['count'], result['checked'], len(calls)), (2, 2, 1))
        self.assertTrue(all('got' not in record for record in files))
        self.updater.updates({'mod': ['Alpha Mod']})
        self.assertEqual(len(calls), 2)

    def test_failed_pages_are_not_reused(self):
        calls = []

        def fetch(*_args):
            calls.append(True)
            return None, 'synthetic failure'

        self.updater._fetch = fetch
        result = self.updater.updates({'mod': ['Alpha Mod', 'Alpha Mod']})
        self.assertEqual((result['count'], result['checked'], len(calls)), (2, 0, 2))
        self.assertEqual([row['verdict'] for row in result['mods']], ['ERROR', 'ERROR'])

    def test_low_quota_still_stops_before_next_row(self):
        def fetch(*_args):
            self.updater._quota['daily-remaining'] = '3'
            return self.page_files(), ''

        self.updater._fetch = fetch
        result = self.updater.updates({'mod': ['Alpha Mod', 'Alpha Mod']})
        self.assertEqual((result['count'], result['unchecked']), (1, 1))
        self.assertTrue(result['stopped'])

    def test_callbacks_reject_other_game_even_with_matching_token(self):
        slot = {'page': self.updater._page_key('Skyrim', 101)}
        self.updater._pending['upd1'] = slot
        self.assertIsNone(self.updater._take('upd1', 'SkyrimSE', 101))
        self.assertIsNone(self.updater._take('unknown', 'SkyrimSE', 101))
        self.assertIs(self.updater._pending['upd1'], slot)
        self.assertIs(self.updater._take('unknown', 'Skyrim', 101), slot)

    def test_callbacks_reject_ambiguous_fallback_and_accept_alias_token(self):
        page = self.updater._page_key('SkyrimVR', 101)
        self.updater._pending.update(upd1={'page': page}, upd2={'page': page})
        self.assertIsNone(self.updater._take('unknown', 'SkyrimSE', 101))
        self.assertEqual(len(self.updater._pending), 2)
        self.assertIsNotNone(self.updater._take('upd1', 'skyrimspecialedition', 101))

    def test_bridge_reply_keeps_correct_game_waiting(self):
        own = {'page': self.updater._page_key('SkyrimSE', 101),
               'event': threading.Event(), 'files': None}
        foreign = {'page': self.updater._page_key('Skyrim', 101),
                   'event': threading.Event(), 'files': None}
        self.updater._pending.update(own=own, foreign=foreign)
        self.updater._on_files('Skyrim', 101, 'missing-token', [])
        self.assertTrue(foreign['event'].is_set())
        self.assertFalse(own['event'].is_set())
        self.assertIn('own', self.updater._pending)


if __name__ == '__main__':
    unittest.main()
