# -*- coding: utf-8 -*-
"""The mod list: enable, disable, refresh, and the irreversible three - priority, name,
removal.

The irreversible three require the key from the documentation in the request body. Without
it a route does nothing and returns an explanation and the path to the README: the value can
only be learned by reading the section about the consequences. The busy lock is checked
before the irreversible one.
"""
import io
import os

import mobase

from . import i18n, modui, winapi
from .base import Domain, flag, safe


class ModOps(Domain):

    def __init__(self, ctx, guard, refresh, plugin_snapshot):
        super().__init__(ctx, guard)
        self.remove_native = modui.remove
        self.refresh_tracker = refresh
        self.plugin_snapshot = plugin_snapshot

    def _setup(self):
        ml = self.o.modList()
        return (os.path.realpath(self.o.profile().absolutePath()),
                [(name, bool(ml.state(name) & mobase.ModState.ACTIVE))
                 for name in ml.allModsByProfilePriority()])

    def _modlist_persisted(self, setup):
        ml = self.o.modList()
        expected = [(name, active) for name, active in setup[1]
                    if not ml.getMod(name).isForeign()]
        names = {name for name, _active in expected}
        with io.open(os.path.join(setup[0], 'modlist.txt'), encoding='utf-8-sig') as stream:
            saved = [(line.rstrip('\r\n')[1:], line[0] != '-') for line in stream
                     if line[:1] in ('+', '-') and line.rstrip('\r\n')[1:] in names]
        return list(reversed(saved)) == expected

    def toggle(self, body):
        def prepare():
            if not body.get('mod') or body.get('active') is None:
                raise ValueError(i18n.t('err.needMod'))
            # The value is parsed in the validation step, not only in the work: a refusal
            # about a wrong type must arrive before the main thread, like every input check.
            flag(body, 'active')

        def f():
            mod, want = body.get('mod'), flag(body, 'active')
            ml = self.o.modList()
            if ml.getMod(mod) is None:
                raise ValueError(i18n.t('err.noSuchMod', mod=mod))
            # The previous state is taken BEFORE the edit: there is nothing else to reverse
            # the operation with, and the bridge will not remember it - that is the caller's
            # business.
            was = bool(ml.state(mod) & mobase.ModState.ACTIVE)
            ml.setActive(mod, bool(want))
            return {'mod': mod, 'active': bool(want), 'was': was,
                    'changed': was != bool(want),
                    'undo': {'route': '/toggle', 'body': {'mod': mod, 'active': was}}}
        return self.change('toggle', f, prepare=prepare)

    def refresh(self, _=None):
        """Re-read mods\\ and the profile.

        This is precisely what removes the need to close MO2: a mod folder created behind
        the manager's back joins the list after a refresh and is then enabled by an ordinary
        toggle.
        """
        def f():
            self.o.refresh(True)
            return {'refreshed': True}
        return self.change('refresh', f)

    # ================================================== irreversible
    def mods_priority(self, body):
        def prepare():
            if not body.get('mod') or body.get('priority') is None:
                raise ValueError(i18n.t('err.needMod'))

        mod, prio = body.get('mod'), body.get('priority')

        def f():
            ml = self.o.modList()
            if ml.getMod(mod) is None:
                raise ValueError(i18n.t('err.noSuchMod', mod=mod))
            was = ml.priority(mod)
            # The irreversible lock's refusal carries what would have been done: from where
            # and to where.
            stop = self.danger(body, 'priority')
            if stop:
                stop.update({'mod': mod, 'from': was, 'to': int(prio)})
                return stop
            ok = ml.setPriority(mod, int(prio))
            self.o.refresh(True)
            return {'applied': bool(ok), 'mod': mod, 'from': was, 'to': ml.priority(mod)}
        return self.change('priority', f, prepare=prepare)

    def mods_rename(self, body):
        mod, new = body.get('mod'), body.get('newName')

        def prepare():
            if not mod or not new:
                raise ValueError(i18n.t('err.needMod'))

        def f():
            ml = self.o.modList()
            m = ml.getMod(mod)
            if m is None:
                raise ValueError(i18n.t('err.noSuchMod', mod=mod))
            was_path = safe(m.absolutePath, '')
            nid = safe(m.nexusId, 0)
            # First "does the mod exist", then "is the key there" - as with priority and
            # removal: a mod that does not exist cannot be renamed, and a "no key" refusal
            # on it would be misleading. The refusal carries what would have been done:
            # from where, and which mod.
            stop = self.danger(body, 'rename')
            if stop:
                stop.update({'mod': mod, 'newName': new, 'fromPath': was_path, 'nexusId': nid})
                return stop
            res = ml.renameMod(m, new)
            self.o.refresh(True)
            got = ml.getMod(new)
            # The folder name is how a mod is identified from outside, so nexusId goes back
            # along with the paths: by it the mod is recognisable even once the name is gone.
            return {'applied': res is not None, 'mod': mod, 'newName': new,
                    'fromPath': was_path,
                    'toPath': safe(got.absolutePath, '') if got else '',
                    'nexusId': nid,
                    'undo': {'route': '/mods/rename',
                             'body': {'mod': new, 'newName': mod}}}
        return self.change('rename', f, prepare=prepare)

    def mods_remove(self, body):
        mod = body.get('mod')

        def prepare():
            if not mod:
                raise ValueError(i18n.t('err.needMod'))
            flag(body, 'withArchive')

        def inspect():
            self.ctx.expected(body)
            stop = self.guard.refusal('op.remove')
            if stop:
                return self._refusal(stop, 'remove', 'busy')
            ml = self.o.modList()
            m = ml.getMod(mod)
            if m is None:
                raise ValueError(i18n.t('err.noSuchMod', mod=mod))
            # The card is taken BEFORE the removal and returned whole: afterwards there is
            # nobody left to ask, and bringing the mod back needs everything - which archive
            # it was built from, which version, where it stood and whether it was enabled.
            card = self.removal_card(m, ml)
            stop = self.danger(body, 'remove')
            if stop:
                stop.update({'mod': mod, 'card': card, 'withArchive': flag(body, 'withArchive')})
                return stop
            plugins = self.plugin_snapshot()
            state.update(card=card, setup=self._setup(),
                         plugins={'order': plugins['order'], 'states': plugins['states']},
                         origins={name: self.o.pluginList().origin(name) for name in plugins['order']})
            return None

        state = {'touched': False, 'preservationStarted': False, 'phase': 'inspect'}

        def check(*, own_confirmation=False):
            self.ctx.expected(body)
            if self._setup() != state['setup']:
                raise RuntimeError(i18n.t('err.removeState'))
            plugins = self.plugin_snapshot()
            if {'order': plugins['order'], 'states': plugins['states']} != state['plugins']:
                raise RuntimeError(i18n.t('err.removeState'))
            if not plugins['persisted'] or not self._modlist_persisted(state['setup']):
                raise RuntimeError(i18n.t('err.removePending'))
            stop = self.guard.refusal('op.remove')
            if stop:
                running = stop.get('running') or {}
                # Only modui's already validated exact confirmation may account
                # for this window lock. Launch callbacks/process evidence still holds.
                window_only = (running.get('windowLocked') is True
                    and running.get('app', False) is None
                    and running.get('pids') == [] and running.get('mo2Run') == []
                    and running.get('viaMO2') is False and running.get('isGame') is False
                    and running.get('heldByUnknown') is True)
                if not own_confirmation or not window_only:
                    raise RuntimeError(i18n.t('err.removeState'))

        def refresh(before):
            wait = None
            try:
                def begin():
                    nonlocal wait
                    if before:
                        check()
                        winapi.check_file_replaceable(os.path.join(state['setup'][0], 'modlist.txt'))
                    else:
                        self.ctx.expected(body)
                        if (os.path.realpath(self.o.profile().absolutePath()) != state['setup'][0]
                                or self.guard.refusal('op.refresh')):
                            raise RuntimeError(i18n.t('err.removeState'))
                    self.refresh_tracker.attach(self.o.pluginList())
                    wait = self.refresh_tracker.arm()
                    token = wait
                    if not self.o.onNextRefresh(lambda: self.refresh_tracker.directory(token), False):
                        raise RuntimeError(i18n.t('err.removeRefresh'))
                    if before:
                        state['preservationStarted'] = True
                    self.o.refresh(True)
                self.run_main(begin)
                # Wait outside Qt: CORE completes the directory and plugin lists later.
                if (not wait['event'].wait(self.timeout('modRemoveRefresh'))
                        or not wait['complete']):
                    raise RuntimeError(i18n.t('err.removeRefresh'))
            finally:
                if wait is not None:
                    self.refresh_tracker.release(wait)

        def work():
            if self.ctx.runner is not None:
                from PyQt6.QtCore import QThread
                if QThread.currentThread() == self.ctx.runner.thread():
                    raise RuntimeError(i18n.t('err.removeThread'))
            stop = self.run_main(inspect)
            if stop:
                return stop
            card = state['card']
            out = {'applied': False, 'mod': mod, 'path': card['path'], 'card': card,
                   'archiveKept': True, 'archiveRecycled': False,
                   'setupBefore': {'profile': state['setup'][0], 'mods': state['setup'][1]},
                   'pluginsBefore': state['plugins'],
                   'removalComplete': False, 'undo': None}
            try:
                state['phase'] = 'preserve'
                # Native removeRows cancels the pending writer and reloads modlist.txt.
                # Save unrelated owner edits while indexes are still valid, then revalidate.
                refresh(before=True)
                state['phase'] = 'native-remove'
                def remove():
                    check()
                    receipt = self.remove_native(self.o, mod, card['path'],
                        timeout=self.timeout('modRemoveConfirm'),
                        poll_ms=int(self.cfg.get('modRemovalPollMs')),
                        before_confirm=lambda: check(own_confirmation=True))
                    state['touched'] = bool(receipt.get('invoked'))
                    return receipt
                out['nativeRemoval'] = self.run_main(remove)
                state['phase'] = 'refresh'
                refresh(before=False)
                state['phase'] = 'readback'
                def verify():
                    self.ctx.expected(body)
                    profile, mods = self._setup()
                    expected = [(name, active) for name, active in state['setup'][1] if name != mod]
                    plugins = self.plugin_snapshot()
                    others = [name for name in state['plugins']['order'] if state['origins'][name] != mod]
                    plugins_preserved = ([name for name in plugins['order'] if name in others] == others
                        and all(plugins['states'].get(name) == state['plugins']['states'][name]
                                for name in others)
                        and set(plugins['order']) <= set(state['plugins']['order']))
                    with io.open(os.path.join(profile, 'modlist.txt'), encoding='utf-8-sig') as stream:
                        saved = [line.rstrip('\r\n')[1:] for line in stream if line[:1] in ('+', '-', '*')]
                    out.update(modelRemoved=self.o.modList().getMod(mod) is None,
                               filesystemRemoved=not os.path.lexists(card['path']),
                               profilePersisted=mod not in saved,
                               otherModsPreserved=profile == state['setup'][0] and mods == expected,
                               otherPluginsPreserved=plugins_preserved)
                    return (out['nativeRemoval'].get('removed') is True and
                            all(out[key] for key in ('modelRemoved', 'filesystemRemoved',
                                                   'profilePersisted', 'otherModsPreserved',
                                                   'otherPluginsPreserved')))
                if not self.run_main(verify):
                    raise RuntimeError(i18n.t('err.removeFailed', mod=mod))
                out.update(applied=True, removalComplete=True)
            except Exception as exc:
                receipt = getattr(exc, 'receipt', None)
                if receipt is not None:
                    out['nativeRemoval'] = receipt
                    state['touched'] = state['touched'] or bool(receipt.get('invoked'))
                error = (i18n.t('err.nativeRemove', reason=exc.code)
                         if isinstance(exc, modui.NativeRemovalError) else str(exc))
                out.update(failed=True, partial=state['touched'] or state['preservationStarted'],
                           preservationStarted=state['preservationStarted'], reason='removal',
                           phase=state['phase'], error=error, reconciliationRequired=True)
                return out
            if flag(body, 'withArchive') and card.get('archivePath'):
                # The archive is the one thing the bridge deletes itself, so only to the
                # Recycle Bin.
                def recycle_archive():
                    self.ctx.expected(body)
                    profile, mods = self._setup()
                    expected = [(name, active) for name, active in state['setup'][1] if name != mod]
                    if (profile != state['setup'][0] or mods != expected
                            or self.guard.refusal('op.remove')):
                        raise RuntimeError(i18n.t('err.removeState'))
                    return bool(safe(lambda: winapi.recycle(card['archivePath']), False))
                try:
                    out['archiveRecycled'] = self.run_main(recycle_archive)
                except Exception as exc:
                    out.update(applied=False, failed=True, partial=True, reason='removal',
                               phase='archive', error=str(exc), reconciliationRequired=True)
                out['archiveKept'] = not out['archiveRecycled']
            out['undo'] = ({'route': '/install',
                            'body': {'archive': card.get('archivePath'), 'name': mod}}
                           if out['archiveKept'] and card.get('archivePath') else None)
            return out
        return self.change('remove', work, prepare=prepare, on_main=False)

    def removal_card(self, m, ml):
        """Everything needed to bring the mod back. Taken before removal, on the main thread."""
        name = m.name()
        arc = safe(m.installationFile, '') or ''
        arc_path = ''
        if arc:
            cand = os.path.join(safe(self.o.downloadsPath, '') or '', arc)
            arc_path = cand if os.path.isfile(cand) else ''
        st = ml.state(name)
        nid = safe(m.nexusId, 0)
        return {'mod': name,
                'path': safe(m.absolutePath, ''),
                'version': safe(lambda: m.version().displayString(), ''),
                'nexusId': nid,
                'url': self.nexus_url(safe(m.gameName, '') or '', nid),
                'categories': list(safe(m.categories, []) or []),
                'notes': safe(m.comments, '') or '',
                'installationFile': arc,
                'archivePath': arc_path,
                'archiveOnDisk': bool(arc_path),
                'priority': ml.priority(name),
                'active': bool(st & mobase.ModState.ACTIVE),
                'files': safe(lambda: sum(len(fs) for _r, _d, fs in
                                          os.walk(m.absolutePath())), -1)}
