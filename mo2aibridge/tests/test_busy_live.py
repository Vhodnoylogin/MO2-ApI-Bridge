# -*- coding: utf-8 -*-
"""Замок занятости на настоящей запущенной программе.

Берётся утилита, а не игра: она тоже запускается через MO2 и тоже держит её замок, но
ничего не считает, пока не нажать кнопку. Имя задаётся переменной MO2AIBRIDGE_TEST_APP и
должно быть ЗАРЕГИСТРИРОВАННЫМ в MO2 - startApplication принимает имя, а не путь.

Закрывается она тем же маршрутом /window, которым мост вообще умеет работать с окнами.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

common.need_live()
APP = os.environ.get('MO2AIBRIDGE_TEST_APP') or 'TexGen'
r = common.Report('замок занятости на запущенной программе (%s)' % APP)

_, ping = common.call('GET', '/ping')
if ping.get('busy'):
    r.note('пропуск', 'что-то уже запущено, проверка недостоверна: %s' % ping['busy'])
    r.done()

r.head('запускаю программу через MO2')
box = {}


def launcher():
    box['res'] = common.call('POST', '/run',
                             {'binary': APP, 'wait': True, 'timeout': 900}, timeout=900)


th = threading.Thread(target=launcher, daemon=True)
th.start()

pid = 0
for _ in range(60):
    time.sleep(1)
    _, ping = common.call('GET', '/ping', timeout=20)
    if ping.get('busy'):
        pid = (ping['busy'].get('pids') or [0])[0]
        break
    if box.get('res') and box['res'][0] != 200:
        r.note('пропуск', 'запустить "%s" не вышло: %s' % (APP, box['res'][1].get('error')))
        r.note('', 'задайте MO2AIBRIDGE_TEST_APP именем утилиты из списка MO2')
        r.done()
if not pid:
    r.case('программа поднялась', False, True)
    r.done()

busy = ping['busy']
r.note('занято', '%s, pid %s, игра: %s' % (busy['app'], busy['pids'], busy['isGame']))

r.head('чтение обязано работать')
for route, key in (('/mods', 'count'), ('/plugins', 'count'), ('/profiles', 'current')):
    code, res = common.call('GET', route, timeout=60)
    r.case('%s отвечает при занятой MO2' % route, code, 200)
    r.note('', '%s = %s' % (key, res.get(key)))

r.head('изменяющие маршруты обязаны отказать')
_, mods = common.call('GET', '/mods')
_, pl = common.call('GET', '/plugins')
some = mods['mods'][5]['mod']
for route, body in (
        ('/refresh', {}),
        ('/install', {'archive': os.path.join(os.sep, 'нет.7z'), 'name': 'Проба занятости'}),
        ('/toggle', {'mod': some, 'active': True}),
        ('/plugins/state', {'set': {pl['plugins'][0]['plugin']: True}, 'apply': True}),
        ('/plugins/order', {'order': [x['plugin'] for x in pl['plugins']], 'apply': True}),
        ('/run', {'binary': APP}),
        ('/mods/priority', {'mod': some, 'priority': 5}),
        ('/mods/rename', {'mod': some, 'newName': some + ' X'}),
        ('/mods/remove', {'mod': some})):
    _, res = common.call('POST', route, body, timeout=60)
    r.case('%s отказал' % route, res.get('busy') is True and res.get('applied') is False, True)

r.head('предпросмотр остаётся доступным')
_, res = common.call('POST', '/plugins/state', {'set': {pl['plugins'][0]['plugin']: True}})
r.case('без apply не считается изменением', res.get('busy'), None)

r.head('закрываю программу тем же мостом')
# Окно ищется с ожиданием, а не одним взглядом: процесс появляется раньше своего диалога,
# и на занятой машине разрыв доходит до десятков секунд. Раньше проверка успевала посмотреть
# до появления кнопок, не находила выхода и оставляла утилиту работать.
target = None
for _ in range(60):
    _, wins = common.call('GET', '/windows?pid=%d' % pid, timeout=60)
    for w in wins.get('windows') or []:
        for b in w.get('buttons') or []:
            cap = b.get('text', '')
            if any(x in cap.lower() for x in ('exit', 'close', 'выход', 'закрыть')):
                target = (w['hwnd'], cap)
                break
        if target:
            break
    if target:
        break
    time.sleep(1)
r.case('кнопка выхода дождалась', target is not None, True)
if target:
    _, res = common.call('POST', '/window',
                         {'hwnd': target[0], 'action': 'click', 'button': target[1]}, timeout=60)
    r.case('кнопка нажата', res.get('did'), 'click')
    r.note('', 'нажато: %s' % res.get('button'))
else:
    for w in wins.get('windows') or []:
        common.call('POST', '/window', {'hwnd': w['hwnd'], 'action': 'close'}, timeout=60)
    r.note('', 'кнопки выхода не нашлось, закрыл окна')

th.join(timeout=180)
for _ in range(60):
    time.sleep(1)
    _, ping = common.call('GET', '/ping', timeout=20)
    if not ping.get('busy'):
        break
r.head('после закрытия')
if ping.get('busy'):
    # Утилиту закрыть не удалось - это сбой проверки, а не замка: гасим, чтобы не оставить
    # сборку занятой для следующего набора и для человека.
    for w in (common.call('GET', '/windows?pid=%d' % pid, timeout=60)[1].get('windows') or []):
        common.call('POST', '/window', {'hwnd': w['hwnd'], 'action': 'close'}, timeout=60)
    r.note('уборка', 'программа не закрылась сама, послал закрытие окнам')
r.case('снова свободно', ping.get('busy'), None)

r.done()
