# -*- coding: utf-8 -*-
"""Гигиена исходников, без MO2.

Регрессия на дефект, который не ловится ни одним поведением: в путях к 7-Zip вместо `\\7`
стояли символы BEL (0x07) - какой-то инструмент по дороге истолковал `\\7` как код символа.
Пути никогда не совпадали, а установка работала только потому, что на этой машине 7z лежит
в PATH. Такое видно только в самих байтах файла, поэтому проверяются они.

Заодно: каждый .py пакета компилируется, каждый .py пакета упомянут в разделе «Раскладка»
обоих README (иначе новый файл остаётся без описания), и ни в одном файле нет путей к
конкретной машине.
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
# Абсолютный путь Windows с буквой диска. Разрешён только в примерах документации.
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
# Проверка ровно того рода поломки, что уже случилась: locale\ стал папкой на язык, обход
# в pack.py остался на один уровень, и в архив уехало ноль переводов - плагин говорил бы
# метками. Незаметно до самой выкладки, поэтому спрашиваем сам pack.py, что он возьмёт.
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
# Только пакет: в проверках подставные пути вроде C:\нет\такого.exe стоят намеренно.
for path in files(common.PKG, ('.py',)):
    hits = [i for i, line in enumerate(read(path).split(chr(10)), 1)
            if DRIVE.search(line) and not line.lstrip().startswith('#')]
    r.case(os.path.relpath(path, common.ROOT), hits, [])

r.done()
