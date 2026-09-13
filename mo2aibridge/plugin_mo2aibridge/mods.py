# -*- coding: utf-8 -*-
"""The mod list: enable, disable, refresh, and the irreversible three - priority, name,
removal.

The irreversible three require the key from the documentation in the request body. Without
it a route does nothing and returns an explanation and the path to the README: the value can
only be learned by reading the section about the consequences. The busy lock is checked
before the irreversible one.
"""
import os

import mobase

from . import i18n, winapi
from .base import Domain, flag, safe


class ModOps(Domain):

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

        def f():
            # Parsed inside the work rather than on entry to the method: the busy lock must
            # answer first, and an exception before change() would beat it to it.
            with_archive = flag(body, 'withArchive')
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
                stop.update({'mod': mod, 'card': card, 'withArchive': with_archive})
                return stop
            ok = ml.removeMod(m)
            self.o.refresh(True)
            out = {'applied': bool(ok), 'mod': mod, 'path': card['path'],
                   'card': card, 'archiveKept': True, 'archiveRecycled': False}
            if with_archive and card.get('archivePath'):
                # The archive is the one thing the bridge deletes itself, so only to the
                # Recycle Bin.
                out['archiveRecycled'] = bool(safe(
                    lambda: winapi.recycle(card['archivePath']), False))
                out['archiveKept'] = not out['archiveRecycled']
            out['undo'] = ({'route': '/install',
                            'body': {'archive': card.get('archivePath'), 'name': mod}}
                           if out['archiveKept'] and card.get('archivePath') else None)
            return out
        return self.change('remove', f, prepare=prepare)

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
