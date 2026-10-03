# -*- coding: utf-8 -*-
"""The update rules: given a Nexus page and what we hold of it, what the verdict is.

Pure decision, and nothing else. Not one import that can reach the network, MO2 or the disk -
so the rules can be exercised with a table of made-up pages, which is the only way they are
checkable at all. `updates.py` keeps the other half: asking Nexus, the daily allowance, the
mod cards. The two halves used to live in one file, and every one of these rules sat behind
an HTTP client.

The rules themselves:

- **Version numbers are never compared.** Authors type them by hand, and they disagree
  between the page header, the file row and meta.ini. Two facts decide instead: which files
  of the page we downloaded (by fileID from the `.meta` beside the archive in downloads\\)
  and which section each file sits in now. A downloaded file that moved to Old files is out
  of date by definition; a downloaded file in a live section is current until the page offers
  a newer file of the same role.
- **Role** is the file name stripped of its version and of noise, but of nothing else:
  JContainers SE and JContainers VR, PapyrusUtil AE and PapyrusUtil GOG stay different roles
  and do not pass themselves off as updates to one another. A patch and a resource are a
  different role from the main file.
- **Doubt counts as "an update exists".** Anything that did not match with confidence lands
  in CANNOT-MATCH rather than being quietly declared current: a short list is something the
  user can look through.

The verdicts are the same words the build index uses: UPDATE-AVAILABLE, UP-TO-DATE,
CANNOT-MATCH, REUPLOADED, DOWNLOADED-NOT-INSTALLED, NO-NEXUS-ID, ERROR. The reason travels as
an i18n key in `why` and its substitutions in `whyArgs`, never as a finished phrase: the
decision does not know what language it will be read in.
"""

import calendar
import datetime
import re

DAY = 86400

# The Nexus file sections a page still offers. A file in any other section - Old files above
# all - is history rather than something on offer.
LIVE = ('main', 'update', 'optional', 'miscellaneous')

RE_VER = re.compile(r'\bv?\d+[\d._-]*[a-z]?\b')
RE_RES = re.compile(r'^\d+k$')
# The Cyrillic range is kept on purpose: translated mods carry Cyrillic in their file names,
# and stripping it would reduce two different files to the same role.
RE_PUNCT = re.compile(r'[^a-z0-9Ѐ-ӿ ]+')
RE_NOISE = re.compile(r'\b(main|file|files|version|ver|final|the|of|and|for)\b')

# Two files whose names differ only within one of these groups are siblings, not successors:
# a GOG build is no update to a Steam build, a UNP body is no update to CBBE.
STORE = set(['gog', 'epic', 'steam', 'gamepass', 'ms'])
BODY = set(['cbbe', 'unp', 'unpb', 'uunp', 'bhunp', '3ba', '3bbb', 'himbo', 'sos', 'tng', 'sam'])
# Edition markers travel alongside a change of store or body, but on their own they do not make
# a sibling: an AE build really can succeed an SE one, and that is exactly the case worth
# reporting.
EDITION = set(['se', 'sse', 'ae', 'vr', 'le', 'oldrim', 'special', 'anniversary', 'edition'])

RE_EPOCH = re.compile(r'-(\d{10})\.[0-9a-z]+$', re.I)
RE_ISO = re.compile(r'\b(20\d\d)-(\d\d)-(\d\d)T(\d\d)-(\d\d)Z\b')


def sibling(a, b):
    """Same product, different store or different body - never an update to one another."""
    d = a ^ b
    return bool(d) and d <= (STORE | BODY | EDITION) and bool(d & (STORE | BODY))


def arc_time(name):
    """When an archive with this name was uploaded to Nexus - read out of the name itself.

    MO2 keeps the canonical Nexus name, which ends in the upload time in seconds; downloads
    under the site's newer naming carry an ISO stamp. Either way the moment is in the name, so
    a file already removed from the page can still be dated."""
    m = RE_EPOCH.search(name or '')
    if m:
        return int(m.group(1))
    m = RE_ISO.search(name or '')
    if m:
        return int(calendar.timegm((int(m.group(1)), int(m.group(2)), int(m.group(3)),
                                    int(m.group(4)), int(m.group(5)), 0, 0, 0, 0)))
    return None


def stamp(ts):
    """A date in words from seconds since the epoch; empty when there is none or it is out
    of range."""
    if not ts:
        return ''
    try:
        return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime('%Y-%m-%d')
    except (OSError, OverflowError, ValueError):
        return ''


def _drop_version(m):
    """4k and 2k are texture resolutions, not versions: strip them and a 4K file and a 2K file
    become one role, and the pair turns into a phantom update."""
    t = m.group(0)
    return t if RE_RES.match(t) else ' '


def role(s):
    """A file name reduced to what it is: the version removed, the variant kept."""
    if not s:
        return ''
    s = s.lower()
    s = RE_VER.sub(_drop_version, s)
    s = RE_PUNCT.sub(' ', s)
    s = RE_NOISE.sub(' ', s)
    return ' '.join(sorted(set(s.split())))


def _brief(f):
    return {'name': f['name'], 'fileName': f['fileName'], 'fileId': f['fileId'],
            'version': f['version'], 'category': f['category'], 'time': f['time'],
            'date': stamp(f['time'])}


def decide(files, got_ids, got_names, installed_time, limit=3, disk_time=None):
    """The decision for one page. A pure function: the page's files, which of them were
    downloaded, and when the archives the page's mods were built from were uploaded.

    files          [{name, fileName, fileId, version, category, time}], category is a word
    got_ids        fileIDs of this page's downloaded files (from .meta in downloads)
    got_names      names of this page's archives present in downloads
    installed_time the latest upload moment among the archives ALL of this page's mods were
                   built from, or None. By page rather than by single mod on purpose: a patch,
                   or the second part of a multi-file page, may be built from an old archive
                   while the page's newest file stands as a neighbouring mod - and that is not
                   "not installed"
    disk_time      the latest upload moment among this page's archives on disk (for the case
                   where no file of the page is marked as downloaded)
    """
    if disk_time is None:
        disk_time = installed_time
    for f in files:
        f['got'] = f['fileId'] in got_ids or (f['fileName'] or '') in got_names
    live_main = sorted([f for f in files if f['category'] in ('main', 'update')],
                       key=lambda f: -(f['time'] or 0))
    res = {'items': [], 'extra': [], 'newest': _brief(live_main[0]) if live_main else None}
    got = [f for f in files if f['got']]

    if not got:
        # No file of the page is marked as downloaded while the mod is installed: the row it
        # was downloaded from is gone - the author replaced the file rather than moving it to
        # Old files. The archive's upload moment is in its name, so a comparison is still
        # possible.
        res['extra'] = [_brief(f) for f in live_main[:4]]
        if disk_time and live_main and (live_main[0]['time'] or 0) > disk_time + DAY:
            res.update(verdict='REUPLOADED', why='upd.replaced',
                       whyArgs={'mine': stamp(disk_time), 'theirs': stamp(live_main[0]['time'])})
        else:
            res.update(verdict='CANNOT-MATCH', why='upd.unmarked', whyArgs={})
        return res

    # What we hold - one per role, the newest wins
    held = {}
    for g in got:
        k = role(g['name'])
        if k not in held or (g['time'] or 0) > (held[k]['time'] or 0):
            held[k] = g

    for k, mine in sorted(held.items()):
        cands = [f for f in files
                 if f['category'] in LIVE and role(f['name']) == k
                 and (f['time'] or 0) > (mine['time'] or 0) and not f['got']
                 # the same version uploaded minutes later is a neighbour of one release,
                 # not something newer
                 and not (f['version'] and f['version'] == mine['version']
                          and (f['time'] - (mine['time'] or 0)) < DAY)]
        cands.sort(key=lambda f: -(f['time'] or 0))
        item = {'role': k, 'mine': _brief(mine), 'newer': [_brief(f) for f in cands[:limit]]}
        if cands:
            item['state'] = 'UPDATE-AVAILABLE'
        elif mine['category'] == 'old':
            # Our file was moved to Old files and nothing of the same name replaced it: the
            # author renamed or merged files, and the page needs human eyes
            item['state'] = 'CANNOT-MATCH'
            item['newer'] = [_brief(f) for f in live_main[:limit]]
        else:
            item['state'] = 'UP-TO-DATE'
        res['items'].append(item)

    newest_held = max([(g['time'] or 0) for g in got])
    taken = set((n['name'], n['time']) for it in res['items'] for n in it['newer'])
    held_tok = [set(role(g['name']).split()) for g in got]
    for f in files:
        if f['category'] not in ('main', 'update') or f['got']:
            continue
        if (f['time'] or 0) <= newest_held or (f['name'], f['time']) in taken:
            continue
        if f['version'] and any(f['version'] == g['version'] and (f['time'] - (g['time'] or 0)) < DAY
                                for g in got):
            continue                      # a neighbour of the same release, uploaded beside it
        # compared against each of our files separately: a page may hold several, and merging
        # their words would hide the one the candidate is actually a sibling of
        ft = set(role(f['name']).split())
        if any(sibling(ht, ft) for ht in held_tok):
            continue                      # a GOG build or another body, not something newer
        res['extra'].append(_brief(f))
    res['extra'].sort(key=lambda f: -(f['time'] or 0))
    hotfix = [f for f in res['extra'] if f['category'] == 'update']

    states = set(i['state'] for i in res['items'])
    newest_main = max([(f['time'] or 0) for f in live_main] or [0])

    # The newest Main file is already marked as downloaded: whatever sits in Old files is
    # download history. What that does not prove is that the file is installed: MO2 remembers
    # which archive a mod was built from, and that archive's upload moment is in its name, so
    # the two can be compared.
    top = [f for f in live_main if (f['time'] or 0) == newest_main]
    if top and any(f['got'] for f in top) and 'UPDATE-AVAILABLE' not in states:
        res['items'] = [i for i in res['items'] if i['state'] == 'UPDATE-AVAILABLE']
        if installed_time and installed_time < newest_main - DAY:
            res.update(verdict='DOWNLOADED-NOT-INSTALLED', why='upd.downloadedNotInstalled',
                       whyArgs={'theirs': stamp(newest_main), 'mine': stamp(installed_time)})
            res['extra'] = [_brief(f) for f in top]
        elif hotfix:
            res.update(verdict='UPDATE-AVAILABLE', why='upd.hotfix', whyArgs={})
        else:
            res.update(verdict='UP-TO-DATE', why='upd.current', whyArgs={})
            res['extra'] = []
        return res

    # All our files are in Old files and Main has moved past them: the author renamed the file
    # between releases, and this is still an update rather than a doubt
    all_old = all(i['mine']['category'] == 'old' for i in res['items'])
    if 'UPDATE-AVAILABLE' in states:
        res.update(verdict='UPDATE-AVAILABLE', why='upd.newer', whyArgs={})
    elif hotfix:
        res.update(verdict='UPDATE-AVAILABLE', why='upd.hotfix', whyArgs={})
    elif 'CANNOT-MATCH' in states and all_old and newest_main > newest_held + DAY:
        res.update(verdict='UPDATE-AVAILABLE', why='upd.renamed', whyArgs={})
    elif 'CANNOT-MATCH' in states:
        res.update(verdict='CANNOT-MATCH', why='upd.retired', whyArgs={})
    elif res['extra']:
        res.update(verdict='CANNOT-MATCH', why='upd.otherRole', whyArgs={})
    else:
        res.update(verdict='UP-TO-DATE', why='upd.current', whyArgs={})
    return res
