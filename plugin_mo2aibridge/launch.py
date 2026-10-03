# -*- coding: utf-8 -*-
"""Launching programs inside the VFS, and driving their windows.

Launching is the bridge's most dangerous operation: a foreign process is raised under MO2
with the virtual Data mounted. It sits behind the busy lock like every other change, and
behind the irreversible key as well. The window work exists for WinAPI tools (the DynDOLOD
and TexGen dialogs): closing them through the bridge is what releases the busy state.
"""
import os

from . import i18n, winapi
from .base import Domain, flag, one, safe


class Launcher(Domain):

    def __init__(self, ctx, guard):
        super().__init__(ctx, guard)
        # What the bridge launched this session: key -> (handle, pid, name)
        self.procs = {}
        self.seq = 0

    def run(self, body):
        """Launch a tool inside the VFS.

        binary is the executable's name AS REGISTERED in MO2, not a path: given a full path,
        startApplication silently creates no process at all.
        """
        def prepare():
            if not body.get('binary'):
                raise ValueError(i18n.t('err.needBinary'))
            flag(body, 'wait')
            # Launching is the bridge's most dangerous operation: a foreign process comes up
            # under MO2 with the virtual Data, and what it does to the setup is beyond the
            # bridge's control. So on top of the busy lock there is the irreversible key,
            # the same one removal uses.
            stop = self.danger(body, 'run')
            if stop:
                stop.update({'binary': body.get('binary'), 'args': body.get('args') or []})
            return stop

        return self.change('run', lambda: self._run(body), on_main=False, prepare=prepare)

    def _run(self, body):
        binary = body.get('binary')
        args = body.get('args') or []
        cwd = body.get('cwd') or ''
        wait = flag(body, 'wait')

        def start():
            # The flag lives inside a single main-thread job: MO2 calls onAboutToRun
            # synchronously from startApplication, and jobs run one at a time.
            with self.guard.starting():
                return self.o.startApplication(binary, args, cwd)
        handle = self.run_main(start)
        self.seq += 1
        key = 'p%d' % self.seq
        pid = winapi.process_id(handle) if handle else 0
        self.procs[key] = (handle, pid, os.path.basename(str(binary)))
        # `started` says whether MO2 handed back a handle at all: given a full path instead
        # of a registered name, startApplication silently returns nothing, and that used to
        # look exactly like a successful launch.
        res = {'key': key, 'pid': pid, 'binary': binary, 'args': args,
               'started': bool(handle)}
        if wait and handle:
            # We wait ourselves rather than through waitForApplication: that one never
            # releases the GIL and stalls the whole interpreter along with the server - the
            # bridge goes silent entirely.
            res['exit'] = winapi.wait_process(
                handle, float(body.get('timeout') or self.timeout('runWait')))
            res['waitedBy'] = 'WaitForSingleObject'
        return res

    def procs_list(self, _=None):
        """What the bridge launched this session - with a mark for whether it is still alive.

        The list accumulates from the first launch and never prunes itself: MO2 does not
        report the completion of our own launches, so there is nothing to wait for. While
        the mark was missing, three long-closed TexGen runs from the checks looked like three
        running programs and raised a false alarm in a neighbouring chat. So liveness is
        asked of the system on every call.
        """
        out = []
        for k, (handle, pid, what) in self.procs.items():
            code = (safe(lambda h=handle: winapi.wait_process(h, 0), 0)
                    if handle else 0)
            out.append({'key': k, 'pid': pid, 'what': what,
                        'alive': code is None, 'exit': code})
        return {'procs': out,
                'running': sum(1 for x in out if x['alive']),
                'launchedByMO2': sorted(self.guard.launched),
                'busy': self.guard.busy()}

    def windows(self, q):
        pid = int(one(q, 'pid', '0') or 0)
        if not pid:
            key = one(q, 'key', '')
            pid = self.procs.get(key, (0, 0, ''))[1]
        if not pid:
            raise ValueError(i18n.t('err.needPidOrKey'))
        return {'pid': pid, 'windows': winapi.windows_of(pid)}

    def window(self, body):
        hwnd = int(body.get('hwnd') or 0)
        action = body.get('action')
        if not hwnd or not winapi.is_window(hwnd):
            raise ValueError(i18n.t('err.noSuchWindow'))
        title = winapi.window_text(hwnd)
        if action == 'close':
            winapi.close(hwnd)
            return {'hwnd': hwnd, 'title': title, 'did': 'close'}
        if action == 'click':
            caption = body.get('button') or ''
            if not caption.strip():
                raise ValueError(i18n.t('err.needButton'))
            hit = winapi.click_by_caption(hwnd, caption)
            if hit is None:
                raise ValueError(i18n.t('err.noButton', button=caption))
            return {'hwnd': hwnd, 'title': title, 'did': 'click', 'button': hit}
        raise ValueError(i18n.t('err.action'))
