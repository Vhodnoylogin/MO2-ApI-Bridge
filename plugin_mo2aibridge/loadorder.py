# -*- coding: utf-8 -*-
"""Plugin order: states and load order, and writing plugins.txt by hand.

Both routes can preview: without apply they change nothing and work even while MO2 is busy.
With apply they are ordinary writes, behind every lock.
"""
import os
import hashlib
import locale
import tempfile
import threading

import mobase

from . import i18n
from .base import Domain, flag, safe, stamp


class OrderRefresh:
    """One host-lifetime native hook, routing only live order verification waits.

    Directory callbacks precede the final PluginList::refresh in MO2. Completion
    requires a subsequent onRefreshed event, emitted after native constraints and
    active load-order indexes have actually been recomputed.
    """
    def __init__(self):
        self.lock = threading.RLock()
        self.attached = False
        self.waits = []

    def attach(self, plugins):
        if not self.attached:
            callback = getattr(plugins, 'onRefreshed', None)
            if callback is None or not callback(self._refreshed):
                raise RuntimeError(i18n.t('err.orderCapability'))
            self.attached = True

    def arm(self):
        wait = {'event': threading.Event(), 'directory': False, 'complete': False}
        with self.lock:
            self.waits.append(wait)
        return wait

    def directory(self, wait):
        with self.lock:
            if any(item is wait for item in self.waits):
                wait['directory'] = True

    def _refreshed(self):
        with self.lock:
            for wait in self.waits:
                if wait['directory']:
                    wait['complete'] = True
                    wait['event'].set()

    def release(self, wait):
        with self.lock:
            self.waits[:] = [item for item in self.waits if item is not wait]

    def cancel(self):
        with self.lock:
            for wait in self.waits:
                wait['event'].set()
            self.waits.clear()


class LoadOrder(Domain):
    def __init__(self, ctx, guard, refresh=None):
        super().__init__(ctx, guard)
        self.order_refresh = refresh if refresh is not None else OrderRefresh()

    def plugins_state(self, body):
        want = body.get('set') or {}
        apply_it = flag(body, 'apply')
        if not isinstance(want, dict) or not want:
            raise ValueError(i18n.t('err.needSet'))
        # Normalize the whole batch before changing memory or touching the profile.
        normalized = {}
        for name, value in want.items():
            if value is None:
                raise ValueError(i18n.t('err.badFlag', key=name, value=value))
            normalized[name] = flag({name: value}, name)
        want = normalized

        def f():
            if apply_it:
                self.ctx.expected(body)
            pl = self.o.pluginList()
            known = set(pl.pluginNames())
            plan, missing = [], []
            for name, active in want.items():
                if name not in known:
                    missing.append(name)
                    continue
                now = bool(pl.state(name) & mobase.PluginState.ACTIVE)
                if now != active:
                    plan.append({'plugin': name, 'from': now, 'to': active})
            if apply_it:
                for p in plan:
                    pl.setState(p['plugin'], mobase.PluginState.ACTIVE if p['to']
                                else mobase.PluginState.INACTIVE)
            res = {'applied': apply_it, 'changes': plan, 'unknown': missing}
            if apply_it and any(name in known for name in want):
                # Retry must reconcile disk even when a previous failed persistence
                # attempt already changed memory and the new plan is empty.
                res['file'] = self.write_plugins_txt(
                    {name.lower(): active for name, active in want.items() if name in known})
                if not res['file']['written'] or res['file'].get('notInFile'):
                    res.update(applied=False, failed=True, partial=True,
                               memoryApplied=bool(plan), persisted=False,
                               reason='persistence', error=i18n.t('err.pluginPersistence'),
                               reconciliationRequired=True,
                               undo={'route': '/plugins/state', 'body': {
                                   'set': {p['plugin']: p['from'] for p in plan}, 'apply': True}})
                else:
                    res['persisted'] = True
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
            # Preserve CRLF and exact recovery bytes, including a possible BOM.
            with open(path, 'rb') as stream:
                original = stream.read()
            raw = original.decode('utf-8-sig', errors='replace')
        except Exception as exc:
            return {'written': False, 'error': str(exc), 'path': path}
        eol = chr(13) + chr(10) if chr(13) + chr(10) in raw else chr(10)
        out, done, matches = [], set(), True
        for line in raw.split(chr(10)):
            bare = line.rstrip(chr(13))
            name = bare[1:] if bare.startswith('*') else bare
            key = name.strip().lower()
            if key in wanted and name.strip():
                matches = matches and bare.startswith('*') == wanted[key]
                out.append(('*' if wanted[key] else '') + name)
                done.add(key)
            else:
                out.append(bare)
        while out and not out[-1]:
            out.pop()
        text = eol.join(out) + eol
        missing = sorted(set(wanted) - done)
        if missing or matches:
            # A converged retry must preserve the reversal bytes of the last actual
            # change. Missing entries are a validation failure, not a reason to save
            # unrelated lines or overwrite that backup.
            return {'written': not missing, 'path': path, 'changed': [],
                    'backup': path + '.bak' if os.path.isfile(path + '.bak') else '',
                    'notInFile': missing, 'unchanged': True}
        # We write through a draft and a swap, not over the original. plugins.txt is the one
        # user file the bridge edits with its own hands, and writing over it meant that any
        # failure during the write (disk full, an antivirus holding the file, the thread cut
        # short as MO2 closes) would leave it truncated or empty. An empty plugins.txt is a
        # profile with every plugin disabled, and a person has nowhere to restore it from.
        #
        # The os.replace swap rules out a torn write by itself, so the copy beside it is not
        # for that but for OUR OWN mistake: if the bridge wrote the wrong states, .bak holds
        # what was there a moment earlier. Publish it only after the main swap succeeds:
        # a failed attempt must preserve the preceding successful change's backup.
        backup = path + '.bak'
        draft, recovery = None, None
        try:
            fd, recovery = tempfile.mkstemp(prefix='.mo2aibridge-', suffix='.recovery',
                                            dir=os.path.dirname(path))
            with os.fdopen(fd, 'wb') as stream:
                stream.write(original)
                stream.flush()
                os.fsync(stream.fileno())
            fd, draft = tempfile.mkstemp(prefix='.mo2aibridge-', suffix='.new',
                                         dir=os.path.dirname(path))
            with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(draft, path)
        except Exception as exc:
            if recovery and os.path.isfile(recovery):
                safe(lambda: os.remove(recovery))
            return {'written': False, 'error': str(exc), 'path': path,
                    'backup': backup if os.path.isfile(backup) else ''}
        finally:
            if draft and os.path.isfile(draft):
                safe(lambda: os.remove(draft))
        result = {'written': True, 'path': path, 'changed': sorted(done),
                  'backup': backup, 'notInFile': []}
        try:
            os.replace(recovery, backup)
            result['backupPublished'] = True
        except Exception as exc:
            # Main persistence succeeded; keep exact reversal bytes under the unique
            # path if publishing the conventional alias fails.
            result.update(backup=recovery, backupPublished=False, backupError=str(exc))
        return result

    def plugins_order(self, body):
        """The full load order, as the list LOOT produced.

        A partial list would send the unmentioned plugins somewhere unpredictable, so an
        incomplete set is rejected outright.
        """
        order = body.get('order') or []
        apply_it = flag(body, 'apply')
        if (not isinstance(order, list) or not order or
                any(not isinstance(n, str) or not n.strip() for n in order)):
            raise ValueError(i18n.t('err.needOrder'))
        if len(set(n.lower() for n in order)) != len(order):
            raise ValueError(i18n.t('err.orderDuplicates'))
        report = self.limit('orderReportLimit')

        def f():
            if apply_it:
                self.ctx.expected(body)
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
            before = sorted(known, key=pl.priority)
            # The previous order is returned IN FULL and always: there is no shorter way to
            # describe it, and without it the operation is irreversible - there are a hundred
            # or so plugins, and nothing else records where each one stood.
            res['before'] = before
            res['undo'] = {'route': '/plugins/order',
                           'body': {'order': before, 'apply': True}}
            return res
        if not apply_it:
            return stamp(self.run_main(f), 'pluginOrder', applied=False)
        return self.change('pluginOrder', lambda: self._apply_order(body, order, f), on_main=False)

    def _order_memory(self):
        pl = self.o.pluginList()
        names = list(pl.pluginNames())
        return {'order': sorted(names, key=pl.priority),
                'states': {n: bool(pl.state(n) & mobase.PluginState.ACTIVE) for n in names},
                'priorities': {n: pl.priority(n) for n in names},
                'loadOrders': {n: pl.loadOrder(n) for n in names}}

    @staticmethod
    def _order_text(raw, system=False):
        if not system:
            return raw.decode('utf-8-sig')
        try:
            from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QStringConverter, QTextStream
        except ImportError:
            # Standalone test doubles have no Qt. MO2 itself always supplies Qt.
            return raw.decode('utf-8-sig' if raw.startswith(b'\xef\xbb\xbf')
                              else locale.getpreferredencoding(False))
        buffer = QBuffer()
        buffer.setData(QByteArray(raw))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        stream = QTextStream(buffer)
        stream.setEncoding(QStringConverter.Encoding.System)
        text = stream.readAll()
        buffer.close()
        return text

    def _order_disk(self, root, order, states):
        result = {'verified': False, 'files': {}}
        try:
            lines = {}
            for name in ('loadorder.txt', 'plugins.txt'):
                path = os.path.join(root, name)
                with open(path, 'rb') as stream:
                    raw = stream.read()
                result['files'][name] = {'path': path, 'bytes': len(raw),
                                         'sha256': hashlib.sha256(raw).hexdigest()}
                lines[name] = [line.strip() for line in self._order_text(
                    raw, system=name == 'plugins.txt').splitlines()
                    if line.strip() and not line.lstrip().startswith('#')]
            names = {n.lower(): n for n in order}
            disk_order = [names[n.lower()] for n in lines['loadorder.txt']]
            primary = {n.lower() for n in self.o.managedGame().primaryPlugins()}
            rows = [(names[n.lstrip('*').lower()], n.startswith('*'))
                    for n in lines['plugins.txt']]
            listed = [name for name, _active in rows]
            non_primary = [n for n in order if n.lower() not in primary]
            active_non_primary = [n for n in non_primary if states[n]]
            has_markers = any(active for _name, active in rows)
            if has_markers:
                states_match = all(states[name] == active for name, active in rows)
                members_match = set(non_primary) <= set(listed)
                style = 'starred'
            else:
                # Gamebryo uses active-only plain names; Creation also retains inactive
                # names, but then every non-primary state must be inactive to omit '*'.
                style = 'plain'
                members_match = ((set(listed) - {n for n in order if n.lower() in primary})
                                 == set(active_non_primary))
                states_match = all(states[n] for n in listed)
                if set(non_primary) <= set(listed) and not any(states[n] for n in non_primary):
                    members_match = True
                    states_match = all(not states[n] for n in listed if n.lower() not in primary)
                    style = 'starred-inactive'
            result.update(order=disk_order, plugins=listed, format=style,
                          statesVerified=states_match)
            result['verified'] = (disk_order == order and len(listed) == len(set(listed))
                and listed == [n for n in order if n in set(listed)]
                and members_match and states_match)
        except Exception as exc:
            result['error'] = str(exc)
        return result

    def _apply_order(self, body, order, prepare):
        """Persist through GamePlugins, then wait OFF Qt for final native refresh.

        setLoadOrder changes priorities only. Neither it nor refresh(True) saves
        those priorities; native writers also return void on some silent failures.
        Exact disk and post-refresh evidence decide the operation's terminal state.
        """
        runner = self.ctx.runner
        if runner is not None and callable(getattr(runner, 'thread', None)):
            from PyQt6.QtCore import QThread
            if QThread.currentThread() is runner.thread():
                raise ValueError(i18n.t('err.orderWorker'))
        state = {'touched': False, 'phase': 'prepare'}
        result = None
        wait = None

        def target():
            self.ctx.expected(body)
            if os.path.normcase(os.path.realpath(self.o.profile().absolutePath())) != state['root']:
                raise RuntimeError(i18n.t('err.orderTarget'))

        def observe(final=False):
            target()
            memory = self._order_memory()
            result.update(after=memory['order'], statesAfter=memory['states'],
                          loadOrdersAfter=memory['loadOrders'])
            disk = self._order_disk(state['root'], order, result['statesBefore'])
            result['filesAfter'] = disk
            result['persisted'] = disk['verified']
            memory_ok = (memory['order'] == order and memory['states'] == result['statesBefore']
                         and sorted(memory['priorities'].values()) == list(range(len(order))))
            result['memoryApplied'] = memory_ok
            if final:
                active = [n for n in order if result['statesBefore'][n]]
                memory_ok = memory_ok and all(memory['loadOrders'][n] == (
                    active.index(n) if n in active else -1) for n in order)
            if not memory_ok or not disk['verified']:
                raise RuntimeError(i18n.t('err.orderPersistence'))

        def begin():
            nonlocal result, wait
            result = prepare()
            if 'before' not in result:
                return False
            stop = self.guard.refusal('op.pluginOrder')
            if stop:
                result = self._refusal(stop, 'pluginOrder', 'busy')
                return False
            self.ctx.expected(body)
            state['root'] = os.path.normcase(os.path.realpath(self.o.profile().absolutePath()))
            memory = self._order_memory()
            result.update(statesBefore=memory['states'], persisted=False, memoryApplied=False,
                          nativeRoundtripVerified=False, refreshRequested=False, refreshed=False,
                          undoState={'route': '/plugins/state', 'body': {
                              'set': memory['states'], 'apply': True}}, filesBefore={})
            feature = self.o.gameFeatures().gameFeature(mobase.GamePlugins)
            if (feature is None or not callable(getattr(feature, 'writePluginLists', None))
                    or not callable(getattr(feature, 'readPluginLists', None))
                    or not callable(getattr(self.o, 'onNextRefresh', None))):
                raise RuntimeError(i18n.t('err.orderCapability'))
            self.order_refresh.attach(self.o.pluginList())
            # Save exact reversal bytes before either native file can be changed.
            state['phase'] = 'snapshot'
            for name in ('loadorder.txt', 'plugins.txt'):
                path = os.path.join(state['root'], name)
                with open(path, 'rb') as stream:
                    original = stream.read()
                fd, recovery = tempfile.mkstemp(prefix='.mo2aibridge-order-',
                    suffix='-' + name + '.recovery', dir=state['root'])
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(original)
                    stream.flush()
                    os.fsync(stream.fileno())
                result['filesBefore'][name] = {'path': path, 'recovery': recovery,
                    'sha256': hashlib.sha256(original).hexdigest(), 'bytes': len(original)}
            target()
            if self.guard.refusal('op.pluginOrder'):
                raise RuntimeError(i18n.t('err.orderPersistence'))
            state.update(touched=True, phase='set-order')
            self.o.pluginList().setLoadOrder(order)
            current = self._order_memory()
            if current['order'] != order or current['states'] != memory['states']:
                raise RuntimeError(i18n.t('err.orderPersistence'))
            result['memoryApplied'] = True
            state['phase'] = 'native-write'
            target()
            if self.guard.refusal('op.pluginOrder'):
                raise RuntimeError(i18n.t('err.orderPersistence'))
            feature.writePluginLists(self.o.pluginList())
            target()
            state['phase'] = 'disk-readback'
            result['filesAfter'] = self._order_disk(state['root'], order, memory['states'])
            result['persisted'] = result['filesAfter']['verified']
            if not result['persisted']:
                raise RuntimeError(i18n.t('err.orderPersistence'))
            state['phase'] = 'native-readback'
            target()
            if self.guard.refusal('op.pluginOrder'):
                raise RuntimeError(i18n.t('err.orderPersistence'))
            feature.readPluginLists(self.o.pluginList())
            observe()
            result['nativeRoundtripVerified'] = True
            state['phase'] = 'refresh'
            target()
            if self.guard.refusal('op.refresh'):
                raise RuntimeError(i18n.t('err.orderPersistence'))
            wait = self.order_refresh.arm()
            refresh, token = self.order_refresh, wait
            def completed():
                # Do not complete here: another external callback may enter a
                # nested Qt loop before CORE applies its final native constraints.
                refresh.directory(token)
            if not self.o.onNextRefresh(completed, False):
                raise RuntimeError(i18n.t('err.orderCapability'))
            target()
            if self.guard.refusal('op.refresh'):
                raise RuntimeError(i18n.t('err.orderPersistence'))
            result['refreshRequested'] = True
            self.o.refresh(True)
            return True

        try:
            if not self.run_main(begin):
                return result
            state['phase'] = 'refresh-wait'
            if not wait['event'].wait(self.timeout('pluginOrderRefresh')):
                raise RuntimeError(i18n.t('err.orderRefreshTimeout'))
            if not wait['complete']:
                raise RuntimeError(i18n.t('err.orderPersistence'))
            state['phase'] = 'refresh-readback'
            def final():
                result['refreshed'] = True
                observe(final=True)
            self.run_main(final)
            result['applied'] = True
            return result
        except Exception as exc:
            if result is None or ('before' not in result):
                raise
            result.update(applied=False, failed=True, partial=state['touched'],
                          reconciliationRequired=True, phase=state['phase'], error=str(exc))
            # Capture actual state without any repair/rollback or an additional native write.
            # A profile switch must not turn this into a read or mutation of its replacement.
            if state['touched'] and 'root' in state and state['phase'] != 'refresh-wait':
                try:
                    def actual():
                        target()
                        memory = self._order_memory()
                        result.update(after=memory['order'], statesAfter=memory['states'])
                        result['memoryApplied'] = (memory['order'] == order and
                                                  memory['states'] == result['statesBefore'])
                        result['filesAfter'] = self._order_disk(state['root'], order, result['statesBefore'])
                        result['persisted'] = result['filesAfter']['verified']
                    self.run_main(actual)
                except Exception as read_error:
                    result['readbackError'] = str(read_error)
            return result
        finally:
            if wait is not None:
                self.order_refresh.release(wait)
