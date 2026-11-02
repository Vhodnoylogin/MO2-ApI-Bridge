# -*- coding: utf-8 -*-
"""Whether MO2 is busy: who is holding the manager right now, and may the setup change.

The central idea of this module. While a game or a tool runs under MO2, the virtual Data is
already mounted into another process, and editing mods, order or plugins on the fly means
the running program sees one setup while the files on disk describe another. So every write
route refuses, and every read keeps working.

Busy is decided by three independent sources, and that is not redundancy:

1. MO2's callbacks (onAboutToRun / onFinishedRun) - they know about launches through the
   manager, but do not survive a missed onFinishedRun;
2. process enumeration - it knows the truth about live programs, but cannot by itself tell a
   launch through MO2 from one beside it. Only this source catches the game: MO2 starts it
   via sksevr_loader.exe, which exits at once, leaving SkyrimVR.exe behind;
3. whether MO2's main window is enabled - the closest thing to what a person sees: while the
   manager waits for a program it shows a modal dialog and its window stops taking input.

Busy is declared if any one of the three fires.
"""
import os
import threading

from . import i18n, winapi


class LaunchState(object):
    """MO2-lifetime evidence, with no HTTP services, runner or operation registry."""

    def __init__(self, note):
        self.note = note
        self._lock = threading.RLock()
        self._revision = 0
        # What MO2 currently holds running: path -> {'n': how many times, 'mine': ours?}.
        # Filled by MO2's own callbacks, so it covers both what the bridge launched and what
        # the user launched with a button in the window.
        self._records = {}
        # Raised for the duration of our own startApplication, to tell our launch from a
        # foreign one.
        self._own_start = False

    def observe(self):
        """A revision and detached records, safe to inspect without holding a lock."""
        with self._lock:
            return self._revision, {key: dict(record) for key, record in self._records.items()}

    def snapshot(self):
        return self.observe()[1]

    def replace(self, records):
        """Compatibility for callers which seed bookkeeping in offline checks."""
        copied = {key: dict(record) for key, record in records.items()}
        with self._lock:
            self._records = copied
            self._revision += 1

    def set_starting(self, value):
        with self._lock:
            previous, self._own_start = self._own_start, bool(value)
            return previous

    def is_starting(self):
        with self._lock:
            return self._own_start

    def reset(self):
        """Explicit local UI/host shutdown reset; never exposed as an API route."""
        with self._lock:
            self._records.clear()
            self._revision += 1

    def remove_observed(self, revision, keys):
        """Retire own records only when process evidence still describes this revision."""
        with self._lock:
            removed = []
            if revision == self._revision:
                for key in keys:
                    if self._records.get(key, {}).get('mine'):
                        del self._records[key]
                        removed.append(key)
                if removed:
                    self._revision += 1
            return removed

    launched = property(snapshot, replace)
    _starting = property(is_starting, set_starting)

    # ================================================== MO2 callbacks
    def on_about_to_run(self, path, *rest):
        """MO2 says it is starting a program. Return True or the launch is cancelled.

        The callback has two signatures: the old one takes a single path, the new one adds
        the working directory and the arguments. Which one a given MO2 build will use is not
        known in advance, so the extras are absorbed into *rest rather than guessed from the
        version.
        """
        key = str(path)
        with self._lock:
            starting = self._own_start
            rec = self._records.get(key)
            if rec is None:
                self._records[key] = {'n': 1, 'mine': starting}
            else:
                rec['n'] += 1
                # A path can represent both native and bridge launches. Once a
                # foreign launch contributes evidence, only native finish/reset
                # may release it; finish callbacks cannot identify which run ended.
                rec['mine'] = rec['mine'] and starting
            self._revision += 1
        self.note(i18n.t('log.mo2Starting', path=key,
                         mine=i18n.t('log.oursSuffix') if starting else ''))
        return True

    def on_finished_run(self, path, exit_code=0):
        """The program finished - drop it from the bookkeeping."""
        key = str(path)
        with self._lock:
            rec = self._records.get(key)
            if rec and rec['n'] > 1:
                rec['n'] -= 1
            else:
                self._records.pop(key, None)
            self._revision += 1
            left = len(self._records)
        self.note(i18n.t('log.mo2Finished', path=key, code=exit_code,
                         left=left))


class BusyGuard(object):
    """The three sources of busy, using host-lifetime launch bookkeeping."""

    def __init__(self, ctx, launches=None):
        self.ctx = ctx
        self.o = ctx.o
        self.run_main = ctx.run_main
        self.note = ctx.note
        self.launches = launches if launches is not None else LaunchState(self.note)
        # Cache these per HTTP boot, without tying the retained evidence to its Context.
        self.game_exe = None
        self._self_hwnd = None

    launched = property(lambda s: s.launches.launched,
                        lambda s, v: setattr(s.launches, 'launched', v))
    _starting = property(lambda s: s.launches._starting,
                         lambda s, v: setattr(s.launches, '_starting', v))

    def on_about_to_run(self, path, *rest):
        return self.launches.on_about_to_run(path, *rest)

    def on_finished_run(self, path, exit_code=0):
        return self.launches.on_finished_run(path, exit_code)

    def prime(self):
        """Remember the game's executable name. Called FROM the main thread, directly."""
        try:
            self.game_exe = os.path.basename(
                (self.o.managedGame().binaryName() or '').strip()).lower()
        except Exception:
            self.game_exe = ''
        # Remember the window at the same time: we are on the main thread, no hurry.
        if self._self_hwnd is None:
            try:
                self._self_hwnd = winapi.main_window(os.getpid())
            except Exception:
                self._self_hwnd = 0
        return self.game_exe

    class Starting(object):
        """The "this launch is ours" flag, for the span of one startApplication.

        It lives inside a single main-thread job: MO2 calls onAboutToRun synchronously from
        startApplication, and jobs run one at a time, so the flag cannot confuse our launch
        with somebody else's.
        """

        def __init__(self, guard):
            self.guard = guard

        def __enter__(self):
            self.previous = self.guard.launches.set_starting(True)
            return self

        def __exit__(self, *_exc):
            self.guard.launches.set_starting(self.previous)
            return False

    def starting(self):
        return BusyGuard.Starting(self)

    # ================================================== the three sources
    def game_binary(self):
        """The game name from cache. If it was never cached - ask, but not for long."""
        if self.game_exe is None:
            try:
                name = self.run_main(lambda: self.o.managedGame().binaryName(),
                                     timeout=self.ctx.cfg.timeout('gameName'))
                self.game_exe = os.path.basename((name or '').strip()).lower()
            except Exception:
                return ''
        return self.game_exe

    def busy(self, refresh_game=True):
        """Who is holding MO2 right now, or None.

        MO2's callbacks know about launches through it but will not survive a missed
        onFinishedRun; process enumeration knows the truth about live programs but cannot by
        itself tell a launch through MO2 from one beside it. Together they give both, and the
        bookkeeping heals itself of stuck records.
        """
        # Health reads must never queue an additional native lookup ahead of their
        # own main-thread deadline. Setup writes retain the normal cold-cache lookup.
        game = self.game_binary() if refresh_game else (self.game_exe or '')
        revision, launched = self.launches.observe()
        names = {os.path.basename(k).lower() for k in launched}
        if game:
            names.add(game)
        alive = winapi.pids_by_exe(names) if names else {}
        # Our own launches we retire ourselves: MO2 does not wait for them and never reports
        # their completion, so the record would hang forever. Foreign ones are held until MO2
        # says otherwise - they are the whole protection.
        alive_names = {value.lower() for value in alive.values()}
        missing = [key for key, record in launched.items()
                   if record.get('mine') and os.path.basename(key).lower() not in alive_names]
        gone = self.launches.remove_observed(revision, missing)
        for k in gone:
            self.note(i18n.t('log.ownRunEnded', path=k))
        locked = self.window_locked()
        launched = self.launches.snapshot()
        if not alive and not launched and not locked:
            return None
        exe = sorted(alive.values())[0] if alive else ''
        # viaMO2 answers "does MO2 currently consider a launch active", not "was this exact
        # file started by it". The difference is real: MO2 starts the game through
        # sksevr_loader.exe, which exits at once, leaving SkyrimVR.exe alive - the names do
        # not match even though the launch went through MO2. What it actually started is
        # visible in mo2Run.
        return {'app': exe or None,
                'pids': sorted(alive),
                'isGame': bool(exe) and exe.lower() == game,
                'viaMO2': bool(launched),
                'mo2Run': sorted(launched),
                'windowLocked': locked,
                # MO2 never reported the launch finished and no familiar process is alive:
                # the lock is held by something it never spoke about. That is how a service
                # started by another plugin FROM INSIDE the game hangs around - MO2 did not
                # launch it, but holds it in its job object.
                'heldByUnknown': not alive and (bool(launched) or locked)}

    def window_locked(self):
        """Is MO2's main window disabled. A direct sign of its own lock.

        The third source of truth, and the closest to what a person sees: while MO2 waits for
        a launched program it shows a modal "locked while an application is running" dialog
        and its window stops taking input. Launch bookkeeping and process enumeration can
        both miss; this cannot.

        The window is found without reading its title. GetWindowTextW sends WM_GETTEXT and
        waits on the window's thread, so it would hang in exactly the case this check exists
        for.
        """
        try:
            hwnd = self._self_hwnd
            # The handle is validated, not taken on trust. The window remembered at startup
            # may be destroyed by now - at startup what is visible is the splash screen, not
            # the main window - and IsWindowEnabled on a dead handle answers "disabled". The
            # bridge then considered MO2 busy forever and refused every change.
            if not hwnd or not winapi.is_window(hwnd):
                hwnd = self._self_hwnd = winapi.main_window(os.getpid())
            return bool(hwnd) and not winapi.is_enabled(hwnd)
        except Exception:
            return False

    def refusal(self, op_key):
        """A refusal when MO2 is busy with a running program; None when work may proceed.

        op_key is the i18n key of the operation name, for example 'op.toggle'.
        """
        busy = self.busy()
        if not busy:
            return None
        return {'applied': False, 'busy': True, 'running': busy,
                'blocked': i18n.t(op_key),
                'why': i18n.t('busy.why', app=busy['app']) if busy['app']
                else i18n.t('busy.whyUnknown', run=', '.join(busy['mo2Run']) or '-')}
