# -*- coding: utf-8 -*-
"""Shared ground for the checks: where the plugin is, how to reach it, how to report.

Not one machine-specific path lives here. The module root is derived from this file's
location, the plugin folder is found beside it by the presence of __init__.py, the token
file name comes from that folder's own name, and the port comes from the environment. So
the checks travel with the mod and work for whoever cloned it.

The checks carry no phrases of their own: their wording lives in
tests\\locale\\<language>\\<section>.json and is read through T().
"""

import io
import json
import os
import sys
import types
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _find_package():
    """The plugin's own folder - the neighbour of tests, the one holding __init__.py.

    We search rather than assemble the name: the module root and the package share a name
    only by convention, and the checks must not depend on conventions."""
    for name in sorted(os.listdir(ROOT)):
        if os.path.isfile(os.path.join(ROOT, name, '__init__.py')):
            return os.path.join(ROOT, name)
    raise SystemExit('no folder with an __init__.py beside tests - where is the plugin?')


PKG = _find_package()
NAME = os.path.basename(PKG)
PORT = int(os.environ.get('MO2AIBRIDGE_PORT') or 8930)
BASE = 'http://127.0.0.1:%d' % PORT


def token():
    """The running plugin's token, or None when MO2 is not up."""
    try:
        return io.open(TOKEN_FILE, encoding='utf-8').read().strip() or None
    except Exception:
        return None


def call(method, route, body=None, timeout=120):
    """A request to the bridge. Returns (code, parsed reply)."""
    data = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib.request.Request(BASE + route, data=data, method=method,
                                 headers={'X-Token': token() or '',
                                          'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode('utf-8'))


def alive():
    """Is the bridge answering right now."""
    if not token():
        return False
    try:
        return call('GET', '/ping', timeout=20)[1].get('ok') is True
    except Exception:
        return False


def need_live():
    """Stop with a clear word when MO2 is not running."""
    if not alive():
        print(T('report.skipped', base=BASE))
        raise SystemExit(77)


def import_package(with_mobase=False):
    """Import the plugin itself.

    In the repository the package folder is called plugin_mo2aibridge, which is a perfectly
    good module name - the import is ordinary. Inside MO2 the same code is called
    mo2aibridge, because there its name is the name of the junction in the plugins folder.
    One must not be derived from the other, so the name is asked of the plugin rather than
    assembled from a path.

    mobase exists only inside the MO2 process. The lower layers do not need it at all, and
    services mentions it only at import level, so a stub is enough for checking the logic -
    and then the checks run without a live manager.
    """
    if not with_mobase and 'mobase' not in sys.modules:
        sys.modules['mobase'] = types.ModuleType('mobase')
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    return __import__(NAME, fromlist=['services', 'i18n', 'winapi'])


# The name MO2 knows the plugin by is asked of the plugin itself: the folder holding it
# deliberately has another name, and one must not be derived from the other.
PLUGIN_ID = import_package().PLUGIN_ID
TOKEN_FILE = os.path.join(PKG, PLUGIN_ID + '-token.txt')

# ---------------------------------------------------------------- what the checks say
# The checks carry no phrases of their own either. Their wording lives in
# tests\locale\<language>\<section>.json and is read by the very Catalog the plugin uses -
# one mechanism, two catalogues. Without this, a repository whose code is English printed
# its check results in Russian, and whoever cloned it could not read what had failed.
LOCALE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'locale')
_catalog = import_package().i18n.Catalog(LOCALE, note=lambda m: print('  ' + m))
# The language is chosen by MO2AIBRIDGE_LANG; 'auto' follows the system, and anything
# unknown falls back to English.
_catalog.set_language(os.environ.get('MO2AIBRIDGE_LANG') or 'en')


def T(key, /, **kw):
    """The wording of one check, by its label. Spelled out at the call site rather than
    hidden inside Report, so that a key is never mistaken for a phrase."""
    return _catalog.t(key, **kw)


def language():
    return _catalog.language()


class Report(object):
    """Counting the checks and printing them the same way for every suite."""

    def __init__(self, title):
        self.ok = self.bad = 0
        print('=== %s ===' % title)

    def head(self, text):
        print()
        print('--- %s' % text)

    def case(self, label, got, want):
        good = got == want
        self.ok, self.bad = self.ok + int(good), self.bad + int(not good)
        print('  %-4s %-50s %s' % (T('report.ok') if good else T('report.failed'), label,
                                   got if good else T('report.gotWanted', got=got, want=want)))
        return good

    def note(self, label, value):
        print('  %-4s %-50s %s' % ('', label, value))

    def done(self):
        print()
        print(T('report.total', ok=self.ok, bad=self.bad))
        raise SystemExit(1 if self.bad else 0)
