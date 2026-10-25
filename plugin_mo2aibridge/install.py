# -*- coding: utf-8 -*-
"""Installing a mod without a single dialog: createMod plus our own unpacking.

installMod is deliberately not used. It runs MO2's installer, which always asks for a name,
asks "replace or merge" when names collide, and opens the wizard for an archive carrying
fomod/ModuleConfig.xml. Those windows cannot be automated: MO2 is Qt, its widgets are drawn
inside a single HWND, and there are no native buttons to press by caption.

createMod, by contrast, registers an empty folder and the bridge places the files itself -
exactly the ones wanted. For FOMOD this is the only correct path anyway: the option choice
is worked out by reading ModuleConfig.xml, not by clicking.
"""
import os
import shutil
import subprocess
import tempfile

import mobase

from . import i18n, winapi
from .base import Domain, folder_name, inside, safe


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
        return self.change('install', lambda: self._install(body), on_main=False,
                           prepare=prepare)

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
