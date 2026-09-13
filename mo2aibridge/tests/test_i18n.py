# -*- coding: utf-8 -*-
"""Слой строк: переводы согласованы между собой и с кодом, без запущенной MO2.

Зачем. Строки вынесены из кода в i18n ради переводчика, и с этого момента у них два
независимых источника расхождений, и ни одно из них не ломает запуск и не видно в логе.

Первый - между языками. Ключ добавили в EN и забыли в RU: русский пользователь молча получает
английскую фразу. В переводе потеряли или переименовали подстановку %(mod)s: форматирование
падает, а t() намеренно не роняет плагин и отдаёт сырой шаблон с процентами - пользователь
видит "%(mod)s" вместо имени мода, и никто не узнаёт.

Второй - между словарём и кодом. Код зовёт ключ, которого нет: вместо фразы показывается сама
метка. Словарь держит ключ, который никто не зовёт: мёртвый текст, который переводчик честно
переведёт. Код передаёт в t() не те имена подстановок, что стоят в шаблоне: снова сырой
шаблон.

Плюс регрессия на сигнатуру t(key, /, **kw). Без косой черты подстановка с именем `key`
(она есть в danger.why) сталкивается с самим параметром, и маршрут вместо внятного отказа
отдавал трассировку "multiple values for argument 'key'".

Пакет сканируется целиком и рекурсивно по common.PKG, а не по списку файлов, поэтому набор
переживёт разбиение на новые модули: важно лишь, чтобы вызовы остались вида t('ключ', ...).
Ключ, доходящий до t() через переменную (op_key в замках), прямым вызовом не считается,
но употреблением считается: строка 'op.toggle' в коде есть, и найти её можно.
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

# Ключи, которые сегодня не используются, и это известно. Ведёт тот, кто правит код.
# Неиспользуемый ключ ВНЕ этого множества - сбой; внутри - только заметка в выводе на каждом
# прогоне, чтобы список не пропадал из виду. Ключ, который попал сюда, но снова зазвучал
# в коде, тоже сбой: множество должно таять, а не копиться. Пустое множество означает, что
# всё определённое обязано звучать хоть где-то.
KNOWN_UNUSED = set()

# Языки берутся из папки lang\, а не из зашитых словарей: их в коде больше нет. Поэтому
# набор проверяет ВСЕ переводы, какие лежат рядом с плагином, а не заранее известную пару -
# положили de.json, и он проверяется наравне с остальными, без правки этого файла.
LANGS = i18n.languages()
EN = LANGS.get(i18n.DEFAULT) or {}
OTHERS = {code: table for code, table in LANGS.items() if code != i18n.DEFAULT}

# %(имя)спецификатор - имя и спецификатор порознь: перевод обязан сохранить и то и другое.
# %(sec).0f против %(sec)s даст разный текст, а %(missing)d против %(missing)s - разное
# поведение на нечисловом значении.
PLACEHOLDER = re.compile(
    r'%\((\w+)\)([-#0 +]*(?:\d+|\*)?(?:\.(?:\d+|\*))?[hlL]?[diouxXeEfFgGcrsa])')
# Прямой вызов t('ключ', ...): литерал целиком первым аргументом. \b отсекает split(, get(,
# format( - у них перед t стоит буква. Хвост [,)] отсекает t('op.' + op): там литерал лишь
# префикс, и такой вызов разбирается ниже по AST как ключ по префиксу.
DIRECT_CALL = re.compile(r'''\bt\(\s*['"]([^'"]+)['"]\s*[,)]''')
# Как выглядит метка: латинская группа, точка, имя. По этой форме опознаётся таблица переводов.
KEY_SHAPE = re.compile(r'^[a-z]+\.[A-Za-z0-9_]+$')
CYRILLIC = re.compile('[А-Яа-яЁё]')


def placeholders(text):
    """{(имя, спецификатор), ...} шаблона."""
    return set(PLACEHOLDER.findall(text))


def stray_percent(text):
    """Процент, который не подстановка и не %%. С любыми kw такой шаблон падает при
    форматировании, и t() тихо вернёт его сырым."""
    return '%' in PLACEHOLDER.sub('', text).replace('%%', '')


def package_files():
    """Все .py пакета, рекурсивно и в устойчивом порядке; __pycache__ не читаем."""
    out = []
    for folder, dirs, files in os.walk(common.PKG):
        dirs[:] = sorted(d for d in dirs if d != '__pycache__')
        out.extend(os.path.join(folder, f) for f in sorted(files) if f.endswith('.py'))
    return out


def rel(path):
    return os.path.relpath(path, common.PKG).replace(os.sep, '/')


def is_t(func):
    """Вызов t: и i18n.t(...), и голое t(...) после from .i18n import t."""
    return ((isinstance(func, ast.Attribute) and func.attr == 't')
            or (isinstance(func, ast.Name) and func.id == 't'))


def call_keys(arg):
    """Ключи, которые может принять первый аргумент: константа либо условное выражение
    из констант ('a' if x else 'b'). None - ключ вычисляется, статически не узнать."""
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return [arg.value]
    if isinstance(arg, ast.IfExp):
        yes, no = call_keys(arg.body), call_keys(arg.orelse)
        if yes is not None and no is not None:
            return yes + no
    return None


def call_prefix(arg):
    """Префикс ключа, если первый аргумент склеивается из литерала и чего-то ещё:
    'op.' + op, 'op.%s' % op, 'op.{}'.format(op), f'op.{op}'. Иначе None.

    Так замки зовут имена операций после разбиения пакета, и тогда литерала 'op.toggle'
    в коде может не быть вовсе; но все ключи с этим префиксом - живые."""
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
    """Что найдено в исходниках пакета. Заполняется один раз, дальше только читается."""

    def __init__(self):
        self.files = 0
        self.broken = []       # файлы, которые не разобрались: где и почему
        self.direct = {}       # ключ из t('...') -> [файл:строка]
        self.literal = {}      # ключ, встреченный любой строковой константой -> [файл:строка]
        self.by_prefix = {}    # префикс из t('op.' + x) -> [файл:строка]
        self.calls = []        # (где, ключи или None, префикс или None, имена kw, **splat, лишних позиционных)
        self.discarded = []    # t(...) как отдельное выражение: отформатировано и выброшено
        self.bypass = []       # log('кириллица') или raise Error('кириллица') мимо i18n

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
        # Что НЕ считается употреблением ключа: докстроки и ключи самой таблицы переводов.
        # Таблица опознаётся по форме, а не по имени файла или переменной, чтобы разбиение
        # словарей по отдельным модулям ничего здесь не сломало.
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
        # Строка с кириллицей, ушедшая в лог или в исключение напрямую, минуя словарь:
        # английский пользователь получит её как есть. Ловятся простые случаи - константа
        # первым аргументом; склейки и форматирование сюда не попадают.
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
# Ключи, которые решение отдаёт словом, а t() получает переменной: why='upd.newer' в updates.py.
# Такие литералы видны в исходнике, и именно по ним ключ считается используемым.
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
    """Имена подстановок, которых ждёт шаблон EN."""
    return sorted(n for n, _ in placeholders(EN[key]))


for at, keys, prefix, names, splat, extra_positional in usage.calls:
    if extra_positional > 0:
        wrong.append('%s: лишних позиционных аргументов %d' % (at, extra_positional))
    if splat:
        unchecked.append(at)
        continue
    if keys is None and prefix:
        # Ключ по префиксу: проверить можно, только если все ключи группы ждут одного и того же.
        wants = {tuple(wanted(k)) for k in EN if k.startswith(prefix)}
        if len(wants) == 1:
            want = list(next(iter(wants)))
            if names != want:
                wrong.append("%s по префиксу '%s': передано %s, в шаблонах %s"
                             % (at, prefix, names, want))
        else:
            dynamic.append(at)
        continue
    if keys is None:
        dynamic.append(at)
        continue
    for key in keys:
        if key not in EN:
            continue    # уже доложено в разделе 3
        if names != wanted(key):
            wrong.append('%s %s: передано %s, в шаблоне %s' % (at, key, names, wanted(key)))
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
# Ключ, которого нет в выбранном языке, берётся из EN. Проверяется на временном ключе, чтобы не
# зависеть от того, есть ли сегодня в словарях непереведённое; сканирование выше уже прошло,
# и подставной ключ в отчёт о неиспользуемых не попадёт.
EN['zz.probe'] = 'only english %(n)d'
try:
    i18n.set_language('ru')
    r.case(T('strings.fallsBackToEnglish'), i18n.t('zz.probe', n=7), 'only english 7')
finally:
    del EN['zz.probe']
    i18n.set_language(i18n.DEFAULT)

r.done()
