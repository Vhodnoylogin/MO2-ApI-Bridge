# -*- coding: utf-8 -*-
"""Bound one removal to MO2 2.5.2's native ModList model and confirmation.

The caller preserves the profile first and owns the final refresh/readback.
ModList::removeRows delegates to removeRowForce, which repairs profile indices
and emits native download accounting; IModList.removeMod omits those steps.
Source: ModOrganizer2/modorganizer, v2.5.2, src/modlist.{h,cpp},
mainwindow.ui and modlistview.cpp. No profile or download metadata is written here.
The stock confirmation's acceptance signals are suppressed only for our automated
Yes. Arbitrary application close-event filters or observers on the button itself
can still reenter native code and are outside this pinned stock adapter's scope.
"""
import math
import os
import time

from . import i18n


class NativeRemovalError(RuntimeError):
    """Machine-readable refusal/failure with the native operation's evidence."""

    def __init__(self, code, receipt=None):
        self.code = code
        self.receipt = dict(receipt or {})
        self.receipt['failure'] = code
        super().__init__(i18n.t('err.nativeRemove', reason=code))


def _canonical(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _target(organizer, name, path):
    if (not isinstance(name, str) or not name or name != name.strip()
            or name in ('.', '..') or any(c in name for c in '/\\:\x00')):
        raise NativeRemovalError('invalid-target')
    root = _canonical(organizer.modsPath())
    expected = os.path.join(root, name)
    mod = organizer.modList().getMod(name)
    if (mod is None or not os.path.isdir(path) or os.path.islink(path)
            or getattr(os.path, 'isjunction', lambda value: False)(path)
            or _canonical(path) != os.path.normcase(expected)
            or _canonical(mod.absolutePath()) != _canonical(path)
            or mod.name() != name):
        raise NativeRemovalError('target-mismatch')
    if any(getattr(mod, flag)() for flag in
           ('isOverwrite', 'isForeign', 'isSeparator', 'isBackup')):
        raise NativeRemovalError('unsupported-target-kind')


def _source(view):
    from PyQt6.QtCore import QAbstractProxyModel

    model = view.model()
    seen = set()
    while isinstance(model, QAbstractProxyModel):
        if id(model) in seen or len(seen) >= 3:
            raise NativeRemovalError('mod-model-unavailable')
        seen.add(id(model))
        model = model.sourceModel()
    if (model is None or model.metaObject().className() != 'ModList'
            or model.columnCount() != 11):
        raise NativeRemovalError('mod-model-unavailable')
    return model


def _row(organizer, model, name, names=None):
    current = list(organizer.modList().allMods())
    if (current.count(name) != 1 or len(current) != model.rowCount()
            or names is not None and current != names):
        raise NativeRemovalError('mod-identity-changed')
    row = current.index(name)
    index = model.index(row, 0)
    # IndexRole is the canonical ModInfo collection index. Display/EditRole
    # strip separator suffixes and cannot establish internal-name identity.
    if not index.isValid() or index.data(257) != row:
        raise NativeRemovalError('mod-identity-changed')
    return row, current


def _owned_by(widget, window):
    while widget is not None:
        if widget is window:
            return True
        widget = widget.parentWidget()
    return False


def remove(organizer, name, path, *, timeout, poll_ms, before_confirm):
    """Invoke the native source model once, accepting only its exact new prompt.

    ``before_confirm`` rechecks the caller's preserved profile/order/state and
    instance/busy guards immediately before Yes. Raise or return False to refuse.
    Timeout cancels only our exact confirmation or a new dialog under our window;
    unrelated parentless windows are never approved or closed. Native filesystem
    operations can outlast the polling deadline and are never forcibly interrupted.
    """
    receipt = {'invoked': False, 'confirmationAccepted': False,
               'confirmationCancelled': False, 'nativeReturned': False,
               'removed': False, 'timerStopped': True}
    try:
        from PyQt6.QtCore import (QCoreApplication, QModelIndex, QSignalBlocker,
                                 QThread, QTimer)
        from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox, QTreeView
    except ImportError as exc:
        raise NativeRemovalError('qt-unavailable', receipt) from exc
    app = QApplication.instance()
    if not isinstance(app, QApplication) or QThread.currentThread() != app.thread():
        raise NativeRemovalError('qt-thread-required', receipt)
    if app.activeModalWidget() is not None:
        raise NativeRemovalError('preexisting-modal', receipt)
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout <= 0
            or type(poll_ms) is not int or poll_ms < 1 or not callable(before_confirm)):
        raise NativeRemovalError('invalid-removal-options', receipt)
    timer = None
    state = {'error': None, 'accepted': None}
    try:
        if organizer.appVersion().canonicalString() not in ('2.5.2', '2.5.2.0'):
            raise NativeRemovalError('unsupported-mod-ui-version')
        windows = [w for w in app.topLevelWidgets()
                   if w.metaObject().className() == 'MainWindow' and w.isVisible()]
        if len(windows) != 1 or not windows[0].isEnabled():
            raise NativeRemovalError('mod-window-unavailable')
        window = windows[0]
        views = window.findChildren(QTreeView, 'modList')
        if (len(views) != 1 or views[0].metaObject().className() != 'ModListView'
                or not views[0].isEnabled()):
            raise NativeRemovalError('mod-view-unavailable')
        view = views[0]
        model = _source(view)
        _target(organizer, name, path)
        row, names = _row(organizer, model, name)
        title = QCoreApplication.translate('ModList', 'Confirm')
        text = QCoreApplication.translate(
            'ModList', 'Are you sure you want to remove "%1"?').replace(
                '%1', organizer.modList().displayName(name))
        existing = set(app.topLevelWidgets())
        deadline = time.monotonic() + timeout
        receipt['sourceRow'] = row

        def check():
            try:
                modal = app.activeModalWidget()
                if time.monotonic() >= deadline:
                    state['error'] = state['error'] or 'removal-timeout'
                if modal is None or modal is state['accepted']:
                    return
                if modal in existing:
                    state['error'] = state['error'] or 'preexisting-modal'
                    return
                exact = (isinstance(modal, QMessageBox) and modal.windowTitle() == title
                         and modal.text() == text)
                if not exact:
                    state['error'] = state['error'] or 'unexpected-native-dialog'
                    if isinstance(modal, QDialog) and _owned_by(modal, window):
                        modal.reject()
                    return
                if (modal.metaObject().className() != 'QMessageBox'
                        or modal.icon() != QMessageBox.Icon.Question
                        or modal.standardButtons() != (QMessageBox.StandardButton.Yes
                                                       | QMessageBox.StandardButton.No)
                        or receipt['confirmationAccepted']):
                    state['error'] = state['error'] or 'confirmation-mismatch'
                if state['error'] is None:
                    try:
                        if _source(view) is not model:
                            raise NativeRemovalError('mod-identity-changed')
                        _target(organizer, name, path)
                        _row(organizer, model, name, names)
                        if before_confirm() is False:
                            raise NativeRemovalError('confirmation-refused')
                        if _source(view) is not model:
                            raise NativeRemovalError('mod-identity-changed')
                        _target(organizer, name, path)
                        _row(organizer, model, name, names)
                        if time.monotonic() >= deadline:
                            raise NativeRemovalError('removal-timeout')
                    except Exception as exc:
                        state['error'] = getattr(exc, 'code', 'confirmation-guard-failed')
                if state['error'] is not None:
                    receipt['confirmationCancelled'] = True
                    modal.reject()
                    return
                button = modal.button(QMessageBox.StandardButton.Yes)
                if button is None or not button.isEnabled():
                    state['error'] = 'confirmation-mismatch'
                    receipt['confirmationCancelled'] = True
                    modal.reject()
                    return
                state['accepted'] = modal
                receipt['confirmationAccepted'] = True
                # Native removeRows reuses its numeric row after exec returns.
                # finished/accepted observers must not reorder that collection
                # after our guard. QDialog.done emits them synchronously within
                # click; block this exact box until that call completes. Keep
                # the button's own signals enabled for QMessageBox's response.
                with QSignalBlocker(modal):
                    receipt['confirmationSignalsBlocked'] = True
                    button.click()
            except RuntimeError:
                state['error'] = state['error'] or 'native-widget-unavailable'

        timer = QTimer()
        timer.setInterval(min(poll_ms, max(1, math.ceil(timeout * 1000))))
        timer.timeout.connect(check)
        timer.start()
        receipt['timerStopped'] = False
        receipt['invoked'] = True
        result = model.removeRows(row, 1, QModelIndex())
        receipt.update(nativeReturned=True, nativeResult=bool(result))
        receipt['folderAbsent'] = not os.path.lexists(path)
        receipt['modAbsent'] = organizer.modList().getMod(name) is None
        receipt['removed'] = receipt['folderAbsent'] and receipt['modAbsent']
        if state['error']:
            raise NativeRemovalError(state['error'])
        if not receipt['confirmationAccepted']:
            raise NativeRemovalError('confirmation-not-accepted')
        if not receipt['removed']:
            raise NativeRemovalError('removal-not-verified')
    except Exception as exc:
        state['error'] = getattr(exc, 'code', 'native-removal-failed')
    finally:
        if timer is not None:
            timer.stop()
            try:
                timer.timeout.disconnect(check)
            except (TypeError, RuntimeError):
                pass
            timer.deleteLater()
            receipt['timerStopped'] = True
        if receipt['invoked'] and 'folderAbsent' not in receipt:
            try:
                receipt['folderAbsent'] = not os.path.lexists(path)
                receipt['modAbsent'] = organizer.modList().getMod(name) is None
                receipt['removed'] = receipt['folderAbsent'] and receipt['modAbsent']
            except Exception:
                receipt['readbackAvailable'] = False
    if state['error']:
        raise NativeRemovalError(state['error'], receipt)
    return receipt
