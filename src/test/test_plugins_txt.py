# -*- coding: utf-8 -*-
"""A plugin state change must reach plugins.txt and survive a /refresh.

A regression check for a defect that cost three ruined runs: setState changed a list in
MO2's memory, MO2 rewrote the file only on exit, the game started from the file, and any
/refresh between the edit and the exit read the file back and silently undid the edit.

The setup is put back as it was, and at the end the file is compared against a byte
snapshot.
"""
import hashlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

T = common.T

common.need_live()
r = common.Report(T('plugins.title'))

_, prof = common.call('GET', '/profiles')
TXT = os.path.join(prof['path'], prof['current'], 'plugins.txt')


def digest():
    return hashlib.md5(io.open(TXT, 'rb').read()).hexdigest()[:12]


def starred(name):
    """Is the asterisk there - by the file, not by memory."""
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
r.note(T('plugins.subject'), T('plugins.subjectValue', plugin=victim, starred=was))
if was is None:
    r.case(T('plugins.inFile'), False, True)
    r.done()

r.head(T('plugins.toggleViaBridge'))
_, res = common.call('POST', '/plugins/state', {'set': {victim: not was}, 'apply': True})
r.case(T('plugins.applied'), res.get('applied'), True)
r.case(T('plugins.fileRewritten'), (res.get('file') or {}).get('written'), True)
r.case(T('plugins.asteriskChanged'), starred(victim), not was)

r.head(T('plugins.refreshKeeps'))
common.call('POST', '/refresh', {})
r.case(T('plugins.fileStillNew'), starred(victim), not was)
_, pl2 = common.call('GET', '/plugins')
r.case(T('plugins.memoryAgrees'),
       next(p['active'] for p in pl2['plugins'] if p['plugin'] == victim), not was)

r.head(T('plugins.restoring'))
_, res = common.call('POST', '/plugins/state', {'set': {victim: was}, 'apply': True})
r.case(T('plugins.appliedBack'), res.get('applied'), True)
common.call('POST', '/refresh', {})
r.case(T('plugins.asteriskBack'), starred(victim), was)
r.case(T('plugins.fileByteIdentical'), digest(), start)
r.case(T('plugins.lineEndingsKept'), line_ends(), crlf_before)

r.done()
