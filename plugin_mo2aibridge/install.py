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


def tree_files(root):
    """Paths of every file in a folder, relative to it, lower-cased.

    Needed for an honest "before and after": the difference shows what was added, the
    intersection what was overwritten. Case is dropped because Windows does not distinguish
    it while archives carry both spellings.
    """
    out = set()
    for dp, _dn, fs in os.walk(root):
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

        mods_root = self.run_main(lambda: self.o.modsPath())
        existed = os.path.isdir(os.path.join(mods_root, name))
        if existed and not mode:
            raise ValueError(i18n.t('err.modExists', mod=name))

        if existed:
            target = os.path.join(mods_root, name)
            # The name is already checked, but the path is verified once more: a junction or
            # a mount point can sit between the check and the work, and making sure is
            # cheaper than taking it on trust.
            if not inside(mods_root, target):
                raise ValueError(i18n.t('err.badName', name=name))
            before = tree_files(target)
        else:
            def mk():
                m = self.o.createMod(mobase.GuessedString(name))
                return None if m is None else m.absolutePath()
            target = self.run_main(mk)
            if not target:
                raise RuntimeError(i18n.t('err.createFailed'))
            if not inside(mods_root, target):
                raise RuntimeError(i18n.t('err.badName', name=target))
            before = set()

        # Unpacking comes BEFORE wiping, and that is not a matter of taste: unpacking is
        # also what checks whether 7-Zip is on the machine and whether the archive is
        # intact. The other way round, a user without 7-Zip got the mod folder in the
        # Recycle Bin and no install in exchange.
        removed = 0
        tmp = self._unpack(arc)
        try:
            if existed and mode == 'replace':
                removed = self._wipe(target)
            copied, skipped_fomod = self._copy(tmp, paths, target)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.run_main(lambda: (self.o.refresh(True), True)[1])
        after = tree_files(target)
        limit = self.limit()
        # "What was and what became", in full: a merge silently overwrites previous files,
        # and without the list of them the caller never learns what was lost.
        overwritten = sorted(before & after) if mode == 'merge' else []
        return {'mod': name, 'path': target, 'files': copied,
                'created': not existed, 'existed': existed, 'mode': mode or 'new',
                'fomodSkipped': skipped_fomod, 'archive': os.path.basename(arc),
                'filesBefore': len(before), 'filesAfter': len(after),
                'added': sorted(after - before)[:limit], 'addedCount': len(after - before),
                'overwritten': overwritten[:limit], 'overwrittenCount': len(overwritten),
                'removedToRecycleBin': removed,
                'undo': ({'route': '/mods/remove', 'body': {'mod': name}}
                         if not existed else None)}

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
                raise RuntimeError(i18n.t('err.recycle', path=full, done=removed))
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
    def _copy(tmp, paths, target):
        copied, skipped_fomod = 0, False
        for sub in paths:
            root = os.path.join(tmp, sub) if sub else tmp
            if not os.path.isdir(root):
                raise ValueError(i18n.t('err.noPathInArchive', path=sub))
            for dp, _dn, fs in os.walk(root):
                rel = os.path.relpath(dp, root)
                # fomod describes the install wizard; the game has no use for it
                if rel.lower().split(os.sep)[0] == 'fomod':
                    skipped_fomod = True
                    continue
                dst = target if rel == '.' else os.path.join(target, rel)
                if not os.path.isdir(dst):
                    os.makedirs(dst)
                for f in fs:
                    if f.lower() == 'meta.ini' and rel == '.':
                        continue
                    shutil.copy2(os.path.join(dp, f), os.path.join(dst, f))
                    copied += 1
        return copied, skipped_fomod
