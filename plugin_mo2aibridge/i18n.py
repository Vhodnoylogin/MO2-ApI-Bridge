# -*- coding: utf-8 -*-
r"""Strings and their translations. This file is a loader and nothing else.

The text lives in `locale\<language>\<section>.json` next to this module - a folder per
language, and inside it any number of files split by section. Not one phrase sits in the code.
That is the whole point: a translator edits JSON, not Python, so a stray quote costs a missing
line rather than a broken import.

A folder per language rather than a file per language, and that is not cosmetic. A section
being added arrives as a NEW file: no existing file is rewritten, and two people working on
different sections never collide. One file per language forces everyone to touch the same
place for every addition.

There is no list of languages anywhere in the code - the list IS what lies in that folder.
Copy `en\` to `de\`, translate the files inside, and German exists.

A key is a short Latin label, not an English phrase. A phrase changes during proofreading, and
if the phrase were the key, every translation would come unstuck from the code at once. A label
never changes. Substitutions go by name (`%(mod)s`), never by position: another language puts
the words in another order.

English is not duplicated in the code as a safety net, deliberately. One more copy would mean
two places to keep in step - exactly the defect this arrangement exists to remove. If the files
are missing the plugin still runs and shows the labels themselves (`err.needMod`): ugly, but
unmistakable, and the log says which folder could not be read.

One key declared twice within a language is a layout mistake, and it is reported rather than
silently resolved in favour of whichever file happened to be read last.

`Catalog` takes a folder, so the same mechanism serves anyone who needs strings out of code -
the plugin reads its own `locale\`, the checks read theirs.
"""
import io
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
LOCALE_DIR = os.path.join(HERE, 'locale')
DEFAULT = 'en'
# A key starting with this is a note for translators, not a string: JSON has no comments, and
# a translator needs somewhere to be told what the placeholders mean.
NOTE_PREFIX = '#'


class Catalog(object):
    r"""The strings of one product, read from locale\<language>\<section>.json."""

    def __init__(self, folder, default=DEFAULT, note=None):
        self.folder = folder
        self.default = default
        self.note = note or (lambda _m: None)
        self._langs = None
        self._current = default

    # ---- the files ---------------------------------------------------------
    def languages(self):
        """Language code -> its merged table. Read once, on first use.

        Every section file of a language is merged into one dictionary. A broken or unreadable
        file is skipped rather than raised: one bad section must not take the rest of the
        language down with it, and the caller finds out from the log.
        """
        if self._langs is not None:
            return self._langs
        self._langs = {}
        try:
            codes = sorted(x for x in os.listdir(self.folder)
                           if os.path.isdir(os.path.join(self.folder, x)))
        except OSError as exc:
            self.note('i18n: %s not read (%s)' % (self.folder, exc))
            return self._langs
        for code in codes:
            self._langs[code.lower()] = self._read_language(os.path.join(self.folder, code))
        return self._langs

    def _read_language(self, folder):
        """Merge every section file of one language into a single table."""
        table, seen = {}, {}
        for name in sorted(os.listdir(folder)):
            if not name.lower().endswith('.json'):
                continue
            path = os.path.join(folder, name)
            try:
                with io.open(path, encoding='utf-8-sig') as fh:
                    data = json.load(fh)
                if not isinstance(data, dict):
                    raise ValueError('a JSON object was expected')
            except Exception as exc:
                self.note('i18n: %s not read (%s)' % (path, exc))
                continue
            for key, text in data.items():
                if key.startswith(NOTE_PREFIX):
                    continue
                if key in seen:
                    # Two sections claiming one key is a layout mistake: whichever file was
                    # read last would win silently, and the other translation would be edited
                    # forever with no effect.
                    self.note('i18n: %s is declared in both %s and %s' % (key, seen[key], name))
                    continue
                seen[key] = name
                table[key] = text
        return table

    def sections(self, code):
        """Which section file each key of a language came from. Used by the checks."""
        folder = os.path.join(self.folder, code)
        out = {}
        try:
            names = sorted(x for x in os.listdir(folder) if x.lower().endswith('.json'))
        except OSError:
            return out
        for name in names:
            try:
                with io.open(os.path.join(folder, name), encoding='utf-8-sig') as fh:
                    data = json.load(fh)
            except Exception:
                continue
            for key in data:
                if not key.startswith(NOTE_PREFIX):
                    out.setdefault(key, []).append(name)
        return out

    def reload(self):
        """Forget what was read. For anyone editing a translation with the plugin running."""
        self._langs = None
        return self.languages()

    # ---- choosing a language ------------------------------------------------
    def set_language(self, code):
        """Pick the language. 'auto' and any unknown code fall back to the default."""
        code = (code or '').strip().lower()
        if code in ('auto', ''):
            code = detect()
        self._current = code if code in self.languages() else self.default
        return self._current

    def language(self):
        return self._current

    # ---- the strings --------------------------------------------------------
    def t(self, key, /, **kw):
        """A string by its label. No translation - use the default; none of that - the label.

        The label is positional-only on purpose. Without the slash, a substitution named `key`
        collides with the parameter itself and the call dies with "t() got multiple values for
        argument 'key'" - which is exactly what happened to the irreversible-operations lock:
        instead of a clear refusal the route returned a traceback.
        """
        langs = self.languages()
        text = (langs.get(self._current) or {}).get(key) \
            or (langs.get(self.default) or {}).get(key) or key
        if kw:
            try:
                return text % kw
            except Exception:
                # A translation with a mistyped placeholder shows raw rather than raising: a
                # bad line is a nuisance, a bad line that breaks a route is a defect.
                return text
        return text


def detect():
    """MO2's interface language. We ask Qt rather than the system: the user may have changed
    it inside MO2."""
    try:
        from PyQt6.QtCore import QLocale
        return (QLocale().name() or '').split('_')[0].lower()
    except Exception:
        return DEFAULT


# The plugin's own catalogue. The module-level helpers below keep the call sites unchanged -
# every layer says i18n.t(...), and none of them needs to know where the strings come from.
_plugin = Catalog(LOCALE_DIR)


def set_note(note):
    """Where to report an unreadable translation file. Set once, at plugin start."""
    _plugin.note = note or (lambda _m: None)


def set_language(code):
    return _plugin.set_language(code)


def language():
    return _plugin.language()


def languages():
    return _plugin.languages()


def sections(code):
    return _plugin.sections(code)


def t(key, /, **kw):
    return _plugin.t(key, **kw)
