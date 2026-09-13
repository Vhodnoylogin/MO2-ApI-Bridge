# -*- coding: utf-8 -*-
"""The busy lock against a real running program.

A tool is used rather than the game: it too is started through MO2 and holds its lock, but
it computes nothing until a button is pressed. The name comes from MO2AIBRIDGE_TEST_APP and
must be REGISTERED in MO2 - startApplication takes a name, not a path.

It is closed through the same /window route the bridge uses for windows in general.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

T = common.T

common.need_live()
APP = os.environ.get('MO2AIBRIDGE_TEST_APP') or 'TexGen'
r = common.Report(T('live.title', app=APP))

_, ping = common.call('GET', '/ping')
if ping.get('busy'):
    r.note(T('live.skipNoApp'), T('live.alreadyBusy', busy=ping['busy']))
    r.done()

r.head(T('live.startingViaMo2'))
box = {}


def launcher():
    # Launching sits behind the irreversible key, like removal: without it the bridge
    # starts nothing.
    box['res'] = common.call('POST', '/run',
                             {'binary': APP, 'wait': True, 'timeout': 900,
                              'iUnderstandTheRisk':
                                  'yes-I-read-the-docs-and-accept-irreversible-changes'},
                             timeout=900)


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
        r.note(T('live.skipNotStarted'),
               T('live.startFailed', app=APP, error=box['res'][1].get('error')))
        r.note(T('live.blank1'), T('live.setTestApp'))
        r.done()
if not pid:
    r.case(T('live.programUp'), False, True)
    r.done()

busy = ping['busy']
r.note(T('live.busy'), T('live.busyValue', app=busy['app'], pids=busy['pids'],
                         game=busy['isGame']))

r.head(T('live.readsMustWork'))
for route, key in (('/mods', 'count'), ('/plugins', 'count'), ('/profiles', 'current')):
    code, res = common.call('GET', route, timeout=60)
    r.case(T('live.readAnswersWhileBusy', route=route), code, 200)
    r.note(T('live.blank2'), '%s = %s' % (key, res.get(key)))

r.head(T('live.writesMustRefuse'))
_, mods = common.call('GET', '/mods')
_, pl = common.call('GET', '/plugins')
some = mods['mods'][5]['mod']
for route, body in (
        ('/refresh', {}),
        ('/install', {'archive': os.path.join(os.sep, 'no-such.7z'), 'name': 'BusyProbe'}),
        ('/toggle', {'mod': some, 'active': True}),
        ('/plugins/state', {'set': {pl['plugins'][0]['plugin']: True}, 'apply': True}),
        ('/plugins/order', {'order': [x['plugin'] for x in pl['plugins']], 'apply': True}),
        ('/run', {'binary': APP}),
        ('/mods/priority', {'mod': some, 'priority': 5}),
        ('/mods/rename', {'mod': some, 'newName': some + ' X'}),
        ('/mods/remove', {'mod': some})):
    _, res = common.call('POST', route, body, timeout=60)
    r.case(T('live.writeRefused', route=route), res.get('busy') is True and res.get('applied') is False, True)

r.head(T('live.previewStaysAvailable'))
_, res = common.call('POST', '/plugins/state', {'set': {pl['plugins'][0]['plugin']: True}})
r.case(T('live.noApplyIsNotAChange'), res.get('busy'), None)

r.head(T('live.closingViaBridge'))
# The window is waited for rather than glanced at once: the process appears before its
# dialog does, and on a busy machine the gap reaches tens of seconds. The check used to look
# before the buttons existed, found no exit, and left the tool running.
target = None
for _ in range(60):
    _, wins = common.call('GET', '/windows?pid=%d' % pid, timeout=60)
    for w in wins.get('windows') or []:
        for b in w.get('buttons') or []:
            cap = b.get('text', '')
            # Russian words on purpose: the tool follows the system language, and its
            # exit button is captioned in it. These are captions to match, not our own text.
            if any(x in cap.lower() for x in ('exit', 'close', 'выход', 'закрыть')):
                target = (w['hwnd'], cap)
                break
        if target:
            break
    if target:
        break
    time.sleep(1)
r.case(T('live.exitButtonAppeared'), target is not None, True)
if target:
    _, res = common.call('POST', '/window',
                         {'hwnd': target[0], 'action': 'click', 'button': target[1]}, timeout=60)
    r.case(T('live.buttonPressed'), res.get('did'), 'click')
    r.note(T('live.blank3'), T('live.pressed', button=res.get('button')))
else:
    for w in wins.get('windows') or []:
        common.call('POST', '/window', {'hwnd': w['hwnd'], 'action': 'close'}, timeout=60)
    r.note(T('live.blank4'), T('live.noExitButton'))

th.join(timeout=180)
for _ in range(60):
    time.sleep(1)
    _, ping = common.call('GET', '/ping', timeout=20)
    if not ping.get('busy'):
        break
r.head(T('live.afterClosing'))
if ping.get('busy'):
    # The tool could not be closed - that is a failure of the check, not of the lock: we
    # shut it down so the setup is not left busy for the next suite and for the person.
    for w in (common.call('GET', '/windows?pid=%d' % pid, timeout=60)[1].get('windows') or []):
        common.call('POST', '/window', {'hwnd': w['hwnd'], 'action': 'close'}, timeout=60)
    r.note(T('live.cleanup'), T('live.sentCloseToWindows'))
r.case(T('live.freeAgain'), ping.get('busy'), None)

r.done()
