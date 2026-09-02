# -*- coding: utf-8 -*-
"""Все маршруты по живой MO2.

Не трогается ничего, что оставляет след: /install, /toggle, /run, /window и /vfsexport
меняют сборку или занимают минуты. Необратимая тройка проверяется НАМЕРЕННО без ключа -
ожидается отказ, и именно он доказывает, что замок на месте.
"""
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

common.need_live()
r = common.Report('маршруты по живой MO2')


def get(route, params=None):
    q = ('?' + urllib.parse.urlencode(params)) if params else ''
    return common.call('GET', route + q)


r.head('чтение')
code, ping = get('/ping')
r.case('/ping отвечает', ping.get('ok'), True)
r.note('профиль и версия', '%s, MO2 %s' % (ping.get('profile'), ping.get('mo2Version')))

code, api = get('/api')
r.case('/api называет четыре класса', len(api), 4)

code, mods = get('/mods')
r.case('/mods отдал список', (mods.get('count') or 0) > 0, True)
r.note('модов', mods.get('count'))
names = [m['mod'] for m in mods['mods'] if not m['mod'].endswith('_separator')]
sample = names[0]

code, card = get('/mod', {'name': sample})
r.case('/mod вернул карточку', card.get('mod'), sample)

code, an = get('/analyze', {'name': sample, 'conflicts': '0'})
r.case('/analyze без споров', isinstance(an.get('files'), int), True)
code, an = get('/analyze', {'name': sample, 'conflicts': '1', 'limit': '40'})
r.case('/analyze со спорами', 'conflicts' in an, True)

code, prof = get('/profiles')
r.case('/profiles знает активный', prof.get('current'), ping.get('profile'))

code, pl = get('/plugins')
r.case('/plugins отдал порядок', (pl.get('count') or 0) > 0, True)
r.note('плагинов', pl.get('count'))

code, vfs = get('/vfs', {'path': '.', 'filter': '*.esp'})
r.case('/vfs нашёл esp в корне Data', len(vfs.get('files') or []) > 0, True)
one = os.path.basename((vfs.get('files') or ['Skyrim.esm'])[0])

code, org = get('/origins', {'path': one})
r.case('/origins назвал поставщика', len(org.get('origins') or []) > 0, True)
code, res = get('/resolve', {'path': one})
r.case('/resolve дал файл на диске', bool(res.get('real')), True)

code, d = get('/dirs', {'path': 'meshes'})
r.case('/dirs нашёл подкаталоги meshes', len(d.get('dirs') or []) > 0, True)

code, pr = get('/procs')
r.case('/procs отвечает', 'procs' in pr, True)

code, w = get('/windows')
r.case('/windows без pid отказывает', code, 500)

r.head('действия без последствий')
code, ref = common.call('POST', '/refresh', {})
r.case('/refresh перечитал списки', ref.get('refreshed'), True)

first = pl['plugins'][0]['plugin']
code, st = common.call('POST', '/plugins/state', {'set': {first: True}})
r.case('/plugins/state без apply ничего не делает', st.get('applied'), False)

order = [x['plugin'] for x in pl['plugins']]
code, od = common.call('POST', '/plugins/order', {'order': order})
r.case('/plugins/order без apply ничего не делает', od.get('applied'), False)
code, od = common.call('POST', '/plugins/order', {'order': order[:5]})
r.case('/plugins/order отвергает неполный список',
       od.get('applied') is False and bool(od.get('error')), True)

r.head('замок на необратимом: ключ намеренно не передан')
for route, body in (('/mods/priority', {'mod': sample, 'priority': 5}),
                    ('/mods/rename', {'mod': sample, 'newName': sample + ' ПРОБА'}),
                    ('/mods/remove', {'mod': sample})):
    code, res = common.call('POST', route, body)
    r.case('%s заблокирован' % route, res.get('applied'), False)
    r.note('', 'причина: %s' % res.get('blocked'))

r.head('не трогалось намеренно')
for route, why in (('/install', 'создал бы мод'),
                   ('/toggle', 'изменил бы состав профиля'),
                   ('/run', 'запустил бы программу'),
                   ('/window', 'нажал бы кнопку'),
                   ('/vfsexport', 'минуты работы')):
    r.note(route, why)

r.done()
