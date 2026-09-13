# -*- coding: utf-8 -*-
"""The domain layer: everything done to MO2. This file is the facade over the areas.

It knows mobase and the setup. It knows nothing of HTTP, JSON or tokens - it takes plain
arguments and returns plain dictionaries. So these methods can be called from anywhere:
from a route, from the menu item, from a test.

The logic itself is split by area, each in its own file:

    busy.py       BusyGuard   whether MO2 is busy: launch bookkeeping and three lock sources
    reading.py    Reader      reads of state and of the virtual Data
    install.py    Installer   installing a mod: fresh, merge, replace
    mods.py       ModOps      enable, disable, refresh; priority, name, removal
    loadorder.py  LoadOrder   plugin states and order, writing plugins.txt
    launch.py     Launcher    launching programs and driving their windows
    base.py       Domain      context, the shared change procedure, the reply shape

`Services` only assembles them and exposes the same methods under the same names: the names
are a contract, referred to by routes.py, plugin.py and the checks. The bookkeeping
attributes (`launched`, `game_exe`, `_self_hwnd`, `procs`) stay here too - the busy-logic
checks set them directly.
"""

from . import base, busy, config, install, launch, loadorder, mods, reading, updates
from .base import DANGER_KEY, DANGER_VALUE, one as _one, safe as _safe  # noqa: F401
from .install import tree_files as _tree_files  # noqa: F401
from .reading import walk_factory as _walk_factory  # noqa: F401


def _seven_zip():
    """Old name kept: the path to 7z.exe from the default settings."""
    return config.Config().seven_zip()


class Services(object):
    def __init__(self, organizer, run_main, docs_path, note=None, cfg=None):
        # note - where to record launches; without it the plugin works silently.
        # cfg - settings; without them the built-in defaults are used and no file read.
        ctx = base.Context(organizer, run_main, docs_path,
                           note or (lambda _msg: None), cfg or config.Config())
        self.ctx = ctx
        self.o = organizer
        self.run_main = run_main
        self.docs = docs_path
        self.note = ctx.note
        self.cfg = ctx.cfg
        self.guard = busy.BusyGuard(ctx)
        self.reader = reading.Reader(ctx, self.guard)
        self.installer = install.Installer(ctx, self.guard)
        self.modops = mods.ModOps(ctx, self.guard)
        self.loadorder = loadorder.LoadOrder(ctx, self.guard)
        self.launcher = launch.Launcher(ctx, self.guard)
        self.updater = updates.Updates(ctx, self.guard)

    # ================================================== updates
    def updates(self, q):
        return self.updater.updates(q)

    # ================================================== is MO2 busy
    def on_about_to_run(self, path, *rest):
        return self.guard.on_about_to_run(path, *rest)

    def on_finished_run(self, path, exit_code=0):
        return self.guard.on_finished_run(path, exit_code)

    def prime(self):
        return self.guard.prime()

    def _busy(self):
        return self.guard.busy()

    def _blocked_while_busy(self, op_key):
        return self.guard.refusal(op_key)

    def _window_locked(self):
        return self.guard.window_locked()

    def _game_binary(self):
        return self.guard.game_binary()

    # The bookkeeping lives in the guard but is visible outside under the old names:
    # plugin.py reads and resets `launched`, and the busy-logic checks set `game_exe`
    # and `_self_hwnd` directly.
    launched = property(lambda s: s.guard.launched,
                        lambda s, v: setattr(s.guard, 'launched', v))
    game_exe = property(lambda s: s.guard.game_exe,
                        lambda s, v: setattr(s.guard, 'game_exe', v))
    _self_hwnd = property(lambda s: s.guard._self_hwnd,
                          lambda s, v: setattr(s.guard, '_self_hwnd', v))
    _starting = property(lambda s: s.guard._starting,
                         lambda s, v: setattr(s.guard, '_starting', v))
    procs = property(lambda s: s.launcher.procs,
                     lambda s, v: setattr(s.launcher, 'procs', v))
    seq = property(lambda s: s.launcher.seq,
                   lambda s, v: setattr(s.launcher, 'seq', v))

    # ================================================== reads
    def ping(self, q=None):
        return self.reader.ping(q)

    def api(self, q=None):
        return self.reader.api(q)

    def mods(self, q=None):
        return self.reader.mods(q)

    def profiles(self, q=None):
        return self.reader.profiles(q)

    def plugins(self, q=None):
        return self.reader.plugins(q)

    def mod(self, q):
        return self.reader.mod(q)

    def analyze(self, q):
        return self.reader.analyze(q)

    def vfs(self, q):
        return self.reader.vfs(q)

    def origins(self, q):
        return self.reader.origins(q)

    def dirs(self, q):
        return self.reader.dirs(q)

    def resolve(self, q):
        return self.reader.resolve(q)

    def vfsexport(self, body):
        return self.reader.vfsexport(body)

    # ================================================== writes
    def toggle(self, body):
        return self.modops.toggle(body)

    def refresh(self, body=None):
        return self.modops.refresh(body)

    def install(self, body):
        return self.installer.install(body)

    def plugins_state(self, body):
        return self.loadorder.plugins_state(body)

    def plugins_order(self, body):
        return self.loadorder.plugins_order(body)

    def _write_plugins_txt(self, wanted):
        return self.loadorder.write_plugins_txt(wanted)

    # ================================================== irreversible
    def _danger(self, body, what_key):
        # The old signature took a whole i18n key ('op.remove'); the new one takes the
        # operation name.
        return self.modops.danger(body, what_key[3:] if what_key.startswith('op.') else what_key)

    def mods_priority(self, body):
        return self.modops.mods_priority(body)

    def mods_rename(self, body):
        return self.modops.mods_rename(body)

    def mods_remove(self, body):
        return self.modops.mods_remove(body)

    def _removal_card(self, m, ml):
        return self.modops.removal_card(m, ml)

    # ================================================== processes and windows
    def run(self, body):
        return self.launcher.run(body)

    def procs_list(self, q=None):
        return self.launcher.procs_list(q)

    def windows(self, q):
        return self.launcher.windows(q)

    def window(self, body):
        return self.launcher.window(body)
