# -*- coding: utf-8 -*-
"""Установка на песочнице: свежая, слияние, замена и уборка за собой.

Работает только со СВОИМ модом, имя которого нельзя спутать с настоящим, и в конце сносит его
вместе с папкой. Ничего чужого не трогается: ни один существующий мод не упоминается.

Здесь же единственное место, где ключ необратимости передаётся с верным значением - иначе
удалить свой же пробник нечем, и путь «ключ принят» не проверен вовсе.
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
r = common.Report('установка: свежая, слияние, замена')

PROBE = 'MO2 ApI Bridge - проба установки'
KEY = {'iUnderstandTheRisk': 'yes-I-read-the-docs-and-accept-irreversible-changes'}


def seven_zip():
    for p in (r'C:\Program Files\7-Zip\7z.exe', r'C:\Program Files (x86)\7-Zip\7z.exe'):
        if os.path.isfile(p):
            return p
    return shutil.which('7z')


SEVEN = seven_zip()
if not SEVEN:
    r.note('пропуск', '7-Zip не найден, собрать архив нечем')
    raise SystemExit(77)


def make_archive(tag, files):
    """Крошечный архив с папкой Data внутри - как у настоящего мода."""
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
                       r'SKSE\Plugins\only-in-a.txt': 'старый файл'})
B = make_archive('b', {r'SKSE\Plugins\probe.ini': 'version=B',
                       r'SKSE\Plugins\only-in-b.txt': 'новый файл'})


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
    r.note('уборка', 'пробник остался от прошлого прогона, сношу')
    common.call('POST', '/mods/remove', dict(KEY, mod=PROBE))

try:
    r.head('свежая установка')
    _, res = common.call('POST', '/install', {'archive': A, 'name': PROBE})
    r.case('создан', res.get('created'), True)
    r.case('режим', res.get('mode'), 'new')
    r.case('файлов положено', res.get('files'), 2)
    r.case('на диске те же два', files_of(target),
           {os.path.join('SKSE', 'Plugins', 'probe.ini').lower(),
            os.path.join('SKSE', 'Plugins', 'only-in-a.txt').lower()})
    r.case('откат подсказан', (res.get('undo') or {}).get('route'), '/mods/remove')

    r.head('повторная установка без режима отказывает')
    code, res = common.call('POST', '/install', {'archive': B, 'name': PROBE})
    r.case('отказ, а не молчаливая перезапись', code, 500)
    r.case('в отказе названы режимы',
           'merge' in str(res.get('error')) and 'replace' in str(res.get('error')), True)

    r.head('слияние: кладём поверх')
    _, res = common.call('POST', '/install',
                         {'archive': B, 'name': PROBE, 'mode': 'merge'})
    r.case('режим', res.get('mode'), 'merge')
    r.case('старый файл уцелел',
           os.path.join('SKSE', 'Plugins', 'only-in-a.txt').lower() in files_of(target), True)
    r.case('новый добавился',
           os.path.join('SKSE', 'Plugins', 'only-in-b.txt').lower() in files_of(target), True)
    r.case('перекрытое названо', [x for x in res.get('overwritten') or []
                                  if x.endswith('probe.ini')] != [], True)
    r.case('содержимое обновлено',
           io.open(os.path.join(target, 'SKSE', 'Plugins', 'probe.ini'),
                   encoding='utf-8').read(), 'version=B')

    r.head('замена: прежнее уходит в Корзину')
    _, res = common.call('POST', '/install',
                         {'archive': A, 'name': PROBE, 'mode': 'replace'})
    r.case('режим', res.get('mode'), 'replace')
    r.case('что-то убрано в Корзину', (res.get('removedToRecycleBin') or 0) > 0, True)
    r.case('файла из B не осталось',
           os.path.join('SKSE', 'Plugins', 'only-in-b.txt').lower() in files_of(target), False)
    r.case('осталось ровно содержимое A', files_of(target),
           {os.path.join('SKSE', 'Plugins', 'probe.ini').lower(),
            os.path.join('SKSE', 'Plugins', 'only-in-a.txt').lower()})
    r.case('meta.ini не тронут', os.path.isfile(os.path.join(target, 'meta.ini')), True)

finally:
    r.head('уборка за собой')
    _, res = common.call('POST', '/mods/remove', dict(KEY, mod=PROBE))
    r.case('пробник удалён', res.get('applied'), True)
    r.case('карточка отдана до сноса', (res.get('card') or {}).get('mod'), PROBE)
    r.case('папки нет', os.path.isdir(target), False)
    for a in (A, B):
        shutil.rmtree(os.path.dirname(a), ignore_errors=True)

r.done()
