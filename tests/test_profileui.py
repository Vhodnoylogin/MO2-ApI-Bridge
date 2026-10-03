# -*- coding: utf-8 -*-
"""Optional native Qt regression checks, run with standalone PyQt6 installed.

Real nested Qt event loops model MO2 2.5's ProfilesDialog and QInputDialog.
They catch hangs, late callbacks, unwanted profile switches and cancelled input.
No installed MO2 or game is accessed. QT_QPA_PLATFORM=offscreen keeps tests invisible.
"""
import os
import sys
import tempfile
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
try:
    from PyQt6.QtCore import QTimer, Qt
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


class ProfilesDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.setObjectName('ProfilesDialog')
        self.window_owner = window
        layout = QVBoxLayout(self)
        self.listing = QListWidget(self)
        self.listing.setObjectName('profilesList')
        self.listing.addItems(['Claude', 'Second'])
        layout.addWidget(self.listing)
        if window.mode != 'missingControls':
            self.button = QPushButton(self)
            self.button.setObjectName('renameButton')
            self.button.clicked.connect(self.rename_selected)
            layout.addWidget(self.button)

    def rename_selected(self):
        old = self.listing.currentItem().text()
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


class MainWindow(QWidget):
    def __init__(self, root, mode='success'):
        super().__init__()
        self.root, self.mode = root, mode
        layout = QVBoxLayout(self)
        self.box = QComboBox(self)
        self.box.setObjectName('profileBox')
        self.box.addItems(['<Manage...>', 'Claude', 'Second'])
        self.box.setCurrentIndex(1)
        self.box.currentIndexChanged.connect(self.changed)
        layout.addWidget(self.box)
        self.show()
        app.processEvents()

    def changed(self, index):
        if index != 0:
            return
        dialog = ProfilesDialog(self)
        dialog.exec()
        self.box.blockSignals(True)
        self.box.clear()
        self.box.addItems(['<Manage...>'] + sorted(os.listdir(self.root)))
        self.box.setCurrentText('Claude')
        self.box.blockSignals(False)
        dialog.deleteLater()


def exercise(mode, old='Second'):
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
            profileui.rename(old, 'Renamed', 0.3, 5)
        except Exception as exc:
            error = exc
        elapsed = time.monotonic() - started
        destination = os.path.join(root, 'Renamed')
        if mode == 'success' and old == 'Second':
            r.case(common.T('profileui.success'), error, None)
            r.case(common.T('profileui.folder'), os.path.isdir(destination), True)
            with open(os.path.join(destination, 'save.ess'), 'rb') as f:
                r.case(common.T('profileui.contents'), f.read(), b'SAVE\x00\xff')
        else:
            r.case(common.T('profileui.failure', mode=mode), isinstance(error, Exception), True)
            r.case(common.T('profileui.source', mode=mode), os.path.isfile(sample), True)
            r.case(common.T('profileui.noDestination', mode=mode), os.path.exists(destination), False)
        r.case(common.T('profileui.bounded', mode=mode), elapsed < 2, True)
        r.case(common.T('profileui.current', mode=mode), window.box.currentText(), 'Claude')
        r.case(common.T('profileui.closed', mode=mode), app.activeModalWidget(), None)
        # Drain pending singleShot callbacks: none may rename anything after refusal.
        for _ in range(4):
            app.processEvents()
            time.sleep(0.01)
        window.close()
        window.deleteLater()
        app.processEvents()


exercise('success')
exercise('missingProfile', 'Missing')
exercise('missingControls')
exercise('cancel')
exercise('unexpectedChild')
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
