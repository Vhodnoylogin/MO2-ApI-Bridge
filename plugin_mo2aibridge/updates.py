# -*- coding: utf-8 -*-
"""Mod updates: the one place where the setup asks Nexus whether a newer file exists.

The bridge asks Nexus itself - through `IOrganizer.createNexusBridge()`, that is by the same
route and with the same API key MO2 uses. Chats and scripts compare nothing: they get both the
facts and the decision from the bridge, and remembering the result is the index's business.

This file is the transport half: the request through MO2's bridge or straight at the API, the
daily allowance, the mod cards and what lies in downloads\\. **The decision is not here** - it
is `updatepolicy.py`, which imports nothing that can reach the network and can therefore be
checked with a table of made-up pages. The rules themselves, and why version numbers are never
compared, are written down at the head of that file.

The verdicts are the same words the build index uses: UPDATE-AVAILABLE, UP-TO-DATE,
CANNOT-MATCH, REUPLOADED, DOWNLOADED-NOT-INSTALLED, NO-NEXUS-ID, ERROR.
"""

import io
import json
import os
import threading
import time
import urllib.error
import urllib.request

from . import i18n, winapi
from .base import Domain, one, safe
from .updatepolicy import arc_time, decide, stamp

# Nexus file sections by category number, as the API reports them. The number is transport -
# it is what the reply carries; the rules downstream speak only in the words.
CATEGORY = {1: 'main', 2: 'update', 3: 'optional', 4: 'old', 5: 'miscellaneous',
            6: 'deleted', 7: 'archived'}


class SweepStop(Exception):
    """The sweep must go no further: the key is bad, the allowance is spent, or the link died.

    A separate kind of error is needed because the failure of ONE mod and the failure of the
    CHANNEL are different things. The first is a row in the answer beside the others; the second
    means the next thousand requests will fail in exactly the same way. The sweep stops, what
    was gathered is returned, and one reason is named for the whole answer.
    """

    def __init__(self, key, **kw):
        super().__init__(i18n.t(key, **kw))
        self.key = key


# ================================================================ the area
class Updates(Domain):

    def __init__(self, ctx, guard):
        super().__init__(ctx, guard)
        self._bridge = None
        self._how = ''
        # How Nexus is being asked: '' - not decided yet, 'bridge' - through MO2's own
        # bridge, 'api' - directly, with MO2's key from the Windows credential store.
        self._mode = ''
        self._api_key = None
        self._quota = {}
        self._pending = {}
        self._lock = threading.Lock()
        self._seq = 0
        self._last_request = 0.0
        # Consecutive network failures. A dead link has no response code, and the only way to
        # tell it from a single mishap is by counting: one happens, five in a row means the
        # channel is down.
        self._net_fails = 0

    # ---- MO2's own Nexus bridge -------------------------------------------
    def _ensure_bridge(self):
        """Create the Nexus bridge once and subscribe to its replies. Called FROM the main
        thread.

        In MO2 2.5.2 subscribing to filesAvailable from Python is impossible: the signal
        carries QList<ModRepositoryFileInfo*>, a type PyQt does not know, and connect is
        refused. MO2's bridge is then not used at all and Nexus is asked directly - see
        _api_files.
        """
        if self._bridge is not None:
            return self._bridge
        bridge = self.o.createNexusBridge()
        self._how = '%s/%s' % (_connect(bridge, 'filesAvailable', self._on_files),
                               _connect(bridge, 'requestFailed', self._on_failed))
        self._bridge = bridge
        self.note(i18n.t('log.nexusBridge', how=self._how))
        return bridge

    def _choose_mode(self, timeout):
        """Once per session: is MO2's bridge usable, or do we ask the API directly with
        MO2's key."""
        if self._mode:
            return self._mode
        try:
            self.run_main(self._ensure_bridge, timeout=timeout)
            self._mode = 'bridge'
        except Exception as exc:
            self.note(i18n.t('log.nexusDirect', error=exc))
            self._mode = 'api'
        return self._mode

    # ---- asking the API directly -------------------------------------------
    def _key(self):
        """The Nexus API key - the very one MO2 keeps, from the Windows credential store.

        Read once and living only in this object's memory: the bridge hands it out through no
        route and never writes it to the log.
        """
        if self._api_key is None:
            target = self.cfg.get('updates', {}).get('credentialTarget') or ''
            self._api_key = winapi.read_generic_credential(target) or ''
        return self._api_key

    def _http_get(self, url, headers, timeout):
        """A GET with headers; separated out so the checks can substitute their own reply."""
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read().decode('utf-8', 'replace')

    def _absorb_quota(self, hdrs):
        """Take the allowance headers off a reply - any reply, a failure included.

        The parsing used to sit on the successful path only, so a 429 - the one reply that MUST
        refresh the remaining count and carry Retry-After - never reached it at all.
        """
        low = {str(k).lower(): v for k, v in dict(hdrs or {}).items()}
        for h in ('x-rl-daily-remaining', 'x-rl-hourly-remaining', 'x-rl-daily-limit'):
            if h in low:
                self._quota[h[5:]] = low[h]
        if 'retry-after' in low:
            self._quota['retry-after'] = low['retry-after']

    def quota_exhausted(self):
        """Is there room left in the daily allowance, or must we stop here.

        The key belongs not to the bridge but to MO2 itself: spend it and the person is left
        without updates and without downloads in the manager, and will look for the cause
        anywhere but in this plugin. So a small reserve is always left behind.
        """
        try:
            left = int(self._quota.get('daily-remaining'))
        except Exception:
            return False
        return left <= int(self.cfg.get('updates', {}).get('quotaReserve') or 50)

    def _api_files(self, game, mod_id, timeout):
        key = self._key()
        if not key:
            raise SweepStop('upd.noApiKey')
        upd = self.cfg.get('updates', {})
        domain = self.nexus_domain(game)
        if not domain:
            return None, i18n.t('upd.noDomain', game=game or '?')
        url = '%s/v1/games/%s/mods/%d/files.json' % (
            (upd.get('apiHost') or 'https://api.nexusmods.com').rstrip('/'), domain, int(mod_id))
        headers = {'apikey': key, 'Accept': 'application/json',
                   'Application-Name': 'MO2ApIBridge',
                   'Application-Version': upd.get('appVersion') or ''}
        try:
            status, hdrs, body = self._http_get(url, headers, timeout)
        except urllib.error.HTTPError as e:
            self._absorb_quota(getattr(e, 'headers', None))
            # A PAGE failing and the CHANNEL failing are different things, and they used to
            # be one. A bad key or a spent allowance applies not to one mod but to all of
            # them: carrying on meant sending fifteen hundred certainly-doomed requests, and
            # on a 429 extending the punishment with every one of them.
            if e.code in (401, 403):
                raise SweepStop('upd.badKey', code=e.code)
            if e.code == 429:
                raise SweepStop('upd.rateLimited',
                                retry=self._quota.get('retry-after') or '?')
            self._net_fails = 0
            return None, 'HTTP %d %s' % (e.code, (e.reason or '')[:80])
        except Exception as exc:
            self._net_fails += 1
            stop_at = int(upd.get('netFailsBeforeStop') or 5)
            if self._net_fails >= stop_at:
                raise SweepStop('upd.networkDown', fails=self._net_fails,
                                error=str(exc)[:120])
            return None, str(exc)[:200]
        self._net_fails = 0
        self._absorb_quota(hdrs)
        try:
            data = json.loads(body)
        except Exception as exc:
            return None, 'bad json: %s' % exc
        out = []
        for f in data.get('files') or []:
            cat = f.get('category_id')
            try:
                cat = int(cat)
            except Exception:
                cat = 0
            out.append({'name': f.get('name') or '', 'fileName': f.get('file_name') or '',
                        'fileId': int(f.get('file_id') or 0),
                        'version': str(f.get('version') or ''),
                        'category': CATEGORY.get(cat, str(cat)),
                        'time': int(f.get('uploaded_timestamp') or 0)})
        return out, ''

    def _take(self, user_data, game=None, mod_id=None):
        with self._lock:
            slot = self._pending.pop(str(user_data), None)
            if slot is None and mod_id is not None:
                # The token did not match - look up by the game/mod pair: it is the same
                # reply
                for key, cand in list(self._pending.items()):
                    if cand['modId'] == int(mod_id):
                        return self._pending.pop(key)
            return slot

    def _on_files(self, game, mod_id, user_data, files):
        slot = self._take(user_data, game, mod_id)
        if slot is None:
            return
        slot['files'] = [_file_record(f) for f in (files or [])]
        slot['event'].set()

    def _on_failed(self, game, mod_id, *rest):
        # Signatures differ between MO2 builds: (game, modId, fileId, userData, error) or
        # (game, modId, userData, error). The error message is always last.
        error = str(rest[-1]) if rest else ''
        user_data = rest[-2] if len(rest) >= 2 else None
        slot = self._take(user_data, game, mod_id)
        if slot is None:
            return
        slot['error'] = error or 'request failed'
        slot['event'].set()

    def _fetch(self, game, mod_id, timeout):
        """The page's file list, or an error.

        Through MO2's bridge the reply arrives as a signal on the main thread while the server
        thread waits on an event; directly it is plain HTTP from the server thread. There is a
        pause between requests either way: the Nexus API has a daily allowance.
        """
        delay = float(self.cfg.get('updates', {}).get('delaySec') or 0)
        wait_for = self._last_request + delay - time.time()
        if wait_for > 0:
            time.sleep(wait_for)
        self._last_request = time.time()
        if self._choose_mode(timeout) == 'api':
            return self._api_files(game, mod_id, timeout)
        self._seq += 1
        token = 'upd%d' % self._seq
        slot = {'event': threading.Event(), 'files': None, 'error': '', 'modId': int(mod_id)}
        with self._lock:
            self._pending[token] = slot
        try:
            self.run_main(lambda: self._ensure_bridge().requestFiles(game, int(mod_id), token),
                          timeout=timeout)
        except Exception as exc:
            with self._lock:
                self._pending.pop(token, None)
            return None, str(exc)
        if not slot['event'].wait(timeout):
            with self._lock:
                self._pending.pop(token, None)
            return None, i18n.t('upd.timeout', sec=timeout)
        return slot['files'], slot['error']

    # ---- local facts -------------------------------------------------------
    def _downloads_meta(self):
        """What we downloaded, from the .meta files beside the archives.
        modID -> {fileID: archive name}."""
        root = safe(self.o.downloadsPath, '') or ''
        got = {}
        try:
            names = os.listdir(root)
        except Exception:
            return got, root
        for name in names:
            if not name.lower().endswith('.meta'):
                continue
            mod_id = file_id = None
            try:
                with io.open(os.path.join(root, name), encoding='utf-8', errors='replace') as fh:
                    for line in fh:
                        k, _, v = line.strip().partition('=')
                        if k == 'modID':
                            mod_id = int(v or 0)
                        elif k == 'fileID':
                            file_id = int(v or 0)
                        if mod_id is not None and file_id is not None:
                            break
            except Exception:
                continue
            if mod_id:
                got.setdefault(mod_id, {})[file_id or 0] = name[:-5]
        return got, root

    def _installed_time(self, arc, root):
        if not arc:
            return None
        t = arc_time(arc)
        if t:
            return t
        try:
            return int(os.path.getmtime(os.path.join(root, arc)))
        except OSError:
            return None

    def _targets(self, names, want_all, offset, limit):
        """The mod cards to check. Called ON the main thread."""
        ml = self.o.modList()
        everyone = list(ml.allModsByProfilePriority())
        # Page -> the archives ALL of its mods were built from: the "downloaded but not
        # installed" decision is made per page rather than per mod, so the page neighbours are
        # needed even when a single mod was asked about.
        pages = {}
        for n in everyone:
            m = ml.getMod(n)
            nid = int(safe(m.nexusId, 0) or 0) if m is not None else 0
            arc = (safe(m.installationFile, '') or '') if m is not None else ''
            if nid and arc:
                pages.setdefault(nid, []).append(arc)
        if want_all:
            names = everyone
        out, total = [], 0
        for n in names:
            m = ml.getMod(n)
            if m is None:
                out.append({'mod': n, 'missing': True})
                continue
            if want_all and safe(m.isSeparator, False):
                continue
            total += 1
            if want_all and (total <= offset or len(out) >= limit):
                continue
            out.append({'mod': n,
                        'nexusId': int(safe(m.nexusId, 0) or 0),
                        'game': safe(m.gameName, '') or safe(
                            lambda: self.o.managedGame().gameShortName(), ''),
                        'installationFile': safe(m.installationFile, '') or '',
                        'version': safe(lambda: m.version().displayString(), ''),
                        'mo2NewestVersion': safe(lambda: m.newestVersion().displayString(), ''),
                        'ignoredVersion': safe(lambda: m.ignoredVersion().displayString(), '')})
        return out, total, pages

    # ---- the route ---------------------------------------------------------
    def updates(self, q):
        """GET /updates?mod=A&mod=B  or  /updates?all=1&offset=0&limit=50

        A check against a live Nexus through MO2. Works while MO2 is busy: it is a read.
        """
        names = (q or {}).get('mod') or []
        if isinstance(names, str):
            names = [names]
        want_all = one(q, 'all', '') not in ('', '0', 'false', 'no')
        if not names and not want_all:
            raise ValueError(i18n.t('err.needModOrAll'))
        upd = self.cfg.get('updates', {})
        limit = int(one(q, 'limit', str(upd.get('maxPerCall') or 50)))
        offset = int(one(q, 'offset', '0') or 0)
        timeout = float(one(q, 'timeout', str(upd.get('timeoutSec') or 30)))
        started = time.time()

        targets, total, pages = self.run_main(
            lambda: self._targets(names, want_all, offset, limit))
        got_all, dl_root = self._downloads_meta()
        rows, stopped = [], ''
        for t in targets:
            # The daily-allowance reserve is checked BEFORE the request, not after: the key
            # here is shared with MO2 itself, and eating it down to zero leaves the person
            # without updates and without downloads in the manager.
            if self.quota_exhausted():
                stopped = i18n.t('upd.quotaLow', left=self._quota.get('daily-remaining'))
                break
            try:
                rows.append(self._check_one(t, got_all, dl_root, timeout, pages))
            except SweepStop as exc:
                stopped = str(exc)
                # The mod the sweep stumbled on still goes into the answer. Otherwise the
                # answer does not show where it stopped, and for a single-mod request nothing
                # would be left at all - only a general reason tied to no mod.
                row = dict(t)
                row.update(checked=False, verdict='ERROR', why=stopped, error=stopped)
                rows.append(row)
                self.note(i18n.t('log.sweepStopped', why=stopped))
                break
        res = {'count': len(rows), 'mods': rows, 'elapsedSec': round(time.time() - started, 1),
               'checked': sum(1 for r in rows if r.get('checked')),
               'rule': 'files-and-dates, versions never compared',
               # The sweep may have stopped short of the list: then it says why, and how
               # many mods went unasked. A silently truncated answer would read as
               # "everything was checked", and that would be untrue.
               'stopped': stopped, 'unchecked': max(0, len(targets) - len(rows)),
               # How Nexus was asked, and how many API requests remain in the allowance
               'via': self._mode or 'none', 'quota': dict(self._quota)}
        if want_all:
            res.update(offset=offset, limit=limit, total=total,
                       more=offset + len(rows) < total)
        return res

    def _check_one(self, t, got_all, dl_root, timeout, pages=None):
        row = dict(t)
        row['checked'] = False
        if t.get('missing'):
            row.update(verdict='ERROR', why=i18n.t('err.noSuchMod', mod=t['mod']))
            return row
        if not t['nexusId']:
            row.update(verdict='NO-NEXUS-ID', why=i18n.t('upd.noNexusId'))
            return row
        files, error = self._fetch(t['game'], t['nexusId'], timeout)
        if files is None:
            row.update(verdict='ERROR', why=i18n.t('upd.failed', error=error), error=error)
            return row
        got = got_all.get(t['nexusId'], {})
        got_names = set(got.values())
        inst_time = self._installed_time(t['installationFile'], dl_root)
        inst_id = next((fid for fid, arc in got.items() if arc == t['installationFile']), 0)
        # The install moment is taken per page: the latest among the archives all of its mods
        # were built from. A patch built from an old archive does not mean the page's newest
        # file is not installed - it stands as a neighbouring mod.
        mates = list((pages or {}).get(t['nexusId'], [])) + [t['installationFile']]
        page_time = max([self._installed_time(a, dl_root) or 0 for a in mates] or [0]) or None
        disk_time = max([page_time or 0] +
                        [self._installed_time(a, dl_root) or 0 for a in got_names]) or None
        d = decide(files, set(got), got_names, page_time,
                   limit=int(self.cfg.get('updates', {}).get('newerLimit') or 3),
                   disk_time=disk_time)
        row.update(checked=True, verdict=d['verdict'],
                   why=i18n.t(d['why'], **d['whyArgs']),
                   installed={'file': t['installationFile'], 'fileId': inst_id,
                              'time': inst_time, 'date': stamp(inst_time),
                              'pageTime': page_time, 'pageDate': stamp(page_time)},
                   downloaded=sorted(got.values()),
                   newest=d['newest'], items=d['items'], extra=d['extra'],
                   files=len(files))
        return row


# ================================================================ mobase plumbing
def _connect(bridge, name, callback):
    """Subscribe to the bridge's reply by whichever means this MO2 build offers.

    In some builds it is a method onFilesAvailable(callback), in others a Qt signal
    filesAvailable or filesAvailable_ with .connect(). All are tried, in order.
    """
    cap = name[0].upper() + name[1:]
    reg = getattr(bridge, 'on' + cap, None)
    if callable(reg):
        reg(callback)
        return 'on' + cap
    for attr in (name, name + '_'):
        sig = getattr(bridge, attr, None)
        connect = getattr(sig, 'connect', None)
        if callable(connect):
            connect(callback)
            return attr
    raise RuntimeError('nexus bridge has no %s' % name)


def _secs(value):
    """A QDateTime, a number or a string -> seconds since the epoch, or 0."""
    if value is None:
        return 0
    f = getattr(value, 'toSecsSinceEpoch', None)
    if callable(f):
        return int(safe(f, 0) or 0)
    try:
        return int(value)
    except Exception:
        return 0


def _text(value):
    f = getattr(value, 'displayString', None)
    if callable(f):
        return safe(f, '') or ''
    return '' if value is None else str(value)


def _file_record(f):
    """A flat file record out of ModRepositoryFileInfo: only what the decision needs."""
    g = lambda attr, default=None: safe(lambda: getattr(f, attr), default)
    cat = g('fileCategory', 0)
    try:
        cat = int(cat)
    except Exception:
        cat = 0
    return {'name': _text(g('name', '')) or '',
            'fileName': _text(g('fileName', '')) or '',
            'fileId': int(g('fileID', 0) or 0),
            'version': _text(g('version', '')),
            'category': CATEGORY.get(cat, str(cat)),
            'time': _secs(g('fileTime'))}
