# -*- coding: utf-8 -*-
"""MO2 ApI Bridge - a local HTTP bridge to a running Mod Organizer 2.

The top layer: the plugin lifecycle and the wiring of everything below it. It does nothing
by hand - it only connects.

Layers, bottom to top; each knows only the one below it:

    winapi.py    Windows windows and buttons     - knows nothing of MO2 or networking
    runtime.py   Qt main thread and HTTP         - knows nothing of what the routes do
    services.py  operations on the setup, facade - knows nothing of HTTP or JSON
    routes.py    which path maps to what         - knows nothing of mobase or sockets
    plugin.py    the plugin lifecycle            - this file

Strings live in i18n.py, configurable values in config.py, and where a line goes once it has
been written is journal.py's business.
"""
import os
import traceback

import mobase

from . import i18n, journal, runtime, routes as routes_mod
from .config import Config
from .services import Services

from . import PLUGIN_ID, __version__

PLUGIN_NAME = 'MO2 ApI Bridge'
HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(HERE, PLUGIN_ID + '-token.txt')
ERROR_LOG = os.path.join(HERE, PLUGIN_ID + '.log')
CONFIG_FILE = os.path.join(HERE, PLUGIN_ID + '-config.json')
DOCS = os.path.join(HERE, 'README.md')


def token_file(port, wanted):
    """Where to write the token for a bridge that bound `port` having asked for `wanted`.

    If the requested port was granted, the name is the familiar one - so for anyone with a
    single MO2 instance nothing changes, whatever the port is configured to. A second
    instance binds the next free port and gets a file of its own: one shared file had them
    overwriting each other, and the client that read the token last knocked on the wrong
    bridge with it. The port is in the name deliberately - it is how a client learns where to
    knock without asking anyone.
    """
    if int(port) == int(wanted):
        return TOKEN_FILE
    return os.path.join(HERE, '%s-token-%d.txt' % (PLUGIN_ID, int(port)))


# Both places a line goes: MO2's log, and a file next to the plugin. The file is there
# because MO2's default log level drops plugin warnings, and then the reason for a refusal
# is lost entirely - that once cost hours of searching, with the bridge quiet and an
# exception being swallowed. Which line reaches which sink is a matter of levels, and a sink
# that dies is announced through the other one rather than swallowed: journal.py.
JOURNAL = journal.Journal([journal.QtSink(journal.INFO),
                           journal.FileSink(ERROR_LOG, journal.DEBUG)], prefix=PLUGIN_NAME)


def log(msg, level=journal.INFO):
    """Write one line. The level is optional so that every call site written before levels
    existed goes on meaning what it meant."""
    JOURNAL.log(level, msg)


_config = None


def config(reload=False):
    """The settings, read from the file beside the plugin; with no file, one is written from
    the defaults.

    Read once and kept, because two moments need the same values and they are far apart: MO2
    asks for the settings list while it is still registering the plugin, long before anything
    has started, and `start()` needs the port and the retry count later. `reload` is how a
    start says "read the file again" - stopping and starting the bridge from its own window
    is the one way a person has to pick up an edited settings file without leaving MO2.
    """
    global _config
    if _config is None or reload:
        _config = Config.load(CONFIG_FILE, note=log)
    return _config


def _version_info():
    """VersionInfo out of __version__: the number is stated once, in __init__.py."""
    parts = [int(x) for x in __version__.split('.')[:3]]
    while len(parts) < 3:
        parts.append(0)
    return mobase.VersionInfo(parts[0], parts[1], parts[2], 0)


class MO2ApIBridge(mobase.IPluginTool):
    def __init__(self):
        super().__init__()
        self._organizer = None
        self._server = None
        self._runner = None
        self._parent = None
        self._svc = None
        self._port = None
        self._token_file = None
        self._quit_hooked = False

    # ---- the MO2 interface -------------------------------------------------
    def init(self, organizer):
        self._organizer = organizer
        # Where to report an unreadable translation file. Set before the first t(): with the
        # strings living in lang\ and nowhere else, a missing folder shows labels instead of
        # text, and the only way to learn why is the log.
        i18n.set_note(log)
        i18n.set_language(self._setting('language', 'auto'))
        log(i18n.t('init.loaded'))
        try:
            organizer.onUserInterfaceInitialized(self._on_ui_ready)
        except Exception:
            log(i18n.t('log.hookFailed', hook='onUserInterfaceInitialized',
                       error=traceback.format_exc()), journal.ERROR)
        return True

    def name(self):
        return PLUGIN_NAME

    def author(self):
        # The owner's name as it appears on Nexus. Licence is MIT; the LICENSE file is next
        # to this one.
        return 'Vhodnoylogin'

    def description(self):
        return i18n.t('plugin.description')

    def version(self):
        return _version_info()

    def settings(self):
        # The port is named in two places, and that is deliberate rather than untidy: the
        # settings file holds the value, MO2's own panel shows it to a person who has never
        # opened that file. The panel wins when it has an answer - it is the one a user can
        # see - and the file supplies what the panel starts from.
        return [mobase.PluginSetting('enabled', i18n.t('setting.enabled'), True),
                mobase.PluginSetting('port', i18n.t('setting.port'), int(config().get('port'))),
                mobase.PluginSetting('language', i18n.t('setting.language'), 'auto')]

    def isActive(self):
        """Lenient on purpose.

        The "Enabled" checkbox on the plugin panel in MO2 2.5 is the manager's own switch,
        not a setting named enabled: pluginSetting returns None for it. A strict `is True`
        comparison never passed, and the bridge silently failed to come up.
        """
        v = self._setting('enabled', True)
        if v is None:
            return True
        if isinstance(v, str):
            return v.strip().lower() not in ('false', '0', 'no', '')
        return bool(v)

    def displayName(self):
        return i18n.t('plugin.displayName')

    def tooltip(self):
        return i18n.t('plugin.tooltip')

    def icon(self):
        from PyQt6.QtGui import QIcon
        return QIcon()

    def setParentWidget(self, widget):
        self._parent = widget

    def display(self):
        """The menu entry doubles as a manual start: if autostart did not fire, the bridge
        can be raised from here and the real reason for a failure seen at once."""
        from PyQt6.QtWidgets import QMessageBox
        if not self._server:
            err = self.start()
            if err:
                QMessageBox.critical(self._parent, i18n.t('plugin.displayName'),
                                     i18n.t('dialog.startFailed', error=err, log=ERROR_LOG))
                return
        # Stuck launch bookkeeping is visible only here and reset only from here: there is
        # deliberately no route for it, or one request would bypass the busy lock.
        if self._svc is not None and self._svc.launched:
            btn = QMessageBox.question(
                self._parent, i18n.t('plugin.displayName'),
                i18n.t('dialog.stuck', run=chr(10).join(sorted(self._svc.launched))),
                QMessageBox.StandardButton.Reset | QMessageBox.StandardButton.Cancel)
            if btn == QMessageBox.StandardButton.Reset:
                self._svc.launched.clear()
                log(i18n.t('log.stuckReset'))
        # The port shown is the one the bridge actually bound, not the one the setting asked
        # for: when the requested port was taken they differ, and a person reading this
        # window must see the truth rather than the intention.
        state = i18n.t('dialog.running' if self._server else 'dialog.stopped')
        box = QMessageBox(self._parent)
        box.setWindowTitle(i18n.t('plugin.displayName'))
        box.setText(i18n.t('dialog.listening', port=self._port or config().get('port'),
                           token=self._token_file or TOKEN_FILE, state=state))
        # The stop button is the only way to bring the bridge down without leaving MO2: the
        # "Enabled" checkbox is read at startup only, and clearing it mid-session is not
        # enough.
        stop_btn = (box.addButton(i18n.t('dialog.stopButton'),
                                  QMessageBox.ButtonRole.DestructiveRole)
                    if self._server else None)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if stop_btn is not None and box.clickedButton() is stop_btn:
            self.stop()

    # ---- startup -----------------------------------------------------------
    def _setting(self, key, default=None):
        try:
            v = self._organizer.pluginSetting(self.name(), key)
            return default if v is None else v
        except Exception:
            return default

    def _subscribe_runs(self, svc):
        """Subscribe to programs launched through MO2.

        These two calls make everything the manager starts visible - both through the bridge
        and through a button in its window. Without them the bridge would know only about its
        own launches and would happily reorder mods under a running game.

        A failed subscription is not fatal: process enumeration remains, and it catches the
        game anyway. So we log it and carry on.
        """
        try:
            self._organizer.onAboutToRun(svc.on_about_to_run)
            self._organizer.onFinishedRun(svc.on_finished_run)
        except Exception:
            log(i18n.t('log.hookFailed', hook='onAboutToRun/onFinishedRun',
                       error=traceback.format_exc()), journal.WARN)

    def _on_ui_ready(self, _window=None):
        log(i18n.t('ui.ready'))
        err = self.start()
        if err:
            log('%s: %s' % (i18n.t('start.failed'), err), journal.ERROR)

    def start(self):
        """Raise the server. None on success, the error text otherwise.

        Swallowing the exception here is not an option: on failure there is no token file, no
        port and no record - from outside that is indistinguishable from "the plugin never
        loaded at all".
        """
        if self._server:
            return None
        if not self.isActive():
            return i18n.t('start.disabled')
        try:
            # Settings are read once per start; with no file, one is created from defaults.
            cfg = config(reload=True)
            wanted = int(self._setting('port', cfg.get('port')) or cfg.get('port'))
            token = runtime.new_token()
            self._runner = runtime.MainThreadRunner()
            svc = self._svc = Services(self._organizer, self._runner.call, DOCS,
                                       note=log, cfg=cfg)
            svc.prime()
            self._subscribe_runs(svc)
            get, post = routes_mod.build(svc)
            # The server comes up BEFORE the token is written: if the bind fails, no file is
            # left behind, and no client gets a key to a bridge that does not exist.
            self._server, self._port = runtime.serve(
                wanted, runtime.make_handler(token, get, post),
                tries=int(cfg.get('portTries')))
            self._token_file = token_file(self._port, wanted)
            with open(self._token_file, 'w', encoding='utf-8') as fh:
                fh.write(token)
            self._hook_quit()
            log(i18n.t('start.ok', port=self._port))
            if self._port != wanted:
                log(i18n.t('start.otherPort', wanted=wanted, port=self._port,
                           token=self._token_file), journal.WARN)
            return None
        except Exception as exc:
            self.stop()
            log(i18n.t('start.failed') + ':' + chr(10) + traceback.format_exc(), journal.ERROR)
            return '%s: %s' % (type(exc).__name__, exc)

    def _hook_quit(self):
        """Bring the bridge down when MO2 exits.

        A plugin in mobase 2.5.2 gets no unload call of its own, but the application signal
        exists and arrives before the embedded interpreter is finalised. Without it the
        server thread was cut short by finalisation mid-wait, and the token file was left
        lying around - a key to a bridge that no longer exists.
        """
        if self._quit_hooked:
            return
        try:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(self.stop)
                self._quit_hooked = True
        except Exception:
            log(i18n.t('log.hookFailed', hook='aboutToQuit',
                       error=traceback.format_exc()), journal.ERROR)

    def stop(self):
        """Bring the bridge down: close the socket, let the thread finish, remove the token.

        Called from three places: on MO2 exit, from the button in its own window, and after a
        failed start - so half a raised bridge is not left hanging. Each step separately and
        silently: shutting down has to run to the end whatever the previous step did.
        """
        srv, self._server = self._server, None
        if srv is not None:
            try:
                srv.shutdown()
            except Exception:
                pass
            try:
                srv.server_close()
            except Exception:
                pass
        try:
            if self._token_file and os.path.isfile(self._token_file):
                os.remove(self._token_file)
        except Exception:
            pass
        self._token_file = None
        self._svc = None
        self._runner = None
        if srv is not None:
            log(i18n.t('stop.ok'))
