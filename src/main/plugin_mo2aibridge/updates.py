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
import uuid
import weakref

from . import i18n, winapi
from .base import Domain, one, resolve_nexus_domain, safe
from .updatepolicy import arc_time, decide, stamp

# Nexus file sections by category number, as the API reports them. The number is transport -
# it is what the reply carries; the rules downstream speak only in the words.
CATEGORY = {1: 'main', 2: 'update', 3: 'optional', 4: 'old', 5: 'miscellaneous',
            6: 'deleted', 7: 'archived'}


def _page_key(game, mod_id, domains):
    """A Nexus page identity, preserving game boundaries and configured aliases."""
    game = (game or '').strip().casefold()
    domain = resolve_nexus_domain(game, domains)
    return (domain or game).casefold(), int(mod_id)


def _download_identity(lines):
    """Read the source identity without depending on metadata field order."""
    fields = {}
    for line in lines:
        key, _, value = line.strip().partition('=')
        if key in ('modID', 'fileID', 'gameName'):
            fields[key] = value
    return (fields.get('gameName', ''), int(fields.get('modID') or 0),
            int(fields.get('fileID') or 0))


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
        self._quota_budget = None
        self._pending = {}
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._generation = uuid.uuid4().hex
        self._subscriptions = []
        self._seq = 0
        self._next_request = 0.0
        self._clock = time.monotonic
        self._wait = self._closed.wait
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
        with self._lock:
            if self._closed.is_set():
                raise SweepStop('upd.closed')
            if self._bridge is not None:
                return self._bridge
        bridge = self.o.createNexusBridge()
        subscriptions = []
        owner = weakref.ref(self)
        generation = self._generation

        def callback(method):
            def receive(*args):
                target = owner()
                if (target is not None and not target._closed.is_set()
                        and target._generation == generation):
                    getattr(target, method)(*args)
            return receive

        try:
            how = '%s/%s' % (
                _connect(bridge, 'filesAvailable', callback('_on_files'), subscriptions),
                _connect(bridge, 'requestFailed', callback('_on_failed'), subscriptions))
        except Exception:
            self._dispose_bridge(bridge, subscriptions)
            raise
        with self._lock:
            closed = self._closed.is_set()
            if not closed:
                self._how, self._bridge, self._subscriptions = how, bridge, subscriptions
        if closed:
            self._dispose_bridge(bridge, subscriptions)
            raise SweepStop('upd.closed')
        self.note(i18n.t('log.nexusBridge', how=self._how))
        return bridge

    @staticmethod
    def _dispose_bridge(bridge, subscriptions):
        """Only called on Qt, apart from deleteLater's thread-safe fallback in close."""
        for unsubscribe in subscriptions:
            safe(unsubscribe)
        safe(lambda: bridge.deleteLater())

    def close(self):
        """Retire this HTTP lifetime; outstanding native/HTTP requests are not cancelled.

        Call before closing the main-thread runner. Waiters wake immediately, callbacks
        cannot publish, and QObject disconnect/deferred deletion runs on its Qt thread.
        No worker is joined and no native call is made while holding the state lock.
        """
        with self._lock:
            if self._closed.is_set():
                return
            self._closed.set()
            self._generation = uuid.uuid4().hex
            for slot in self._pending.values():
                slot['state'] = 'closed'
                slot['event'].set()
            self._pending.clear()
            bridge, subscriptions = self._bridge, self._subscriptions
            self._bridge, self._subscriptions, self._api_key = None, [], None
        if bridge is not None:
            try:
                self.run_main(lambda: self._dispose_bridge(bridge, subscriptions))
            except Exception:
                # A runner already closed cannot dispatch disconnect. Closed weak
                # callbacks remain inert; QObject deletion is deferred to its owner.
                safe(lambda: bridge.deleteLater())

    def _choose_mode(self, timeout):
        """Once per session: is MO2's bridge usable, or do we ask the API directly with
        MO2's key."""
        with self._lock:
            if self._closed.is_set():
                raise SweepStop('upd.closed')
            if self._mode:
                return self._mode
        try:
            self.run_main(self._ensure_bridge, timeout=timeout)
            mode = 'bridge'
        except Exception as exc:
            self.note(i18n.t('log.nexusDirect', error=exc))
            mode = 'api'
        with self._lock:
            if self._closed.is_set():
                raise SweepStop('upd.closed')
            # A missing-token callback may already have retired native correlation.
            if not self._mode:
                self._mode = mode
            return self._mode

    # ---- asking the API directly -------------------------------------------
    def _key(self):
        """The Nexus API key - the very one MO2 keeps, from the Windows credential store.

        Read once and living only in this object's memory: the bridge hands it out through no
        route and never writes it to the log.
        """
        with self._lock:
            if self._closed.is_set():
                raise SweepStop('upd.closed')
            key = self._api_key
        if key is None:
            target = self.cfg.get('updates', {}).get('credentialTarget') or ''
            key = winapi.read_generic_credential(target) or ''
            with self._lock:
                if self._closed.is_set():
                    raise SweepStop('upd.closed')
                self._api_key = key
        return key

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
        with self._lock:
            if self._closed.is_set():
                return
            for h in ('x-rl-daily-remaining', 'x-rl-hourly-remaining', 'x-rl-daily-limit'):
                if h in low:
                    value = low[h]
                    if h == 'x-rl-daily-remaining':
                        try:
                            # Admission already reserved known quota. Out-of-order
                            # replies cannot restore allowance spent by other callers.
                            observed = int(value)
                            self._quota_budget = (observed if self._quota_budget is None else
                                                  min(observed, self._quota_budget))
                        except (ValueError, TypeError):
                            pass
                    # Public quota fields preserve the observed response headers;
                    # the reservation budget is separate process-local bookkeeping.
                    self._quota[h[5:]] = value
            if 'retry-after' in low:
                self._quota['retry-after'] = low['retry-after']

    def _remaining_quota(self):
        """Internal allowance after reservations; called with _lock held."""
        try:
            observed = int(self._quota.get('daily-remaining'))
        except (ValueError, TypeError):
            return self._quota_budget
        return observed if self._quota_budget is None else min(observed, self._quota_budget)

    def _quota_low(self):
        """Called with _lock held; quota is conservative within this HTTP lifetime."""
        left = self._remaining_quota()
        return (left is not None and
                left <= int(self.cfg.get('updates', {}).get('quotaReserve') or 50))

    def quota_exhausted(self):
        """Is there room left in the daily allowance, or must we stop here.

        The key belongs not to the bridge but to MO2 itself: spend it and the person is left
        without updates and without downloads in the manager, and will look for the cause
        anywhere but in this plugin. So a small reserve is always left behind.
        """
        with self._lock:
            return self._quota_low()

    def _try_admit_locked(self):
        """Commit dispatch now or return its delay; never wait or call transport here."""
        if self._closed.is_set():
            raise SweepStop('upd.closed')
        if self._quota_low():
            raise SweepStop('upd.quotaLow', left=self._remaining_quota())
        now = self._clock()
        wait_for = self._next_request - now
        if wait_for > 0:
            return wait_for
        delay = max(0.0, float(self.cfg.get('updates', {}).get('delaySec') or 0))
        self._next_request = now + delay
        left = self._remaining_quota()
        if left is not None:
            self._quota_budget = left - 1
        return 0.0

    def _admit(self):
        """Worker-side dispatch gate. Native Qt callers use its nonblocking check."""
        while True:
            with self._lock:
                wait_for = self._try_admit_locked()
                if wait_for <= 0:
                    return
            self._wait(wait_for)

    def _api_files(self, game, mod_id, timeout):
        domain = self.nexus_domain(game)
        if not domain:
            return None, i18n.t('upd.noDomain', game=game or '?')
        key = self._key()
        if not key:
            raise SweepStop('upd.noApiKey')
        upd = self.cfg.get('updates', {})
        url = '%s/v1/games/%s/mods/%d/files.json' % (
            (upd.get('apiHost') or 'https://api.nexusmods.com').rstrip('/'), domain, int(mod_id))
        headers = {'apikey': key, 'Accept': 'application/json',
                   'Application-Name': 'MO2ApIBridge',
                   'Application-Version': upd.get('appVersion') or ''}
        # Prepare domain/key/headers before admission, so credential or Qt delays
        # cannot accumulate already-admitted direct requests either.
        self._admit()
        try:
            status, hdrs, body = self._http_get(url, headers, timeout)
        except urllib.error.HTTPError as e:
            if self._closed.is_set():
                raise SweepStop('upd.closed')
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
            with self._lock:
                self._net_fails = 0
            return None, 'HTTP %d %s' % (e.code, (e.reason or '')[:80])
        except Exception as exc:
            with self._lock:
                if self._closed.is_set():
                    raise SweepStop('upd.closed')
                self._net_fails += 1
                fails = self._net_fails
            stop_at = int(upd.get('netFailsBeforeStop') or 5)
            if fails >= stop_at:
                raise SweepStop('upd.networkDown', fails=fails,
                                error=str(exc)[:120])
            return None, str(exc)[:200]
        with self._lock:
            if self._closed.is_set():
                raise SweepStop('upd.closed')
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
        page = self._page_key(game, mod_id) if mod_id is not None else None
        with self._lock:
            if self._closed.is_set():
                return None
            if user_data is None or user_data == '':
                # Tokenless payloads cannot distinguish a current request from a
                # timed-out predecessor, even with just one same-page waiter.
                # Retire native correlation and refetch safely via the direct API;
                # the uncorrelated files/error never satisfy any slot.
                if page is not None and any(s['page'] == page for s in self._pending.values()):
                    self._mode = 'api'
                    for slot in self._pending.values():
                        slot['state'] = 'uncorrelated'
                        slot['event'].set()
                    self._pending.clear()
                return None
            token = str(user_data)
            slot = self._pending.get(token)
            if slot is not None:
                if page is not None and slot['page'] != page:
                    return None
                if self._clock() >= slot['deadline']:
                    self._pending.pop(token)
                    slot['state'] = 'timeout'
                    slot['event'].set()
                    return None
                # Keep the slot registered until publishing under the same lock.
                # close() must also retire replies currently being converted.
                return slot
            return None

    def _complete(self, user_data, slot, **result):
        with self._lock:
            token = str(user_data)
            if self._closed.is_set() or self._pending.get(token) is not slot:
                return
            self._pending.pop(token)
            if self._clock() >= slot['deadline']:
                slot['state'] = 'timeout'
            else:
                slot.update(result, state='done')
            slot['event'].set()

    def _on_files(self, game, mod_id, user_data, files):
        slot = self._take(user_data, game, mod_id)
        if slot is None:
            return
        self._complete(user_data, slot, files=[_file_record(f) for f in (files or [])])

    def _on_failed(self, game, mod_id, *rest):
        # Signatures differ between MO2 builds: (game, modId, fileId, userData, error) or
        # (game, modId, userData, error). The error message is always last.
        error = str(rest[-1]) if rest else ''
        user_data = rest[-2] if len(rest) >= 2 else None
        slot = self._take(user_data, game, mod_id)
        if slot is None:
            return
        self._complete(user_data, slot, error=error or 'request failed')

    def _fetch(self, game, mod_id, timeout):
        """The page's file list, or an error.

        Through MO2's bridge the reply arrives as a signal on the main thread while the server
        thread waits on an event; directly it is plain HTTP from the server thread. There is a
        pause between requests either way: the Nexus API has a daily allowance.
        """
        if not self.nexus_domain(game):
            return None, i18n.t('upd.noDomain', game=game or '?')
        self._choose_mode(timeout)
        with self._lock:
            if self._closed.is_set():
                raise SweepStop('upd.closed')
            if self._mode == 'api':
                slot = None
            else:
                slot = {'event': threading.Event(), 'files': None, 'error': '',
                        'page': self._page_key(game, mod_id), 'state': 'pending',
                        'deadline': self._clock() + timeout, 'generation': self._generation,
                        'issued': False}
                self._seq += 1
                token = 'upd%s-%d' % (self._generation, self._seq)
                self._pending[token] = slot
        if slot is None:
            result = self._api_files(game, mod_id, timeout)
            if self._closed.is_set():
                raise SweepStop('upd.closed')
            return result

        def request():
            bridge = self._ensure_bridge()
            with self._lock:
                if self._closed.is_set() or self._pending.get(token) is not slot:
                    return
                if self._clock() >= slot['deadline']:
                    self._pending.pop(token)
                    slot['state'] = 'timeout'
                    slot['event'].set()
                    return
                # Qt may have stalled after the worker queued this job. Commit
                # timing/quota only here, immediately before actual native dispatch.
                wait_for = self._try_admit_locked()
                if wait_for > 0:
                    return wait_for
                slot['issued'] = True
            bridge.requestFiles(game, int(mod_id), token)
            return 0.0

        try:
            while True:
                remaining = slot['deadline'] - self._clock()
                if remaining <= 0:
                    break
                wait_for = self.run_main(request, timeout=remaining)
                with self._lock:
                    if slot['state'] != 'pending' or slot['issued']:
                        break
                # A deferred Qt job returns immediately. Only its HTTP worker
                # waits, then requeues the same token under the same deadline.
                self._wait(min(wait_for or 0, max(0.0, slot['deadline'] - self._clock())))
        except SweepStop:
            with self._lock:
                self._pending.pop(token, None)
                slot['state'] = 'closed' if self._closed.is_set() else 'failed'
                slot['event'].set()
            raise
        except Exception as exc:
            with self._lock:
                if slot['state'] == 'pending':
                    self._pending.pop(token, None)
                    slot.update(state='failed', error=str(exc))
                    slot['event'].set()
        slot['event'].wait(max(0.0, slot['deadline'] - self._clock()))
        with self._lock:
            if slot['state'] == 'pending':
                self._pending.pop(token, None)
                slot['state'] = 'timeout'
            state = slot['state']
        if self._closed.is_set() or state == 'closed':
            raise SweepStop('upd.closed')
        if state == 'uncorrelated':
            result = self._api_files(game, mod_id, timeout)
            if self._closed.is_set():
                raise SweepStop('upd.closed')
            return result
        if state == 'timeout':
            return None, i18n.t('upd.timeout', sec=timeout)
        return slot['files'], slot['error']

    # ---- local facts -------------------------------------------------------
    def _page_key(self, game, mod_id):
        return _page_key(game, mod_id, self.cfg.get('nexusDomains') or {})

    def _downloads_meta(self):
        """What we downloaded, from the .meta files beside the archives.
        (Nexus game, modID) -> {fileID: archive name}."""
        root, managed_game = self.run_main(lambda: (
            safe(self.o.downloadsPath, '') or '',
            safe(lambda: self.o.managedGame().gameShortName(), '') or ''))
        got = {}
        try:
            names = os.listdir(root)
        except Exception:
            return got, root
        for name in names:
            if not name.lower().endswith('.meta'):
                continue
            archive = name[:-5]
            if not os.path.isfile(os.path.join(root, archive)):
                continue
            try:
                with io.open(os.path.join(root, name), encoding='utf-8', errors='replace') as fh:
                    game, mod_id, file_id = _download_identity(fh)
            except Exception:
                continue
            if mod_id:
                # Older sidecars omit gameName and belong to the managed game.
                page = self._page_key(game or managed_game, mod_id)
                got.setdefault(page, {})[file_id] = archive
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
        managed_game = safe(lambda: self.o.managedGame().gameShortName(), '') or ''
        # Page -> the archives ALL of its mods were built from: the "downloaded but not
        # installed" decision is made per page rather than per mod, so the page neighbours are
        # needed even when a single mod was asked about.
        pages = {}
        for n in everyone:
            m = ml.getMod(n)
            nid = int(safe(m.nexusId, 0) or 0) if m is not None else 0
            arc = (safe(m.installationFile, '') or '') if m is not None else ''
            if nid and arc:
                game = safe(m.gameName, '') or managed_game
                pages.setdefault(self._page_key(game, nid), []).append(arc)
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
                        'game': safe(m.gameName, '') or managed_game,
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
        started = self._clock()

        targets, total, pages = self.run_main(
            lambda: self._targets(names, want_all, offset, limit))
        got_all, dl_root = self._downloads_meta()
        rows, stopped = [], ''
        file_cache = {}
        for t in targets:
            # The daily-allowance reserve is checked BEFORE the request, not after: the key
            # here is shared with MO2 itself, and eating it down to zero leaves the person
            # without updates and without downloads in the manager.
            if self.quota_exhausted():
                with self._lock:
                    left = self._remaining_quota()
                stopped = i18n.t('upd.quotaLow', left=left)
                break
            try:
                rows.append(self._check_one(t, got_all, dl_root, timeout, pages, file_cache))
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
        with self._lock:
            quota, mode = dict(self._quota), self._mode
        res = {'count': len(rows), 'mods': rows, 'elapsedSec': round(self._clock() - started, 1),
               'checked': sum(1 for r in rows if r.get('checked')),
               'rule': 'files-and-dates, versions never compared',
               # The sweep may have stopped short of the list: then it says why, and how
               # many mods went unasked. A silently truncated answer would read as
               # "everything was checked", and that would be untrue.
               'stopped': stopped, 'unchecked': max(0, len(targets) - len(rows)),
               # How Nexus was asked, and how many API requests remain in the allowance
               'via': mode or 'none', 'quota': quota}
        if want_all:
            res.update(offset=offset, limit=limit, total=total,
                       more=offset + len(rows) < total)
        return res

    def _check_one(self, t, got_all, dl_root, timeout, pages=None, file_cache=None):
        row = dict(t)
        row['checked'] = False
        if t.get('missing'):
            row.update(verdict='ERROR', why=i18n.t('err.noSuchMod', mod=t['mod']))
            return row
        if not t['nexusId']:
            row.update(verdict='NO-NEXUS-ID', why=i18n.t('upd.noNexusId'))
            return row
        page = self._page_key(t['game'], t['nexusId'])
        if file_cache is not None and page in file_cache:
            files, error = file_cache[page], ''
        else:
            files, error = self._fetch(t['game'], t['nexusId'], timeout)
            if files is not None and file_cache is not None:
                file_cache[page] = files
        if files is None:
            row.update(verdict='ERROR', why=i18n.t('upd.failed', error=error), error=error)
            return row
        # Keep accepting the older unscoped snapshots supplied to this helper;
        # route snapshots above always use the qualified page identity.
        got = got_all.get(page, got_all.get(t['nexusId'], {}))
        got_names = set(got.values())
        inst_time = self._installed_time(t['installationFile'], dl_root)
        inst_id = next((fid for fid, arc in got.items() if arc == t['installationFile']), 0)
        # The install moment is taken per page: the latest among the archives all of its mods
        # were built from. A patch built from an old archive does not mean the page's newest
        # file is not installed - it stands as a neighbouring mod.
        mates = list((pages or {}).get(page, (pages or {}).get(t['nexusId'], []))) + [t['installationFile']]
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
def _connect(bridge, name, callback, subscriptions=None):
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
            if subscriptions is not None:
                subscriptions.append(lambda: sig.disconnect(callback))
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
