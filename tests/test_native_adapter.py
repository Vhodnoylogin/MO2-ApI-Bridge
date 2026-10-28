# -*- coding: utf-8 -*-
"""Offline native-installer contract checks; no manager or game is accessed.

The model below retains archive indices when copying, like ArchiveFileTree. The
tests exercise the one-use claim boundary, cancellation semantics and the exact
native tree identity. Optional real Qt checks exercise nested-dialog cancellation.
"""
import copy
from enum import IntEnum
from importlib import import_module
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

native = import_module(common.NAME + '.nativeinstall')


class Result(IntEnum):
    SUCCESS = 0
    FAILED = 1
    CANCELED = 2
    NOTATTEMPTED = 4


MOB = types.SimpleNamespace(
    IPluginInstallerSimple=object, InstallResult=Result,
    GuessQuality=types.SimpleNamespace(USER=5), VersionInfo=lambda *v: v)


class Entry:
    def __init__(self, index, file=True):
        self.index, self.file = index, file

    def isFile(self):
        return self.file


class Tree:
    def __init__(self, files=None):
        self.files = dict(files or {})

    def find(self, path):
        return self.files.get(path)

    def createOrphanTree(self):
        return type(self)()

    def copy(self, entry, destination):
        if destination in self.files:
            raise ValueError(destination)
        self.files[destination] = copy.copy(entry)
        return self.files[destination]

    def clear(self):
        self.files.clear()

    def merge(self, other):
        self.files.update(other.files)


class Name:
    def __init__(self, value='suggestion'):
        self.value = value

    def update(self, value, quality):
        self.value = value

    def __str__(self):
        return self.value


class Organizer:
    def __init__(self, root):
        self.root, self.present, self.enabled = root, False, True

    def modsPath(self):
        return os.path.join(self.root, 'mods')

    def downloadsPath(self):
        return os.path.join(self.root, 'downloads')

    def profile(self):
        return types.SimpleNamespace(absolutePath=lambda: os.path.join(self.root, 'profiles', 'p'))

    def modList(self):
        return types.SimpleNamespace(getMod=lambda name: object() if self.present else None)

    def isPluginEnabled(self, name):
        return self.enabled


class NativeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.organizer = Organizer(self.temp.name)
        self.controller = native._Controller(object(), self.organizer)
        self.arc = os.path.join(self.organizer.downloadsPath(), 'original.7z')
        self.old = native._controller
        self.mobase = patch.dict(sys.modules, {'mobase': MOB})
        self.mobase.start()

    def tearDown(self):
        native._controller = self.old
        self.mobase.stop()
        self.temp.cleanup()

    def plan(self, mappings=None):
        self.controller.plan = {
            'archive': native._canonical(self.arc), 'name': 'Chosen',
            'mappings': mappings or [{'source': 'option/a.esp', 'destination': 'a.esp'}],
            'metadata': {}, 'bound': False, 'consumed': False, 'error': None,
            'receipt': {'installerInvoked': False, 'selectionPrepared': False}}
        return self.controller.plan

    def tree(self):
        return Tree({'option/a.esp': Entry(17), 'other/b.esp': Entry(22),
                     'directory': Entry(-1, file=False)})

    def test_inactive_companion_never_claims_manual_installs(self):
        self.assertFalse(self.controller.supports())
        self.controller.start(self.arc, False, None)
        self.assertFalse(self.controller.supports())

    def test_exact_archive_and_new_install_context_required(self):
        for archive, reinstall, current in ((self.arc + '.other', False, None),
                                            (self.arc, True, None),
                                            (self.arc, False, object())):
            with self.subTest(archive=archive, reinstall=reinstall):
                self.plan()
                self.controller.start(archive, reinstall, current)
                self.assertFalse(self.controller.supports())

    def test_copies_native_indices_and_returns_original_tree(self):
        plan, original = self.plan(), self.tree()
        self.controller.start(self.arc, False, None)
        self.assertTrue(self.controller.supports())
        name = Name()
        result = self.controller.select(name, original, '1.2', 45)
        self.assertEqual(result[0], Result.SUCCESS)
        self.assertIs(result[1], original)
        self.assertEqual(result[2:], ('1.2', 45))
        self.assertEqual(name.value, 'Chosen')
        self.assertEqual(set(original.files), {'a.esp'})
        self.assertEqual(original.files['a.esp'].index, 17)
        self.assertTrue(plan['receipt']['selectionPrepared'])

    def test_selection_cannot_be_consumed_twice(self):
        self.plan()
        self.controller.start(self.arc, False, None)
        self.assertEqual(self.controller.select(Name(), self.tree(), '', 0)[0], Result.SUCCESS)
        self.assertEqual(self.controller.select(Name(), self.tree(), '', 0), Result.CANCELED)

    def test_missing_and_directory_members_cancel_before_tree_mutation(self):
        for source in ('absent.esp', 'directory'):
            with self.subTest(source=source):
                plan = self.plan([{'source': source, 'destination': 'a.esp'}])
                original = self.tree()
                before = dict(original.files)
                self.controller.start(self.arc, False, None)
                self.assertEqual(self.controller.select(Name(), original, '', 0), Result.CANCELED)
                self.assertEqual(original.files, before)
                self.assertEqual(plan['error'], 'source-not-file')

    def test_target_appearing_after_preflight_cancels(self):
        plan = self.plan()
        self.controller.start(self.arc, False, None)
        self.organizer.present = True
        self.assertEqual(self.controller.select(Name(), self.tree(), '', 0), Result.CANCELED)
        self.assertEqual(plan['error'], 'target-exists')

    def test_modal_failure_cancels_instead_of_falling_through(self):
        plan = self.plan()
        self.controller.start(self.arc, False, None)
        plan['error'] = 'unexpected-native-dialog'
        original = self.tree()
        self.assertEqual(self.controller.select(Name(), original, '', 0), Result.CANCELED)
        self.assertIn('other/b.esp', original.files)

    def test_name_filter_changes_are_refused(self):
        self.plan()
        self.controller.start(self.arc, False, None)
        name = Name('different')
        name.update = lambda value, quality: None
        self.assertEqual(self.controller.select(name, self.tree(), '', 0), Result.CANCELED)
        self.assertEqual(self.controller.plan['error'], 'native-name-changed')

    def test_proxy_identity_matching_uses_instance_paths(self):
        native._controller = self.controller
        other_proxy = Organizer(self.temp.name)
        self.assertTrue(native.check_available(other_proxy))
        other_proxy.enabled = False
        with self.assertRaises(native.NativeInstallError) as error:
            native.check_available(other_proxy)
        self.assertEqual(error.exception.code, 'companion-disabled')
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(native.NativeInstallError) as error:
                native.check_available(Organizer(root))
            self.assertEqual(error.exception.code, 'companion-unavailable')

    def test_registered_companion_factory_is_lazy_and_inactive(self):
        companion = native.create_installer()
        self.assertTrue(companion.init(self.organizer))
        self.assertEqual(companion.name(), native.INSTALLER_NAME)
        self.assertEqual(companion.priority(), 0xffffffff)
        self.assertFalse(companion.isManualInstaller())
        self.assertFalse(companion.isArchiveSupported(self.tree()))

    def test_destination_escape_reserved_and_case_duplicates_rejected(self):
        for destination in ('../x', 'C:/x', '/x', 'x:stream', 'meta.ini',
                            'fomod/x', 'INI Tweaks/x', 'a//b'):
            with self.subTest(destination=destination):
                with self.assertRaises(native.NativeInstallError):
                    native._mappings([{'source': 'option/a.esp', 'destination': destination}])
        with self.assertRaises(native.NativeInstallError):
            native._mappings([{'source': 'a', 'destination': 'x.esp'},
                              {'source': 'b', 'destination': 'X.esp'}])


try:
    from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, QTimer, Qt
    from PyQt6.QtWidgets import (QApplication, QCheckBox, QDialog, QMainWindow,
                                QProgressDialog, QPushButton, QTreeView)
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, 'PyQt6')
class NativeModalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def test_prompt_is_rejected_and_native_plan_cancelled(self):
        plan = {'error': None, 'receipt': {'cancelledDialogs': []}}
        timer = native._guard_modals(self.app, plan, 1)
        dialog = QDialog()
        # The bounded watchdog prevents a regression from hanging this test.
        QTimer.singleShot(250, dialog.reject)
        try:
            dialog.exec()
            self.assertEqual(plan['error'], 'unexpected-native-dialog')
            self.assertEqual(plan['receipt']['cancelledDialogs'], ['QDialog'])
        finally:
            timer.stop()
            timer.deleteLater()
            dialog.deleteLater()

    def test_progress_is_allowed(self):
        plan = {'error': None, 'receipt': {'cancelledDialogs': []}}
        timer = native._guard_modals(self.app, plan, 1)
        dialog = QProgressDialog()
        QTimer.singleShot(30, dialog.accept)
        try:
            dialog.exec()
            self.assertIsNone(plan['error'])
            self.assertEqual(plan['receipt']['cancelledDialogs'], [])
        finally:
            timer.stop()
            timer.deleteLater()
            dialog.deleteLater()


@unittest.skipIf(QApplication is None, 'PyQt6')
class NativeDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        class MainWindow(QMainWindow):
            pass

        class DownloadListView(QTreeView):
            pass

        class DownloadList(QAbstractTableModel):
            def __init__(self, parent):
                super().__init__(parent)
                self.status = 'Downloaded'

            def rowCount(self, parent=QModelIndex()):
                return 2

            def columnCount(self, parent=QModelIndex()):
                return 8

            def data(self, index, role=Qt.ItemDataRole.DisplayRole):
                if role == Qt.ItemDataRole.DisplayRole:
                    if index.column() == 0:
                        # Deliberately unrelated metadata display names.
                        return 'Release metadata label'
                    if index.column() == 1:
                        return self.status
                return None

        self.temp = tempfile.TemporaryDirectory()
        self.organizer = Organizer(self.temp.name)
        os.makedirs(self.organizer.downloadsPath())
        self.archive = os.path.join(self.organizer.downloadsPath(), 'Original.7z')
        with open(self.archive, 'wb') as stream:
            stream.write(b'archive')
        self.organizer.appVersion = lambda: types.SimpleNamespace(canonicalString=lambda: '2.5.2')

        def download_path(row):
            if row != 0:
                raise IndexError(row)  # Native pending request has no file path.
            return self.archive

        self.organizer.downloadManager = lambda: types.SimpleNamespace(downloadPath=download_path)
        self.window = MainWindow()
        self.button = QPushButton(self.window)
        self.button.setObjectName('btnRefreshDownloads')
        self.box = QCheckBox(self.window)
        self.box.setObjectName('showHiddenBox')
        self.view = DownloadListView(self.window)
        self.view.setObjectName('downloadView')
        self.model = DownloadList(self.view)
        self.proxy = QSortFilterProxyModel(self.view)
        self.proxy.setSourceModel(self.model)
        # Hide every visible row to prove lookup uses the raw native source model.
        self.proxy.setFilterRegularExpression('never-matches')
        self.view.setModel(self.proxy)
        self.refresh_count = 0

        def refresh():
            self.refresh_count += 1

        self.button.clicked.connect(refresh)
        plugin = types.SimpleNamespace(_parentWidget=lambda: self.window)
        self.old = native._controller
        native._controller = native._Controller(plugin, self.organizer)

    def tearDown(self):
        native._controller = self.old
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_exact_path_identity_ignores_display_name_and_proxy_filter(self):
        self.model.status = 'Installed'
        result = native.refresh_downloads(self.organizer, self.archive)
        self.assertTrue(result['verified'])
        self.assertTrue(result['installed'])
        self.assertEqual(result['sourceRow'], 0)
        self.assertEqual(self.refresh_count, 1)
        self.assertFalse(self.box.isChecked())

    def test_preinstall_refresh_requires_completed_exact_download(self):
        result = native.refresh_downloads(self.organizer, self.archive, require_installed=False)
        self.assertTrue(result['found'])
        self.assertTrue(result['verified'])
        self.assertFalse(result['installed'])
        with self.assertRaises(native.NativeInstallError) as error:
            native.refresh_downloads(self.organizer, self.archive)
        self.assertEqual(error.exception.code, 'download-not-installed')
        self.assertTrue(error.exception.receipt['found'])

    def test_incomplete_download_and_wrong_path_are_refused(self):
        self.model.status = 'Downloading'
        with self.assertRaises(native.NativeInstallError) as error:
            native.refresh_downloads(self.organizer, self.archive, require_installed=False)
        self.assertEqual(error.exception.code, 'download-not-complete')
        wrong = self.archive + '.different'
        self.organizer.downloadManager = lambda: types.SimpleNamespace(downloadPath=lambda row: wrong)
        with self.assertRaises(native.NativeInstallError) as error:
            native.refresh_downloads(self.organizer, self.archive, require_installed=False)
        self.assertEqual(error.exception.code, 'download-row-not-unique')

    def test_unknown_version_or_missing_native_controls_refuse_before_click(self):
        self.organizer.appVersion = lambda: types.SimpleNamespace(canonicalString=lambda: '2.6.0')
        with self.assertRaises(native.NativeInstallError) as error:
            native.refresh_downloads(self.organizer, self.archive, require_installed=False)
        self.assertEqual(error.exception.code, 'unsupported-download-ui-version')
        self.assertEqual(self.refresh_count, 0)
        self.organizer.appVersion = lambda: types.SimpleNamespace(canonicalString=lambda: '2.5.2')
        self.button.setObjectName('different')
        with self.assertRaises(native.NativeInstallError) as error:
            native.refresh_downloads(self.organizer, self.archive, require_installed=False)
        self.assertEqual(error.exception.code, 'download-controls-unavailable')
        self.assertEqual(self.refresh_count, 0)


if __name__ == '__main__':
    unittest.main()
