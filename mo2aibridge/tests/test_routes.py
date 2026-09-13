# -*- coding: utf-8 -*-
"""Every route against a live MO2.

Nothing that leaves a trace is touched: /install, /toggle, /run, /window and /vfsexport
change the setup or take minutes. The irreversible three are checked DELIBERATELY without
the key - a refusal is expected, and it is the refusal that proves the lock is in place.
"""
import os
import subprocess
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

T = common.T

common.need_live()
r = common.Report(T('routes.title'))


def _alive_pids():
    """Ids of live processes - we ask the system, not the bridge."""
    out = subprocess.run(['powershell', '-NoProfile', '-Command',
                          '(Get-Process).Id -join ","'],
                         capture_output=True, text=True).stdout.strip()
    return {int(x) for x in out.split(',') if x.strip().isdigit()}


def get(route, params=None):
    q = ('?' + urllib.parse.urlencode(params)) if params else ''
    return common.call('GET', route + q)


r.head(T('routes.reads'))
code, ping = get('/ping')
r.case(T('routes.pingAnswers'), ping.get('ok'), True)
r.note(T('routes.profileAndVersion'), '%s, MO2 %s' % (ping.get('profile'), ping.get('mo2Version')))

code, api = get('/api')
r.case(T('routes.apiNamesFour'), len(api), 4)

code, mods = get('/mods')
r.case(T('routes.modsList'), (mods.get('count') or 0) > 0, True)
r.note(T('routes.modsCount'), mods.get('count'))
names = [m['mod'] for m in mods['mods'] if not m['mod'].endswith('_separator')]
sample = names[0]

code, card = get('/mod', {'name': sample})
r.case(T('routes.modCard'), card.get('mod'), sample)

code, an = get('/analyze', {'name': sample, 'conflicts': '0'})
r.case(T('routes.analyzeNoConflicts'), isinstance(an.get('files'), int), True)
code, an = get('/analyze', {'name': sample, 'conflicts': '1', 'limit': '40'})
r.case(T('routes.analyzeWithConflicts'), 'conflicts' in an, True)

code, prof = get('/profiles')
r.case(T('routes.profilesKnowsActive'), prof.get('current'), ping.get('profile'))

code, pl = get('/plugins')
r.case(T('routes.pluginsOrder'), (pl.get('count') or 0) > 0, True)
r.note(T('routes.pluginsCount'), pl.get('count'))

code, vfs = get('/vfs', {'path': '.', 'filter': '*.esp'})
r.case(T('routes.vfsFoundEsp'), len(vfs.get('files') or []) > 0, True)
one = os.path.basename((vfs.get('files') or ['Skyrim.esm'])[0])

code, org = get('/origins', {'path': one})
r.case(T('routes.originsNamedProvider'), len(org.get('origins') or []) > 0, True)
code, res = get('/resolve', {'path': one})
r.case(T('routes.resolveGaveFile'), bool(res.get('real')), True)

code, d = get('/dirs', {'path': 'meshes'})
r.case(T('routes.dirsFoundMeshes'), len(d.get('dirs') or []) > 0, True)

code, pr = get('/procs')
r.case(T('routes.procsAnswers'), 'procs' in pr, True)
# A regression check: the launch list accumulates all session and never prunes itself.
# While it carried no liveness mark, long-closed programs looked like running ones.
r.case(T('routes.eachEntryAlive'),
       all('alive' in x for x in pr.get('procs') or []), True)
ghosts = [x for x in pr.get('procs') or []
          if x['alive'] and x['pid'] not in _alive_pids()]
r.case(T('routes.noWalkingDead'), ghosts, [])

code, w = get('/windows')
r.case(T('routes.windowsNeedsPid'), code, 500)

r.head(T('routes.actionsNoEffect'))
code, ref = common.call('POST', '/refresh', {})
r.case(T('routes.refreshReread'), ref.get('refreshed'), True)

first = pl['plugins'][0]['plugin']
code, st = common.call('POST', '/plugins/state', {'set': {first: True}})
r.case(T('routes.stateNoApply'), st.get('applied'), False)

order = [x['plugin'] for x in pl['plugins']]
code, od = common.call('POST', '/plugins/order', {'order': order})
r.case(T('routes.orderNoApply'), od.get('applied'), False)
code, od = common.call('POST', '/plugins/order', {'order': order[:5]})
r.case(T('routes.orderRejectsPartial'),
       od.get('applied') is False and bool(od.get('error')), True)

r.head(T('routes.dangerKeyWithheld'))
for route, body in (('/mods/priority', {'mod': sample, 'priority': 5}),
                    ('/mods/rename', {'mod': sample, 'newName': sample + ' PROBE'}),
                    ('/mods/remove', {'mod': sample})):
    code, res = common.call('POST', route, body)
    r.case(T('routes.blocked', route=route), res.get('applied'), False)
    r.note(T('routes.blank'), T('routes.reason', why=res.get('blocked')))

r.head(T('routes.untouchedOnPurpose'))
for route, why in (('/install', T('routes.wouldCreateMod')),
                   ('/toggle', T('routes.wouldChangeProfile')),
                   ('/run', T('routes.wouldLaunchProgram')),
                   ('/window', T('routes.wouldPressButton')),
                   ('/vfsexport', T('routes.takesMinutes'))):
    r.note(route, why)

r.done()
