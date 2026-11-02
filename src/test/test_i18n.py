# -*- coding: utf-8 -*-
"""The strings layer: the translations agree with one another and with the code, no MO2.

Why. The strings were taken out of the code and into i18n for the translator's sake, and from
that moment they have two independent sources of drift - and neither of them breaks a run or
shows up in the log.

The first is between languages. A key added to EN and forgotten in RU: the Russian user silently
gets an English phrase. A substitution %(mod)s lost or renamed in a translation: formatting
fails, and t() deliberately does not bring the plugin down but hands back the raw template with
its percent signs - the user sees "%(mod)s" where a mod name should be, and nobody finds out.

The second is between the catalogue and the code. The code asks for a key that does not exist:
the label itself is shown instead of a phrase. The catalogue holds a key nobody asks for: dead
text, which a translator will honestly translate. The code passes t() substitution names the
template does not carry: the raw template again.

Plus a regression on the signature t(key, /, **kw). Without the slash a substitution named `key`
(danger.why has one) collides with the parameter itself, and instead of a plain refusal the route
handed back a traceback reading "multiple values for argument 'key'".

The package is scanned whole and recursively from common.PKG rather than by a list of files, so
the set survives a split into new modules: all that matters is that the calls still look like
t('key', ...). A key that reaches t() through a variable (op_key in the locks) does not count as
a direct call, but it does count as a use: the string 'op.toggle' is in the code and can be
found.
"""
import ast
import inspect
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

T = common.T

pkg = common.import_package()
i18n = pkg.i18n
r = common.Report(T('strings.title'))

# Keys that go unused today, and that is known. Kept by whoever edits the code. An unused key
# OUTSIDE this set is a failure; inside it, only a note in the output on every run, so the list
# does not drop out of sight. A key that landed here and then sounded in the code again is a
# failure too: the set is meant to melt away, not to accumulate. An empty set means everything
# defined has to be said somewhere.
KNOWN_UNUSED = set()

# The languages come from the locale\ folder rather than from tables inside the code - there are
# none there any more. So the set checks EVERY translation lying beside the plugin rather than a
# pair known in advance: drop in a de\ folder and it is checked along with the others, with no
# edit to this file.
LANGS = i18n.languages()
EN = LANGS.get(i18n.DEFAULT) or {}
OTHERS = {code: table for code, table in LANGS.items() if code != i18n.DEFAULT}

# %(name)specifier - the name and the specifier apart: a translation has to keep both.
# %(sec).0f against %(sec)s gives different text, and %(missing)d against %(missing)s gives
# different behaviour on a non-numeric value.
PLACEHOLDER = re.compile(
    r'%\((\w+)\)([-#0 +]*(?:\d+|\*)?(?:\.(?:\d+|\*))?[hlL]?[diouxXeEfFgGcrsa])')
# A direct call t('key', ...): a whole literal as the first argument. \b cuts off split(, get(,
# format( - they have a letter in front of the t. The tail [,)] cuts off t('op.' + op): there the
# literal is only a prefix, and a call like that is taken apart by AST below as a key by prefix.
DIRECT_CALL = re.compile(r'''\bt\(\s*['"]([^'"]+)['"]\s*[,)]''')
# What a label looks like: a Latin group, a dot, a name. A translation table is recognised by
# that shape.
KEY_SHAPE = re.compile(r'^[a-z]+\.[A-Za-z0-9_]+$')
CYRILLIC = re.compile('[А-Яа-яЁё]')


def placeholders(text):
    """{(name, specifier), ...} of a template."""
    return set(PLACEHOLDER.findall(text))


def stray_percent(text):
    """A percent that is neither a substitution nor %%. With any kw at all such a template fails
    at formatting, and t() quietly hands it back raw."""
    return '%' in PLACEHOLDER.sub('', text).replace('%%', '')


def package_files():
    """Every .py of the package, recursively and in a stable order; __pycache__ is not read."""
    out = []
    for folder, dirs, files in os.walk(common.PKG):
        dirs[:] = sorted(d for d in dirs if d != '__pycache__')
        out.extend(os.path.join(folder, f) for f in sorted(files) if f.endswith('.py'))
    return out


def rel(path):
    return os.path.relpath(path, common.PKG).replace(os.sep, '/')


def is_t(func):
    """A call to t: both i18n.t(...) and a bare t(...) after from .i18n import t."""
    return ((isinstance(func, ast.Attribute) and func.attr == 't')
            or (isinstance(func, ast.Name) and func.id == 't'))


def call_keys(arg):
    """The keys the first argument may carry: a constant, or a conditional expression of
    constants ('a' if x else 'b'). None - the key is computed and cannot be known statically."""
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return [arg.value]
    if isinstance(arg, ast.IfExp):
        yes, no = call_keys(arg.body), call_keys(arg.orelse)
        if yes is not None and no is not None:
            return yes + no
    return None


def call_prefix(arg):
    """The prefix of a key, if the first argument is glued out of a literal and something else:
    'op.' + op, 'op.%s' % op, 'op.{}'.format(op), f'op.{op}'. None otherwise.

    That is how the locks ask for operation names now the package is split, and then the literal
    'op.toggle' may not be in the code at all; but every key with that prefix is live."""
    head = None
    if (isinstance(arg, ast.BinOp) and isinstance(arg.op, (ast.Add, ast.Mod))
            and isinstance(arg.left, ast.Constant) and isinstance(arg.left.value, str)):
        head = arg.left.value.split('%')[0] if isinstance(arg.op, ast.Mod) else arg.left.value
    elif isinstance(arg, ast.JoinedStr) and arg.values and isinstance(arg.values[0], ast.Constant):
        head = arg.values[0].value
    elif (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute)
            and arg.func.attr == 'format' and isinstance(arg.func.value, ast.Constant)
            and isinstance(arg.func.value.value, str)):
        head = arg.func.value.value.split('{')[0]
    return head if head and head.endswith('.') else None


class Usage(object):
    """What was found in the sources of the package. Filled once, read from then on."""

    def __init__(self):
        self.files = 0
        self.files = 0
        self.broken = []       # files that would not parse: where, and why
        self.direct = {}       # key from t('...') -> [file:line]
        self.literal = {}      # key met as any string constant -> [file:line]
        self.by_prefix = {}    # prefix from t('op.' + x) -> [file:line]
        self.calls = []        # (where, keys or None, prefix or None, kw names, **splat, extra positional)
        self.discarded = []    # t(...) as a statement of its own: formatted and thrown away
        self.bypass = []       # log('Cyrillic') or raise Error('Cyrillic') straight past i18n

    def scan(self, path):
        src = io.open(path, encoding='utf-8').read()
        where = rel(path)
        self.files += 1
        for m in DIRECT_CALL.finditer(src):
            line = src.count('\n', 0, m.start()) + 1
            self.direct.setdefault(m.group(1), []).append('%s:%d' % (where, line))
        try:
            tree = ast.parse(src, where)
        except SyntaxError as exc:
            self.broken.append('%s: %s' % (where, exc))
            return
        # What does NOT count as a use of a key: docstrings, and the keys of the translation
        # table itself. The table is recognised by its shape rather than by the name of a file or
        # a variable, so splitting the catalogues across modules breaks nothing here.
        skip = set()
        for node in ast.walk(tree):
            body = getattr(node, 'body', None)
            if (isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                  ast.AsyncFunctionDef))
                    and body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                skip.add(id(body[0].value))
            elif isinstance(node, ast.Dict):
                keys = [k for k in node.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)]
                if len(keys) >= 5 and all(KEY_SHAPE.match(k.value) for k in keys):
                    skip.update(id(k) for k in keys)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in skip and node.value in EN):
                self.literal.setdefault(node.value, []).append('%s:%d' % (where, node.lineno))
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) \
                    and is_t(node.value.func):
                self.discarded.append('%s:%d' % (where, node.lineno))
            elif isinstance(node, ast.Call):
                self._call(node, where)

    def _call(self, node, where):
        at = '%s:%d' % (where, node.lineno)
        if is_t(node.func):
            first = node.args[0] if node.args else None
            keys = None if isinstance(first, ast.Starred) or first is None else call_keys(first)
            prefix = call_prefix(first) if keys is None and first is not None else None
            if prefix:
                self.by_prefix.setdefault(prefix, []).append(at)
            names = sorted(k.arg for k in node.keywords if k.arg)
            splat = any(k.arg is None for k in node.keywords)
            self.calls.append((at, keys, prefix, names, splat, len(node.args) - 1))
            return
        # A string with Cyrillic in it that went to the log or into an exception directly, past
        # the catalogue: an English-speaking user gets it as it stands. The simple cases are
        # caught - a constant as the first argument; glued and formatted strings are not.
        f = node.func
        name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else '')
        if (name == 'log' or name.endswith('Error')) and node.args:
            a = node.args[0]
            if isinstance(a, ast.Constant) and isinstance(a.value, str) and CYRILLIC.search(a.value):
                self.bypass.append('%s %s(%r)' % (at, name, a.value))


usage = Usage()
for path in package_files():
    usage.scan(path)

# ---------------------------------------------------------------------------------------
r.head(T('strings.h1KeySets'))
r.note(T('strings.localeFiles'), '%d: %s' % (len(LANGS), ', '.join(sorted(LANGS))))
r.note(T('strings.keysIn', lang=i18n.DEFAULT), len(EN))
r.case(T('strings.defaultRead'), bool(EN), True)
for code in sorted(OTHERS):
    r.case(T('strings.missingFrom', lang=code, other=i18n.DEFAULT),
           sorted(set(EN) - set(OTHERS[code])), [])
    r.case(T('strings.missingFromDefault', lang=i18n.DEFAULT, other=code),
           sorted(set(OTHERS[code]) - set(EN)), [])

# ---------------------------------------------------------------------------------------
r.head(T('strings.h2Placeholders'))
mismatch, stray = [], []
for code in sorted(OTHERS):
    table = OTHERS[code]
    for key in sorted(set(EN) & set(table)):
        a, b = placeholders(table[key]), placeholders(EN[key])
        if a != b:
            mismatch.append('%s/%s: %s, %s %s' % (code, key, sorted(a), i18n.DEFAULT, sorted(b)))
    stray += ['%s/%s' % (code, k) for k in sorted(table) if stray_percent(table[k])]
r.case(T('strings.namesAndSpecsMatch'), mismatch, [])
r.case(T('strings.noStrayPercentInTranslations'), stray, [])
r.case(T('strings.noStrayPercentIn', lang=i18n.DEFAULT),
       sorted(k for k in EN if stray_percent(EN[k])), [])

# ---------------------------------------------------------------------------------------
r.head(T('strings.h3CodeKeysExist'))
r.note(T('strings.filesScanned'), usage.files)
r.case(T('strings.allFilesParse'), usage.broken, [])
r.case(T('strings.directCallsFound'), len(usage.direct) > 0, True)
r.note(T('strings.keysInDirectCalls'), len(usage.direct))
unknown = ['%s <- %s' % (k, ', '.join(v)) for k, v in usage.direct.items() if k not in EN]
unknown += ["%s* <- %s" % (p, ', '.join(v)) for p, v in usage.by_prefix.items()
            if not any(k.startswith(p) for k in EN)]
r.case(T('strings.noUnknownKeys'), sorted(unknown), [])

# ---------------------------------------------------------------------------------------
r.head(T('strings.h4EveryKeyUsed'))
indirect = sorted(k for k in usage.literal if k not in usage.direct)
for key in indirect:
    r.note(T('strings.viaVariableOnly', key=key), ', '.join(usage.literal[key]))
used = set(usage.literal)
# Keys the decision names in a word while t() receives them in a variable: why='upd.newer' in
# updatepolicy.py. Such literals are visible in the source, and that is what makes the key used.
WHY_LITERAL = re.compile(r'''why=['"]([a-z]+\.[A-Za-z0-9_]+)['"]''')
for path in package_files():
    used.update(WHY_LITERAL.findall(open(path, encoding='utf-8').read()))
for prefix, places in sorted(usage.by_prefix.items()):
    group = sorted(k for k in EN if k.startswith(prefix))
    used.update(group)
    r.note(T('strings.byPrefix', prefix=prefix, count=len(group)), ', '.join(places))
unused = sorted(k for k in EN if k not in used)
for key in unused:
    r.note(T('strings.unusedKey', key=key),
           T('strings.inKnownUnused') if key in KNOWN_UNUSED else T('strings.outsideList'))
r.case(T('strings.noUnusedOutsideKnown'),
       sorted(k for k in unused if k not in KNOWN_UNUSED), [])
r.case(T('strings.knownUnusedNotUsed'),
       sorted(k for k in KNOWN_UNUSED if k in used), [])
r.case(T('strings.knownUnusedExists'),
       sorted(k for k in KNOWN_UNUSED if k not in EN), [])

# ---------------------------------------------------------------------------------------
r.head(T('strings.h5CallsMatchTemplate'))
wrong, dynamic, unchecked = [], [], []


def wanted(key):
    """The substitution names the EN template expects."""
    return sorted(n for n, _ in placeholders(EN[key]))


for at, keys, prefix, names, splat, extra_positional in usage.calls:
    if extra_positional > 0:
        wrong.append(T('strings.extraPositional', at=at, n=extra_positional))
    if splat:
        unchecked.append(at)
        continue
    if keys is None and prefix:
        # A key by prefix: checkable only if every key of the group expects the same thing.
        wants = {tuple(wanted(k)) for k in EN if k.startswith(prefix)}
        if len(wants) == 1:
            want = list(next(iter(wants)))
            if names != want:
                wrong.append(T('strings.prefixCallMismatch', at=at, prefix=prefix,
                                               got=names, want=want))
        else:
            dynamic.append(at)
        continue
    if keys is None:
        dynamic.append(at)
        continue
    for key in keys:
        if key not in EN:
            continue    # already reported in section 3
        if names != wanted(key):
            wrong.append(T('strings.callMismatch', at=at, key=key, got=names,
                                           want=wanted(key)))
for at in dynamic:
    r.note(T('strings.keyComputed'), at)
for at in unchecked:
    r.note(T('strings.kwargsSplat'), at)
for at in usage.discarded:
    r.note(T('strings.formattedAndDiscarded'), at)
for line in usage.bypass:
    r.note(T('strings.cyrillicPastI18n'), line)
r.case(T('strings.constKeyCallsAgree'), wrong, [])

# ---------------------------------------------------------------------------------------
r.head(T('strings.h6Signature'))
sig = inspect.signature(i18n.t)
kinds = {p.name: p.kind for p in sig.parameters.values()}
r.case(T('strings.keyPositionalOnly'), kinds.get('key'), inspect.Parameter.POSITIONAL_ONLY)
r.case(T('strings.substitutionsByKeyword'), inspect.Parameter.VAR_KEYWORD in kinds.values(), True)
r.case(T('strings.dangerWhyTakesKey'),
       'ПРОБА' in i18n.t('danger.why', key='ПРОБА'), True)

# ---------------------------------------------------------------------------------------
r.head(T('strings.h7LanguageChoice'))
r.case(T('strings.setRuReturnsRu'), i18n.set_language('ru'), 'ru')
r.case(T('strings.toggleInRussian'), i18n.t('op.toggle'), 'включение или выключение мода')
r.case(T('strings.paddedCodeNormalised'), i18n.set_language(' RU '), 'ru')
r.case(T('strings.unknownLanguageGivesEn'), i18n.set_language('xx'), 'en')
r.case(T('strings.toggleInEnglish'), i18n.t('op.toggle'), 'enabling or disabling a mod')
r.case(T('strings.autoGivesKnown'), i18n.set_language('auto') in LANGS, True)
i18n.set_language('en')
r.case(T('strings.unknownKeyAsIs'), i18n.t('нет.такого'), 'нет.такого')
r.case(T('strings.unknownKeyWithKwargs'), i18n.t('нет.такого', a=1), 'нет.такого')
r.case(T('strings.goodSubstitutionsFormat'),
       i18n.t('err.orderIncomplete', missing=2, extra=0), EN['err.orderIncomplete'] % {'missing': 2, 'extra': 0})
r.case(T('strings.foreignNameRawTemplate'),
       i18n.t('err.noSuchMod', wrong=1), EN['err.noSuchMod'])
r.case(T('strings.nonNumericRawTemplate'),
       i18n.t('err.orderIncomplete', missing='x', extra=1), EN['err.orderIncomplete'])
# A key the chosen language does not have is taken from EN. Checked on a temporary key so as not
# to depend on whether anything is untranslated today; the scan above has already run, and a
# stand-in key will not turn up in the report of unused ones.
EN['zz.probe'] = 'only english %(n)d'
try:
    i18n.set_language('ru')
    r.case(T('strings.fallsBackToEnglish'), i18n.t('zz.probe', n=7), 'only english 7')
finally:
    del EN['zz.probe']
    i18n.set_language(i18n.DEFAULT)

r.done()
