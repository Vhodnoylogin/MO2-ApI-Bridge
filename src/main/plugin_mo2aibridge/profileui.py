# -*- coding: utf-8 -*-
"""Invoke MO2 2.5's native profile rename on the Qt main thread.

IOrganizer/IProfile expose no rename operation. Driving the native ProfilesDialog
keeps MO2's profile objects, UI list and profileRenamed callbacks consistent.
Only dialogs opened by this call are filled or cancelled; an existing modal is refused.
"""
import time

from . import i18n


def owned_by(widget, owner):
    """QWidget.isAncestorOf stops at window boundaries; QDialog is a window."""
    while widget is not None:
        if widget is owner:
            return True
        widget = widget.parentWidget()
    return False


def rename(old, new, timeout, poll_ms):
    return operate('rename', old, new, timeout, poll_ms)


def clone(old, new, timeout, poll_ms):
    return operate('clone', old, new, timeout, poll_ms)


def select(name, timeout, poll_ms):
    return operate('select', name, None, timeout, poll_ms)


def operate(action, old, new, timeout, poll_ms):
    from PyQt6.QtCore import QTimer, Qt
    from PyQt6.QtWidgets import QApplication, QComboBox, QListWidget, QPushButton, QInputDialog

    app = QApplication.instance()
    if app is None or app.activeModalWidget() is not None:
        raise RuntimeError(i18n.t('err.profileDialog'))
    boxes = [w.findChild(QComboBox, 'profileBox') for w in app.topLevelWidgets()]
    boxes = [b for b in boxes if b is not None and b.isVisible() and b.isEnabled()]
    if len(boxes) != 1 or boxes[0].currentIndex() <= 0:
        raise RuntimeError(i18n.t('err.profileDialog'))
    box = boxes[0]
    state = {'phase': 'profiles', 'dialog': None, 'input': None, 'error': None, 'done': False}
    deadline = time.monotonic() + timeout

    def fail(exc):
        state['error'] = exc
        state['done'] = True
        # Cancel a nested dialog owned by our profile manager before its parent.
        modal = app.activeModalWidget()
        owner = state['dialog']
        if modal is not None and owner is not None and modal is not owner and owned_by(modal, owner):
            try:
                modal.reject()
            except RuntimeError:
                pass
        # Reject only our own dialogs, never an unrelated user window.
        for widget in (state['input'], state['dialog']):
            if widget is not None:
                try:
                    widget.reject()
                except RuntimeError:
                    # A native dialog may already have been destroyed after its exec().
                    pass

    def step():
        if state['done']:
            return
        try:
            if time.monotonic() >= deadline:
                raise RuntimeError(i18n.t('err.profileDialogTimeout', sec=timeout))
            modal = app.activeModalWidget()
            if state['phase'] == 'profiles':
                if modal is None:
                    QTimer.singleShot(poll_ms, step)
                    return
                if (modal.metaObject().className() == 'ProfilesDialog'
                        and owned_by(modal, box.window())):
                    state['dialog'] = modal
                listing = modal.findChild(QListWidget, 'profilesList')
                button = modal.findChild(QPushButton, {'rename': 'renameButton',
                                                      'clone': 'copyProfileButton',
                                                      'select': 'select'}[action])
                if (state['dialog'] is None or listing is None or button is None
                        or not owned_by(modal, box.window())):
                    raise RuntimeError(i18n.t('err.profileDialog'))
                state['dialog'] = modal
                items = listing.findItems(old, Qt.MatchFlag.MatchExactly)
                if len(items) != 1:
                    raise RuntimeError(i18n.t('err.noSuchProfile', profile=old))
                listing.setCurrentItem(items[0])
                if not button.isEnabled():
                    raise RuntimeError(i18n.t('err.profileDialog'))
                if action == 'select':
                    state['phase'], state['done'] = 'finish', True
                    button.click()
                    return
                state['phase'] = 'input'
                QTimer.singleShot(poll_ms, step)
                button.click()
                return
            if state['phase'] == 'input':
                if not isinstance(modal, QInputDialog) or modal.parentWidget() is not state['dialog']:
                    QTimer.singleShot(poll_ms, step)
                    return
                state['input'] = modal
                state['phase'] = 'finish'
                QTimer.singleShot(poll_ms, step)
                modal.setTextValue(new)
                modal.accept()
                return
            if state['phase'] == 'finish':
                if modal is state['dialog']:
                    state['done'] = True
                    modal.reject()  # Close without selecting a different active profile.
                else:
                    QTimer.singleShot(poll_ms, step)
        except Exception as exc:
            fail(exc)

    QTimer.singleShot(poll_ms, step)
    try:
        box.setCurrentIndex(0)  # MO2's native <Manage...> entry opens ProfilesDialog.exec().
    finally:
        state['done'] = True
    if state['error'] is not None:
        raise state['error']
    if state['phase'] != 'finish':
        raise RuntimeError(i18n.t('err.profileDialog'))
