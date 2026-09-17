# -*- coding: utf-8 -*-
"""The plugin's configurable values: the port, timeouts, list limits, where to look for 7-Zip.

Everything that used to be a constant in the code lives here in one defaults dictionary.
The file `<PLUGIN_ID>-config.json` next to the plugin is created from those defaults on
first start and is then read on top of them: whatever the file omits comes from the
defaults, so an old file survives the addition of new keys.

There are no absolute paths here. The 7z.exe candidates are assembled from the
`ProgramFiles` and `ProgramFiles(x86)` environment variables rather than written by hand,
and are backed up by a PATH lookup.
"""
import io
import json
import os
import shutil

from . import i18n


def _seven_zip_candidates():
    """Where 7z.exe usually lives. The 7-Zip installer does not touch PATH, so we look."""
    out = []
    for var in ('ProgramFiles', 'ProgramW6432', 'ProgramFiles(x86)'):
        base = os.environ.get(var)
        if base:
            cand = os.path.join(base, '7-Zip', '7z.exe')
            if cand not in out:
                out.append(cand)
    return out


DEFAULTS = {
    # The port on 127.0.0.1. The likeliest value in this file to need changing - a firewall
    # rule, or another program already holding the number - and it is here so that changing
    # it never means editing Python. MO2's own plugin panel shows the same setting and wins
    # when it has an answer; this is the value it starts from.
    'port': 8930,
    # How many consecutive ports to try when the requested one is taken. The usual culprit is
    # a second MO2 instance - and that is not trouble but an ordinary day: Skyrim and Fallout
    # are often both open.
    'portTries': 10,
    # Seconds to wait for MO2's main thread, by kind of work. Ping is short on purpose:
    # it is the one needed when MO2 is unresponsive, and it has to answer quickly.
    'timeouts': {
        'ping': 15,
        'gameName': 10,
        'analyze': 600,
        'unpack': 1800,
        'vfsExport': 1800,
        'runWait': 3600,
    },
    # How many rows to return in the added/overwritten/sample lists, so a reply stays sane.
    'listLimit': 200,
    'analyzeLimit': 80,
    'orderReportLimit': 20,
    # The tail of 7-Zip's output that goes into the text of an unpack error.
    'unpackErrorTail': 300,
    # Paths to 7z.exe in order; an empty list means "PATH lookup only".
    'sevenZip': _seven_zip_candidates(),
    # Name of the unpack folder inside %TEMP%; a unique suffix is added per call.
    'unpackDirName': 'mo2aibridge-unpack',
    # Nexus section by MO2's game name - used for the link to a mod page.
    'nexusDomains': {
        'SkyrimSE': 'skyrimspecialedition',
        'SkyrimVR': 'skyrimspecialedition',
        'Skyrim': 'skyrim',
        'Fallout4': 'fallout4',
        'Fallout4VR': 'fallout4',
    },
    # Empty on purpose: the Nexus section is asked of MO2 itself (gameNexusName), and the
    # table above is only an override for cases like Skyrim VR, which has no section of its
    # own. The previous default of 'skyrimspecialedition' sent a mod of an unknown game
    # into a foreign section, and the bridge passed confident judgement on the files of a
    # different mod with the same id. The value here is a last resort for anyone whose MO2
    # will not name the section.
    'nexusDomainDefault': '',
    # Data root folders for the fallback breadth-first VFS walk.
    'walkRoots': ['meshes', 'textures', 'scripts', 'sound', 'music', 'interface', 'seq',
                  'strings', 'video', 'grass', 'lodsettings', 'shadersfx', 'skse', 'source'],
    # Update checks through Nexus: the pause between requests protects the daily API
    # allowance, maxPerCall bounds a single /updates?all=1 call, and newerLimit says how
    # many newer files to name per role.
    'updates': {
        'delaySec': 0.5,
        'timeoutSec': 30,
        'maxPerCall': 50,
        'newerLimit': 3,
        # The direct path to the API, used when MO2's own Nexus bridge is unusable from
        # Python: the key comes from the Windows credential store, under the name MO2
        # itself keeps it.
        'credentialTarget': 'ModOrganizer2_APIKEY',
        'apiHost': 'https://api.nexusmods.com',
        'appVersion': '2.1.0',
        # How many consecutive network failures count as a dead channel and stop the sweep.
        # One failure happens to anyone; five in a row mean the remaining thousand requests
        # will fail the same way, only slower - each waiting out its own timeout.
        'netFailsBeforeStop': 5,
        # The daily-allowance reserve below which the sweep stops. The key is shared with
        # MO2 itself: eating it down to zero leaves the user without updates and without
        # downloads in the manager.
        'quotaReserve': 50,
    },
}


def _merge(base, over):
    """A dictionary over a dictionary: nested dictionaries merge, everything else replaces."""
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config(object):
    """Values by key with defaults. An instance without a file is just the defaults."""

    def __init__(self, values=None, path=None):
        self.path = path
        self.values = _merge(DEFAULTS, values)

    @classmethod
    def load(cls, path, note=None):
        """Read the file; with no file, write the defaults out and work from them.

        A corrupted file does not bring the plugin down: it is reported through note and
        the defaults are used.
        """
        note = note or (lambda _m: None)
        if not os.path.isfile(path):
            try:
                with io.open(path, 'w', encoding='utf-8') as fh:
                    json.dump(DEFAULTS, fh, ensure_ascii=False, indent=1)
            except Exception as exc:
                note(i18n.t('config.writeFailed', path=path, error=exc))
            return cls(None, path)
        try:
            with io.open(path, encoding='utf-8-sig') as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError(i18n.t('config.notObject'))
            return cls(data, path)
        except Exception as exc:
            note(i18n.t('config.unreadable', path=path, error=exc))
            return cls(None, path)

    def get(self, key, default=None):
        return self.values.get(key, default)

    def timeout(self, name):
        return float(self.values['timeouts'].get(name) or DEFAULTS['timeouts'][name])

    def seven_zip(self):
        """Path to 7z.exe: the configured candidates first, then PATH. None if nowhere."""
        for p in self.values.get('sevenZip') or []:
            if p and os.path.isfile(p):
                return p
        return shutil.which('7z')
