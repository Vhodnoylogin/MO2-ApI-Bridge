# -*- coding: utf-8 -*-
"""Real Qt source-model removal/confirmation checks over temporary files only."""
from importlib import import_module
import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

modui = import_module(common.NAME + '.modui')
try:
    from PyQt6.QtCore import (QAbstractTableModel, QCoreApplication, QModelIndex,
                             QSortFilterProxyModel, QTimer, Qt)
    from PyQt6.QtWidgets import (QApplication, QDialog, QMainWindow, QMessageBox,
                                QTreeView)
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, 'PyQt6')
class ModUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mo2-modui-')
        self.root = os.path.join(self.temp.name, 'mods')
        self.names = ['First', 'Target', 'Last']
        self.paths = {name: os.path.join(self.root, name) for name in self.names}
        self.mods = {}
        for name, path in self.paths.items():
            os.makedirs(path)
            with open(os.path.join(path, 'payload.bin'), 'wb') as stream:
                stream.write(name.encode('ascii'))
            self.mods[name] = types.SimpleNamespace(
                name=lambda value=name: value, absolutePath=lambda value=path: value,
                isOverwrite=lambda: False, isForeign=lambda: False,
                isSeparator=lambda: False, isBackup=lambda: False)
        self.version = '2.5.2'
        self.columns = 11
        self.role_offset = 0
        self.mode = 'success'
        self.calls = []
        self.dialog_results = []
        self.guard_calls = 0
        self.armed = []
        listing = types.SimpleNamespace(
            allMods=lambda: list(self.names), getMod=lambda name: self.mods.get(name),
            displayName=lambda name: name)
        self.organizer = types.SimpleNamespace(
            modsPath=lambda: self.root, modList=lambda: listing,
            appVersion=lambda: types.SimpleNamespace(canonicalString=lambda: self.version))

        class MainWindow(QMainWindow):
            pass

        class ModListView(QTreeView):
            pass

        class ModListSortProxy(QSortFilterProxyModel):
            pass

        owner = self

        class ModList(QAbstractTableModel):
            def rowCount(self, parent=QModelIndex()):
                return 0 if parent.isValid() else len(owner.names)

            def columnCount(self, parent=QModelIndex()):
                return owner.columns

            def data(self, index, role=Qt.ItemDataRole.DisplayRole):
                if not index.isValid():
                    return None
                if role == 257:
                    return index.row() + owner.role_offset
                if role == Qt.ItemDataRole.DisplayRole:
                    return 'Display label unrelated to canonical folder identity'
                return None

            def removeRows(self, row, count, parent=QModelIndex()):
                owner.calls.append((row, count, parent.isValid()))
                name = owner.names[row]
                title = QCoreApplication.translate('ModList', 'Confirm')
                text = QCoreApplication.translate(
                    'ModList', 'Are you sure you want to remove "%1"?').replace('%1', name)
                if owner.mode in ('wrong-text', 'unowned-wrong-text'):
                    text += ' Different operation'
                dialog = QMessageBox(
                    QMessageBox.Icon.Question, title, text,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    owner.window if owner.mode == 'wrong-text' else None)
                if owner.mode == 'wrong-icon':
                    dialog.setIcon(QMessageBox.Icon.Warning)
                if owner.mode == 'changed-order':
                    QTimer.singleShot(0, lambda: owner.names.reverse())
                if owner.mode == 'cancel':
                    QTimer.singleShot(1, lambda: dialog.button(
                        QMessageBox.StandardButton.No).click())
                if owner.mode == 'finished-reindex':
                    def reindex(result):
                        owner.finished_observed.append(result)
                        owner.names[0], owner.names[1] = owner.names[1], owner.names[0]

                    dialog.finished.connect(reindex)
                # A failed adapter must never hang the standalone suite.
                watchdog = QTimer(dialog)
                watchdog.setSingleShot(True)
                watchdog.timeout.connect(dialog.reject)
                watchdog.start(500)
                if owner.mode == 'unowned-wrong-text':
                    watchdog.start(50)
                result = dialog.exec()
                watchdog.stop()
                owner.dialog_results.append(result)
                dialog.deleteLater()
                if result == QMessageBox.StandardButton.Yes:
                    if owner.mode != 'native-false':
                        # Native removeRows uses the numeric row again after exec,
                        # then removeRowForce re-fetches ModInfo by that row.
                        # Retaining the pre-dialog name here would hide reentrancy.
                        name = owner.names[row]
                        self.beginRemoveRows(QModelIndex(), row, row)
                        owner.names.remove(name)
                        owner.mods.pop(name)
                        self.endRemoveRows()
                        if owner.mode != 'kept-folder':
                            shutil.rmtree(owner.paths[name])
                return owner.mode != 'native-false'

        self.window = MainWindow()
        self.view = ModListView(self.window)
        self.view.setObjectName('modList')
        self.window.setCentralWidget(self.view)
        self.model = ModList(self.view)
        self.group_proxy = QSortFilterProxyModel(self.view)
        self.group_proxy.setSourceModel(self.model)
        self.proxy = ModListSortProxy(self.view)
        self.proxy.setSourceModel(self.group_proxy)
        self.proxy.setFilterRegularExpression('not-a-visible-mod')
        self.view.setModel(self.proxy)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        for dialog in self.armed:
            dialog.reject()
            dialog.deleteLater()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, 0)
        self.temp.cleanup()

    def guard(self):
        self.guard_calls += 1

    def invoke(self, **kwargs):
        name = kwargs.pop('name', 'Target')
        return modui.remove(self.organizer, name, self.paths[name],
                            timeout=kwargs.pop('timeout', 0.2),
                            poll_ms=kwargs.pop('poll_ms', 2),
                            before_confirm=kwargs.pop('before_confirm', self.guard), **kwargs)

    def failure(self, code, **kwargs):
        with self.assertRaises(modui.NativeRemovalError) as caught:
            self.invoke(**kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertTrue(caught.exception.receipt['timerStopped'])
        self.assertEqual(self.app.activeModalWidget(), None)
        return caught.exception.receipt

    def test_exact_prompt_uses_canonical_source_row_through_hidden_proxies(self):
        self.assertEqual(self.proxy.rowCount(), 0)
        result = self.invoke()
        self.assertEqual(self.calls, [(1, 1, False)])
        self.assertTrue(result['invoked'])
        self.assertTrue(result['confirmationAccepted'])
        self.assertTrue(result['nativeReturned'])
        self.assertTrue(result['removed'])
        self.assertTrue(result['timerStopped'])
        self.assertEqual(self.guard_calls, 1)
        for name in ('First', 'Last'):
            with open(os.path.join(self.paths[name], 'payload.bin'), 'rb') as stream:
                self.assertEqual(stream.read(), name.encode('ascii'))

    def test_cancel_is_not_native_boolean_success(self):
        self.mode = 'cancel'
        result = self.failure('confirmation-not-accepted', poll_ms=20)
        self.assertTrue(result['nativeResult'])
        self.assertFalse(result['confirmationAccepted'])
        self.assertTrue(os.path.isdir(self.paths['Target']))

    def test_wrong_text_owned_dialog_is_never_approved(self):
        self.mode = 'wrong-text'
        result = self.failure('unexpected-native-dialog')
        self.assertFalse(result['confirmationAccepted'])
        self.assertNotEqual(self.dialog_results, [QMessageBox.StandardButton.Yes])
        self.assertEqual(self.guard_calls, 0)

    def test_parentless_wrong_text_is_untouched_until_test_watchdog(self):
        self.mode = 'unowned-wrong-text'
        result = self.failure('unexpected-native-dialog')
        self.assertFalse(result['confirmationAccepted'])
        self.assertFalse(result['confirmationCancelled'])

    def test_known_confirmation_wrong_icon_is_cancelled(self):
        self.mode = 'wrong-icon'
        result = self.failure('confirmation-mismatch')
        self.assertTrue(result['confirmationCancelled'])
        self.assertFalse(result['confirmationAccepted'])

    def test_changed_native_order_before_confirmation_refuses(self):
        self.mode = 'changed-order'
        result = self.failure('mod-identity-changed')
        self.assertTrue(result['confirmationCancelled'])
        self.assertTrue(os.path.isdir(self.paths['Target']))

    def test_guard_error_cancels_exact_confirmation(self):
        def changed():
            raise ValueError('changed-state')

        result = self.failure('confirmation-guard-failed', before_confirm=changed)
        self.assertTrue(result['confirmationCancelled'])
        self.assertTrue(os.path.isdir(self.paths['Target']))

    def test_guard_false_refuses(self):
        self.failure('confirmation-refused', before_confirm=lambda: False)

    def test_guard_cannot_replace_target_before_acceptance(self):
        def changed():
            self.mods['Target'].absolutePath = lambda: self.paths['First']

        self.failure('target-mismatch', before_confirm=changed)

    def test_finished_observer_cannot_reindex_before_native_removal_resumes(self):
        self.mode = 'finished-reindex'
        self.finished_observed = []
        result = self.invoke()
        self.assertTrue(result['removed'])
        self.assertEqual(self.finished_observed, [])
        self.assertFalse(os.path.lexists(self.paths['Target']))
        self.assertIsNone(self.organizer.modList().getMod('Target'))
        for name in ('First', 'Last'):
            with open(os.path.join(self.paths[name], 'payload.bin'), 'rb') as stream:
                self.assertEqual(stream.read(), name.encode('ascii'))

    def test_native_true_with_retained_folder_fails(self):
        self.mode = 'kept-folder'
        result = self.failure('removal-not-verified')
        self.assertTrue(result['nativeResult'])
        self.assertTrue(result['modAbsent'])
        self.assertFalse(result['folderAbsent'])

    def test_native_false_does_not_prove_removal(self):
        self.mode = 'native-false'
        result = self.failure('removal-not-verified')
        self.assertFalse(result['nativeResult'])
        self.assertTrue(os.path.isdir(self.paths['Target']))

    def test_version_and_shape_and_index_mismatches_refuse_before_invocation(self):
        self.version = '2.5.3'
        self.failure('unsupported-mod-ui-version')
        self.version, self.columns = '2.5.2.0', 12
        self.failure('mod-model-unavailable')
        self.columns, self.role_offset = 11, 1
        self.failure('mod-identity-changed')
        self.assertEqual(self.calls, [])

    def test_bound_path_and_regular_kind_are_required(self):
        original = self.mods['Target'].absolutePath
        self.mods['Target'].absolutePath = lambda: self.paths['First']
        self.failure('target-mismatch')
        self.mods['Target'].absolutePath = original
        self.mods['Target'].isForeign = lambda: True
        self.failure('unsupported-target-kind')
        self.assertEqual(self.calls, [])

    def test_regular_backup_like_name_is_allowed_by_native_flags(self):
        name = 'Targetbackup2'
        path = os.path.join(self.root, name)
        os.rename(self.paths['Target'], path)
        self.paths[name] = path
        self.names[1] = name
        mod = self.mods.pop('Target')
        mod.name = lambda: name
        mod.absolutePath = lambda: path
        self.mods[name] = mod
        result = self.invoke(name=name)
        self.assertTrue(result['removed'])
        self.assertEqual(self.calls, [(1, 1, False)])

    def test_missing_main_window_and_invalid_options_refuse(self):
        self.window.setEnabled(False)
        self.failure('mod-window-unavailable')
        self.window.setEnabled(True)
        self.failure('invalid-removal-options', timeout=float('nan'))
        self.failure('invalid-removal-options', poll_ms=0)
        self.assertEqual(self.calls, [])

    def test_qt_app_and_main_thread_are_required(self):
        with patch.object(QApplication, 'instance', return_value=None):
            self.failure('qt-thread-required')
        with patch('PyQt6.QtCore.QThread.currentThread', return_value=object()):
            self.failure('qt-thread-required')
        self.assertEqual(self.calls, [])

    def test_preexisting_modal_is_refused_and_left_open(self):
        dialog = QDialog(self.window)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.armed.append(dialog)
        dialog.show()
        self.app.processEvents()
        with self.assertRaises(modui.NativeRemovalError) as caught:
            self.invoke()
        self.assertEqual(caught.exception.code, 'preexisting-modal')
        self.assertTrue(dialog.isVisible())
        self.assertEqual(self.calls, [])

    def test_timeout_cancels_exact_prompt_and_stops_timer(self):
        def delayed_guard():
            import time
            time.sleep(0.02)

        result = self.failure('removal-timeout', timeout=0.01, before_confirm=delayed_guard)
        self.assertTrue(result['confirmationCancelled'])
        self.assertFalse(result['confirmationAccepted'])

    def test_timer_is_disconnected_and_inactive_after_success(self):
        original = QTimer
        timers = []

        class TrackedTimer(original):
            def __init__(self):
                super().__init__()
                timers.append(self)

        with patch('PyQt6.QtCore.QTimer', TrackedTimer):
            self.invoke()
        self.assertEqual(len(timers), 1)
        self.assertFalse(timers[0].isActive())
        self.assertEqual(timers[0].receivers(timers[0].timeout), 0)


if __name__ == '__main__':
    unittest.main()
