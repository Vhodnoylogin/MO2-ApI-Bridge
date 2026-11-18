# -*- coding: utf-8 -*-
"""Optional native Qt regression checks, run with standalone PyQt6 installed.

Real nested Qt event loops model MO2 2.5's ProfilesDialog and QInputDialog.
They catch hangs, late callbacks, unwanted profile switches and cancelled input.
No installed MO2 or game is accessed. QT_QPA_PLATFORM=offscreen keeps tests invisible.
"""
import os
import shutil
import sys
import tempfile
import time
import weakref

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
try:
    from PyQt6 import sip
    from PyQt6.QtCore import QTimer, Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import (QApplication, QComboBox, QDialog, QInputDialog,
                                QListWidget, QPushButton, QVBoxLayout, QWidget)
except ImportError:
    print(common.T('profileui.skipped'))
    raise SystemExit(77)

from importlib import import_module
profileui = import_module(common.NAME + '.profileui')
r = common.Report(common.T('profileui.title'))
app = QApplication.instance() or QApplication([])
app.setQuitOnLastWindowClosed(False)


class PendingProfileWriter:
    """A native-shaped pending writer owned solely by a list item's QVariant.

    MO2 stores flags/priority by global index and resolves names when writing.
    createMod can leave A appended until refresh sorts the global collection.
    Releasing this object after that refresh would turn Overwrite's flag into +B.
    No timer is needed: destruction flushes pending work just as Profile does.
    """
    def __init__(self, window, profile):
        self.window = weakref.ref(window)
        self.profile = profile
        self.flags = [profile == 'Claude', True, profile == 'Second', False]
        self.expected = self.rows(window.index_names)
        window.writers_created += 1

    def rows(self, names):
        return [('+' if self.flags[index] else '-') + names[index]
                for index in (1, 3, 2, 0) if names[index] != 'Overwrite']

    def __del__(self):
        window = self.window()
        if window is not None:
            window.writes.append((self.profile, self.expected, self.rows(window.index_names)))


class ProfilesDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.setObjectName('ProfilesDialog')
        self.window_owner = window
        self.selected = None
        layout = QVBoxLayout(self)
        self.listing = QListWidget(self)
        self.listing.setObjectName('profilesList')
        self.listing.addItems(['Claude', 'Second'])
        self.listing.blockSignals(window.mode == 'blockedListing')
        for index in range(self.listing.count()):
            item = self.listing.item(index)
            item.setData(Qt.ItemDataRole.UserRole, PendingProfileWriter(window, item.text()))
        layout.addWidget(self.listing)
        if window.mode != 'missingControls':
            self.button = QPushButton(self)
            self.button.setObjectName('renameButton')
            self.button.clicked.connect(self.rename_selected)
            layout.addWidget(self.button)
            self.copy_button = QPushButton(self)
            self.copy_button.setObjectName('copyProfileButton')
            self.copy_button.clicked.connect(self.copy_selected)
            layout.addWidget(self.copy_button)
            self.select_button = QPushButton(self)
            self.select_button.setObjectName('select')
            self.select_button.clicked.connect(self.select_profile)
            layout.addWidget(self.select_button)

    def copy_selected(self):
        old = self.listing.currentItem().text()
        if self.window_owner.mode == 'cancel':
            QTimer.singleShot(0, lambda: app.activeModalWidget().reject())
        new, ok = QInputDialog.getText(self, '', '', text=old)
        if ok:
            # Native copy retains its Profile::Ptr while copying.
            profile = self.listing.currentItem().data(Qt.ItemDataRole.UserRole)
            try:
                shutil.copytree(os.path.join(self.window_owner.root, old),
                                os.path.join(self.window_owner.root, new))
                self.listing.addItem(new)
                self.listing.item(self.listing.count() - 1).setData(
                    Qt.ItemDataRole.UserRole, PendingProfileWriter(self.window_owner, new))
            finally:
                del profile
            if self.window_owner.mode == 'closeAfterInput':
                QTimer.singleShot(0, self.user_close)

    def user_close(self, escape=False):
        # Exercise closure while items are still owned by the native dialog,
        # before the next automation poll can release them itself.
        self.window_owner.rows_at_user_close = self.listing.count()
        if escape:
            QTest.keyClick(self, Qt.Key.Key_Escape)
        else:
            self.close()

    def select_profile(self):
        self.selected = self.listing.currentItem().text()
        self.accept()

    def rename_selected(self):
        old = self.listing.currentItem().text()
        # Native rename retains its Profile::Ptr across the nested input loop.
        profile = self.listing.currentItem().data(Qt.ItemDataRole.UserRole)
        try:
            if self.window_owner.mode == 'unexpectedChild':
                QDialog(self).exec()
                return
            if self.window_owner.mode == 'cancel':
                QTimer.singleShot(0, lambda: app.activeModalWidget().reject())
            new, ok = QInputDialog.getText(self, '', '', text=old)
            if ok:
                os.rename(os.path.join(self.window_owner.root, old),
                          os.path.join(self.window_owner.root, new))
                self.listing.currentItem().setText(new)
        finally:
            # C++ locals release on slot return even if a Python callback's
            # saved exception traceback retains the completed Python frame.
            del profile
        if ok and self.window_owner.mode == 'closeAfterInput':
            QTimer.singleShot(0, self.user_close)


class MainWindow(QWidget):
    def __init__(self, root, mode='success'):
        super().__init__()
        self.root, self.mode = root, mode
        self.index_names = ['B', 'Overwrite', 'Z', 'A']
        self.manage_count = self.writers_created = 0
        self.writes = []
        self.rows_before_refresh = None
        self.writes_before_refresh = None
        self.blocked_after_release = None
        self.rows_at_user_close = None
        layout = QVBoxLayout(self)
        self.box = QComboBox(self)
        self.box.setObjectName('profileBox')
        self.box.addItems(['<Manage...>', 'Claude', 'Second'])
        self.box.setCurrentIndex(1)
        if mode == 'duplicateProfile':
            self.box.addItem('Second')
        if mode == 'disabledBox':
            self.box.setEnabled(False)
        self.box.currentIndexChanged.connect(self.changed)
        layout.addWidget(self.box)
        self.show()
        app.processEvents()

    def changed(self, index):
        if index != 0:
            self.index_names = ['A', 'B', 'Overwrite', 'Z']
            return
        self.manage_count += 1
        dialog = ProfilesDialog(self)
        try:
            if self.mode == 'earlyEscape':
                QTimer.singleShot(0, lambda: dialog.user_close(escape=True))
            dialog.exec()
            self.rows_before_refresh = dialog.listing.count()
            self.writes_before_refresh = len(self.writes)
            self.blocked_after_release = dialog.listing.signalsBlocked()
            self.box.blockSignals(True)
            self.box.clear()
            self.box.addItems(['<Manage...>'] + sorted(os.listdir(self.root)))
            self.box.setCurrentText(dialog.selected or 'Claude')
            self.box.blockSignals(False)
            # MainWindow recursively activates the selected profile before the
            # outer ProfilesDialog is destroyed, including when it was rejected.
            self.index_names = ['A', 'B', 'Overwrite', 'Z']
        finally:
            sip.delete(dialog)


def exercise(mode, old='Second', action='rename'):
    with tempfile.TemporaryDirectory(prefix='mo2-profileui-') as root:
        for name in ('Claude', 'Second'):
            os.makedirs(os.path.join(root, name))
        sample = os.path.join(root, 'Second', 'save.ess')
        with open(sample, 'wb') as f:
            f.write(b'SAVE\x00\xff')
        window = MainWindow(root, mode)
        error = None
        started = time.monotonic()
        try:
            if action == 'select':
                profileui.select(old, 0.3, 5)
            else:
                getattr(profileui, action)(old, 'Renamed', 0.3, 5)
        except Exception as exc:
            error = exc
        elapsed = time.monotonic() - started
        destination = os.path.join(root, 'Renamed')
        success = mode in ('success', 'blockedListing', 'closeAfterInput') or (mode == 'missingControls'
                                                           and action == 'select')
        if success and old == 'Second':
            r.case(common.T('profileui.success'), error, None)
            r.case(common.T('profileui.folder'), os.path.isdir(destination) if action != 'select'
                   else os.path.isdir(os.path.join(root, old)), True)
            with open(sample if action == 'select' else os.path.join(destination, 'save.ess'), 'rb') as f:
                r.case(common.T('profileui.contents'), f.read(), b'SAVE\x00\xff')
        else:
            r.case(common.T('profileui.failure', mode=mode), isinstance(error, Exception), True)
            r.case(common.T('profileui.source', mode=mode), os.path.isfile(sample), True)
            r.case(common.T('profileui.noDestination', mode=mode), os.path.exists(destination), False)
        r.case(common.T('profileui.bounded', mode=mode), elapsed < 2, True)
        r.case(common.T('profileui.current', mode=mode), window.box.currentText(),
               old if action == 'select' and success else 'Claude')
        if action == 'select':
            r.case(common.T('profileui.noManage', mode=mode), window.manage_count, 0)
            r.case(common.T('profileui.noWriters', mode=mode), window.writers_created, 0)
        else:
            r.case(common.T('profileui.released', mode=mode), window.rows_before_refresh, 0)
            r.case(common.T('profileui.allWriters', mode=mode), len(window.writes),
                   window.writers_created)
            r.case(common.T('profileui.writersBeforeRefresh', mode=mode),
                   window.writes_before_refresh, window.writers_created)
            for profile, expected, actual in window.writes:
                r.case(common.T('profileui.stableRows', mode=mode, profile=profile),
                       actual, expected)
            r.case(common.T('profileui.signalState', mode=mode), window.blocked_after_release,
                   mode == 'blockedListing')
        if action == 'clone':
            r.case(common.T('profileui.source', mode=mode), os.path.isfile(sample), True)
        r.case(common.T('profileui.closed', mode=mode), app.activeModalWidget(), None)
        if mode in ('earlyEscape', 'closeAfterInput'):
            r.case(common.T('profileui.userClose', mode=mode),
                   window.rows_at_user_close, window.writers_created)
        # Drain pending singleShot callbacks: none may rename anything after refusal.
        for _ in range(4):
            app.processEvents()
            time.sleep(0.01)
        if mode == 'closeAfterInput':
            # The operation's observer must not attach cleanup to later dialogs
            # opened by the same manager after its native call has returned.
            later = ProfilesDialog(window)
            later.setModal(True)
            later.show()
            app.processEvents()
            later.close()
            r.case(common.T('profileui.laterKept'), later.listing.count(), 2)
            sip.delete(later)
        window.close()
        window.deleteLater()
        app.processEvents()


exercise('success')
exercise('missingProfile', 'Missing')
exercise('missingControls')
exercise('cancel')
exercise('unexpectedChild')
exercise('earlyEscape')
exercise('closeAfterInput')
exercise('success', action='clone')
exercise('blockedListing', action='clone')
exercise('cancel', action='clone')
exercise('missingControls', action='clone')
exercise('earlyEscape', action='clone')
exercise('closeAfterInput', action='clone')
exercise('success', action='select')
exercise('missingControls', action='select')
exercise('missingProfile', 'Missing', action='select')
exercise('duplicateProfile', action='select')
exercise('disabledBox', action='select')
with tempfile.TemporaryDirectory(prefix='mo2-profileui-') as root:
    window = MainWindow(root)
    foreign = QDialog(window)
    foreign.setModal(True)
    foreign.show()
    app.processEvents()
    error = None
    try:
        profileui.rename('Second', 'Renamed', 0.3, 5)
    except RuntimeError as exc:
        error = exc
    r.case(common.T('profileui.foreignRefused'), isinstance(error, RuntimeError), True)
    r.case(common.T('profileui.foreignKept'), foreign.isVisible(), True)
    foreign.reject()
    window.close()
    app.processEvents()
r.done()
