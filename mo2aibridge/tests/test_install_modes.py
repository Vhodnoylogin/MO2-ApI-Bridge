# -*- coding: utf-8 -*-
"""Installing in a sandbox: fresh, merge, replace, and cleaning up afterwards.

It works only with a mod OF ITS OWN, whose name cannot be confused with a real one, and it
removes that mod and its folder at the end. Nothing belonging to anyone else is touched: not
one existing mod is even named.

This is also the single place where the irreversible key is passed with the correct value -
otherwise there is nothing to remove our own probe with, and the "key accepted" path goes
unchecked entirely.
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

T = common.T

common.need_live()
r = common.Report(T('install.title'))

PROBE = 'MO2 ApI Bridge - install probe'
KEY = {'iUnderstandTheRisk': 'yes-I-read-the-docs-and-accept-irreversible-changes'}


def seven_zip():
    for p in (r'C:\Program Files\7-Zip\7z.exe', r'C:\Program Files (x86)\7-Zip\7z.exe'):
        if os.path.isfile(p):
            return p
    return shutil.which('7z')


SEVEN = seven_zip()
if not SEVEN:
    r.note(T('install.skip'), T('install.no7z'))
    raise SystemExit(77)


def make_archive(tag, files):
    """A tiny archive with a Data folder inside - like a real mod has."""
    work = os.path.join(tempfile.gettempdir(), 'mo2aibridge-probe-' + tag)
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(os.path.join(work, 'src', 'SKSE', 'Plugins'))
    for rel, text in files.items():
        full = os.path.join(work, 'src', rel)
        if not os.path.isdir(os.path.dirname(full)):
            os.makedirs(os.path.dirname(full))
        io.open(full, 'w', encoding='utf-8').write(text)
    arc = os.path.join(work, 'probe-%s.7z' % tag)
    subprocess.run([SEVEN, 'a', arc, os.path.join(work, 'src', '*')],
                   capture_output=True, timeout=120)
    return arc


A = make_archive('a', {r'SKSE\Plugins\probe.ini': 'version=A',
                       r'SKSE\Plugins\only-in-a.txt': 'the old file'})
B = make_archive('b', {r'SKSE\Plugins\probe.ini': 'version=B',
                       r'SKSE\Plugins\only-in-b.txt': 'the new file'})


def files_of(path):
    out = set()
    for dp, _dn, fs in os.walk(path):
        rel = os.path.relpath(dp, path)
        for f in fs:
            if f.lower() != 'meta.ini':
                out.add((f if rel == '.' else os.path.join(rel, f)).lower())
    return out


_, ping = common.call('GET', '/ping')
target = os.path.join(ping['modsPath'].replace('/', os.sep), PROBE)
if os.path.isdir(target):
    r.note(T('install.cleanup'), T('install.leftoverProbe'))
    common.call('POST', '/mods/remove', dict(KEY, mod=PROBE))

try:
    r.head(T('install.freshInstall'))
    _, res = common.call('POST', '/install', {'archive': A, 'name': PROBE})
    r.case(T('install.created'), res.get('created'), True)
    r.case(T('install.mode'), res.get('mode'), 'new')
    r.case(T('install.filesPlaced'), res.get('files'), 2)
    r.case(T('install.sameTwoOnDisk'), files_of(target),
           {os.path.join('SKSE', 'Plugins', 'probe.ini').lower(),
            os.path.join('SKSE', 'Plugins', 'only-in-a.txt').lower()})
    r.case(T('install.undoOffered'), (res.get('undo') or {}).get('route'), '/mods/remove')

    r.head(T('install.repeatWithoutModeRefuses'))
    code, res = common.call('POST', '/install', {'archive': B, 'name': PROBE})
    r.case(T('install.refusalNotSilentOverwrite'), code, 500)
    r.case(T('install.refusalNamesModes'),
           'merge' in str(res.get('error')) and 'replace' in str(res.get('error')), True)

    r.head(T('install.mergeOverlay'))
    _, res = common.call('POST', '/install',
                         {'archive': B, 'name': PROBE, 'mode': 'merge'})
    r.case(T('install.mergeMode'), res.get('mode'), 'merge')
    r.case(T('install.oldFileSurvived'),
           os.path.join('SKSE', 'Plugins', 'only-in-a.txt').lower() in files_of(target), True)
    r.case(T('install.newFileAdded'),
           os.path.join('SKSE', 'Plugins', 'only-in-b.txt').lower() in files_of(target), True)
    r.case(T('install.overwrittenNamed'), [x for x in res.get('overwritten') or []
                                  if x.endswith('probe.ini')] != [], True)
    r.case(T('install.contentsUpdated'),
           io.open(os.path.join(target, 'SKSE', 'Plugins', 'probe.ini'),
                   encoding='utf-8').read(), 'version=B')

    r.head(T('install.replaceToRecycleBin'))
    _, res = common.call('POST', '/install',
                         {'archive': A, 'name': PROBE, 'mode': 'replace'})
    r.case(T('install.replaceMode'), res.get('mode'), 'replace')
    r.case(T('install.somethingRecycled'), (res.get('removedToRecycleBin') or 0) > 0, True)
    r.case(T('install.noFileFromB'),
           os.path.join('SKSE', 'Plugins', 'only-in-b.txt').lower() in files_of(target), False)
    r.case(T('install.exactlyContentsOfA'), files_of(target),
           {os.path.join('SKSE', 'Plugins', 'probe.ini').lower(),
            os.path.join('SKSE', 'Plugins', 'only-in-a.txt').lower()})
    r.case(T('install.metaIniUntouched'), os.path.isfile(os.path.join(target, 'meta.ini')), True)

finally:
    r.head(T('install.cleaningUpAfter'))
    _, res = common.call('POST', '/mods/remove', dict(KEY, mod=PROBE))
    r.case(T('install.probeRemoved'), res.get('applied'), True)
    r.case(T('install.cardBeforeRemoval'), (res.get('card') or {}).get('mod'), PROBE)
    r.case(T('install.folderGone'), os.path.isdir(target), False)
    for a in (A, B):
        shutil.rmtree(os.path.dirname(a), ignore_errors=True)

r.done()
