# -*- coding: utf-8 -*-
"""Plugin order: states and load order, and writing plugins.txt by hand.

Both routes can preview: without apply they change nothing and work even while MO2 is busy.
With apply they are ordinary writes, behind every lock.
"""
import io
import os
import shutil

import mobase

from . import i18n
from .base import Domain, flag, stamp


class LoadOrder(Domain):

    def plugins_state(self, body):
        want = body.get('set') or {}
        apply_it = flag(body, 'apply')
        if not isinstance(want, dict) or not want:
            raise ValueError(i18n.t('err.needSet'))

        def f():
            pl = self.o.pluginList()
            known = set(pl.pluginNames())
            plan, missing = [], []
            for name, active in want.items():
                if name not in known:
                    missing.append(name)
                    continue
                now = bool(pl.state(name) & mobase.PluginState.ACTIVE)
                if now != bool(active):
                    plan.append({'plugin': name, 'from': now, 'to': bool(active)})
            if apply_it:
                for p in plan:
                    pl.setState(p['plugin'], mobase.PluginState.ACTIVE if p['to']
                                else mobase.PluginState.INACTIVE)
            res = {'applied': apply_it, 'changes': plan, 'unknown': missing}
            if apply_it and plan:
                res['file'] = self.write_plugins_txt(
                    {p['plugin'].lower(): p['to'] for p in plan})
            return res
        # A preview is always available: it changes nothing.
        if not apply_it:
            return stamp(self.run_main(f), 'pluginState', applied=False)
        return self.change('pluginState', f)

    def write_plugins_txt(self, wanted):
        """Set the asterisks in the profile's plugins.txt. Called FROM the main thread.

        setState changes a list in MO2's memory, and MO2 rewrites the file at moments of its
        own choosing - usually on exit. The game starts from the file, and any /refresh
        between the edit and the exit reads the file back, silently undoing the edit. This
        cost three ruined test runs: the bridge answered applied: true while the plugin
        stayed disabled in the game.

        We write by moving the asterisk in existing lines and nothing else. The set of lines
        and their order are not touched at all - that is MO2's business, and rewriting the
        file whole would mean taking on the load order, the esl flag and everything else it
        puts there.
        """
        path = os.path.join(self.o.profile().absolutePath(), 'plugins.txt')
        try:
            # newline='' is mandatory: without it text mode collapses CRLF into LF, the
            # line-ending detection below sees LF, and the file is rewritten in a foreign
            # format.
            raw = io.open(path, encoding='utf-8-sig', errors='replace',
                          newline='').read()
        except Exception as exc:
            return {'written': False, 'error': str(exc), 'path': path}
        eol = chr(13) + chr(10) if chr(13) + chr(10) in raw else chr(10)
        out, done = [], set()
        for line in raw.split(chr(10)):
            bare = line.rstrip(chr(13))
            name = bare[1:] if bare.startswith('*') else bare
            key = name.strip().lower()
            if key in wanted and name.strip():
                out.append(('*' if wanted[key] else '') + name)
                done.add(key)
            else:
                out.append(bare)
        while out and not out[-1]:
            out.pop()
        text = eol.join(out) + eol
        # We write through a draft and a swap, not over the original. plugins.txt is the one
        # user file the bridge edits with its own hands, and writing over it meant that any
        # failure during the write (disk full, an antivirus holding the file, the thread cut
        # short as MO2 closes) would leave it truncated or empty. An empty plugins.txt is a
        # profile with every plugin disabled, and a person has nowhere to restore it from.
        #
        # The os.replace swap rules out a torn write by itself, so the copy beside it is not
        # for that but for OUR OWN mistake: if the bridge wrote the wrong states, .bak holds
        # what was there a moment earlier. Hence refreshing it on every write - a week-old
        # copy would also undo everything the person changed since.
        backup = path + '.bak'
        try:
            shutil.copyfile(path, backup)
            draft = path + '.new'
            io.open(draft, 'w', encoding='utf-8', newline='').write(text)
            os.replace(draft, path)
        except Exception as exc:
            return {'written': False, 'error': str(exc), 'path': path,
                    'backup': backup if os.path.isfile(backup) else ''}
        return {'written': True, 'path': path, 'changed': sorted(done),
                'backup': backup, 'notInFile': sorted(set(wanted) - done)}

    def plugins_order(self, body):
        """The full load order, as the list LOOT produced.

        A partial list would send the unmentioned plugins somewhere unpredictable, so an
        incomplete set is rejected outright.
        """
        order = body.get('order') or []
        apply_it = flag(body, 'apply')
        if not isinstance(order, list) or not order:
            raise ValueError(i18n.t('err.needOrder'))
        report = self.limit('orderReportLimit')

        def f():
            pl = self.o.pluginList()
            known = list(pl.pluginNames())
            missing = sorted(set(known) - set(order))
            extra = sorted(set(order) - set(known))
            res = {'applied': False, 'count': len(order),
                   'notListed': missing[:report], 'unknown': extra[:report]}
            if missing or extra:
                res['error'] = i18n.t('err.orderIncomplete',
                                      missing=len(missing), extra=len(extra))
                return res
            before = sorted(known, key=lambda n: pl.loadOrder(n)
                            if pl.loadOrder(n) >= 0 else 10 ** 6)
            # The previous order is returned IN FULL and always: there is no shorter way to
            # describe it, and without it the operation is irreversible - there are a hundred
            # or so plugins, and nothing else records where each one stood.
            res['before'] = before
            res['undo'] = {'route': '/plugins/order',
                           'body': {'order': before, 'apply': True}}
            if apply_it:
                pl.setLoadOrder(order)
                self.o.refresh(True)
                res['applied'] = True
            return res
        if not apply_it:
            return stamp(self.run_main(f), 'pluginOrder', applied=False)
        return self.change('pluginOrder', f)
