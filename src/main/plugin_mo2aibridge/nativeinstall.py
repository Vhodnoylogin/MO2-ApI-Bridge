# -*- coding: utf-8 -*-
"""A one-use selection installer for MO2's native installation/accounting path.

The caller must validate the archive hash and complete file manifest first, and
call ``install`` on the Qt thread. Only new, unique mod names are supported.
No mod metadata or download sidecar is written here.

Verified against the official MO2 2.5.2 source archive:
* installationmanager.cpp: install(), doInstall(), notifyInstallationStart().
* organizercore.cpp: installArchive() adds installedFiles and marks the download.
* plugin_python/src/mobase/wrappers/pyplugins.h: the simple-installer return tuple.
* archivefiletree.cpp: native entry copies retain their archive member indices.
https://github.com/ModOrganizer2/modorganizer/tree/v2.5.2/src

Python has no setInstallationFile binding, and IDownloadManager has no
markInstalled method. Restructuring the original native archive tree lets MO2
perform both operations itself. The companion must be registered by the package's
createPlugins factory; merely constructing a Python object does not register it.
"""
import ntpath
import os

from . import __version__, i18n


INSTALLER_NAME = 'MO2 ApI Bridge Native Installer'
_controller = None


class NativeInstallError(RuntimeError):
    """A machine-readable failure with the native continuation's evidence."""

    def __init__(self, code, receipt=None):
        self.code = code
        self.receipt = dict(receipt or {})
        self.receipt['failure'] = code
        super().__init__(i18n.t('err.nativeInstall', reason=code))


def _canonical(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _host(organizer):
    # Every plugin receives its own OrganizerProxy. Python object identity cannot
    # establish that the bridge and its companion belong to the same instance.
    return (_canonical(organizer.modsPath()), _canonical(organizer.downloadsPath()),
            _canonical(organizer.profile().absolutePath()))


def _relative(value):
    if not isinstance(value, str) or not value or '\x00' in value:
        raise NativeInstallError('invalid-mapping')
    path = value.replace('\\', '/')
    if ntpath.isabs(path) or ':' in path or any(
            part in ('', '.', '..') for part in path.split('/')):
        raise NativeInstallError('invalid-mapping')
    return path


def _mappings(values):
    if not isinstance(values, list) or not values:
        raise NativeInstallError('empty-selection')
    result, destinations = [], set()
    for value in values:
        if not isinstance(value, dict):
            raise NativeInstallError('invalid-mapping')
        source, destination = _relative(value.get('source')), _relative(value.get('destination'))
        lower = destination.casefold()
        components = lower.split('/')
        if (lower in destinations or 'meta.ini' in components or 'fomod' in components
                or components[0] == 'ini tweaks'):
            raise NativeInstallError('invalid-destination')
        destinations.add(lower)
        result.append({'source': source, 'destination': destination})
    return result


def _new_target(organizer, name):
    if (not isinstance(name, str) or not name.strip() or name != name.strip()
            or name in ('.', '..') or any(c in name for c in '/\\:\x00')):
        raise NativeInstallError('invalid-name')
    target = os.path.join(organizer.modsPath(), name)
    if (os.path.lexists(target)
            or organizer.modList().getMod(name) is not None):
        raise NativeInstallError('target-exists')
    return target


class _Controller:
    def __init__(self, plugin, organizer):
        self.plugin, self.organizer = plugin, organizer
        self.plan = None

    def start(self, archive, reinstallation, current_mod):
        plan = self.plan
        if plan is None:
            return
        matches = _canonical(archive) == plan['archive']
        plan['bound'] = matches and not reinstallation and current_mod is None
        plan['receipt']['nativeStartObserved'] = matches
        if not plan['bound']:
            plan['error'] = 'native-context-mismatch'

    def supports(self):
        # No plan, or a different native installation, belongs to ordinary MO2.
        return self.plan is not None and self.plan['bound']

    def select(self, mod_name, tree, version, nexus_id):
        import mobase
        plan = self.plan
        if plan is None or not plan['bound']:
            return mobase.InstallResult.CANCELED
        receipt = plan['receipt']
        receipt['installerInvoked'] = True
        try:
            if plan['error']:
                raise NativeInstallError(plan['error'])
            if plan['consumed']:
                raise NativeInstallError('selection-already-consumed')
            plan['consumed'] = True
            _new_target(self.organizer, plan['name'])
            entries = []
            for item in plan['mappings']:
                entry = tree.find(item['source'])
                if entry is None or not entry.isFile():
                    raise NativeInstallError('source-not-file')
                entries.append((entry, item['destination']))

            # The virtual native factory/copy keeps ArchiveFileTree entries and
            # their member indices. Return the SAME root that MO2 supplied.
            selected = tree.createOrphanTree()
            for entry, destination in entries:
                selected.copy(entry, destination)
            mod_name.update(plan['name'], mobase.GuessQuality.USER)
            if str(mod_name) != plan['name']:
                raise NativeInstallError('native-name-changed')
            tree.clear()
            tree.merge(selected)
            receipt['selectionPrepared'] = True
            receipt['selectedFiles'] = len(entries)
            metadata = plan['metadata']
            return (mobase.InstallResult.SUCCESS, tree,
                    metadata.get('version', version), metadata.get('nexusId', nexus_id))
        except Exception as exc:
            plan['error'] = getattr(exc, 'code', 'selection-failed')
            receipt['failure'] = plan['error']
            # FAILED opens MO2's error QMessageBox; NOTATTEMPTED opens another
            # installer's wizard. CANCELED ends this native installer chain.
            return mobase.InstallResult.CANCELED


def create_installer():
    """Construct the companion lazily, keeping ordinary imports mobase-free."""
    import mobase

    class NativeInstaller(mobase.IPluginInstallerSimple):
        def __init__(self):
            super().__init__()
            self.controller = None

        def init(self, organizer):
            global _controller
            self.controller = _Controller(self, organizer)
            _controller = self.controller
            return True

        def name(self):
            return INSTALLER_NAME

        def localizedName(self):
            return i18n.t('plugin.nativeInstallerName')

        def author(self):
            return 'Vhodnoylogin'

        def description(self):
            return i18n.t('plugin.nativeInstallerDescription')

        def version(self):
            parts = [int(part) for part in __version__.split('.')[:3]]
            return mobase.VersionInfo(*(parts + [0] * (3 - len(parts))), 0)

        def settings(self):
            return []

        def priority(self):
            return 0xffffffff  # Maximum value of the native unsigned-int API.

        def isManualInstaller(self):
            return False

        def onInstallationStart(self, archive, reinstallation, current_mod):
            if self.controller is not None:
                self.controller.start(archive, reinstallation, current_mod)

        def isArchiveSupported(self, tree):
            return self.controller is not None and self.controller.supports()

        def install(self, mod_name, tree, version, nexus_id):
            return self.controller.select(mod_name, tree, version, nexus_id)

    return NativeInstaller()


def check_available(organizer):
    """Refuse before writes if this instance has no enabled registered companion."""
    controller = _controller
    if controller is None or _host(controller.organizer) != _host(organizer):
        raise NativeInstallError('companion-unavailable')
    if not organizer.isPluginEnabled(INSTALLER_NAME):
        raise NativeInstallError('companion-disabled')
    if controller.plan is not None:
        raise NativeInstallError('native-install-active')
    return True


def _download_ui(organizer):
    """Resolve only the exact native MO2 2.5.2 download controls/model.

    Source: downloadstab.cpp 7-28, 78-80; downloadlist.h EColumn;
    downloadmanager.cpp getFilePath()/downloadPath(). Display names can be
    metadata-derived, so they are NEVER used as archive identity.
    """
    from PyQt6.QtCore import QAbstractProxyModel
    from PyQt6.QtWidgets import QCheckBox, QPushButton, QTreeView

    check_available(organizer)
    version = organizer.appVersion().canonicalString()
    if version not in ('2.5.2', '2.5.2.0'):
        raise NativeInstallError('unsupported-download-ui-version')
    window = _controller.plugin._parentWidget()
    if (window is None or window.metaObject().className() != 'MainWindow'
            or not window.isEnabled()):
        raise NativeInstallError('download-window-unavailable')
    buttons = window.findChildren(QPushButton, 'btnRefreshDownloads')
    views = window.findChildren(QTreeView, 'downloadView')
    boxes = window.findChildren(QCheckBox, 'showHiddenBox')
    if (len(buttons) != 1 or len(views) != 1 or len(boxes) != 1
            or not buttons[0].isEnabled()
            or views[0].metaObject().className() != 'DownloadListView'):
        raise NativeInstallError('download-controls-unavailable')
    model = views[0].model()
    seen = set()
    while isinstance(model, QAbstractProxyModel):
        if id(model) in seen or len(seen) >= 8:
            raise NativeInstallError('download-model-unavailable')
        seen.add(id(model))
        model = model.sourceModel()
    if (model is None or model.metaObject().className() != 'DownloadList'
            or model.columnCount() != 8):
        raise NativeInstallError('download-model-unavailable')
    return buttons[0], views[0], boxes[0], model


def refresh_downloads(organizer, archive, require_installed=True):
    """Refresh native Downloads and verify this exact archive's Installed row.

    This invokes the existing native refresh button; it does not write sidecars,
    set model data, change filters or toggle the show-hidden preference. MO2's
    ordinary refreshList itself performs its normal orphan-sidecar cleanup.

    The unproxied DownloadList row is the download-manager index in 2.5.2.
    IDownloadManager.downloadPath(row) therefore supplies exact archive identity,
    even when the user's displayed download names are metadata-derived. The
    model exposes no integer state role: its localized COL_STATUS DisplayRole is
    compared with the SAME DownloadList translation context used by native code.
    """
    from PyQt6.QtCore import QCoreApplication, QThread, Qt
    from PyQt6.QtWidgets import QApplication

    receipt = {'verified': False, 'found': False, 'installed': False,
               'nativeRefreshInvoked': False, 'requireInstalled': require_installed}
    try:
        app = QApplication.instance()
        if app is None or QThread.currentThread() != app.thread():
            raise NativeInstallError('qt-thread-required', receipt)
        if app.activeModalWidget() is not None:
            raise NativeInstallError('preexisting-modal', receipt)
        if not isinstance(require_installed, bool):
            raise NativeInstallError('invalid-download-verification', receipt)
        if (not os.path.isfile(archive)
                or _canonical(os.path.dirname(archive)) != _canonical(organizer.downloadsPath())):
            raise NativeInstallError('archive-outside-downloads', receipt)
        button, _view, box, _model = _download_ui(organizer)
        was_hidden = box.isChecked()
        receipt['nativeRefreshInvoked'] = True
        button.click()
        _button, _view, box, model = _download_ui(organizer)
        if box.isChecked() != was_hidden:
            raise NativeInstallError('download-preference-changed', receipt)
        manager = organizer.downloadManager()
        matches = []
        for row in range(model.rowCount()):
            try:
                path = manager.downloadPath(row)
            except Exception:
                # The model may append pending Nexus requests after actual
                # downloads. They have no file path and cannot match an archive.
                continue
            if path and _canonical(path) == _canonical(archive):
                matches.append((row, path))
        if len(matches) != 1:
            raise NativeInstallError('download-row-not-unique', receipt)
        row, path = matches[0]
        status = model.data(model.index(row, 1), Qt.ItemDataRole.DisplayRole)
        expected = QCoreApplication.translate('DownloadList', 'Installed')
        completed = {QCoreApplication.translate('DownloadList', source)
                     for source in ('Downloaded', 'Installed', 'Uninstalled')}
        receipt.update(archive=path, sourceRow=row, status=status, found=True,
                       expectedStatus=expected, model='DownloadList',
                       identity='IDownloadManager.downloadPath(sourceRow)')
        if not isinstance(status, str) or status not in completed:
            raise NativeInstallError('download-not-complete', receipt)
        if require_installed and status != expected:
            raise NativeInstallError('download-not-installed', receipt)
        receipt.update(verified=True, installed=status == expected)
        return receipt
    except NativeInstallError as exc:
        merged = dict(receipt)
        merged.update(exc.receipt)
        raise NativeInstallError(exc.code, merged) from exc
    except Exception as exc:
        raise NativeInstallError('download-refresh-unavailable', receipt) from exc


def _guard_modals(app, plan, poll_ms):
    """Cancel newly created native prompts during the synchronous install only.

    The native extraction progress window is expected. Prompt cancellation marks
    the plan failed as well: closing an unmapped-category QMessageBox alone would
    otherwise make MO2 proceed with the installation.
    """
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QDialog, QProgressDialog

    existing = {widget for widget in app.topLevelWidgets() if widget.isVisible()}
    timer = QTimer()
    timer.setInterval(poll_ms)

    def check():
        try:
            modal = app.activeModalWidget()
            if modal is None or modal in existing or not isinstance(modal, QDialog):
                return
            if isinstance(modal, QProgressDialog):
                if plan['error']:
                    modal.cancel()
                return
            plan['error'] = plan['error'] or 'unexpected-native-dialog'
            name = modal.metaObject().className()
            if name not in plan['receipt']['cancelledDialogs']:
                plan['receipt']['cancelledDialogs'].append(name)
            modal.reject()
        except RuntimeError:
            # Native nested loops may delete a dialog between observation and use.
            plan['error'] = plan['error'] or 'native-dialog-unavailable'

    timer.timeout.connect(check)
    timer.start()
    return timer


def install(organizer, archive, name, mappings, metadata=None, *, poll_ms=25):
    """Return ``(IModInterface, receipt)`` after a native new-mod installation.

    The root route owns hash, manifest, busy-state, expected-instance, inventory,
    provenance persistence and rollback evidence. NativeInstallError.receipt must
    be retained if this call fails: MO2 may already have changed the target.
    Optional metadata keys are version, nexusId and gameName; MO2 otherwise uses
    the original download's metadata. There is no merge/replace fallback.
    """
    from PyQt6.QtCore import QThread
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None or QThread.currentThread() != app.thread():
        raise NativeInstallError('qt-thread-required')
    if app.activeModalWidget() is not None:
        raise NativeInstallError('preexisting-modal')
    if isinstance(poll_ms, bool) or not isinstance(poll_ms, int) or poll_ms < 1:
        raise NativeInstallError('invalid-poll-interval')
    check_available(organizer)
    controller = _controller
    _new_target(organizer, name)
    # Preserve the actual basename spelling for MO2's case-sensitive download
    # lookup. normcase is for identity checks, never the native install argument.
    archive = os.path.realpath(os.path.abspath(archive))
    if (not os.path.isfile(archive)
            or _canonical(os.path.dirname(archive)) != _canonical(organizer.downloadsPath())):
        raise NativeInstallError('archive-outside-downloads')
    mappings = _mappings(mappings)
    if metadata is not None and not isinstance(metadata, dict):
        raise NativeInstallError('invalid-metadata')
    metadata = dict(metadata or {})
    if (set(metadata) - {'version', 'nexusId', 'gameName'}
            or ('nexusId' in metadata and (type(metadata['nexusId']) is not int
                                         or metadata['nexusId'] <= 0))
            or any(key in metadata and (not isinstance(metadata[key], str)
                                       or not metadata[key].strip())
                   for key in ('version', 'gameName'))):
        raise NativeInstallError('invalid-metadata')
    receipt = {'native': True, 'nativeStartObserved': False, 'installerInvoked': False,
               'selectionPrepared': False, 'selectedFiles': 0, 'cancelledDialogs': []}
    plan = {'archive': _canonical(archive), 'name': name, 'mappings': mappings, 'metadata': metadata,
            'bound': False, 'consumed': False, 'error': None, 'receipt': receipt}
    controller.plan = plan
    timer = None
    try:
        timer = _guard_modals(app, plan, poll_ms)
        mod = organizer.installMod(archive, name)
        if plan['error']:
            raise NativeInstallError(plan['error'], receipt)
        if not receipt['selectionPrepared']:
            raise NativeInstallError('companion-not-invoked', receipt)
        if mod is None:
            raise NativeInstallError('native-install-failed', receipt)
        if mod.name() != name or _canonical(mod.absolutePath()) != _canonical(
                os.path.join(organizer.modsPath(), name)):
            raise NativeInstallError('native-target-mismatch', receipt)
        if 'gameName' in metadata:
            mod.setGameName(metadata['gameName'])
        return mod, dict(receipt)
    except NativeInstallError:
        raise
    except Exception as exc:
        raise NativeInstallError('native-install-exception', receipt) from exc
    finally:
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        controller.plan = None
