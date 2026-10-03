# -*- coding: utf-8 -*-
r"""Source hygiene, without MO2.

A regression check for a defect no behaviour can catch: in the paths to 7-Zip, instead of
`\7` there stood BEL characters (0x07) - some tool along the way read `\7` as a character
code. The paths never matched, and installing worked only because 7z happens to be on PATH
on this machine. That is visible only in the bytes of the file, so the bytes are what gets
checked.

Alongside it: every .py of the package compiles, every .py of the package is mentioned in
the "Layout" section of both READMEs (or a new file is left undescribed), and no file holds
a path to one particular machine.
"""
import io
import os
import py_compile
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

T = common.T

r = common.Report(T('sources.title'))
HERE = os.path.dirname(os.path.abspath(__file__))
CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')
# An absolute Windows path with a drive letter. Allowed only in documentation examples.
DRIVE = re.compile(r'(?<![A-Za-z])[A-Z]:\\(?!\\)')


def files(root, exts):
    out = []
    for dp, _dn, fs in os.walk(root):
        if '__pycache__' in dp:
            continue
        for f in fs:
            if f.lower().endswith(exts):
                out.append(os.path.join(dp, f))
    return sorted(out)


def read(path):
    return io.open(path, encoding='utf-8', errors='replace').read()


r.head(T('sources.controlChars'))
for path in files(common.PKG, ('.py', '.md')) + files(HERE, ('.py', '.md')):
    hits = [i for i, line in enumerate(read(path).split(chr(10)), 1) if CONTROL.search(line)]
    r.case(os.path.relpath(path, common.ROOT), hits, [])

r.head(T('sources.compiles'))
for path in files(common.PKG, ('.py',)):
    try:
        py_compile.compile(path, doraise=True)
        ok = True
    except Exception as exc:
        ok = str(exc)
    r.case(os.path.basename(path), ok, True)

r.head(T('sources.inReadme'))
modules = sorted(os.path.basename(p) for p in files(common.PKG, ('.py',))
                 if os.path.basename(p) != '__init__.py')
for name in ('README.md', 'README.ru.md'):
    text = read(os.path.join(common.PKG, name))
    missing = [m for m in modules if '`%s`' % m not in text]
    r.case(name, missing, [])

r.head(T('sources.archiveComplete'))
# A check for exactly the kind of breakage that already happened: locale\ became a folder
# per language, the walk in pack.py stayed one level deep, and zero translations went into
# the archive - the plugin would have spoken in bare labels. Invisible until release day, so
# we ask pack.py itself what it is going to take.
sys.path.insert(0, common.ROOT)
pack = __import__('pack')
taken, _left = pack.collect(common.PKG)
r.case(T('sources.stringsShip'), sorted(x for x in taken if x.startswith('locale')) != [], True)
for code in sorted(common.import_package().i18n.languages()):
    r.case(T('sources.languageInArchive', code=code),
           any(x.startswith(os.path.join('locale', code)) for x in taken), True)
r.case(T('sources.codeShips'), '__init__.py' in taken, True)
r.case(T('sources.docsShip'), [x for x in ('README.md', 'README.ru.md', 'LICENSE')
                                if x not in taken], [])
r.case(T('sources.localOnlyStays'),
       [x for x in taken if 'token' in x or x.endswith('.log') or 'config.json' in x], [])

r.head(T('sources.noMachinePaths'))
# The package only: in the checks, stand-in paths like C:\no\such.exe are deliberate.
for path in files(common.PKG, ('.py',)):
    hits = [i for i, line in enumerate(read(path).split(chr(10)), 1)
            if DRIVE.search(line) and not line.lstrip().startswith('#')]
    r.case(os.path.relpath(path, common.ROOT), hits, [])

r.done()
