# -*- coding: utf-8 -*-
"""Shared ground for the domain classes: context, the change wrapper, the reply shape.

The domain layer is split into classes by area - busy state, reads, installing, the mod
list, plugin order, launching. They all need the same things: `IOrganizer`, a way into the
main thread, somewhere to record what happened, the settings. That is `Context`. `Domain`
is the base of an area class: it holds the context and the busy lock, and it knows one
shared procedure for any change.

The procedure, and it is the same one for all nine write routes:

    refuse if MO2 is busy  ->  validate the input  ->  do the work  ->  sign the reply

Signing means two keys added to every reply about a change: `op` (which operation) and
`applied` (was it done), plus `reason` (`busy` or `danger`) on any refusal. Existing keys
are left alone: a reply shape may only gain keys, or other callers break silently.
"""
import os

from . import i18n

DANGER_KEY = 'iUnderstandTheRisk'
DANGER_VALUE = 'yes-I-read-the-docs-and-accept-irreversible-changes'


def one(q, key, default):
    """One parameter from the query string: parse_qs hands back lists, we want a string."""
    v = (q or {}).get(key)
    return v[0] if isinstance(v, list) and v else (v if isinstance(v, str) else default)


def flag(body, key, default=False):
    """A boolean field of the request body, strictly.

    `bool()` will not do here: the string "false" is truthy in Python, so a request of
    {"active": "false"} used to enable a mod and report success - the opposite of what was
    asked. Shell and curl clients send booleans as strings all the time, so strings are
    parsed explicitly and anything unrecognised is a refusal rather than a guess: on a
    switch like withArchive, the price of guessing is a file in the Recycle Bin.
    """
    v = (body or {}).get(key)
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, int) and v in (0, 1):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ('true', 'yes', 'on', '1'):
            return True
        if s in ('false', 'no', 'off', '0'):
            return False
    raise ValueError(i18n.t('err.badFlag', key=key, value=v))


def folder_name(name):
    """A single-component folder name fit to be joined into a path, or ValueError.

    A mod name arrives from outside and is joined into a path inside mods\\. A single
    component cannot escape that folder; anything else can. os.path.join on Windows drops
    the first part when the second is absolute, so 'C:\\Users\\...' would name a stranger's
    folder, and '..' would climb one level up - to the profiles, the downloads and
    overwrite. The price of a miss here is other people's files in the Recycle Bin, so the
    check comes before any work at all.
    """
    n = (name or '').strip()
    if not n:
        raise ValueError(i18n.t('err.needName'))
    if (n in ('.', '..') or os.path.basename(n) != n or ':' in n
            or os.sep in n or (os.altsep and os.altsep in n)):
        raise ValueError(i18n.t('err.badName', name=n))
    return n


def inside(root, path):
    """Is path inside root.

    The second line of defence after folder_name: the name is checked, but the folder
    itself is created by MO2, and making sure it landed where expected is cheaper than
    taking it on trust.
    """
    r = os.path.realpath(root)
    p = os.path.realpath(path)
    return p == r or p.startswith(r + os.sep)


def safe(fn, default=None):
    """The call's value or a default: on some mods the card methods raise."""
    try:
        v = fn()
        return v if v is not None else default
    except Exception:
        return default


def stamp(res, op, applied=True):
    """Sign a reply with its operation. It only adds: existing keys are never touched."""
    if isinstance(res, dict):
        res.setdefault('op', op)
        res.setdefault('applied', applied)
    return res


class Context(object):
    """Everything any area needs: MO2, the main thread, the log, the docs, the settings."""

    def __init__(self, organizer, run_main, docs, note, cfg):
        self.o = organizer
        self.run_main = run_main
        self.docs = docs
        self.note = note
        self.cfg = cfg


class Domain(object):
    """Base of a domain area. Holds the context and the busy lock."""

    def __init__(self, ctx, guard):
        self.ctx = ctx
        self.o = ctx.o
        self.run_main = ctx.run_main
        self.note = ctx.note
        self.cfg = ctx.cfg
        self.guard = guard
        self._domains = {}

    # ---- the shared change procedure ---------------------------------------
    def change(self, op, fn, timeout=None, on_main=True, prepare=None):
        """Run a write operation through the shared procedure.

        op       operation name without a prefix: 'toggle', 'install', ... - i18n key 'op.<op>'
        fn       the work itself; by default it runs on MO2's main thread
        timeout  seconds to wait for the main thread; None means the transport default
        on_main  False when the work enters the main thread by itself where needed
                 (installing: unpacking and copying do not need the main thread)
        prepare  input validation on the server thread, before the main one; it may raise
                 ValueError or return a ready refusal (the irreversible lock, for instance)

        Busy is checked FIRST, before the arguments are parsed: a "busy" refusal must come
        back even for an obviously malformed request, or the reply will not reveal that the
        manager is busy at all.
        """
        stop = self.guard.refusal('op.' + op)
        if stop:
            return self._refusal(stop, op, 'busy')
        if prepare is not None:
            stop = prepare()
            if stop:
                return self._refusal(stop, op, 'danger')
        if on_main:
            res = self.run_main(fn, timeout=timeout) if timeout else self.run_main(fn)
        else:
            res = fn()
        return stamp(res, op)

    def danger(self, body, op):
        """The irreversible lock's refusal, or None when the key was passed correctly."""
        if (body.get(DANGER_KEY) or '') != DANGER_VALUE:
            return self._refusal({'applied': False,
                                  'blocked': i18n.t('op.' + op),
                                  'why': i18n.t('danger.why', key=DANGER_KEY),
                                  'docs': self.ctx.docs}, op, 'danger')
        return None

    @staticmethod
    def _refusal(res, op, reason):
        res.setdefault('reason', reason)
        return stamp(res, op, applied=False)

    # ---- small things several areas need ------------------------------------
    def timeout(self, name):
        return self.cfg.timeout(name)

    def limit(self, name='listLimit'):
        return int(self.cfg.get(name) or 0)

    def nexus_domain(self, game):
        """The Nexus section for a game, or an empty string when it cannot be determined.

        Ask MO2 itself: gameNexusName() returns exactly the name the game lives under on
        Nexus, and it is right for every game the manager can run at all. The table in the
        settings is only an override for cases like Skyrim VR, which has no section of its
        own and takes its mods from the SSE one.

        There must be no blind default here. While there was one, a mod of an unknown game
        was looked up in the Skyrim SE section, Nexus honestly returned the page of a
        DIFFERENT mod with the same id, and the bridge passed confident judgement on
        someone else's files. A false answer is worse than a refusal: a refusal at least
        shows that there is no answer.
        """
        key = game or ''
        if key in self._domains:
            return self._domains[key]
        domains = self.cfg.get('nexusDomains') or {}
        dom = domains.get(key) or ''
        if not dom:
            dom = (safe(lambda: self.run_main(
                lambda: self.o.managedGame().gameNexusName()), '') or '').strip()
        if not dom:
            dom = (self.cfg.get('nexusDomainDefault') or '').strip()
        self._domains[key] = dom
        return dom

    def nexus_url(self, game, nid):
        """A link to the mod page. Built from nexusId: on some mods m.url() returns a bare
        domain with no /mods/<id>, which makes a link to nowhere."""
        if not nid or nid <= 0:
            return ''
        dom = self.nexus_domain(game)
        if not dom:
            return ''
        return 'https://www.nexusmods.com/%s/mods/%d' % (dom, nid)
