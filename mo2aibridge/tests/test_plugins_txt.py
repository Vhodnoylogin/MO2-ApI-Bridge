# -*- coding: utf-8 -*-
"""Правка состояния плагина должна доезжать до plugins.txt и переживать /refresh.

Регрессия на дефект, стоивший трёх сорванных прогонов: setState менял список в памяти MO2,
файл она переписывала только при выходе, игра стартовала с файла, а любой /refresh между
правкой и выходом читал файл обратно и молча отменял правку.

Состояние сборки возвращается в исходное, и в конце файл сверяется с байтовым снимком.
"""
import hashlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

T = common.T

common.need_live()
r = common.Report('состояние плагинов доезжает до plugins.txt')

_, prof = common.call('GET', '/profiles')
TXT = os.path.join(prof['path'], prof['current'], 'plugins.txt')


def digest():
    return hashlib.md5(io.open(TXT, 'rb').read()).hexdigest()[:12]


def starred(name):
    """Стоит ли звёздочка - по файлу, а не по памяти."""
    raw = io.open(TXT, encoding='utf-8-sig', errors='replace', newline='').read()
    for line in raw.split(chr(10)):
        b = line.rstrip(chr(13))
        if b.lstrip('*').strip().lower() == name.lower():
            return b.startswith('*')
    return None


def line_ends():
    b = io.open(TXT, 'rb').read()
    return b.count(b'\r\n'), b.count(b'\n') - b.count(b'\r\n')


start, crlf_before = digest(), line_ends()
_, pl = common.call('GET', '/plugins')
victim = next((p['plugin'] for p in pl['plugins']
               if not p['active'] and p['plugin'].lower().endswith('.esp')),
              pl['plugins'][0]['plugin'])
was = starred(victim)
r.note('подопытный', '%s, звёздочка сейчас: %s' % (victim, was))
if was is None:
    r.case('плагин есть в plugins.txt', False, True)
    r.done()

r.head('переключаем через мост')
_, res = common.call('POST', '/plugins/state', {'set': {victim: not was}, 'apply': True})
r.case('применено', res.get('applied'), True)
r.case('файл переписан', (res.get('file') or {}).get('written'), True)
r.case('звёздочка в файле изменилась', starred(victim), not was)

r.head('/refresh не отменяет правку')
common.call('POST', '/refresh', {})
r.case('в файле по-прежнему новое', starred(victim), not was)
_, pl2 = common.call('GET', '/plugins')
r.case('память согласна с файлом',
       next(p['active'] for p in pl2['plugins'] if p['plugin'] == victim), not was)

r.head('возвращаем как было')
_, res = common.call('POST', '/plugins/state', {'set': {victim: was}, 'apply': True})
r.case('применено', res.get('applied'), True)
common.call('POST', '/refresh', {})
r.case('звёздочка вернулась', starred(victim), was)
r.case('файл побайтно как в начале', digest(), start)
r.case('концы строк не подменены', line_ends(), crlf_before)

r.done()
