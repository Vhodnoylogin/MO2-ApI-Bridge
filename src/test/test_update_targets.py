# -*- coding: utf-8 -*-
"""Update-source identity and sweep reuse over synthetic files; no network or MO2."""
import importlib
import json
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_mo2
fake_mo2.install()
import common

services = importlib.import_module(common.NAME + '.services')
updates = importlib.import_module(common.NAME + '.updates')
base = importlib.import_module(common.NAME + '.base')
try:
    from PyQt6.QtCore import QCoreApplication
except ImportError:
    QCoreApplication = None


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
        self.updater.close()
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
        slot = {'page': self.updater._page_key('Skyrim', 101),
                'deadline': self.updater._clock() + 30, 'event': threading.Event()}
        self.updater._pending['upd1'] = slot
        self.assertIsNone(self.updater._take('upd1', 'SkyrimSE', 101))
        self.assertIsNone(self.updater._take('unknown', 'SkyrimSE', 101))
        self.assertIs(self.updater._pending['upd1'], slot)
        self.assertIsNone(self.updater._take('unknown', 'Skyrim', 101))
        self.assertIs(self.updater._take('upd1', 'Skyrim', 101), slot)

    def test_callbacks_require_exact_token_and_accept_page_alias(self):
        page = self.updater._page_key('SkyrimVR', 101)
        self.updater._pending.update(
            upd1={'page': page, 'deadline': self.updater._clock() + 30,
                  'event': threading.Event()},
            upd2={'page': page, 'deadline': self.updater._clock() + 30,
                  'event': threading.Event()})
        self.assertIsNone(self.updater._take('unknown', 'SkyrimSE', 101))
        self.assertEqual(len(self.updater._pending), 2)
        self.assertIsNotNone(self.updater._take('upd1', 'skyrimspecialedition', 101))

    def test_bridge_reply_keeps_correct_game_waiting(self):
        own = {'page': self.updater._page_key('SkyrimSE', 101),
               'event': threading.Event(), 'files': None,
               'deadline': self.updater._clock() + 30}
        foreign = {'page': self.updater._page_key('Skyrim', 101),
                   'event': threading.Event(), 'files': None,
                   'deadline': self.updater._clock() + 30}
        self.updater._pending.update(own=own, foreign=foreign)
        self.updater._on_files('Skyrim', 101, 'missing-token', [])
        self.assertFalse(foreign['event'].is_set())
        self.updater._on_files('Skyrim', 101, 'foreign', [])
        self.assertTrue(foreign['event'].is_set())
        self.assertFalse(own['event'].is_set())
        self.assertIn('own', self.updater._pending)


class FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def disconnect(self, callback):
        self.callbacks.remove(callback)

    def emit(self, *args):
        for callback in list(self.callbacks):
            callback(*args)


class FakeBridge:
    def __init__(self):
        self.filesAvailable, self.requestFailed = FakeSignal(), FakeSignal()
        self.requests, self.deleted = [], 0
        self.reply = lambda *_args: None

    def requestFiles(self, game, mod_id, token):
        self.requests.append((game, mod_id, token))
        self.reply(game, mod_id, token)

    def deleteLater(self):
        self.deleted += 1


class FakeClock:
    def __init__(self):
        self.now, self.lock = 100.0, threading.Lock()

    def __call__(self):
        with self.lock:
            return self.now

    def advance(self, seconds):
        with self.lock:
            self.now += seconds


class UpdateLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.fx = fake_mo2.Fixture()
        self.svc = services.Services(self.fx.organizer,
                                    run_main=lambda f, timeout=None: f(), docs_path='README.md')
        self.updater = self.svc.updater
        self.svc.cfg.values['updates']['delaySec'] = 0
        self.bridge = FakeBridge()
        self.fx.organizer.createNexusBridge = lambda: self.bridge
        self.clock = FakeClock()
        self.updater._clock = self.clock
        self.updater._wait = lambda seconds: self.clock.advance(seconds)
        self.updater._api_key = 'synthetic-key'
        self.api_calls = []

        def http(url, _headers, _timeout):
            self.assert_unlocked()
            self.api_calls.append((url, self.clock()))
            return 200, {}, '{"files": []}'

        self.updater._http_get = http

    def tearDown(self):
        self.updater.close()
        self.fx.cleanup()

    def assert_unlocked(self):
        self.assertTrue(self.updater._lock.acquire(blocking=False))
        self.updater._lock.release()

    def worker(self, fn):
        result = {}

        def run():
            try:
                result['value'] = fn()
            except Exception as exc:
                result['error'] = exc

        thread = threading.Thread(target=run)
        thread.start()
        self.addCleanup(lambda: thread.join(2))
        return thread, result

    def test_timeout_a_then_b_ignores_late_a_for_same_page(self):
        old_token = []

        def timeout(_game, _mod_id, token):
            old_token.append(token)
            self.clock.advance(2)

        self.bridge.reply = timeout
        self.assertIsNone(self.updater._fetch('SkyrimVR', 101, 1)[0])
        self.assertEqual(self.updater._pending, {})

        def reply(game, mod_id, token):
            self.assert_unlocked()
            self.bridge.filesAvailable.emit(game, mod_id, old_token[0], [])
            self.assertIn(token, self.updater._pending)
            self.bridge.filesAvailable.emit(game, mod_id, token, [])

        self.bridge.reply = reply
        self.assertEqual(self.updater._fetch('SkyrimVR', 101, 1), ([], ''))
        self.assertNotEqual(self.bridge.requests[0][2], self.bridge.requests[1][2])

    def test_foreign_page_cannot_satisfy_matching_token(self):
        def reply(game, mod_id, token):
            self.bridge.filesAvailable.emit('Skyrim', mod_id, token, [])
            self.assertIn(token, self.updater._pending)
            self.bridge.filesAvailable.emit('Skyrim', mod_id, None, [])
            self.assertEqual(self.updater._mode, 'bridge')
            self.bridge.requestFailed.emit('Skyrim', mod_id, token, 'foreign error')
            self.assertIn(token, self.updater._pending)
            self.bridge.filesAvailable.emit(game, mod_id, token, [])

        self.bridge.reply = reply
        self.assertEqual(self.updater._fetch('SkyrimVR', 101, 1), ([], ''))

    def test_missing_userdata_discards_payload_and_refetches_directly(self):
        self.svc.cfg.values['updates']['delaySec'] = 2

        def reply(game, mod_id, _token):
            self.bridge.filesAvailable.emit(game, mod_id, None, [object()])

        self.bridge.reply = reply
        self.assertEqual(self.updater._fetch('SkyrimVR', 101, 1), ([], ''))
        self.assertEqual(self.updater._mode, 'api')
        self.assertEqual(self.api_calls[0][1], 102)
        self.assertEqual(self.updater._pending, {})
        self.updater._fetch('SkyrimVR', 101, 1)
        self.assertEqual(len(self.bridge.requests), 1)

    def test_missing_userdata_after_timeout_does_not_satisfy_new_waiter(self):
        self.bridge.reply = lambda *_args: self.clock.advance(2)
        self.assertIsNone(self.updater._fetch('SkyrimVR', 101, 1)[0])
        self.bridge.reply = lambda game, mod_id, _token: (
            self.bridge.filesAvailable.emit(game, mod_id, '', [object()]))
        self.assertEqual(self.updater._fetch('SkyrimVR', 101, 1), ([], ''))
        self.assertEqual(len(self.api_calls), 1)

    def test_missing_userdata_in_failure_callback_refetches_directly(self):
        self.bridge.reply = lambda game, mod_id, _token: (
            self.bridge.requestFailed.emit(game, mod_id, 0, None, 'uncorrelated failure'))
        self.assertEqual(self.updater._fetch('SkyrimVR', 101, 1), ([], ''))
        self.assertEqual(self.updater._mode, 'api')
        self.assertEqual(len(self.api_calls), 1)

    def test_parallel_same_page_waiters_are_correlated_independently(self):
        entered = threading.Event()
        self.bridge.reply = lambda *_args: entered.set()
        first, result = self.worker(lambda: self.updater._fetch('SkyrimVR', 101, 30))
        self.assertTrue(entered.wait(2))

        def second_reply(game, mod_id, token):
            first_token = self.bridge.requests[0][2]
            self.bridge.filesAvailable.emit(game, mod_id, token, [])
            self.assertIn(first_token, self.updater._pending)
            self.bridge.requestFailed.emit(game, mod_id, first_token, 'first request failed')

        self.bridge.reply = second_reply
        self.assertEqual(self.updater._fetch('SkyrimVR', 101, 1), ([], ''))
        first.join(2)
        self.assertFalse(first.is_alive())
        self.assertEqual(result['value'], (None, 'first request failed'))

    def test_parallel_pacing_uses_one_monotonic_gate(self):
        self.updater._mode = 'api'
        self.svc.cfg.values['updates']['delaySec'] = 2
        self.updater._next_request = 102
        barrier, wait_lock = threading.Barrier(2), threading.Lock()
        count = [0]

        def wait(seconds):
            self.assert_unlocked()
            target = self.clock() + seconds
            with wait_lock:
                count[0] += 1
                first_wait = count[0] <= 2
            if first_wait:
                barrier.wait(timeout=2)
            with self.clock.lock:
                self.clock.now = max(self.clock.now, target)

        self.updater._wait = wait
        first, a = self.worker(lambda: self.updater._fetch('SkyrimVR', 101, 1))
        second, b = self.worker(lambda: self.updater._fetch('SkyrimVR', 102, 1))
        first.join(3)
        second.join(3)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertNotIn('error', a)
        self.assertNotIn('error', b)
        self.assertEqual(sorted(t for _url, t in self.api_calls), [102, 104])

    def test_known_quota_is_reserved_before_parallel_admission(self):
        self.updater._quota['daily-remaining'] = '51'
        self.updater._admit()
        self.assertEqual(self.updater._quota['daily-remaining'], '51')
        self.assertEqual(self.updater._quota_budget, 50)
        with self.assertRaises(updates.SweepStop):
            self.updater._admit()
        self.updater._absorb_quota({'X-RL-Daily-Remaining': '52'})
        self.assertEqual(self.updater._quota['daily-remaining'], '52')
        self.assertTrue(self.updater.quota_exhausted())

    def test_quota_preserves_raw_latest_headers_without_restoring_reserved_budget(self):
        self.updater._absorb_quota({'X-RL-Daily-Remaining': '2400'})
        self.updater._admit()
        self.updater._admit()
        self.updater._absorb_quota({'X-RL-Daily-Remaining': '2400'})
        self.assertEqual(self.updater._quota['daily-remaining'], '2400')
        self.assertEqual(self.updater._quota_budget, 2398)
        self.updater._absorb_quota({'X-RL-Daily-Remaining': '2397'})
        self.assertEqual(self.updater._quota['daily-remaining'], '2397')
        self.assertEqual(self.updater._quota_budget, 2397)

    def test_close_retires_waiters_disconnects_on_qt_and_ignores_saved_callback(self):
        entered, on_qt = threading.Event(), []

        def run_main(fn, timeout=None):
            on_qt.append(True)
            try:
                self.assert_unlocked()
                return fn()
            finally:
                on_qt.pop()

        self.updater.run_main = run_main
        original_delete = self.bridge.deleteLater

        def delete():
            self.assertTrue(on_qt)
            original_delete()

        self.bridge.deleteLater = delete
        self.bridge.reply = lambda *_args: entered.set()
        thread, result = self.worker(lambda: self.updater._fetch('SkyrimVR', 101, 30))
        self.assertTrue(entered.wait(2))
        callback = self.bridge.filesAvailable.callbacks[0]
        token = self.bridge.requests[0][2]
        self.updater.close()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(result.get('error'), updates.SweepStop)
        self.assertEqual(result['error'].key, 'upd.closed')
        self.assertEqual((self.updater._pending, self.bridge.deleted), ({}, 1))
        self.assertEqual(self.bridge.filesAvailable.callbacks, [])
        self.assertEqual(self.bridge.requestFailed.callbacks, [])
        callback('SkyrimVR', 101, token, [object()])
        self.updater.close()
        self.assertEqual(self.bridge.deleted, 1)
        with self.assertRaises(updates.SweepStop):
            self.updater._fetch('SkyrimVR', 101, 1)

    def test_close_interrupts_pacing_without_issuing_request(self):
        self.updater._mode = 'api'
        self.updater._next_request = self.clock() + 60
        waiting = threading.Event()

        def wait(seconds):
            waiting.set()
            return self.updater._closed.wait(seconds)

        self.updater._wait = wait
        thread, result = self.worker(lambda: self.updater._fetch('SkyrimVR', 101, 1))
        self.assertTrue(waiting.wait(2))
        self.updater.close()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(result.get('error'), updates.SweepStop)
        self.assertEqual(self.api_calls, [])

    def test_close_before_queued_native_dispatch_prevents_request(self):
        self.updater._choose_mode(1)
        once = [False]

        def run_main(fn, timeout=None):
            if not once[0]:
                once[0] = True
                self.updater.close()
            return fn()

        self.updater.run_main = run_main
        with self.assertRaises(updates.SweepStop) as failure:
            self.updater._fetch('SkyrimVR', 101, 1)
        self.assertEqual(failure.exception.key, 'upd.closed')
        self.assertEqual(self.bridge.requests, [])

    def test_native_dispatch_after_deadline_does_not_issue_request(self):
        self.updater._choose_mode(1)

        def run_main(fn, timeout=None):
            self.clock.advance(2)
            return fn()

        self.updater.run_main = run_main
        self.assertIsNone(self.updater._fetch('SkyrimVR', 101, 1)[0])
        self.assertEqual(self.bridge.requests, [])

    def test_deferred_native_deadline_does_not_spend_quota_or_issue(self):
        self.updater._next_request = self.clock() + 2
        self.updater._quota['daily-remaining'] = '100'
        self.assertIsNone(self.updater._fetch('SkyrimVR', 101, 1)[0])
        self.assertEqual(self.bridge.requests, [])
        self.assertIsNone(self.updater._quota_budget)
        self.assertEqual(self.updater._pending, {})

    def test_close_interrupts_deferred_native_dispatch_without_spending_quota(self):
        self.updater._next_request = self.clock() + 60
        self.updater._quota['daily-remaining'] = '100'
        waiting = threading.Event()

        def wait(seconds):
            waiting.set()
            return self.updater._closed.wait(seconds)

        self.updater._wait = wait
        thread, result = self.worker(lambda: self.updater._fetch('SkyrimVR', 101, 30))
        self.assertTrue(waiting.wait(2))
        self.updater.close()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(result.get('error'), updates.SweepStop)
        self.assertEqual(self.bridge.requests, [])
        self.assertIsNone(self.updater._quota_budget)

    def test_native_dispatch_quota_rejection_stops_sweep_and_retires_slot(self):
        self.updater._quota['daily-remaining'] = '50'
        with self.assertRaises(updates.SweepStop) as failure:
            self.updater._fetch('SkyrimVR', 101, 1)
        self.assertEqual(failure.exception.key, 'upd.quotaLow')
        self.assertEqual(self.bridge.requests, [])
        self.assertEqual(self.updater._pending, {})

    def test_close_while_native_payload_is_converted_prevents_publication(self):
        updater = self.updater

        class NativeFile:
            @property
            def name(self):
                updater.close()
                return 'late native payload'

        self.bridge.reply = lambda game, mod_id, token: (
            self.bridge.filesAvailable.emit(game, mod_id, token, [NativeFile()]))
        with self.assertRaises(updates.SweepStop) as failure:
            self.updater._fetch('SkyrimVR', 101, 1)
        self.assertEqual(failure.exception.key, 'upd.closed')
        self.assertEqual(self.updater._pending, {})

    def test_close_during_direct_request_discards_late_response(self):
        self.updater._mode = 'api'
        entered, release = threading.Event(), threading.Event()

        def http(*_args):
            entered.set()
            self.assertTrue(release.wait(2))
            return 200, {'X-RL-Daily-Remaining': '100'}, '{"files": []}'

        self.updater._http_get = http
        thread, result = self.worker(lambda: self.updater._fetch('SkyrimVR', 101, 1))
        self.assertTrue(entered.wait(2))
        self.updater.close()
        release.set()
        thread.join(2)
        self.assertIsInstance(result.get('error'), updates.SweepStop)
        self.assertEqual(self.updater._quota, {})

    def test_subscription_failure_deletes_partial_native_bridge_and_uses_api(self):
        def refuse(_callback):
            raise TypeError('synthetic unbound native payload')

        self.bridge.requestFailed.connect = refuse
        self.assertEqual(self.updater._fetch('SkyrimVR', 101, 1), ([], ''))
        self.assertEqual(self.updater._mode, 'api')
        self.assertEqual(self.bridge.deleted, 1)
        self.assertEqual(self.bridge.filesAvailable.callbacks, [])


@unittest.skipIf(QCoreApplication is None, 'standalone PyQt6 is unavailable')
class UpdateQtDispatchTests(unittest.TestCase):
    def test_stalled_qt_queue_defers_native_dispatch_without_blocking_or_extra_quota(self):
        runtime = importlib.import_module(common.NAME + '.runtime')
        app = QCoreApplication.instance() or QCoreApplication([])
        fx = fake_mo2.Fixture()
        runner, bridge = runtime.MainThreadRunner(), FakeBridge()
        fx.organizer.createNexusBridge = lambda: bridge
        svc = services.Services(fx.organizer, run_main=runner.call, docs_path='README.md')
        updater = svc.updater
        delay = 0.1
        svc.cfg.values['updates']['delaySec'] = delay
        updater._quota['daily-remaining'] = '100'
        updater._choose_mode(1)
        dispatches, results, workers = [], [], []

        def reply(game, mod_id, token):
            self.assertTrue(updater._lock.acquire(blocking=False))
            updater._lock.release()
            dispatches.append(time.monotonic())
            bridge.filesAvailable.emit(game, mod_id, token, [])

        bridge.reply = reply

        def fetch(mod_id):
            try:
                results.append(updater._fetch('SkyrimVR', mod_id, 2))
            except Exception as exc:
                results.append(exc)

        try:
            for mod_id in (101, 102):
                worker = threading.Thread(target=fetch, args=(mod_id,))
                workers.append(worker)
                worker.start()
            # Simulate a real GUI stall: both worker jobs queue, but Qt dispatch
            # does not run until more than one pacing interval has elapsed.
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                with runner.lock:
                    queued = len(runner.jobs)
                if queued >= 2:
                    break
                time.sleep(0.002)
            self.assertEqual(queued, 2)
            with updater._lock:
                tokens = set(updater._pending)
            time.sleep(delay * 2.2)
            self.assertEqual(bridge.requests, [])
            self.assertIsNone(updater._quota_budget)

            app.processEvents()
            # Both queued callbacks have returned: one issued and the other
            # deferred to its worker. Qt remains free during the pacing wait.
            self.assertEqual(len(dispatches), 1)
            self.assertEqual(updater._quota_budget, 99)
            deadline = time.monotonic() + 2
            while any(worker.is_alive() for worker in workers) and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.002)
            self.assertFalse(any(worker.is_alive() for worker in workers))
            self.assertEqual(results, [([], ''), ([], '')])
            self.assertEqual(len(dispatches), 2)
            self.assertGreaterEqual(dispatches[1] - dispatches[0], delay - 0.005)
            self.assertEqual({token for _game, _mod_id, token in bridge.requests}, tokens)
            self.assertEqual(updater._quota_budget, 98)
            self.assertEqual(updater._quota['daily-remaining'], '100')
        finally:
            updater.close()
            runner.close()
            for worker in workers:
                worker.join(2)
            app.processEvents()
            fx.cleanup()


if __name__ == '__main__':
    unittest.main()
