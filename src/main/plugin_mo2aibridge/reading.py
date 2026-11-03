# -*- coding: utf-8 -*-
"""Reads of state and of the virtual Data. Changes nothing and always works, even while MO2
is busy with a running program.

Everything here answers the question "what is there right now": a mod's card and analysis,
the lists of mods, profiles and plugins, the files of the virtual Data and who provides
them, and a full VFS export.
"""
import os
import json
import hashlib

import mobase

from . import i18n
from .base import Domain, one, safe
from .provenance import provenance_status


def diagnostic_error(report, phase, path, error, limit):
    """Retain bounded evidence while counting every incomplete diagnostic step."""
    if report is None:
        return
    report['errorCount'] += 1
    if len(report['errors']) < max(0, limit):
        report['errors'].append({'phase': phase, 'path': str(path), 'error': str(error)})


def installed_file_metadata(path, limit):
    """Read only the bounded native source-ID array; never inspect payload or downloads."""
    if not path or not os.path.isfile(os.path.join(path, 'meta.ini')):
        return None, 'native-metadata-unavailable'
    try:
        from PyQt6.QtCore import QSettings
        metadata = QSettings(os.path.join(path, 'meta.ini'), QSettings.Format.IniFormat)
        metadata.setFallbacksEnabled(False)
        if metadata.status() != QSettings.Status.NoError:
            return None, 'native-metadata-unreadable'
        if not metadata.contains('installedFiles/size'):
            return None, 'installed-files-unavailable'
        declared = metadata.value('installedFiles/size')
        if isinstance(declared, bool) or not isinstance(declared, (int, str)):
            return None, 'installed-files-invalid'
        declared = int(declared)
        if declared < 0 or declared > max(0, limit):
            return None, 'installed-files-limit'
        count = metadata.beginReadArray('installedFiles')
        try:
            if count != declared:
                return None, 'installed-files-invalid'
            rows = []
            for index in range(count):
                metadata.setArrayIndex(index)
                rows.append({'modId': metadata.value('modid'),
                             'fileId': metadata.value('fileid')})
        finally:
            metadata.endArray()
        if metadata.status() != QSettings.Status.NoError:
            return None, 'native-metadata-unreadable'
        return rows, None
    except Exception:
        return None, 'native-metadata-unavailable'


def read_provenance(raw, current, path, limit, unavailable=False):
    """Keep the existing decoded record while adding an explicitly limited assessment."""
    if unavailable:
        record, record_error = None, 'setting-unavailable'
    elif raw is None:
        record, record_error = None, None
    else:
        try:
            record, record_error = json.loads(raw), None
        except Exception:
            record, record_error = None, 'invalid-json'
    installed_files, metadata_error = None, None
    snapshot = record.get('nativeMetadata') if isinstance(record, dict) else None
    if isinstance(snapshot, dict):
        installed_files, metadata_error = installed_file_metadata(path, limit)
    status = provenance_status(record, current, installed_files, metadata_error, limit)
    if record_error:
        status.update(state='unknown' if unavailable else 'malformed', reasons=[record_error])
    return record, status


class Reader(Domain):

    def native_identity(self, name, include_order=False):
        """Observe native identity/order on Qt; this is not an external-writer revision."""
        profile, ml = self.o.profile(), self.o.modList()
        mod = ml.getMod(name)
        state = ml.state(name) if mod is not None else 0
        target = ({'path': safe(mod.absolutePath, ''),
                   'active': bool(state & mobase.ModState.ACTIVE),
                   'essential': bool(state & mobase.ModState.ESSENTIAL), 'priority': ml.priority(name),
                   'installationFile': safe(mod.installationFile),
                   'version': safe(lambda: mod.version().displayString()),
                   'nexusId': safe(mod.nexusId), 'gameName': safe(mod.gameName)}
                  if mod is not None else None)
        identity = {'serverBootId': self.ctx.boot_id, 'instanceId': self.ctx.instance_id,
                    'profile': profile.name(), 'profilePath': os.path.realpath(profile.absolutePath()),
                    'modsPath': os.path.realpath(self.o.modsPath()), 'mod': name, 'target': target}
        if include_order:
            order = []
            for mod_name in ml.allModsByProfilePriority():
                state = ml.state(mod_name)
                order.append((mod_name, bool(state & mobase.ModState.ACTIVE),
                              bool(state & mobase.ModState.ESSENTIAL), ml.priority(mod_name)))
            identity.update(modCount=len(order), modOrderSha256=hashlib.sha256(
                json.dumps(order, ensure_ascii=False).encode('utf-8')).hexdigest())
        return identity

    @staticmethod
    def snapshot_evidence(observations):
        """Report observed divergence separately from existing collection-completeness flags."""
        values = list(observations.values())
        observed = [value for value in values if value is not None]
        # Order is sampled only at analysis boundaries, rather than walking the whole
        # mod list for ordinary /mod cards or for both sides of its short native job.
        fields = set().union(*(value.keys() for value in observed))
        diverged = False
        for field in fields:
            samples = [value[field] for value in observed if field in value]
            if any(sample != samples[0] for sample in samples[1:]):
                diverged = True
                break
        state = ('diverged' if diverged else 'unknown' if len(observed) != len(values) or not values
                 else 'no-observed-divergence')
        return {'state': state, 'observations': observations,
                'nativeAtomic': False, 'diskAtomic': False, 'externalWritersTracked': False,
                'unobservedIntermediateChangesPossible': True}

    # ================================================== state
    def ping(self, _=None):
        def f():
            # A cold cache is warmed inside this one main-thread job, never by
            # a separate ten-second wait in the HTTP thread's busy check.
            if self.guard.game_exe is None:
                self.guard.prime()
            return {'ok': True,
                    'profile': self.o.profile().name(),
                    'game': self.o.managedGame().gameShortName(),
                    'mo2Version': self.o.appVersion().displayString(),
                    'modsPath': self.o.modsPath(),
                    'overwrite': self.o.overwritePath(),
                    'downloads': self.o.downloadsPath()}
        # Busy is determined first and without the main thread: this is the very call needed
        # when MO2 is not responding, and it has to answer quickly.
        res = {'busy': self.guard.busy(refresh_game=False)}
        try:
            res.update(self.run_main(f, timeout=self.timeout('ping')))
            # The first successful native read may have discovered the game name.
            res['busy'] = self.guard.busy(refresh_game=False)
            res['mainThread'] = 'ok'
        except Exception as exc:
            # An unresponsive main thread is an answer, not an error: it means MO2 is busy
            # with something.
            res['ok'] = False
            res['mainThread'] = str(exc)
        # Null busy with an unavailable executable cache is not proof of idleness.
        res['busyKnown'] = bool(self.guard.game_exe)
        return res

    def api(self, _=None):
        """What this MO2 build can actually do - without guessing from the documentation."""
        def f():
            out = {}
            for label, obj in (('IOrganizer', self.o), ('IModList', self.o.modList()),
                               ('IPluginList', self.o.pluginList()),
                               ('IProfile', self.o.profile())):
                try:
                    out[label] = sorted(x for x in dir(obj) if not x.startswith('_'))
                except Exception as exc:
                    out[label] = ['error: %s' % exc]
            return out
        return self.run_main(f)

    def mods(self, _=None):
        def f():
            ml = self.o.modList()
            out = []
            for name in ml.allModsByProfilePriority():
                st = ml.state(name)
                out.append({'mod': name,
                            'active': bool(st & mobase.ModState.ACTIVE),
                            'essential': bool(st & mobase.ModState.ESSENTIAL),
                            'priority': ml.priority(name)})
            return {'count': len(out), 'mods': out}
        return self.run_main(f)

    def profiles(self, _=None):
        def f():
            root = os.path.dirname(self.o.profile().absolutePath())
            names = sorted(x for x in os.listdir(root) if os.path.isdir(os.path.join(root, x)))
            return {'current': self.o.profile().name(), 'profiles': names, 'path': root}
        return self.run_main(f)

    def plugins(self, _=None):
        """The load order and everything MO2 knows about the plugins.

        Otherwise this has to be stitched together by hand from plugins.txt, loadorder.txt
        and the TES4 headers - and the esl flag and the "has no records" mark are not in
        those files at all.
        """
        def f():
            pl = self.o.pluginList()
            out = []
            for n in pl.pluginNames():
                st = pl.state(n)
                out.append({'plugin': n,
                            'active': bool(st & mobase.PluginState.ACTIVE),
                            'priority': pl.priority(n),
                            'loadOrder': pl.loadOrder(n),
                            'origin': pl.origin(n),
                            # isMaster is deprecated and writes a warning into MO2's log for
                            # every plugin on every /plugins call
                            'master': pl.isMasterFlagged(n) or pl.hasMasterExtension(n),
                            'esl': pl.isLightFlagged(n) or pl.hasLightExtension(n),
                            'empty': pl.hasNoRecords(n),
                            'masters': list(pl.masters(n))})
            out.sort(key=lambda x: x['loadOrder'] if x['loadOrder'] >= 0 else 10 ** 6)
            return {'count': len(out), 'plugins': out}
        return self.run_main(f)

    # ================================================== card and analysis
    def mod(self, q, *, snapshot_order=False):
        """Everything MO2 knows about a mod. Three of these fields are not in meta.ini at all:
        isSeparator (otherwise deduced from a name suffix, that is, from a convention),
        categories (MO2 keeps its own categories separately) and ignoredVersion with endorsed
        (which updates the user has already dismissed).
        """
        name = one(q, 'name', '')
        if not name:
            raise ValueError(i18n.t('err.needName'))

        def f():
            before = self.native_identity(name)
            ml = self.o.modList()
            m = ml.getMod(name)
            if m is None:
                raise ValueError(i18n.t('err.noSuchMod', mod=name))
            st = ml.state(name)
            current = {'nexusId': safe(lambda: m.nexusId()),
                       'gameName': safe(lambda: m.gameName()),
                       'version': safe(lambda: m.version().displayString()),
                       'installationFile': safe(lambda: m.installationFile())}
            nid = current['nexusId'] if current['nexusId'] is not None else 0
            game = current['gameName'] or ''
            own_url = safe(m.url, '') or ''
            url = self.nexus_url(game, nid) or own_url
            try:
                raw, unavailable = m.pluginSetting('mo2aibridge', 'installProvenance', 'null'), False
            except Exception:
                raw, unavailable = None, True
            card = {'mod': name,
                    'displayName': safe(lambda: ml.displayName(name), name),
                    'path': safe(m.absolutePath, ''),
                    'active': bool(st & mobase.ModState.ACTIVE),
                    'essential': bool(st & mobase.ModState.ESSENTIAL),
                    'priority': ml.priority(name),
                    'isSeparator': safe(m.isSeparator, False),
                    'isForeign': safe(m.isForeign, False),
                    'isBackup': safe(m.isBackup, False),
                    'isOverwrite': safe(m.isOverwrite, False),
                    'nexusId': nid, 'url': url, 'urlFromMO2': own_url, 'gameName': game,
                    'version': current['version'] or '',
                    'newestVersion': safe(lambda: m.newestVersion().displayString(), ''),
                    'ignoredVersion': safe(lambda: m.ignoredVersion().displayString(), ''),
                    'installationFile': current['installationFile'] or '',
                    'categories': list(safe(m.categories, []) or []),
                    'primaryCategory': safe(m.primaryCategory, 0),
                    'notes': safe(m.notes, ''), 'comments': safe(m.comments, ''),
                    'endorsed': str(safe(m.endorsedState, '')),
                    'tracked': str(safe(m.trackedState, ''))}
            card['snapshot'] = self.snapshot_evidence(
                {'before': before, 'after': self.native_identity(name, snapshot_order)})
            return card, raw, current, unavailable
        card, raw, current, unavailable = self.run_main(f)
        # The Qt job only snapshots native getters. JSON parsing and bounded INI
        # evidence stay outside it, and neither can certify the archive or payload.
        card['installProvenance'], card['installProvenanceStatus'] = read_provenance(
            raw, current, card['path'], self.limit(), unavailable)
        return card

    def analyze(self, q):
        """A mod analysed in one call: the card, the files, the plugins and who contests what.

        Unlike a calculation over the mod list this is the real order of the active profile.
        conflicts=0 switches off the most expensive part - checking every file.
        """
        name = one(q, 'name', '')
        if not name:
            raise ValueError(i18n.t('err.needName'))
        want_conf = one(q, 'conflicts', '1') != '0'
        limit = int(one(q, 'limit', str(self.limit('analyzeLimit'))))
        diagnostics = {'errorCount': 0, 'errors': []}
        error_limit = self.limit()

        def f():
            before = self.native_identity(name, include_order=True)
            ml = self.o.modList()
            m = ml.getMod(name)
            if m is None:
                raise ValueError(i18n.t('err.noSuchMod', mod=name))
            root = m.absolutePath()
            files = []
            def inventory_error(exc):
                diagnostic_error(diagnostics, 'inventory', getattr(exc, 'filename', root),
                                 exc, error_limit)
            try:
                for dp, _dn, fs in os.walk(root, onerror=inventory_error):
                    rel_dir = os.path.relpath(dp, root)
                    for x in fs:
                        if x.lower() == 'meta.ini' and rel_dir == '.':
                            continue
                        files.append(x if rel_dir == '.' else os.path.join(rel_dir, x))
            except Exception as exc:
                inventory_error(exc)
            inventory_complete = diagnostics['errorCount'] == 0
            wins, loses, contested, succeeded = [], [], 0, 0
            if want_conf:
                for rel in files:
                    try:
                        org = list(self.o.getFileOrigins(rel))
                    except Exception as exc:
                        diagnostic_error(diagnostics, 'origins', rel, exc, error_limit)
                        continue
                    succeeded += 1
                    if len(org) < 2:
                        continue
                    contested += 1
                    (wins if org[0] == name else loses).append({'file': rel, 'providers': org})
            return {'mod': name, 'path': root, 'files': len(files),
                    'snapshot': self.snapshot_evidence(
                        {'collectionBefore': before,
                         'collectionAfter': self.native_identity(name, include_order=True)}),
                    'plugins': [x for x in files if x.lower().endswith(('.esp', '.esm', '.esl'))],
                    'sample': sorted(files)[:limit],
                    'complete': diagnostics['errorCount'] == 0,
                    'partial': diagnostics['errorCount'] != 0,
                    'inventoryComplete': inventory_complete,
                    'errorCount': diagnostics['errorCount'], 'errors': diagnostics['errors'],
                    'conflicts': {'checked': want_conf, 'contested': contested,
                                  'complete': want_conf and diagnostics['errorCount'] == 0,
                                  'attempted': len(files) if want_conf else 0,
                                  'succeeded': succeeded,
                                  'failed': len(files) - succeeded if want_conf else 0,
                                  'winsCount': len(wins), 'losesCount': len(loses),
                                  'wins': wins[:limit], 'loses': loses[:limit]}}
        res = self.run_main(f, timeout=self.timeout('analyze'))
        # Keep card failures isolated, and assess provenance disk evidence off Qt. The
        # runner supports same-thread calls; separate jobs need identity comparison,
        # because neither their native observations nor the disk reads are a transaction.
        try:
            res['card'] = self.mod({'name': [name]}, snapshot_order=True)
        except Exception as exc:
            res['card'] = {'error': str(exc)}
            diagnostic_error(diagnostics, 'card', name, exc, error_limit)
            res.update(complete=False, partial=True, errorCount=diagnostics['errorCount'])
        observations = res['snapshot']['observations']
        card_observations = res['card'].get('snapshot', {}).get('observations', {})
        observations.update(cardBefore=card_observations.get('before'),
                            cardAfter=card_observations.get('after'))
        res['snapshot'] = self.snapshot_evidence(observations)
        return res

    # ================================================== the virtual Data
    def vfs(self, q):
        path = one(q, 'path', '.')
        filt = one(q, 'filter', '*')

        def f():
            return {'path': path, 'filter': filt,
                    'files': list(self.o.findFiles(path, filt))}
        return self.run_main(f)

    def origins(self, q):
        rel = one(q, 'path', '')

        def f():
            # order matters: the first in the list is the one whose file the game sees
            return {'path': rel, 'origins': list(self.o.getFileOrigins(rel))}
        return self.run_main(f)

    def dirs(self, q):
        path = one(q, 'path', '.')

        def f():
            return {'path': path, 'dirs': list(self.o.listDirectories(path))}
        return self.run_main(f)

    def resolve(self, q):
        p = one(q, 'path', '')
        if not p:
            raise ValueError(i18n.t('err.needPath'))

        def f():
            return {'path': p, 'real': self.o.resolvePath(p)}
        return self.run_main(f)

    def vfsexport(self, body):
        """Export the whole virtual Data together with every provider of every file.

        The tree comes from MO2 itself (virtualFileTree). The breadth-first walk is kept as a
        fallback and is selected by legacyWalk: it only ever existed because
        listDirectories('') returns nothing and the root looked unreachable.
        """
        out_dir = body.get('outDir') or os.path.join(self.o.basePath(), 'vfs-export')
        timeout = float(body.get('timeout') or self.timeout('vfsExport'))
        how = 'walk' if body.get('legacyWalk') else 'virtualFileTree'
        diagnostics = {'errorCount': 0, 'errors': []}
        error_limit = self.limit()

        def by_tree():
            rows = []

            def visit(path, entry):
                if not entry.isDir():
                    rel = (path or '') + entry.name()
                    try:
                        org = list(self.o.getFileOrigins(rel))
                    except Exception as exc:
                        diagnostic_error(diagnostics, 'origins', rel, exc, error_limit)
                        org = []
                    rows.append((rel, org[0] if org else '', '', '|'.join(org)))
                return mobase.IFileTree.WalkReturn.CONTINUE

            self.o.virtualFileTree().walk(visit, chr(92))
            return self.o.profile().name(), rows

        walk = walk_factory(self.o, self.cfg.get('walkRoots') or [], diagnostics, error_limit)
        if how == 'walk':
            profile, rows = self.run_main(walk, timeout=timeout)
        else:
            try:
                profile, rows = self.run_main(by_tree, timeout=timeout)
            except Exception as exc:
                diagnostic_error(diagnostics, 'virtualFileTree', '.', exc, error_limit)
                how = 'walk (fallback)'
                self.note(i18n.t('vfs.fallback', error=exc))
                profile, rows = self.run_main(walk, timeout=timeout)

        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        path = os.path.join(out_dir, 'vfs-%s.csv' % profile)
        with open(path, 'w', encoding='utf-8', newline='') as fh:
            fh.write('rel;winner;archive;providers' + chr(10))
            for r in rows:
                fh.write(';'.join(x.replace(';', ',') for x in r) + chr(10))
        return {'profile': profile, 'files': len(rows), 'how': how,
                'fromArchive': sum(1 for r in rows if r[2]),
                'contested': sum(1 for r in rows if '|' in r[3]),
                'complete': diagnostics['errorCount'] == 0,
                'partial': diagnostics['errorCount'] != 0,
                'errorCount': diagnostics['errorCount'], 'errors': diagnostics['errors'],
                'csv': path}


def walk_factory(o, roots, diagnostics=None, error_limit=200):
    """The fallback breadth-first walk: the root is assembled from the mods' own trees."""
    def walk():
        ml = o.modList()
        seen_roots = set(x.lower() for x in roots)
        for name in ml.allModsByProfilePriority():
            if not (ml.state(name) & mobase.ModState.ACTIVE):
                continue
            try:
                for entry in ml.getMod(name).fileTree():
                    if entry.isDir():
                        seen_roots.add(entry.name().lower())
            except Exception as exc:
                diagnostic_error(diagnostics, 'modTree', name, exc, error_limit)
        rows, queue, seen = [], ['.'] + sorted(seen_roots), set()
        while queue:
            d = queue.pop()
            if d.lower() in seen:
                continue
            seen.add(d.lower())
            at_root = (d == '.')
            try:
                for sub in o.listDirectories(d):
                    queue.append(sub if at_root else d + chr(92) + sub)
            except Exception as exc:
                diagnostic_error(diagnostics, 'directories', d, exc, error_limit)
            try:
                infos = o.findFileInfos(d, lambda fi: True)
            except Exception as exc:
                diagnostic_error(diagnostics, 'fileInfos', d, exc, error_limit)
                continue
            for fi in infos:
                org = list(fi.origins) if fi.origins else []
                base = os.path.basename(fi.filePath)
                rel = base if at_root else d + chr(92) + base
                rows.append((rel, org[0] if org else '', fi.archive or '', '|'.join(org)))
        return o.profile().name(), rows
    return walk
