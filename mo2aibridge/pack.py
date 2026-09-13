# -*- coding: utf-8 -*-
r"""Build the release archive of the plugin.

Why a separate step. The development layout is a junction, or `xcopy /E` of the whole package
folder, and for a release neither will do: beside the code live the token file, the log and the
settings the plugin creates on every start, plus `__pycache__` from every import. Recursive
copying does not tell them apart, so a stranger's token, a stranger's log with the author's own
paths, and frozen settings - including paths to a 7-Zip another person may not have - end up in
the archive. `.gitignore` does not save you here: it is about git, not about copying files.

So this script lists what SHIPS rather than what is excluded. A forgotten file then means "did
not make it into the archive" instead of "somebody else's file did": the price of the first
mistake is a bug report, the price of the second is a leak.

The folder inside the archive is named after the name MO2 knows the plugin by (PLUGIN_ID), not
after what it is called in the repository: Python takes the package name from the folder name,
and `plugin_mo2aibridge` would yield a package MO2 does not look for.

    python pack.py [where]

With no argument the archive lands in `dist\` next to the module. There are no machine-specific
paths here: the root is derived from this file's location, and the version and name come from
the plugin itself.
"""
import io
import os
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))

# What ships. Everything else does not, however useful it may look.
SHIP_SUFFIX = ('.py',)
SHIP_EXACT = ('README.md', 'README.ru.md', 'LICENSE')


def find_package():
    """The plugin's own folder - the neighbour holding __init__.py. We search rather than
    assemble the name: the module root and the package share a name only by convention, and
    the checks must not depend on conventions."""
    for name in sorted(os.listdir(ROOT)):
        if os.path.isfile(os.path.join(ROOT, name, '__init__.py')):
            return os.path.join(ROOT, name)
    raise SystemExit('no folder with an __init__.py beside pack.py - where is the plugin?')


def plugin_id_and_version(pkg):
    """The MO2 name and the version number - from the plugin itself, by reading __init__.py.

    By reading and not by importing: an import would pull in PyQt6 and mobase, which do not
    exist outside the manager's process, and building would only be possible from inside MO2.
    """
    text = io.open(os.path.join(pkg, '__init__.py'), encoding='utf-8').read()
    out = {}
    for line in text.split(chr(10)):
        for key in ('PLUGIN_ID', '__version__'):
            if line.startswith(key):
                out[key] = line.split('=', 1)[1].strip().strip('"').strip("'")
    if not out.get('PLUGIN_ID') or not out.get('__version__'):
        raise SystemExit('PLUGIN_ID and __version__ were not found in __init__.py')
    return out['PLUGIN_ID'], out['__version__']


def collect(pkg):
    """The package files that ship. Returns (taken, left behind)."""
    take, leave = [], []
    for name in sorted(os.listdir(pkg)):
        full = os.path.join(pkg, name)
        if os.path.isdir(full):
            leave.append(name + os.sep)
            continue
        if name in SHIP_EXACT or name.endswith(SHIP_SUFFIX):
            take.append(name)
        else:
            leave.append(name)
    return take, leave


def main(out_dir):
    pkg = find_package()
    plugin_id, version = plugin_id_and_version(pkg)
    take, leave = collect(pkg)

    stage = os.path.join(out_dir, plugin_id)
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    os.makedirs(stage)
    for name in take:
        shutil.copy2(os.path.join(pkg, name), os.path.join(stage, name))

    archive = os.path.join(out_dir, '%s-%s.zip' % (plugin_id, version))
    if os.path.isfile(archive):
        os.remove(archive)
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name in take:
            zf.write(os.path.join(stage, name), os.path.join(plugin_id, name))

    print('archive: %s' % archive)
    print('folder inside the archive: %s%s  (the name MO2 knows the plugin by)'
          % (plugin_id, os.sep))
    print()
    print('taken (%d):' % len(take))
    for name in take:
        print('  %s' % name)
    if leave:
        print()
        print('left behind (%d) - these are created on the user machine:' % len(leave))
        for name in leave:
            print('  %s' % name)
    return 0


if __name__ == '__main__':
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'dist')
    target = os.path.abspath(target)
    os.makedirs(target, exist_ok=True)
    raise SystemExit(main(target))
