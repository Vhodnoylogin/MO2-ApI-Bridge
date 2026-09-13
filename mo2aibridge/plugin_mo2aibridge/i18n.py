# -*- coding: utf-8 -*-
r"""Strings and their translations. This file is a loader and nothing else.

The text lives in `lang\<code>.json` next to this module - one file per language, and not a
single phrase inside the code. That is the whole point: a translator edits JSON, not Python,
so a stray quote costs a missing line rather than a broken import, and adding a language means
dropping a file into the folder. There is no list of languages anywhere in the code - the list
IS what lies in that folder.

A key is a short Latin label, not an English phrase. A phrase changes during proofreading, and
if the phrase were the key, every translation would come unstuck from the code at once. A label
never changes.

English is not duplicated in the code as a safety net, deliberately. One more copy would mean
two places to keep in step - exactly the defect this arrangement exists to remove. If the files
are missing or broken the plugin still runs and shows the labels themselves (`err.needMod`):
ugly, but unmistakable, and the log says which folder was read.

`Catalog` takes a folder, so the same mechanism serves anyone who needs strings out of code -
the plugin reads its own `lang\`, the checks read theirs.
"""
import io
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
LANG_DIR = os.path.join(HERE, 'lang')
DEFAULT = 'en'
# A key starting with this is a note for translators, not a string: JSON has no comments, and
# a translator needs somewhere to be told what the placeholders mean.
NOTE_PREFIX = '#'


class Catalog(object):
    """The strings of one product, read from a folder of per-language JSON files."""

    def __init__(self, folder, default=DEFAULT, note=None):
        self.folder = folder
        self.default = default
        self.note = note or (lambda _m: None)
        self._langs = None
        self._current = default

    # ---- the files ---------------------------------------------------------
    def languages(self):
        """Language code -> its table. Read once, on first use.

        A broken or unreadable file is skipped rather than raised: one bad translation must not
        take the others down with it, and the caller finds out from the log.
        """
        if self._langs is not None:
            return self._langs
        self._langs = {}
        try:
            names = sorted(os.listdir(self.folder))
        except OSError as exc:
            self.note('i18n: %s not read (%s)' % (self.folder, exc))
            return self._langs
        for name in names:
            if not name.lower().endswith('.json'):
                continue
            code = os.path.splitext(name)[0].lower()
            path = os.path.join(self.folder, name)
            try:
                with io.open(path, encoding='utf-8-sig') as fh:
                    data = json.load(fh)
                if not isinstance(data, dict):
                    raise ValueError('a JSON object was expected')
            except Exception as exc:
                self.note('i18n: %s not read (%s)' % (path, exc))
                continue
            self._langs[code] = {k: v for k, v in data.items()
                                 if not k.startswith(NOTE_PREFIX)}
        return self._langs

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
_plugin = Catalog(LANG_DIR)


def set_note(note):
    """Where to report an unreadable translation file. Set once, at plugin start."""
    _plugin.note = note or (lambda _m: None)


def set_language(code):
    return _plugin.set_language(code)


def language():
    return _plugin.language()


def languages():
    return _plugin.languages()


def t(key, /, **kw):
    return _plugin.t(key, **kw)
