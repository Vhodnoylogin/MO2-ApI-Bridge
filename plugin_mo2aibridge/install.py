# -*- coding: utf-8 -*-
"""Selected native archive installation and the compatible createMod/copy backend.

The opt-in native backend passes an exact, hash-checked file selection through a
one-use companion installer. MO2 then owns metadata and Downloads accounting.
Legacy folder/merge/replace operations preserve their file-copy contract and
explicitly disclose unverified archive accounting. The caller reads FOMOD and
resolves compatibility/dependencies; neither backend chooses options for it.
"""
import os
import hashlib
import json
import ntpath
import shutil
import subprocess
import tempfile

import mobase

from . import PLUGIN_ID, i18n, winapi
from .base import Domain, flag, folder_name, inside, safe


def walk_error(error):
    raise error


def tree_files(root):
    """Paths of every file in a folder, relative to it, lower-cased.

    Needed for an honest "before and after": the difference shows what was added, the
    intersection what was overwritten. Case is dropped because Windows does not distinguish
    it while archives carry both spellings.
    """
    out = set()
    for dp, _dn, fs in os.walk(root, onerror=walk_error):
        rel = os.path.relpath(dp, root)
        for f in fs:
            out.add((f if rel == '.' else os.path.join(rel, f)).lower())
    return out


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def archive_member(value):
    """A portable, unambiguous relative Windows filename, never an ADS or alias."""
    if not isinstance(value, str) or not value or '\x00' in value:
        raise ValueError(i18n.t('err.installFiles'))
    parts = value.replace('\\', '/').split('/')
    reserved = {'CON', 'PRN', 'AUX', 'NUL'} | {
        prefix + str(n) for prefix in ('COM', 'LPT') for n in range(1, 10)}
    if ntpath.isabs(value) or any(
            p in ('', '.', '..') or p != p.rstrip(' .') or
            any(c in p for c in ':<>"|?*') or any(ord(c) < 32 for c in p) or
            p.split('.')[0].upper() in reserved for p in parts):
        raise ValueError(i18n.t('err.installFiles'))
    return '/'.join(parts)


class Installer(Domain):

    def install(self, body):
        """body: {archive, name, paths: ["subfolder in the archive", ...], mode}

        mode is only needed when the mod folder is already taken, and it mirrors what MO2's
        installer asks with a dialog:

            merge    lay the selection over the previous contents
            replace  remove the previous contents, then lay the selection down

        Without mode, a taken folder is a refusal: nothing is written over someone's work
        silently. On replace the previous contents go TO THE RECYCLE BIN, while meta.ini
        stays: it holds nexusId, the category and the archive name - the mod's identity
        beyond its folder name.
        """
        def prepare():
            arc = body.get('archive')
            if not arc or not os.path.isfile(arc):
                raise ValueError(i18n.t('err.noArchive', archive=arc))
            folder_name(body.get('name'))
            mode = (body.get('mode') or '').strip().lower()
            native = flag(body, 'native')
            if native:
                if mode or 'paths' in body:
                    raise ValueError(i18n.t('err.nativeNewOnly'))
                archive_member(body.get('name'))
            if mode and mode not in ('merge', 'replace'):
                raise ValueError(i18n.t('err.badMode', mode=mode))
            # Replace sends the previous contents to the Recycle Bin, losing someone's work,
            # and so it sits behind the key just like removal. A plain install and a merge
            # lose nothing, need no key and change no contract: other callers already use
            # them, and breaking those for the sake of the one dangerous mode is not on.
            if mode == 'replace':
                return self.danger(body, 'installReplace')
            return None

        # Unpacking and copying do not need the main thread; it is entered pointwise inside.
        return self.change('install', lambda: (self._install_native(body)
                           if flag(body, 'native') else self._install(body)), on_main=False,
                           prepare=prepare)

    def _install_native(self, body):
        """Opt-in selected native install; no direct edits of MO2-owned INI files.

        A successful native installer call alone is insufficient. Verify every selected
        payload, archive identity, native metadata, download sidecar and durable provenance.
        Existing targets retain the legacy copy backend until a native merge adapter is
        qualified; do not accidentally open MO2's overwrite wizard.
        """
        from . import nativeinstall
        arc = os.path.realpath(body['archive'])
        name = folder_name(body.get('name'))
        expected = body.get('archiveSha256')
        if not isinstance(expected, str) or len(expected) != 64 or any(
                c not in '0123456789abcdefABCDEF' for c in expected):
            raise ValueError(i18n.t('err.archiveHash'))
        if file_sha256(arc) != expected.lower():
            raise ValueError(i18n.t('err.archiveHash'))
        mappings = body.get('files')
        if not isinstance(mappings, list) or not mappings:
            raise ValueError(i18n.t('err.installFiles'))
        files, destinations = [], set()
        for row in mappings:
            if not isinstance(row, dict) or set(row) != {'source', 'destination'}:
                raise ValueError(i18n.t('err.installFiles'))
            src, dst = archive_member(row['source']), archive_member(row['destination'])
            key = dst.casefold()
            if (key in destinations or dst.split('/')[0].casefold() in ('fomod', 'ini tweaks')
                    or key == 'meta.ini' or src.split('/')[0].casefold() == 'fomod'
                    or src.casefold() == 'meta.ini'):
                raise ValueError(i18n.t('err.installFiles'))
            destinations.add(key)
            files.append({'source': src, 'destination': dst})
        if any('/'.join(p.split('/')[:n]).casefold() in destinations
               for p in destinations for n in range(1, len(p.split('/')))):
            raise ValueError(i18n.t('err.installFiles'))
        selection = body.get('selection', {})
        if not isinstance(selection, dict):
            raise ValueError(i18n.t('err.installSelection'))
        # Canonical JSON both validates the supplied receipt and detaches mutable input.
        selection = json.loads(json.dumps(selection, ensure_ascii=False, allow_nan=False))

        def preflight():
            self.ctx.expected(body)
            if self.guard.refusal('op.install'):
                raise RuntimeError(i18n.t('err.installRefresh'))
            downloads = os.path.realpath(self.o.downloadsPath())
            if os.path.normcase(os.path.dirname(arc)) != os.path.normcase(downloads):
                raise ValueError(i18n.t('err.nativeDownloads'))
            if os.path.exists(os.path.join(self.o.modsPath(), name)) or self.o.modList().getMod(name):
                raise ValueError(i18n.t('err.nativeNewOnly'))
            nativeinstall.check_available(self.o)
            return os.path.join(self.o.modsPath(), name)
        target = self.run_main(preflight)
        tmp = self._unpack_selected(arc, [row['source'] for row in files])
        manifest, fomods = [], []
        try:
            for dp, dirs, names in os.walk(tmp, onerror=walk_error):
                if any(not inside(tmp, os.path.join(dp, n)) for n in dirs + names):
                    raise ValueError(i18n.t('err.installFiles'))
                for entry in names:
                    path = os.path.join(dp, entry)
                    rel = os.path.relpath(path, tmp).replace(os.sep, '/')
                    if rel.lower().endswith('fomod/moduleconfig.xml'):
                        fomods.append({'path': rel, 'sha256': file_sha256(path)})
            if fomods and not selection:
                raise ValueError(i18n.t('err.installSelection'))
            for row in files:
                path = os.path.join(tmp, *row['source'].split('/'))
                if not inside(tmp, path) or not os.path.isfile(path):
                    raise ValueError(i18n.t('err.noPathInArchive', path=row['source']))
                manifest.append(dict(row, sha256=file_sha256(path), bytes=os.path.getsize(path)))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        provenance = {'schemaVersion': 1, 'archive': arc, 'archiveSha256': expected.lower(),
                      'archiveBytes': os.path.getsize(arc), 'selection': selection,
                      'fomod': fomods, 'files': manifest}
        res = {'mod': name, 'path': target, 'created': None, 'existed': False, 'mode': 'new',
               'archive': os.path.basename(arc), 'backend': 'native',
               'files': 0, 'filesVerified': False, 'archiveInstalled': False,
               'archiveMetadataPreserved': False,
               'downloadStatusVerified': False,
               'installationComplete': False, 'provenanceSaved': False,
               'refreshRequested': False, 'undo': None}
        touched, owned = False, False
        original_download = {}
        try:
            def perform():
                nonlocal touched
                preflight()
                if file_sha256(arc) != expected.lower():
                    raise ValueError(i18n.t('err.archiveHash'))
                from PyQt6.QtCore import QSettings
                res['downloadBefore'] = nativeinstall.refresh_downloads(
                    self.o, arc, require_installed=False)
                if res['downloadBefore'].get('found') is not True:
                    raise RuntimeError(i18n.t('err.nativeInstall', reason='download-not-indexed'))
                download = QSettings(arc + '.meta', QSettings.Format.IniFormat)
                if download.status() != QSettings.Status.NoError:
                    raise RuntimeError(i18n.t('err.nativeInstall', reason='download-metadata-unreadable'))
                original_download.update({key: download.value(key) for key in download.allKeys()
                                          if key not in ('installed', 'uninstalled')})
                touched = True  # Native install may have partial effects even when it fails.
                return nativeinstall.install(self.o, arc, name, files,
                    poll_ms=int(self.cfg.get('nativeInstallPollMs')))
            mod, native_receipt = self.run_main(perform)
            res['native'] = native_receipt
            if mod is None:
                raise RuntimeError(i18n.t('err.nativeInstall', reason='no-mod'))
            actual_target = self.run_main(mod.absolutePath)
            if os.path.normcase(os.path.realpath(actual_target)) != os.path.normcase(os.path.realpath(target)):
                raise RuntimeError(i18n.t('err.nativeInstall', reason='target-mismatch'))
            owned = True  # A confirmed native result, not a concurrent folder's existence.
            res['created'] = True
            wanted = {r['destination'].replace('/', os.sep).lower() for r in manifest}
            actual = tree_files(target) - {'meta.ini'}
            if actual != wanted:
                raise RuntimeError(i18n.t('err.nativeInstall', reason='payload-inventory'))
            for row in manifest:
                dest = os.path.join(target, *row['destination'].split('/'))
                if not inside(target, dest) or file_sha256(dest) != row['sha256']:
                    raise RuntimeError(i18n.t('err.nativeInstall', reason='payload-hash'))
            if file_sha256(arc) != expected.lower():
                raise RuntimeError(i18n.t('err.archiveHash'))
            res.update(files=len(manifest), filesVerified=True)

            def finish():
                self.ctx.expected(body)
                if self.guard.refusal('op.install'):
                    raise RuntimeError(i18n.t('err.installRefresh'))
                # Core has already added installedFiles and marked the original download.
                # Save through the model so a later native save cannot erase provenance.
                installed_from = mod.installationFile()
                if not installed_from or os.path.basename(installed_from.replace('\\', '/')) != os.path.basename(arc):
                    raise RuntimeError(i18n.t('err.nativeInstall', reason='archive-association'))
                encoded = json.dumps(provenance, ensure_ascii=False, sort_keys=True)
                if not mod.setPluginSetting(PLUGIN_ID, 'installProvenance', encoded):
                    raise RuntimeError(i18n.t('err.nativeInstall', reason='provenance-write'))
                from PyQt6.QtCore import QSettings
                metadata = QSettings(os.path.join(target, 'meta.ini'), QSettings.Format.IniFormat)
                if (mod.pluginSetting(PLUGIN_ID, 'installProvenance', '') != encoded or
                        metadata.value('Plugins/' + PLUGIN_ID + '/installProvenance', '') != encoded or
                        metadata.status() != QSettings.Status.NoError):
                    raise RuntimeError(i18n.t('err.nativeInstall', reason='provenance-readback'))
                res['provenanceSaved'] = True
                mod_id = original_download.get('modID', 0)
                file_id = original_download.get('fileID', 0)
                try:
                    mod_id, file_id = int(mod_id or 0), int(file_id or 0)
                except (ValueError, TypeError):
                    raise RuntimeError(i18n.t('err.nativeInstall', reason='source-identity-invalid'))
                if mod_id > 0 and mod.nexusId() != mod_id:
                    raise RuntimeError(i18n.t('err.nativeInstall', reason='mod-id-readback'))
                game = original_download.get('gameName')
                version = original_download.get('version')
                if game and mod.gameName() != game:
                    raise RuntimeError(i18n.t('err.nativeInstall', reason='game-readback'))
                if version and mod.version().displayString() != mobase.VersionInfo(str(version)).displayString():
                    raise RuntimeError(i18n.t('err.nativeInstall', reason='version-readback'))
                if mod_id > 0 and file_id > 0:
                    installed_pairs = []
                    for index in range(metadata.beginReadArray('installedFiles')):
                        metadata.setArrayIndex(index)
                        installed_pairs.append((int(metadata.value('modid', 0)),
                                                int(metadata.value('fileid', 0))))
                    metadata.endArray()
                    if (mod_id, file_id) not in installed_pairs:
                        raise RuntimeError(i18n.t('err.nativeInstall', reason='installed-file-identity'))
                download = QSettings(arc + '.meta', QSettings.Format.IniFormat)
                res['archiveMetadataPreserved'] = all(
                    download.contains(key) and download.value(key) == value
                    for key, value in original_download.items())
                if not res['archiveMetadataPreserved']:
                    # Never put credentials/signed URLs or their original values in replies.
                    raise RuntimeError(i18n.t('err.nativeInstall', reason='download-metadata-changed'))
                res['archiveInstalled'] = (os.path.isfile(arc + '.meta') and
                    download.status() == QSettings.Status.NoError and
                    download.value('installed', False, type=bool) and
                    not download.value('uninstalled', False, type=bool))
                if not res['archiveInstalled']:
                    raise RuntimeError(i18n.t('err.nativeInstall', reason='download-accounting'))
                res['downloadStatus'] = nativeinstall.refresh_downloads(self.o, arc)
                res['downloadStatusVerified'] = res['downloadStatus'].get('verified') is True
                if not res['downloadStatusVerified']:
                    raise RuntimeError(i18n.t('err.nativeInstall', reason='download-model-unverified'))
                self.ctx.expected(body)
                if self.guard.refusal('op.refresh'):
                    raise RuntimeError(i18n.t('err.installRefresh'))
                self.o.refresh(True)
                res['refreshRequested'] = True
            self.run_main(finish)
            res.update(installationComplete=True, installProvenance=provenance,
                       undo={'route': '/mods/remove', 'body': {'mod': name}})
        except Exception as exc:
            if not touched:
                raise
            res.update(applied=False, failed=True, partial=True, error=str(exc),
                       reconciliationRequired=True)
            if getattr(exc, 'receipt', None):
                res['native'] = exc.receipt
            res['targetExists'] = os.path.isdir(target)
            res['undo'] = ({'route': '/mods/remove', 'body': {'mod': name}} if owned else None)
        return res

    def _install(self, body):
        arc = body.get('archive')
        name = folder_name(body.get('name'))
        paths = body.get('paths') or ['']
        mode = (body.get('mode') or '').strip().lower()

        def preflight():
            self.ctx.expected(body)
            stop = self.guard.refusal('op.install')
            if stop:
                return self._refusal(stop, 'install', 'busy')
            if os.path.isdir(os.path.join(self.o.modsPath(), name)) and not mode:
                raise ValueError(i18n.t('err.modExists', mod=name))
        stop = self.run_main(preflight)
        if stop:
            return stop

        # Complete extraction AND selection validation before registering or wiping a mod.
        tmp = self._unpack(arc)
        state = {'touched': False, 'target': '', 'existed': False, 'before': set()}
        removed, copied, skipped_fomod = 0, None, False
        error, refresh_error, refreshed = None, None, False
        progress = {'attempted': [], 'completed': []}
        try:
            self._validate_paths(tmp, paths)

            def begin():
                self.ctx.expected(body)
                stop = self.guard.refusal('op.install')
                if stop:
                    return self._refusal(stop, 'install', 'busy')
                mods_root = self.o.modsPath()
                target = os.path.join(mods_root, name)
                existed = os.path.isdir(target)
                if not inside(mods_root, target):
                    raise ValueError(i18n.t('err.badName', name=name))
                if existed and not mode:
                    raise ValueError(i18n.t('err.modExists', mod=name))
                state.update(target=target, existed=existed,
                             before=tree_files(target) if existed else set())
                if not existed:
                    state['touched'] = True  # createMod itself can have partial effects.
                    mod = self.o.createMod(mobase.GuessedString(name))
                    if mod is None:
                        raise RuntimeError(i18n.t('err.createFailed'))
                    target = mod.absolutePath()
                    if not inside(mods_root, target):
                        raise RuntimeError(i18n.t('err.badName', name=target))
                    state['target'] = target
                return None

            stop = self.run_main(begin)
            if stop:
                return stop
            state['touched'] = True
            if state['existed'] and mode == 'replace':
                removed = self._wipe(state['target'])
            copied, skipped_fomod = self._copy(tmp, paths, state['target'], progress)
        except Exception as exc:
            if not state['touched']:
                raise
            error = exc
            removed = getattr(exc, 'removedToRecycleBin', removed)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            if state['touched']:
                try:
                    def reconcile():
                        # A new native launch must not be disturbed just to repair a scan.
                        if self.guard.refusal('op.refresh'):
                            return False
                        self.o.refresh(True)
                        return True
                    refreshed = self.run_main(reconcile)
                except Exception as exc:
                    refresh_error = str(exc)
        target, existed, before = state['target'], state['existed'], state['before']
        inventory_error = None
        try:
            after = tree_files(target) if os.path.isdir(target) else set()
        except Exception as exc:
            after, inventory_error = None, str(exc)
        limit = self.limit()
        # "What was and what became", in full: a merge silently overwrites previous files,
        # and without the list of them the caller never learns what was lost.
        confirmed = sorted(before & set(progress['completed'])) if mode == 'merge' else []
        possible = sorted(before & (set(progress['attempted']) - set(progress['completed'])))
        # Keep the legacy successful inventory intersection. Failure replies, which
        # did not exist before, must not claim untouched files as overwritten.
        overwritten = (sorted(before & after) if error is None and after is not None
                       else confirmed) if mode == 'merge' else []
        res = {'mod': name, 'path': target, 'files': copied,
                'backend': 'copy', 'installationComplete': False,
                'archiveInstalled': False, 'accountingRequired': True,
                'created': not existed, 'existed': existed, 'mode': mode or 'new',
                'fomodSkipped': skipped_fomod, 'archive': os.path.basename(arc),
                'filesBefore': len(before), 'filesAfter': len(after) if after is not None else None,
                'added': sorted(after - before)[:limit] if after is not None else [],
                'addedCount': len(after - before) if after is not None else None,
                'overwritten': overwritten[:limit], 'overwrittenCount': len(overwritten),
                'confirmedOverwritten': confirmed[:limit],
                'possibleOverwritten': possible[:limit],
                'inventoryKnown': after is not None,
                'removedToRecycleBin': removed,
                'undo': ({'route': '/mods/remove', 'body': {'mod': name}}
                         if not existed else None), 'refreshRequested': refreshed}
        if inventory_error:
            res['inventoryError'] = inventory_error
        if error is not None or not refreshed or inventory_error:
            res.update(applied=False, failed=True, partial=True,
                       error=(str(error) if error is not None else inventory_error
                              or i18n.t('err.installRefresh')),
                       reconciliationRequired=True)
            if refresh_error:
                res['refreshError'] = refresh_error
        return res

    @staticmethod
    def _validate_paths(tmp, paths):
        if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
            raise ValueError(i18n.t('err.installPaths'))
        for sub in paths:
            root = os.path.join(tmp, sub) if sub else tmp
            if not inside(tmp, root):
                raise ValueError(i18n.t('err.installPaths'))
            if not os.path.isdir(root):
                raise ValueError(i18n.t('err.noPathInArchive', path=sub))
            for dp, dirs, files in os.walk(root, onerror=walk_error):
                if any(not inside(tmp, os.path.join(dp, entry)) for entry in dirs + files):
                    raise ValueError(i18n.t('err.installPaths'))

    def _wipe(self, target):
        """Send the previous contents TO THE RECYCLE BIN, not past it: replace is the one
        install that loses something, and it has to lose it reversibly. meta.ini is left
        alone - it belongs to MO2 and holds the mod's identity beyond its folder name."""
        removed = 0
        for entry in sorted(os.listdir(target)):
            if entry.lower() == 'meta.ini':
                continue
            full = os.path.join(target, entry)
            if safe(lambda p=full: winapi.recycle(p), False):
                removed += 1
            else:
                # How many already went is a mandatory part of the refusal: the wipe broke
                # off midway, and without that number the caller cannot know what to look
                # for in the Recycle Bin.
                exc = RuntimeError(i18n.t('err.recycle', path=full, done=removed))
                exc.removedToRecycleBin = removed
                raise exc
        return removed

    def _unpack(self, arc):
        """Unpack the archive into a temporary folder of its own. Each call gets its own:
        two installs in a row must not overwrite each other."""
        seven = self.cfg.seven_zip()
        if not seven:
            raise RuntimeError(i18n.t('err.no7z'))
        tmp = tempfile.mkdtemp(prefix=self.cfg.get('unpackDirName') + '-')
        r = subprocess.run([seven, 'x', arc, '-o' + tmp, '-y'],
                           capture_output=True, text=True, timeout=self.timeout('unpack'))
        if r.returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            tail = int(self.cfg.get('unpackErrorTail') or 300)
            raise RuntimeError(i18n.t('err.unpack', detail=(r.stdout or r.stderr)[-tail:]))
        return tmp

    def _unpack_selected(self, arc, sources):
        """Inspect the original archive, extracting only chosen files and FOMOD XML.

        Do not unpack every mutually exclusive runtime/installer variant simply
        to verify a selection. 7-Zip's UTF-8 list file avoids Windows argv limits;
        literal-path mode prevents archive names from becoming wildcard patterns.
        Ambiguous technical listings are refused before native installation.
        """
        seven = self.cfg.seven_zip()
        if not seven:
            raise RuntimeError(i18n.t('err.no7z'))
        result = subprocess.run([seven, 'l', '-slt', '-ba', '-sccUTF-8', '--', arc],
            capture_output=True, text=True, encoding='utf-8', timeout=self.timeout('unpack'))
        if result.returncode != 0:
            raise RuntimeError(i18n.t('err.nativeInstall', reason='archive-listing-failed'))
        members = {}
        for block in result.stdout.strip().split('\n\n'):
            fields = {}
            for line in block.splitlines():
                key, sep, value = line.partition(' = ')
                if not sep or key in fields:
                    raise ValueError(i18n.t('err.installFiles'))
                fields[key] = value
            if fields.get('Folder') == '+' or fields.get('Attributes', '').startswith('D'):
                continue
            path = archive_member(fields.get('Path'))
            key = path.lower()
            if key in members or fields.get('Encrypted') == '+':
                raise ValueError(i18n.t('err.installFiles'))
            members[key] = path
        selected = []
        for source in sources:
            if source.lower() not in members:
                raise ValueError(i18n.t('err.noPathInArchive', path=source))
            selected.append(members[source.lower()])
        selected.extend(path for path in members.values() if
                        path.lower() == 'fomod/moduleconfig.xml' or
                        path.lower().endswith('/fomod/moduleconfig.xml'))
        tmp = tempfile.mkdtemp(prefix=self.cfg.get('unpackDirName') + '-')
        descriptor, listing = tempfile.mkstemp(prefix=self.cfg.get('unpackDirName') + '-', suffix='.txt')
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as output:
                for path in sorted(set(selected)):
                    output.write('"' + path + '"\n')
            result = subprocess.run([seven, 'x', '-spd', '-scsUTF-8', '-sccUTF-8',
                '-o' + tmp, '-y', '-i@' + listing, '--', arc], capture_output=True,
                text=True, encoding='utf-8', timeout=self.timeout('unpack'))
            if result.returncode != 0:
                raise RuntimeError(i18n.t('err.nativeInstall', reason='selected-extraction-failed'))
            if tree_files(tmp) != {p.replace('/', os.sep).lower() for p in selected}:
                raise RuntimeError(i18n.t('err.nativeInstall', reason='selected-extraction-inventory'))
            return tmp
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        finally:
            os.remove(listing)

    @staticmethod
    def _copy(tmp, paths, target, progress=None):
        progress = progress if progress is not None else {'attempted': [], 'completed': []}
        copied, skipped_fomod = 0, False
        for sub in paths:
            root = os.path.join(tmp, sub) if sub else tmp
            if not os.path.isdir(root):
                raise ValueError(i18n.t('err.noPathInArchive', path=sub))
            for dp, _dn, fs in os.walk(root, onerror=walk_error):
                rel = os.path.relpath(dp, root)
                # fomod describes the install wizard; the game has no use for it
                if rel.lower().split(os.sep)[0] == 'fomod':
                    skipped_fomod = True
                    continue
                dst = target if rel == '.' else os.path.join(target, rel)
                if not inside(target, dst):
                    raise ValueError(i18n.t('err.installPaths'))
                if not os.path.isdir(dst):
                    os.makedirs(dst)
                for f in fs:
                    if f.lower() == 'meta.ini' and rel == '.':
                        continue
                    if not inside(target, os.path.join(dst, f)):
                        raise ValueError(i18n.t('err.installPaths'))
                    relative = os.path.relpath(os.path.join(dst, f), target).lower()
                    progress['attempted'].append(relative)
                    shutil.copy2(os.path.join(dp, f), os.path.join(dst, f))
                    progress['completed'].append(relative)
                    copied += 1
        return copied, skipped_fomod
