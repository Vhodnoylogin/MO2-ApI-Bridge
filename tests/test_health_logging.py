# -*- coding: utf-8 -*-
"""Real Qt regressions for bounded cold health reads and Unicode diagnostics.

Uses disposable fake organizer data; never connects to installed MO2.
"""
import importlib
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_mo2
fake_mo2.install()
import common
try:
    from PyQt6.QtCore import QCoreApplication, qInstallMessageHandler
except ImportError:
    print('SKIPPED: standalone PyQt6 is required')
    raise SystemExit(77)

pkg = common.import_package()
services = importlib.import_module(common.NAME + '.services')
runtime = importlib.import_module(common.NAME + '.runtime')
config = importlib.import_module(common.NAME + '.config')
journal = importlib.import_module(common.NAME + '.journal')
app = QCoreApplication.instance() or QCoreApplication([])


class HealthLoggingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runner = runtime.MainThreadRunner()
        self.addCleanup(self.runner.close)
        self.o = fake_mo2.FakeOrganizer(self.temp.name)
        self.svc = services.Services(self.o, self.runner.call, 'docs',
                                     cfg=config.Config({'timeouts': {'ping': 0.08, 'gameName': 1}}))
        self.addCleanup(self.svc.ctx.operations.close)

    def cold_timeout(self, game=None, launched=None):
        self.svc.game_exe = game
        if launched:
            self.svc.launched = launched
        outcomes = []
        start = time.monotonic()
        thread = threading.Thread(target=lambda: outcomes.append(self.svc.ping()))
        thread.start()
        thread.join(0.6)  # No Qt events: the native queue cannot execute.
        self.assertFalse(thread.is_alive(), 'health exceeded its one native budget')
        self.assertLess(time.monotonic() - start, 0.6)
        self.assertFalse(outcomes[0]['ok'])
        return outcomes[0]

    def test_cold_ping_has_only_one_queued_native_job(self):
        with patch.object(self.o.managedGame(), 'binaryName') as binary:
            result = self.cold_timeout()
            self.assertFalse(result['busyKnown'])
            app.processEvents()  # Expired read must not run later and warm the cache.
            binary.assert_not_called()
            self.assertIsNone(self.svc.game_exe)

    def test_warm_degraded_ping_keeps_foreign_busy_record(self):
        result = self.cold_timeout('skyrimvr.exe', {'not-running.exe': {'n': 1, 'mine': False}})
        self.assertTrue(result['busyKnown'])
        self.assertTrue(result['busy']['heldByUnknown'])
        self.assertTrue(result['busy']['viaMO2'])

    def test_cold_success_warms_once_and_reports_matching_game(self):
        with patch.object(self.o.managedGame(), 'binaryName', return_value=os.path.basename(sys.executable)) as binary:
            result = self.svc.ping()
            self.assertTrue(result['ok'])
            self.assertTrue(result['busyKnown'])
            self.assertTrue(result['busy']['isGame'])
            self.assertIn(os.getpid(), result['busy']['pids'])
            self.svc.ping()
            binary.assert_called_once()

    def test_setup_busy_check_keeps_native_lookup(self):
        with patch.object(self.o.managedGame(), 'binaryName', return_value=os.path.basename(sys.executable)) as binary:
            self.assertTrue(self.svc.guard.busy()['isGame'])
            binary.assert_called_once()

    def test_utf8_qt_message_round_trip_and_percent_literals(self):
        messages = []
        previous = qInstallMessageHandler(lambda _level, _context, text: messages.append(text))
        try:
            sink = journal.QtSink()
            logger = journal.Journal([sink])
            for text in ('Проверка: профиль «Игрок» — 100% %s', '日本語 😀', 'plain ASCII'):
                logger.log(journal.INFO, text)
                self.assertEqual(messages[-1], logger.line(journal.INFO, text))
                self.assertTrue(sink.alive)
        finally:
            qInstallMessageHandler(previous)


if __name__ == '__main__':
    unittest.main()
