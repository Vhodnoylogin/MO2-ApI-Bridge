# -*- coding: utf-8 -*-
"""Договор маршрутов, без запущенной MO2.

Зачем. Мост ничего не помнит между вызовами, и потому весь его договор - это ключи ответа:
что было до правки, чем её обратить, почему отказано. Живой набор test_routes.py проверяет
это на настоящей MO2, но менеджер поднимают редко, и переделка services.py между двумя такими
прогонами может тихо переименовать поле - узнается это в чужом чате, когда откат не соберётся.

Здесь MO2 подменена fake_mo2 на временной папке с настоящими файлами, поэтому набор идёт за
секунды и на любой машине, а маршруты, которые пишут на диск, проверяются чтением файлов:
plugins.txt после /plugins/state, папка мода после /install и /mods/remove, CSV после
/vfsexport. Ожидаемые ключи взяты из операторов return в services.py и из разделов README
«Мост выполняет, помнит вызывающий» и «Когда запущена игра».

Что не проверяется по-настоящему: /run и /window - подставная startApplication ничего не
запускает, а окон у подставной MO2 нет. От них проверяются только отказы и ключи ответа.

Код выхода 0 - всё сошлось, 1 - есть сбои. Установка через /install требует 7-Zip; без него
её случаи помечаются пропущенными, а не сбойными.
"""
import ctypes  # noqa: F401  - до подмены путей к dll, иначе _ctypes не грузится
import atexit
import importlib
import io
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_mo2  # noqa: E402

# Подставной mobase встаёт ДО импорта пакета: services связывает имя на импорте, и
# common.import_package() импортирует пакет уже при собственном импорте.
fake_mo2.install()
import common  # noqa: E402

T = common.T

pkg = common.import_package(with_mobase=True)
# routes не входит в список import_package - подгружается отдельно, под именем самого пакета
routes = importlib.import_module(pkg.__name__ + '.routes')
services, i18n, winapi = pkg.services, pkg.i18n, pkg.winapi
i18n.set_language('ru')
r = common.Report('договор маршрутов без MO2')

# Ключ необратимых операций - тот, что напечатан в README, разделе «Необратимые операции».
# Литерал, а не константа из пакета: договор - это документированное значение.
KEY = {'iUnderstandTheRisk': 'yes-I-read-the-docs-and-accept-irreversible-changes'}
ЧУЖОЙ_ЗАПУСК = {'C:' + chr(92) + 'x' + chr(92) + 'foo.exe': {'n': 1, 'mine': False}}

GET_ROUTES = ['/ping', '/api', '/mods', '/mod', '/analyze', '/profiles', '/plugins', '/vfs',
              '/origins', '/resolve', '/dirs', '/procs', '/windows', '/updates']
POST_ROUTES = ['/refresh', '/install', '/toggle', '/plugins/state', '/plugins/order',
               '/vfsexport', '/run', '/window', '/mods/priority', '/mods/rename',
               '/mods/remove']
# Девять маршрутов, которые обязаны отказывать, пока MO2 занята (README, «Когда запущена игра»)
MUTATING = ['/refresh', '/install', '/toggle', '/run', '/plugins/state', '/plugins/order',
            '/mods/priority', '/mods/rename', '/mods/remove']

# Наборы ключей ответов - из операторов return в services.py. Сравниваются отсортированными.
PING_KEYS = sorted(['busy', 'ok', 'profile', 'game', 'mo2Version', 'modsPath', 'overwrite',
                    'downloads', 'mainThread'])
CARD_KEYS = sorted(['mod', 'displayName', 'path', 'active', 'essential', 'priority',
                    'isSeparator', 'isForeign', 'isBackup', 'isOverwrite', 'nexusId', 'url',
                    'urlFromMO2', 'gameName', 'version', 'newestVersion', 'ignoredVersion',
                    'installationFile', 'categories', 'primaryCategory', 'notes', 'comments',
                    'endorsed', 'tracked'])
ANALYZE_KEYS = sorted(['mod', 'path', 'files', 'plugins', 'sample', 'conflicts', 'card'])
CONFLICT_KEYS = sorted(['checked', 'contested', 'winsCount', 'losesCount', 'wins', 'loses'])
PLUGIN_KEYS = sorted(['plugin', 'active', 'priority', 'loadOrder', 'origin', 'master', 'esl',
                      'empty', 'masters'])
BUSY_KEYS = sorted(['app', 'pids', 'isGame', 'viaMO2', 'mo2Run', 'windowLocked',
                    'heldByUnknown'])
REFUSAL_KEYS = sorted(['applied', 'busy', 'running', 'blocked', 'why'])
INSTALL_KEYS = sorted(['mod', 'path', 'files', 'created', 'existed', 'mode', 'fomodSkipped',
                       'archive', 'filesBefore', 'filesAfter', 'added', 'addedCount',
                       'overwritten', 'overwrittenCount', 'removedToRecycleBin', 'undo'])
REMOVAL_CARD_KEYS = sorted(['mod', 'path', 'version', 'nexusId', 'url', 'categories', 'notes',
                            'installationFile', 'archivePath', 'archiveOnDisk', 'priority',
                            'active', 'files'])
FULL_ORDER = ['Alpha.esp', 'Beta.esp', 'Gamma.esl']

fixtures = []
# Временные сборки убираются и при аварийном выходе: иначе после первого же падения в
# %TEMP% остаётся папка mo2aibridge-fake-*
atexit.register(lambda: [f.cleanup() for f in fixtures])


def make():
    """Свежая сборка на диске и службы над ней в свободном состоянии.

    game_exe - заведомо несуществующий процесс, окно MO2 не ищется: занятость должна
    отвечать None, пока её не изобразят подстановкой в launched.
    """
    fx = fake_mo2.Fixture()
    fixtures.append(fx)
    log = []
    svc = services.Services(fx.organizer, run_main=lambda f, timeout=None: f(),
                            docs_path='README.md', note=log.append)
    svc.game_exe = 'NoSuchProcess.exe'.lower()
    svc._self_hwnd = 0
    get, post = routes.build(svc)
    return fx, svc, get, post, log


def keys(d):
    return sorted(d) if isinstance(d, dict) else d


def has(d, expected):
    """Ожидаемые ключи среди полученных. Возвращает expected, если все на месте, иначе -
    список недостающих: так в отчёте видно, чего именно нет.

    Сверка именно на вхождение, а не на равенство: форма ответа по договору меняется только
    добавлением ключей, и новый ключ не должен ломать проверку прежнего контракта.
    """
    if not isinstance(d, dict):
        return d
    missing = sorted(set(expected) - set(d))
    return expected if not missing else ['НЕТ: ' + k for k in missing]


def raised(fn, *args):
    """Исключение вызова или None. Тип и текст сверяются отдельно: договор - это
    ValueError с текстом из i18n, а не трассировка."""
    try:
        fn(*args)
        return None
    except Exception as exc:
        return exc


def is_clean_error(exc, key, **kw):
    """ValueError с ровно тем текстом, что даёт i18n, - без «multiple values» и прочего."""
    return isinstance(exc, ValueError) and str(exc) == i18n.t(key, **kw) \
        and 'multiple values' not in str(exc) and 'Traceback' not in str(exc)


def read_text(path):
    with io.open(path, 'rb') as fh:
        return fh.read().decode('utf-8')


def fake_recycle(path):
    """Вместо Корзины: временная папка Корзины не имеет, и SHFileOperation там отказывает."""
    if os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.remove(path)
    return True


# ================================================================ таблицы маршрутов
r.head('таблицы маршрутов: ровно 13 чтений и 11 действий')
fx, svc, get, post, log = make()
r.case('чтение - документированные 13', sorted(get), sorted(GET_ROUTES))
r.case('действия - документированные 11', sorted(post), sorted(POST_ROUTES))
r.case('пути не пересекаются', sorted(set(get) & set(post)), [])
r.case('каждый маршрут - вызываемый',
       all(callable(f) for f in list(get.values()) + list(post.values())), True)

# ================================================================ чтение
r.head('/ping')
res = get['/ping']({})
r.case('ключи', has(res, PING_KEYS), PING_KEYS)
r.case('ok', res['ok'], True)
r.case('busy - None в свободном состоянии', res['busy'], None)
r.case('mainThread', res['mainThread'], 'ok')
r.case('profile', res['profile'], 'Claude')
r.case('game', res['game'], 'SkyrimVR')
r.case('modsPath', res['modsPath'], fx.path('mods'))
r.case('overwrite', res['overwrite'], fx.path('overwrite'))
r.case('downloads', res['downloads'], fx.path('downloads'))
r.case('mo2Version - строка', isinstance(res['mo2Version'], str), True)

r.head('/mods')
res = get['/mods']({})
r.case('ключи', has(res, ['count', 'mods']), ['count', 'mods'])
r.case('count равен длине списка', res['count'], len(res['mods']))
r.case('четыре мода по приоритету', [m['mod'] for m in res['mods']],
       ['Alpha Mod', 'Beta Mod', 'Gamma Mod', 'Delta Mod'])
r.case('ключи записи', has(res['mods'][0], ['active', 'essential', 'mod', 'priority']), ['active', 'essential', 'mod', 'priority'])
r.case('приоритеты 0..3', [m['priority'] for m in res['mods']], [0, 1, 2, 3])
r.case('Delta выключен', [m['active'] for m in res['mods']], [True, True, True, False])
r.case('essential - булево', all(m['essential'] is False for m in res['mods']), True)

r.head('/mod - карточка')
res = get['/mod']({'name': ['Alpha Mod']})
r.case('ключи', has(res, CARD_KEYS), CARD_KEYS)
r.case('mod', res['mod'], 'Alpha Mod')
r.case('path', res['path'], fx.mod_path('Alpha Mod'))
r.case('active', res['active'], True)
r.case('priority', res['priority'], 0)
r.case('nexusId из meta.ini', res['nexusId'], 101)
r.case('url собрана из nexusId', res['url'],
       'https://www.nexusmods.com/skyrimspecialedition/mods/101')
r.case('urlFromMO2 - как отдала MO2', res['urlFromMO2'], '')
r.case('version', res['version'], '1.0')
r.case('installationFile', res['installationFile'], 'Alpha Mod-101-1-0.7z')
r.case('categories - список', res['categories'], ['3'])
r.case('primaryCategory', res['primaryCategory'], 3)
r.case('notes и comments различаются', (res['notes'], res['comments']),
       ('alpha note', 'alpha comment'))
r.case('isSeparator', res['isSeparator'], False)
r.case('endorsed - строка', isinstance(res['endorsed'], str), True)
res = get['/mod']({'name': ['Gamma Mod']})
r.case('без nexusId url пустая', res['url'], '')
r.case('без name - ValueError', is_clean_error(raised(get['/mod'], {}), 'err.needName'), True)
r.case('нет такого мода - ValueError',
       is_clean_error(raised(get['/mod'], {'name': ['Nope']}), 'err.noSuchMod', mod='Nope'),
       True)
r.case('name строкой тоже принимается', get['/mod']({'name': 'Beta Mod'})['mod'], 'Beta Mod')

r.head('/analyze - разбор с конфликтами')
res = get['/analyze']({'name': ['Alpha Mod']})
r.case('ключи', has(res, ANALYZE_KEYS), ANALYZE_KEYS)
r.case('ключи conflicts', has(res['conflicts'], CONFLICT_KEYS), CONFLICT_KEYS)
r.case('карточка внутри - та же', has(res['card'], CARD_KEYS), CARD_KEYS)
r.case('files - без meta.ini', res['files'], 3)
r.case('plugins', res['plugins'], ['Alpha.esp'])
r.case('sample отсортирован', res['sample'], sorted(res['sample']))
c = res['conflicts']
r.case('проверено', c['checked'], True)
r.case('спорный файл один', c['contested'], 1)
r.case('Alpha проигрывает Beta', (c['winsCount'], c['losesCount']), (0, 1))
r.case('ключи записи спора', has(c['loses'][0], ['file', 'providers']), ['file', 'providers'])
r.case('победитель первым', c['loses'][0]['providers'][0], 'Beta Mod')
r.case('оба поставщика, выключенный Delta не в счёт', c['loses'][0]['providers'],
       ['Beta Mod', 'Alpha Mod'])
res = get['/analyze']({'name': ['Beta Mod']})
r.case('Beta побеждает', (res['conflicts']['winsCount'], res['conflicts']['losesCount']),
       (1, 0))
res = get['/analyze']({'name': ['Alpha Mod'], 'conflicts': ['0'], 'limit': ['1']})
r.case('conflicts=0 - не проверено', res['conflicts']['checked'], False)
r.case('conflicts=0 - счётчики нули', (res['conflicts']['contested'],
                                       res['conflicts']['winsCount']), (0, 0))
r.case('limit режет sample', len(res['sample']), 1)
r.case('без name - ValueError', is_clean_error(raised(get['/analyze'], {}), 'err.needName'),
       True)
r.case('нет такого мода - ValueError',
       isinstance(raised(get['/analyze'], {'name': ['Nope']}), ValueError), True)

r.head('/profiles')
res = get['/profiles']({})
r.case('ключи', has(res, ['current', 'path', 'profiles']), ['current', 'path', 'profiles'])
r.case('current', res['current'], 'Claude')
r.case('все папки профилей', res['profiles'], ['Claude', 'Second'])
r.case('path - папка profiles', res['path'], fx.path('profiles'))

r.head('/plugins')
res = get['/plugins']({})
r.case('ключи', has(res, ['count', 'plugins']), ['count', 'plugins'])
r.case('count', res['count'], 3)
r.case('ключи записи', has(res['plugins'][0], PLUGIN_KEYS), PLUGIN_KEYS)
r.case('порядок из plugins.txt', [p['plugin'] for p in res['plugins']], FULL_ORDER)
r.case('звёздочки из plugins.txt', [p['active'] for p in res['plugins']], [True, False, True])
r.case('loadOrder по порядку', [p['loadOrder'] for p in res['plugins']], [0, 1, 2])
r.case('origin - мод-поставщик', [p['origin'] for p in res['plugins']],
       ['Alpha Mod', 'Beta Mod', 'Gamma Mod'])
r.case('esl по расширению', [p['esl'] for p in res['plugins']], [False, False, True])
r.case('masters - список', all(isinstance(p['masters'], list) for p in res['plugins']), True)
r.case('плагин выключенного мода не виден',
       'Delta.esp' in [p['plugin'] for p in res['plugins']], False)

r.head('/vfs, /origins, /dirs, /resolve')
res = get['/vfs']({'path': ['meshes'], 'filter': ['*.nif']})
r.case('ключи /vfs', has(res, ['files', 'filter', 'path']), ['files', 'filter', 'path'])
r.case('путь и маска возвращаются', (res['path'], res['filter']), ('meshes', '*.nif'))
r.case('файл-победитель из Beta', res['files'], [fx.mod_path('Beta Mod') + os.sep + 'meshes'
                                                 + os.sep + 'shared.nif'])
res = get['/vfs']({})
r.case('умолчания: корень и *', (res['path'], res['filter']), ('.', '*'))
r.case('в корне три плагина', sorted(os.path.basename(x) for x in res['files']), FULL_ORDER)
res = get['/origins']({'path': ['meshes' + chr(92) + 'shared.nif']})
r.case('ключи /origins', has(res, ['origins', 'path']), ['origins', 'path'])
r.case('первый побеждает', res['origins'], ['Beta Mod', 'Alpha Mod'])
r.case('неизвестный путь - пусто', get['/origins']({'path': ['nope.esp']})['origins'], [])
res = get['/dirs']({})
r.case('ключи /dirs', has(res, ['dirs', 'path']), ['dirs', 'path'])
r.case('подкаталоги корня', sorted(res['dirs']), ['meshes', 'scripts', 'textures'])
res = get['/resolve']({'path': ['textures' + chr(92) + 'gamma.dds']})
r.case('ключи /resolve', has(res, ['path', 'real']), ['path', 'real'])
r.case('настоящий путь', res['real'], os.path.join(fx.mod_path('Gamma Mod'), 'textures',
                                                    'gamma.dds'))
r.case('несуществующий - пустая строка', get['/resolve']({'path': ['nope']})['real'], '')
r.case('без path - ValueError', is_clean_error(raised(get['/resolve'], {}), 'err.needPath'),
       True)

r.head('/api')
res = get['/api']({})
r.case('ключи', has(res, ['IModList', 'IOrganizer', 'IPluginList', 'IProfile']), ['IModList', 'IOrganizer', 'IPluginList', 'IProfile'])
r.case('списки имён без служебных', all(isinstance(v, list) and not any(x.startswith('_')
                                                                          for x in v)
                                         for v in res.values()), True)
r.case('modList виден у IOrganizer', 'modList' in res['IOrganizer'], True)

r.head('/procs и /windows')
res = get['/procs']({})
r.case('ключи', has(res, ['busy', 'launchedByMO2', 'procs', 'running']), ['busy', 'launchedByMO2', 'procs', 'running'])
r.case('пусто до запусков', (res['procs'], res['running'], res['launchedByMO2']), ([], 0, []))
r.case('busy - None', res['busy'], None)
r.case('/windows без pid - ValueError',
       is_clean_error(raised(get['/windows'], {}), 'err.needPidOrKey'), True)
r.case('/windows с неизвестным key - ValueError',
       isinstance(raised(get['/windows'], {'key': ['p99']}), ValueError), True)
res = get['/windows']({'pid': [str(os.getpid())]})
r.case('/windows с pid - ключи', has(res, ['pid', 'windows']), ['pid', 'windows'])
r.case('pid возвращается числом', res['pid'], os.getpid())
r.case('windows - список', isinstance(res['windows'], list), True)

# ================================================================ изменения
r.head('/refresh')
before = fx.organizer.refreshed
res = post['/refresh']({})
r.case('refreshed', res.get('refreshed'), True)
r.case('MO2 действительно перечитала', fx.organizer.refreshed, before + 1)

r.head('/toggle')
res = post['/toggle']({'mod': 'Alpha Mod', 'active': False})
r.case('ключи', has(res, ['active', 'changed', 'mod', 'undo', 'was']), ['active', 'changed', 'mod', 'undo', 'was'])
r.case('было включено', res['was'], True)
r.case('стало выключено', res['active'], False)
r.case('changed', res['changed'], True)
r.case('undo - готовый запрос', res['undo'],
       {'route': '/toggle', 'body': {'mod': 'Alpha Mod', 'active': True}})
r.case('MO2 видит выключенным',
       [m['active'] for m in get['/mods']({})['mods'] if m['mod'] == 'Alpha Mod'], [False])
r.case('плагин выключенного мода пропал из /plugins',
       [p['plugin'] for p in get['/plugins']({})['plugins']], ['Beta.esp', 'Gamma.esl'])
res = post['/toggle'](res['undo']['body'])
r.case('откат по undo возвращает', (res['active'], res['was'], res['changed']),
       (True, False, True))
res = post['/toggle']({'mod': 'Alpha Mod', 'active': True})
r.case('повтор без изменения: changed False', (res['changed'], res['was']), (False, True))
r.case('без mod - ValueError',
       is_clean_error(raised(post['/toggle'], {'active': True}), 'err.needMod'), True)
r.case('без active - ValueError',
       is_clean_error(raised(post['/toggle'], {'mod': 'Alpha Mod'}), 'err.needMod'), True)
r.case('нет такого мода - ValueError',
       is_clean_error(raised(post['/toggle'], {'mod': 'Nope', 'active': True}),
                      'err.noSuchMod', mod='Nope'), True)

# Булевы поля разбираются строго. bool("false") в Python - True, и раньше запрос
# {"active": "false"} ВКЛЮЧАЛ мод, возвращая changed: true, то есть делал обратное
# заказанному и объявлял это успехом. Клиенты на shell и curl шлют строки постоянно.
for word, want in (('false', False), ('true', True), ('0', False), ('1', True),
                   ('no', False), ('yes', True), ('OFF', False)):
    post['/toggle']({'mod': 'Alpha Mod', 'active': not want})
    res = post['/toggle']({'mod': 'Alpha Mod', 'active': word})
    r.case('active=%r понято как %s' % (word, want), res['active'], want)
for junk in ('maybe', '', 'дa', 2, [], {}):
    r.case('active=%r отвергнуто, а не угадано' % (junk,),
           isinstance(raised(post['/toggle'], {'mod': 'Alpha Mod', 'active': junk}),
                      ValueError), True)
r.case('withArchive тоже строгий',
       isinstance(raised(post['/mods/remove'],
                         dict(KEY, mod='Alpha Mod', withArchive='наверное')),
                  ValueError), True)
r.case('apply тоже строгий',
       isinstance(raised(post['/plugins/state'],
                         {'set': {'alpha.esp': True}, 'apply': 'наверное'}), ValueError), True)
post['/toggle']({'mod': 'Alpha Mod', 'active': True})

r.head('/plugins/state - предпросмотр')
raw_before = fx.read_bytes('profiles', 'Claude', 'plugins.txt')
res = post['/plugins/state']({'set': {'Alpha.esp': False, 'Beta.esp': True,
                                      'Gamma.esl': True, 'Delta.esp': True}})
r.case('ключи предпросмотра', has(res, ['applied', 'changes', 'unknown']), ['applied', 'changes', 'unknown'])
r.case('applied False', res['applied'], False)
r.case('changes - только реальные перемены', res['changes'],
       [{'plugin': 'Alpha.esp', 'from': True, 'to': False},
        {'plugin': 'Beta.esp', 'from': False, 'to': True}])
r.case('unknown - плагин выключенного мода', res['unknown'], ['Delta.esp'])
r.case('файл не тронут', fx.read_bytes('profiles', 'Claude', 'plugins.txt'), raw_before)
r.case('память MO2 не тронута', [p['active'] for p in get['/plugins']({})['plugins']],
       [True, False, True])
r.case('без set - ValueError',
       is_clean_error(raised(post['/plugins/state'], {}), 'err.needSet'), True)
r.case('set не словарь - ValueError',
       isinstance(raised(post['/plugins/state'], {'set': ['Alpha.esp']}), ValueError), True)

r.head('/plugins/state - apply пишет plugins.txt')
res = post['/plugins/state']({'set': {'Alpha.esp': False, 'Beta.esp': True}, 'apply': True})
r.case('ключи с файлом', has(res, ['applied', 'changes', 'file', 'unknown']), ['applied', 'changes', 'file', 'unknown'])
r.case('applied True', res['applied'], True)
r.case('ключи file', has(res['file'], ['changed', 'notInFile', 'path', 'written']), ['changed', 'notInFile', 'path', 'written'])
r.case('written', res['file']['written'], True)
r.case('path - plugins.txt профиля', res['file']['path'], fx.plugins_txt())
r.case('changed - имена в нижнем регистре', res['file']['changed'], ['alpha.esp', 'beta.esp'])
r.case('notInFile пуст', res['file']['notInFile'], [])
raw_after = fx.read_bytes('profiles', 'Claude', 'plugins.txt')
r.case('файл изменился', raw_after != raw_before, True)
r.case('CRLF сохранён', raw_after.count(b'\r\n'), raw_before.count(b'\r\n'))
r.case('LF без CR не появился', raw_after.count(b'\n'), raw_after.count(b'\r\n'))
lines = raw_after.decode('utf-8').split(fake_mo2.CRLF)
r.case('строки переставлены точечно', lines,
       ['# This file was automatically generated by Mod Organizer.',
        'Alpha.esp', '*Beta.esp', '*Gamma.esl', ''])
r.case('память MO2 тоже', [p['active'] for p in get['/plugins']({})['plugins']],
       [False, True, True])
r.case('обратная установка по changes',
       post['/plugins/state']({'set': {c['plugin']: c['from'] for c in res['changes']},
                               'apply': True})['file']['changed'], ['alpha.esp', 'beta.esp'])
r.case('файл вернулся к исходному', fx.read_bytes('profiles', 'Claude', 'plugins.txt'),
       raw_before)
res = post['/plugins/state']({'set': {'Alpha.esp': True}, 'apply': True})
r.case('apply без перемен: ключа file нет', has(res, ['applied', 'changes', 'unknown']), ['applied', 'changes', 'unknown'])
r.case('apply без перемен: applied всё же True', (res['applied'], res['changes']), (True, []))

r.head('/plugins/order')
res = post['/plugins/order']({'order': ['Alpha.esp']})
r.case('неполный список - ключи с error', has(res, ['applied', 'count', 'error', 'notListed', 'unknown']), ['applied', 'count', 'error', 'notListed', 'unknown'])
r.case('неполный - не применено', res['applied'], False)
r.case('notListed', res['notListed'], ['Beta.esp', 'Gamma.esl'])
r.case('error - текст i18n', res['error'], i18n.t('err.orderIncomplete', missing=2, extra=0))
res = post['/plugins/order']({'order': FULL_ORDER + ['Nope.esp']})
r.case('лишний плагин - unknown', (res['unknown'], 'error' in res), (['Nope.esp'], True))
new_order = ['Gamma.esl', 'Alpha.esp', 'Beta.esp']
res = post['/plugins/order']({'order': new_order})
r.case('полный без apply - ключи', has(res, ['applied', 'before', 'count', 'notListed', 'undo', 'unknown']), ['applied', 'before', 'count', 'notListed', 'undo', 'unknown'])
r.case('предпросмотр не применяет', res['applied'], False)
r.case('before - весь прежний порядок', res['before'], FULL_ORDER)
r.case('undo - готовый запрос', res['undo'],
       {'route': '/plugins/order', 'body': {'order': FULL_ORDER, 'apply': True}})
r.case('порядок в MO2 не тронут', [p['plugin'] for p in get['/plugins']({})['plugins']],
       FULL_ORDER)
res = post['/plugins/order']({'order': new_order, 'apply': True})
r.case('apply - applied True', res['applied'], True)
r.case('apply - before прежний', res['before'], FULL_ORDER)
r.case('порядок в MO2 сменился', [p['plugin'] for p in get['/plugins']({})['plugins']],
       new_order)
res = post['/plugins/order'](res['undo']['body'])
r.case('откат по undo', (res['applied'], [p['plugin'] for p in get['/plugins']({})['plugins']]),
       (True, FULL_ORDER))
r.case('без order - ValueError',
       is_clean_error(raised(post['/plugins/order'], {}), 'err.needOrder'), True)

r.head('/vfsexport')
res = post['/vfsexport']({'outDir': fx.path('export')})
r.case('ключи', has(res, ['contested', 'csv', 'files', 'fromArchive', 'how', 'profile']), ['contested', 'csv', 'files', 'fromArchive', 'how', 'profile'])
r.case('профиль', res['profile'], 'Claude')
r.case('через virtualFileTree', res['how'], 'virtualFileTree')
r.case('все файлы активных модов', res['files'], 7)
r.case('спорный файл один', res['contested'], 1)
r.case('из архивов - ноль', res['fromArchive'], 0)
r.case('csv лежит в outDir', res['csv'], fx.path('export', 'vfs-Claude.csv'))
r.case('csv существует', os.path.isfile(res['csv']), True)
csv = read_text(res['csv']).split(chr(10))
r.case('заголовок csv', csv[0], 'rel;winner;archive;providers')
r.case('строк по числу файлов', len([x for x in csv[1:] if x]), res['files'])
row = [x for x in csv if x.lower().startswith('meshes' + chr(92) + 'shared.nif')]
r.case('спорный файл: победитель и поставщики', row,
       ['meshes' + chr(92) + 'shared.nif;Beta Mod;;Beta Mod|Alpha Mod'])
res = post['/vfsexport']({'outDir': fx.path('export'), 'legacyWalk': True})
r.case('legacyWalk - обход вширь', res['how'], 'walk')
r.case('обход вширь даёт те же файлы', res['files'], 7)
res = post['/vfsexport']({})
r.case('без outDir - basePath/vfs-export', res['csv'],
       os.path.join(fx.root, 'vfs-export', 'vfs-Claude.csv'))

# ================================================================ /install
r.head('/install - проверки входа')
r.case('без archive - ValueError',
       is_clean_error(raised(post['/install'], {'name': 'X'}), 'err.noArchive', archive=None),
       True)
r.case('несуществующий archive - ValueError',
       is_clean_error(raised(post['/install'], {'archive': fx.path('nope.7z'), 'name': 'X'}),
                      'err.noArchive', archive=fx.path('nope.7z')), True)
dummy = fx.path('downloads', 'Alpha Mod-101-1-0.7z')
r.case('без name - ValueError',
       is_clean_error(raised(post['/install'], {'archive': dummy}), 'err.needName'), True)
r.case('чужой mode - ValueError',
       is_clean_error(raised(post['/install'], {'archive': dummy, 'name': 'X', 'mode': 'ovr'}),
                      'err.badMode', mode='ovr'), True)
r.case('папка занята, mode нет - ValueError',
       is_clean_error(raised(post['/install'], {'archive': dummy, 'name': 'Alpha Mod'}),
                      'err.modExists', mod='Alpha Mod'), True)
r.case('отказ ничего не создал', os.path.isdir(fx.mod_path('X')), False)

r.head('/install - новая папка, слияние, замена')
archive = fake_mo2.build_archive(fx.path('downloads', 'Zeta Mod-999-1-0.7z'))
if archive is None:
    r.note('ПРОПУСК', '7-Zip не найден - установка из архива не проверяется')
elif not shutil.which('7z'):
    # Пакет ищет 7z.exe по-своему; PATH - единственное место, за которое отвечаем мы.
    # Добавляется только в окружение этого процесса, на диске ничего не меняется.
    os.environ['PATH'] = os.path.dirname(fake_mo2.seven_zip()) + os.pathsep + \
        os.environ.get('PATH', '')
    r.note('7z добавлен в PATH процесса', os.path.dirname(fake_mo2.seven_zip()))
if archive is not None:
    # Распаковка идёт через %TEMP%; на время проверки он ведёт во временную сборку, чтобы
    # не задеть чужую распаковку, если настоящий мост работает в этот же момент.
    temp_was = os.environ.get('TEMP')
    os.environ['TEMP'] = fx.path('temp')
    os.makedirs(fx.path('temp'))
    recycle_was = winapi.recycle
    winapi.recycle = fake_recycle
    res = None
    try:
        # Установка одного варианта - как ставится FOMOD по разобранным опциям.
        # Если пакет не нашёл 7z.exe, это пропуск, а не сбой: проверить было нечем.
        try:
            res = post['/install']({'archive': archive, 'name': 'Zeta Mod',
                                    'paths': ['00 Core']})
        except RuntimeError as exc:
            if str(exc) != i18n.t('err.no7z'):
                raise
            r.note('ПРОПУСК', 'пакет не нашёл 7z.exe - установка не проверяется')
            res = None
    finally:
        if res is None:
            winapi.recycle = recycle_was
            if temp_was is None:
                os.environ.pop('TEMP', None)
            else:
                os.environ['TEMP'] = temp_was
if archive is not None and res is not None:
    try:
        r.case('ключи', has(res, INSTALL_KEYS), INSTALL_KEYS)
        r.case('created, mode new', (res['created'], res['existed'], res['mode']),
               (True, False, 'new'))
        r.case('файлы варианта скопированы', res['files'], 2)
        r.case('fomod не встречался', res['fomodSkipped'], False)
        r.case('archive - имя файла', res['archive'], 'Zeta Mod-999-1-0.7z')
        r.case('path - папка мода', res['path'], fx.mod_path('Zeta Mod'))
        r.case('undo - снос созданной папки', res['undo'],
               {'route': '/mods/remove', 'body': {'mod': 'Zeta Mod'}})
        r.case('filesBefore 0', res['filesBefore'], 0)
        # meta.ini в added - его пишет createMod до копирования, и «было» для новой папки пусто
        r.case('added - вариант лёг в корень, в нижнем регистре', res['added'],
               sorted(['zeta.esp', os.path.join('textures', 'zeta.dds'), 'meta.ini']))
        r.case('addedCount', res['addedCount'], 3)
        r.case('overwritten пуст', (res['overwritten'], res['overwrittenCount']), ([], 0))
        r.case('removedToRecycleBin 0', res['removedToRecycleBin'], 0)
        r.case('на диске ровно added', sorted(fake_mo2.tree_files(fx.mod_path('Zeta Mod'))),
               res['added'])
        r.case('мод виден MO2 после refresh',
               'Zeta Mod' in [m['mod'] for m in get['/mods']({})['mods']], True)
        r.case('мод выключен после установки',
               get['/mod']({'name': ['Zeta Mod']})['active'], False)

        # Весь архив целиком: подпапки вариантов ложатся как есть, fomod и корневой
        # meta.ini пропускаются
        res = post['/install']({'archive': archive, 'name': 'Zeta Whole', 'paths': ['']})
        r.case('весь архив - fomodSkipped', res['fomodSkipped'], True)
        r.case('весь архив - файлов без fomod и meta.ini', res['files'], 3)
        r.case('весь архив - подпапки как есть', res['added'],
               sorted([os.path.join('00 core', 'zeta.esp'),
                       os.path.join('00 core', 'textures', 'zeta.dds'),
                       os.path.join('10 extra', 'meshes', 'zeta.nif'), 'meta.ini']))
        r.case('весь архив - fomod на диске нет',
               os.path.isdir(fx.path('mods', 'Zeta Whole', 'fomod')), False)
        r.case('весь архив - meta.ini из архива не перекрыл meta.ini MO2',
               'modid=0' in read_text(fx.path('mods', 'Zeta Whole', 'meta.ini')), True)

        # слияние: только 00 Core поверх Zeta Mod - два файла перекрыты
        res = post['/install']({'archive': archive, 'name': 'Zeta Mod', 'paths': ['00 Core'],
                                'mode': 'merge'})
        r.case('merge - ключи те же', has(res, INSTALL_KEYS), INSTALL_KEYS)
        r.case('merge - existed, mode', (res['created'], res['existed'], res['mode']),
               (False, True, 'merge'))
        # overwritten считается как пересечение «было» и «стало», то есть в него попадает
        # и то, что лежало в папке и не перезаписывалось (meta.ini). Договор здесь - что
        # реально перекрытые файлы в списке есть и счётчик равен длине списка.
        r.case('merge - перекрытые файлы в списке',
               {'zeta.esp', os.path.join('textures', 'zeta.dds')} <= set(res['overwritten']),
               True)
        r.case('merge - overwrittenCount равен списку', res['overwrittenCount'],
               len(res['overwritten']))
        r.case('merge - ничего не добавлено', (res['added'], res['addedCount']), ([], 0))
        r.case('merge - undo None', res['undo'], None)
        r.case('merge - filesBefore == filesAfter', res['filesBefore'], res['filesAfter'])
        r.case('merge - fomod не затронут', res['fomodSkipped'], False)
        r.case('merge - файлы на месте', sorted(fake_mo2.tree_files(fx.mod_path('Zeta Mod'))),
               sorted(['meta.ini', 'zeta.esp', os.path.join('textures', 'zeta.dds')]))

        # замена без ключа: она уносит прежнее содержимое в Корзину, то есть теряет чужую
        # работу, и потому закрыта замком необратимого наравне с удалением. Слияние и
        # обычная установка ключа не требуют - они ничего не теряют.
        res = post['/install']({'archive': archive, 'name': 'Zeta Mod', 'paths': ['10 Extra'],
                                'mode': 'replace'})
        r.case('replace без ключа - отказ', (res['applied'], res['reason']),
               (False, 'danger'))
        r.case('replace без ключа - файлы на месте',
               sorted(fake_mo2.tree_files(fx.mod_path('Zeta Mod'))),
               sorted(['meta.ini', 'zeta.esp', os.path.join('textures', 'zeta.dds')]))

        # замена: прежнее в Корзину, meta.ini остаётся, кладётся другой вариант
        res = post['/install'](dict(KEY, archive=archive, name='Zeta Mod',
                                    paths=['10 Extra'], mode='replace'))
        r.case('replace - ключи те же', has(res, INSTALL_KEYS), INSTALL_KEYS)
        r.case('replace - mode', res['mode'], 'replace')
        r.case('replace - в Корзину ушли две записи, meta.ini нет',
               res['removedToRecycleBin'], 2)
        r.case('replace - на диске только новое и meta.ini',
               sorted(fake_mo2.tree_files(fx.mod_path('Zeta Mod'))),
               sorted(['meta.ini', os.path.join('meshes', 'zeta.nif')]))
        r.case('replace - filesBefore/filesAfter', (res['filesBefore'], res['filesAfter']),
               (3, 2))
        r.case('replace - added: чего не было до замены',
               (res['added'], res['addedCount']), ([os.path.join('meshes', 'zeta.nif')], 1))
        r.case('replace - overwritten пуст', (res['overwritten'], res['overwrittenCount']),
               ([], 0))
        r.case('replace - undo None', res['undo'], None)
        r.case('meta.ini пережил замену',
               'modid=0' in read_text(fx.path('mods', 'Zeta Mod', 'meta.ini')), True)
        r.case('папка распаковки прибрана после установки', os.listdir(fx.path('temp')), [])
        r.case('нет пути в архиве - ValueError',
               is_clean_error(raised(post['/install'], {'archive': archive, 'name': 'Zeta Mod',
                                                        'paths': ['99 Nope'], 'mode': 'merge'}),
                              'err.noPathInArchive', path='99 Nope'), True)
        r.case('отказ по пути ничего не положил',
               sorted(fake_mo2.tree_files(fx.mod_path('Zeta Mod'))),
               sorted(['meta.ini', os.path.join('meshes', 'zeta.nif')]))
        r.note('после отказа по пути в %TEMP% осталось', os.listdir(fx.path('temp')))

        # --- имя мода не выпускается из mods\ ---------------------------------------
        # Имя подставляется в путь, и на Windows os.path.join отбрасывает первый кусок,
        # если второй абсолютный: 'C:\\Users\\...' указал бы на чужую папку, а '..' - на
        # уровень выше, где лежат профили, загрузки и overwrite. С mode=replace это
        # означало бы чужие файлы в Корзине, поэтому имя проверяется до всякой работы.
        outside = fx.path('outside')
        os.makedirs(outside, exist_ok=True)
        io.open(os.path.join(outside, 'важное.txt'), 'w', encoding='utf-8').write('не трогать')
        for bad in (outside, '..', os.path.join('..', 'profiles'), 'a/b', 'a' + os.sep + 'b',
                    'C:', '.'):
            r.case('имя вне mods отвергнуто: %r' % bad,
                   is_clean_error(raised(post['/install'],
                                         dict(KEY, archive=archive, name=bad,
                                              mode='replace')), 'err.badName', name=bad),
                   True)
        r.case('чужая папка цела после всех попыток',
               sorted(os.listdir(outside)), ['важное.txt'])
    finally:
        winapi.recycle = recycle_was
        if temp_was is None:
            os.environ.pop('TEMP', None)
        else:
            os.environ['TEMP'] = temp_was

# ================================================================ необратимое
r.head('/mods/priority')
res = post['/mods/priority']({'mod': 'Alpha Mod', 'priority': 2})
r.case('без ключа - ключи отказа', has(res, ['applied', 'blocked', 'docs', 'from', 'mod', 'to', 'why']), ['applied', 'blocked', 'docs', 'from', 'mod', 'to', 'why'])
r.case('без ключа - не применено', res['applied'], False)
r.case('blocked - имя операции', res['blocked'], i18n.t('op.priority'))
r.case('why - текст про ключ', res['why'], i18n.t('danger.why', key='iUnderstandTheRisk'))
r.case('docs - путь к документации', res['docs'], 'README.md')
r.case('from/to показывают, что было бы', (res['from'], res['to']), (0, 2))
r.case('приоритет не тронут', get['/mod']({'name': ['Alpha Mod']})['priority'], 0)
res = post['/mods/priority'](dict({'mod': 'Alpha Mod', 'priority': 2}, **KEY))
r.case('с ключом - ключи', has(res, ['applied', 'from', 'mod', 'to']), ['applied', 'from', 'mod', 'to'])
r.case('с ключом - применено', res['applied'], True)
r.case('from/to', (res['from'], res['to']), (0, 2))
r.case('порядок сменился', [m['mod'] for m in get['/mods']({})['mods']][:3],
       ['Beta Mod', 'Gamma Mod', 'Alpha Mod'])
r.case('обратный ход по from',
       post['/mods/priority'](dict({'mod': 'Alpha Mod', 'priority': res['from']}, **KEY))['to'],
       0)
r.case('без mod - ValueError',
       is_clean_error(raised(post['/mods/priority'], {'priority': 1}), 'err.needMod'), True)
r.case('без priority - ValueError',
       is_clean_error(raised(post['/mods/priority'], {'mod': 'Alpha Mod'}), 'err.needMod'),
       True)
r.case('нет такого мода даже без ключа - ValueError',
       is_clean_error(raised(post['/mods/priority'], {'mod': 'Nope', 'priority': 1}),
                      'err.noSuchMod', mod='Nope'), True)

r.head('/mods/rename')
res = post['/mods/rename']({'mod': 'Gamma Mod', 'newName': 'Gamma Renamed'})
r.case('без ключа - ключи отказа', has(res, ['applied', 'blocked', 'docs', 'mod', 'newName', 'why']), ['applied', 'blocked', 'docs', 'mod', 'newName', 'why'])
r.case('без ключа - не применено', res['applied'], False)
r.case('blocked', res['blocked'], i18n.t('op.rename'))
r.case('папка на месте', os.path.isdir(fx.mod_path('Gamma Mod')), True)
res = post['/mods/rename'](dict({'mod': 'Gamma Mod', 'newName': 'Gamma Renamed'}, **KEY))
r.case('с ключом - ключи', has(res, ['applied', 'fromPath', 'mod', 'newName', 'nexusId', 'toPath', 'undo']), ['applied', 'fromPath', 'mod', 'newName', 'nexusId', 'toPath', 'undo'])
r.case('применено', res['applied'], True)
r.case('fromPath/toPath', (res['fromPath'], res['toPath']),
       (fx.mod_path('Gamma Mod'), fx.mod_path('Gamma Renamed')))
r.case('nexusId сохранён в ответе', res['nexusId'], 0)
r.case('undo - обратное переименование', res['undo'],
       {'route': '/mods/rename', 'body': {'mod': 'Gamma Renamed', 'newName': 'Gamma Mod'}})
r.case('папка переименована', (os.path.isdir(fx.mod_path('Gamma Mod')),
                               os.path.isdir(fx.mod_path('Gamma Renamed'))), (False, True))
r.case('MO2 знает новое имя', get['/mod']({'name': ['Gamma Renamed']})['mod'],
       'Gamma Renamed')
res = post['/mods/rename'](dict(res['undo']['body'], **KEY))
r.case('откат по undo', (res['applied'], os.path.isdir(fx.mod_path('Gamma Mod'))),
       (True, True))
r.case('без newName - ValueError',
       is_clean_error(raised(post['/mods/rename'], {'mod': 'Gamma Mod'}), 'err.needMod'), True)
r.case('нет такого мода с ключом - ValueError',
       is_clean_error(raised(post['/mods/rename'],
                             dict({'mod': 'Nope', 'newName': 'N'}, **KEY)),
                      'err.noSuchMod', mod='Nope'), True)
r.case('нет такого мода БЕЗ ключа - тоже ValueError, а не отказ замка',
       is_clean_error(raised(post['/mods/rename'], {'mod': 'Nope', 'newName': 'N'}),
                      'err.noSuchMod', mod='Nope'), True)
res = post['/mods/rename']({'mod': 'Gamma Mod', 'newName': 'Gamma Renamed'})
r.case('отказ замка несёт fromPath и nexusId', has(res, ['fromPath', 'nexusId']),
       ['fromPath', 'nexusId'])

r.head('/mods/remove')
res = post['/mods/remove']({'mod': 'Alpha Mod'})
r.case('без ключа - ключи отказа', has(res, ['applied', 'blocked', 'card', 'docs', 'mod', 'why', 'withArchive']), ['applied', 'blocked', 'card', 'docs', 'mod', 'why', 'withArchive'])
r.case('без ключа - не применено', res['applied'], False)
r.case('blocked', res['blocked'], i18n.t('op.remove'))
r.case('withArchive по умолчанию False', res['withArchive'], False)
card = res['card']
r.case('карточка - ключи', has(card, REMOVAL_CARD_KEYS), REMOVAL_CARD_KEYS)
r.case('карточка - опознание', (card['mod'], card['nexusId'], card['version']),
       ('Alpha Mod', 101, '1.0'))
r.case('карточка - url', card['url'], 'https://www.nexusmods.com/skyrimspecialedition/mods/101')
r.case('карточка - архив найден в downloads',
       (card['installationFile'], card['archivePath'], card['archiveOnDisk']),
       ('Alpha Mod-101-1-0.7z', fx.path('downloads', 'Alpha Mod-101-1-0.7z'), True))
r.case('карточка - где стоял и был ли включён', (card['priority'], card['active']), (0, True))
r.case('карточка - число файлов с meta.ini', card['files'], 4)
r.case('карточка - categories список', card['categories'], ['3'])
r.case('папка цела', os.path.isdir(fx.mod_path('Alpha Mod')), True)
card = post['/mods/remove']({'mod': 'Gamma Mod'})['card']
r.case('архива нет на диске - archiveOnDisk False',
       (card['archivePath'], card['archiveOnDisk'], card['url']), ('', False, ''))

res = post['/mods/remove'](dict({'mod': 'Beta Mod'}, **KEY))
r.case('с ключом - ключи', has(res, ['applied', 'archiveKept', 'archiveRecycled', 'card', 'mod', 'path', 'undo']), ['applied', 'archiveKept', 'archiveRecycled', 'card', 'mod', 'path', 'undo'])
r.case('применено', res['applied'], True)
r.case('path - папка до сноса', res['path'], fx.mod_path('Beta Mod'))
r.case('карточка снята до сноса', (has(res['card'], REMOVAL_CARD_KEYS), res['card']['files']),
       (REMOVAL_CARD_KEYS, 4))
r.case('архив оставлен', (res['archiveKept'], res['archiveRecycled']), (True, False))
r.case('undo - переустановка из архива', res['undo'],
       {'route': '/install', 'body': {'archive': fx.path('downloads', 'Beta Mod-202-2-1.7z'),
                                      'name': 'Beta Mod'}})
r.case('папка снесена', os.path.isdir(fx.mod_path('Beta Mod')), False)
r.case('архив на месте', os.path.isfile(fx.path('downloads', 'Beta Mod-202-2-1.7z')), True)
r.case('MO2 мода больше не знает',
       'Beta Mod' in [m['mod'] for m in get['/mods']({})['mods']], False)
r.case('его плагин пропал', 'Beta.esp' in [p['plugin'] for p in get['/plugins']({})['plugins']],
       False)

recycle_was = winapi.recycle
winapi.recycle = fake_recycle
try:
    res = post['/mods/remove'](dict({'mod': 'Alpha Mod', 'withArchive': True}, **KEY))
finally:
    winapi.recycle = recycle_was
r.case('withArchive - архив в Корзину', (res['archiveKept'], res['archiveRecycled']),
       (False, True))
r.case('withArchive - архива нет', os.path.isfile(fx.path('downloads', 'Alpha Mod-101-1-0.7z')),
       False)
r.case('withArchive - undo None: восстановить нечем', res['undo'], None)
r.case('нет архива - archiveKept True, undo None',
       (lambda x: (x['archiveKept'], x['archiveRecycled'], x['undo']))(
           post['/mods/remove'](dict({'mod': 'Gamma Mod', 'withArchive': True}, **KEY))),
       (True, False, None))
r.case('без mod - ValueError',
       is_clean_error(raised(post['/mods/remove'], {}), 'err.needMod'), True)
r.case('нет такого мода без ключа - ValueError',
       is_clean_error(raised(post['/mods/remove'], {'mod': 'Nope'}), 'err.noSuchMod',
                      mod='Nope'), True)

# ================================================================ занятость
r.head('MO2 занята: девять изменяющих маршрутов отказывают')
fx, svc, get, post, log = make()
svc.launched = {k: dict(v) for k, v in ЧУЖОЙ_ЗАПУСК.items()}
plugins_before = fx.read_bytes('profiles', 'Claude', 'plugins.txt')
modlist_before = fx.read_bytes('profiles', 'Claude', 'modlist.txt')
res = get['/ping']({})
r.case('/ping показывает занятость', has(res['busy'], BUSY_KEYS), BUSY_KEYS)
r.case('/ping - держит безымянный', (res['busy']['app'], res['busy']['viaMO2'],
                                     res['busy']['heldByUnknown']), (None, True, True))
r.case('/ping - mo2Run', res['busy']['mo2Run'], sorted(ЧУЖОЙ_ЗАПУСК))
r.case('/procs - тот же busy и launchedByMO2',
       (has(get['/procs']({})['busy'], BUSY_KEYS), get['/procs']({})['launchedByMO2']),
       (BUSY_KEYS, sorted(ЧУЖОЙ_ЗАПУСК)))
BODIES = {
    '/refresh': {},
    '/install': {'archive': fx.path('downloads', 'Alpha Mod-101-1-0.7z'), 'name': 'New'},
    '/toggle': {'mod': 'Alpha Mod', 'active': False},
    '/run': {'binary': 'FakeTool'},
    '/plugins/state': {'set': {'Alpha.esp': False}, 'apply': True},
    '/plugins/order': {'order': ['Gamma.esl', 'Alpha.esp', 'Beta.esp'], 'apply': True},
    '/mods/priority': dict({'mod': 'Alpha Mod', 'priority': 2}, **KEY),
    '/mods/rename': dict({'mod': 'Alpha Mod', 'newName': 'Alpha Renamed'}, **KEY),
    '/mods/remove': dict({'mod': 'Alpha Mod'}, **KEY),
}
OPS = {'/refresh': 'op.refresh', '/install': 'op.install', '/toggle': 'op.toggle',
       '/run': 'op.run', '/plugins/state': 'op.pluginState', '/plugins/order': 'op.pluginOrder',
       '/mods/priority': 'op.priority', '/mods/rename': 'op.rename',
       '/mods/remove': 'op.remove'}
r.case('все девять в списке действий', sorted(set(MUTATING) - set(post)), [])
why_expected = i18n.t('busy.whyUnknown', run=', '.join(sorted(ЧУЖОЙ_ЗАПУСК)))
for route in MUTATING:
    res = post[route](BODIES[route])
    r.case('%s - ключи отказа' % route, has(res, REFUSAL_KEYS), REFUSAL_KEYS)
    r.case('%s - applied False, busy True' % route, (res['applied'], res['busy']),
           (False, True))
    r.case('%s - running' % route, has(res['running'], BUSY_KEYS), BUSY_KEYS)
    r.case('%s - blocked' % route, res['blocked'], i18n.t(OPS[route]))
    r.case('%s - why' % route, res['why'], why_expected)
    r.case('%s - подпись: reason busy, op' % route, (res.get('reason'), res.get('op')),
           ('busy', OPS[route][3:]))
r.case('why - не трассировка', 'Traceback' in why_expected or 'multiple values' in why_expected,
       False)
r.case('учёт чужого запуска цел', sorted(svc.launched), sorted(ЧУЖОЙ_ЗАПУСК))
r.case('plugins.txt не тронут', fx.read_bytes('profiles', 'Claude', 'plugins.txt'),
       plugins_before)
r.case('modlist.txt не тронут', fx.read_bytes('profiles', 'Claude', 'modlist.txt'),
       modlist_before)
r.case('папки модов целы', all(os.path.isdir(fx.mod_path(n)) for n, _s in fake_mo2.MODS), True)
r.case('папка New не создана', os.path.isdir(fx.mod_path('New')), False)
r.case('startApplication не звался', fx.organizer.started, [])
r.case('MO2 не просили перечитать', fx.organizer.refreshed, 0)

r.head('MO2 занята: чтение и предпросмотр работают')
r.case('/mods отвечает', get['/mods']({})['count'], 4)
r.case('/plugins отвечает', get['/plugins']({})['count'], 3)
r.case('/analyze отвечает', get['/analyze']({'name': ['Alpha Mod']})['files'], 3)
res = post['/plugins/state']({'set': {'Alpha.esp': False}})
r.case('/plugins/state без apply - предпросмотр', has(res, ['applied', 'changes', 'unknown']), ['applied', 'changes', 'unknown'])
r.case('/plugins/state без apply - есть перемены',
       (res['applied'], len(res['changes'])), (False, 1))
res = post['/plugins/order']({'order': ['Gamma.esl', 'Alpha.esp', 'Beta.esp']})
r.case('/plugins/order без apply - предпросмотр', has(res, ['applied', 'before', 'count', 'notListed', 'undo', 'unknown']), ['applied', 'before', 'count', 'notListed', 'undo', 'unknown'])
r.case('/plugins/order без apply - before', (res['applied'], res['before']), (False, FULL_ORDER))
r.case('/vfsexport не заперт', post['/vfsexport']({'outDir': fx.path('export')})['files'], 7)
r.case('/windows не заперт', keys(get['/windows']({'pid': [str(os.getpid())]})),
       ['pid', 'windows'])
r.case('/plugins/state: проверка set идёт раньше замка',
       is_clean_error(raised(post['/plugins/state'], {'apply': True}), 'err.needSet'), True)
r.case('/plugins/order: проверка order идёт раньше замка',
       is_clean_error(raised(post['/plugins/order'], {'apply': True}), 'err.needOrder'), True)

r.head('MO2 отпустила: onFinishedRun снимает замок')
svc.on_finished_run(sorted(ЧУЖОЙ_ЗАПУСК)[0], 0)
r.case('busy None', get['/ping']({})['busy'], None)
r.case('/refresh снова работает', post['/refresh']({}).get('refreshed'), True)

# ================================================================ /run и /window
r.head('/run - без запуска процесса')
fx, svc, get, post, log = make()
r.case('без binary - ValueError',
       is_clean_error(raised(post['/run'], {}), 'err.needBinary'), True)
res = post['/run'](dict({'binary': 'FakeTool', 'args': ['-x']}, **KEY))
r.case('ключи', has(res, ['args', 'binary', 'key', 'pid']), ['args', 'binary', 'key', 'pid'])
r.case('key p1', res['key'], 'p1')
r.case('pid 0: процесса нет', res['pid'], 0)
r.case('binary и args возвращаются', (res['binary'], res['args']), ('FakeTool', ['-x']))
r.case('MO2 просили запустить именно это', fx.organizer.started, [('FakeTool', ['-x'], '')])
r.case('второй запуск - p2', post['/run'](dict({'binary': 'FakeTool'}, **KEY))['key'], 'p2')
res = get['/procs']({})
r.case('/procs видит оба', (len(res['procs']), res['running']), (2, 0))
r.case('/procs - ключи записи', has(res['procs'][0], ['alive', 'exit', 'key', 'pid', 'what']), ['alive', 'exit', 'key', 'pid', 'what'])
r.case('what - имя файла', res['procs'][0]['what'], 'FakeTool')
r.case('/windows по key с pid 0 - ValueError',
       is_clean_error(raised(get['/windows'], {'key': ['p1']}), 'err.needPidOrKey'), True)

r.head('/run - свой запуск учитывается и снимается сам')
fx.organizer.onAboutToRun(svc.on_about_to_run)
post['/run'](dict({'binary': 'FakeTool'}, **KEY))
r.case('в учёте после onAboutToRun', sorted(svc.launched), ['FakeTool'])
r.case('помечен своим', svc.launched['FakeTool']['mine'], True)
r.case('процесса нет - снят с учёта, свободно', (get['/ping']({})['busy'], svc.launched),
       (None, {}))
r.case('след в note остался', any('FakeTool' in x for x in log), True)

r.head('/window - только отказы')
r.case('без hwnd - ValueError',
       is_clean_error(raised(post['/window'], {}), 'err.noSuchWindow'), True)
r.case('мёртвый hwnd - ValueError',
       is_clean_error(raised(post['/window'], {'hwnd': 1, 'action': 'close'}),
                      'err.noSuchWindow'), True)

# ================================================================ тексты ошибок
r.head('тексты i18n - не трассировки')
for key in ('op.toggle', 'op.remove', 'busy.whyUnknown', 'danger.why', 'err.noSuchMod',
            'err.orderIncomplete', 'err.modExists'):
    text = i18n.t(key, mod='m', run='r', key='k', missing=1, extra=2)
    r.case('%s - подставлено' % key, '%(' in text or 'multiple values' in text, False)
r.case('t(key=...) не падает на имени key', i18n.t('danger.why', key='X').count('X') >= 1, True)

for fx in fixtures:
    fx.cleanup()
# ================================================================ обновления
r.head('/updates: единственная точка проверки обновлений')
updates_mod = importlib.import_module(pkg.__name__ + '.updates')
fx, svc, get, post, log = make()
svc.cfg.values['updates']['delaySec'] = 0
r.case('без mod и без all - ValueError',
       is_clean_error(raised(get['/updates'], {}), 'err.needModOrAll'), True)
res = get['/updates']({'mod': ['Alpha Mod', 'Beta Mod', 'Gamma Mod', 'Delta Mod', 'Nope']})
r.case('ключи', has(res, ['count', 'mods', 'checked', 'elapsedSec', 'rule']),
       ['count', 'mods', 'checked', 'elapsedSec', 'rule'])
r.case('по записи на мод', res['count'], 5)
by = dict((m['mod'], m) for m in res['mods'])
r.case('ключи записи', has(by['Alpha Mod'], ['mod', 'nexusId', 'checked', 'verdict', 'why',
                                              'installed', 'downloaded', 'newest', 'items',
                                              'extra', 'files']),
       ['mod', 'nexusId', 'checked', 'verdict', 'why', 'installed', 'downloaded', 'newest',
        'items', 'extra', 'files'])
r.case('Alpha - UPDATE-AVAILABLE', by['Alpha Mod']['verdict'], 'UPDATE-AVAILABLE')
r.case('Alpha - наш файл опознан по .meta',
       (by['Alpha Mod']['installed']['fileId'], by['Alpha Mod']['items'][0]['mine']['fileId']),
       (1001, 1001))
r.case('Alpha - назван файл новее', by['Alpha Mod']['items'][0]['newer'][0]['fileId'], 1002)
r.case('Alpha - why словами', by['Alpha Mod']['why'], i18n.t('upd.newer'))
r.case('Beta - UP-TO-DATE, патч другой роли не обновление', by['Beta Mod']['verdict'],
       'UP-TO-DATE')
r.case('Gamma - NO-NEXUS-ID', (by['Gamma Mod']['verdict'], by['Gamma Mod']['checked']),
       ('NO-NEXUS-ID', False))
r.case('Delta - ERROR с текстом отказа',
       (by['Delta Mod']['verdict'], 'moderation' in (by['Delta Mod'].get('error') or '')),
       ('ERROR', True))
r.case('Nope - ERROR, нет такого мода', by['Nope']['verdict'], 'ERROR')
r.case('проверено трое: у двоих нет страницы или ответа', res['checked'], 2)
r.case('мост к Nexus создан один раз', len(fx.organizer.bridges), 1)
r.case('запросы ушли по nexusId', sorted(q[1] for q in fx.organizer.bridges[0].requests),
       [101, 202, 404])
res = get['/updates']({'all': ['1'], 'limit': ['2']})
r.case('all: страница из двух', (res['count'], res['total'], res['more']), (2, 4, True))
res = get['/updates']({'all': ['1'], 'offset': ['2']})
r.case('all: со смещением до конца', (res['count'], res['more']), (2, False))
svc.launched = {k: dict(v) for k, v in ЧУЖОЙ_ЗАПУСК.items()}
r.case('при занятой MO2 работает', get['/updates']({'mod': ['Beta Mod']})['mods'][0]['verdict'],
       'UP-TO-DATE')

r.head('/updates: прямой путь к API, когда мост MO2 не подключается')
fx, svc, get, post, log = make()
svc.cfg.values['updates']['delaySec'] = 0
API_PAGES = {
    101: {'files': [
        {'file_id': 1001, 'name': 'Alpha Mod', 'file_name': 'Alpha Mod-101-1-0.7z',
         'version': '1.0', 'category_id': 4, 'uploaded_timestamp': fake_mo2.T0},
        {'file_id': 1002, 'name': 'Alpha Mod', 'file_name': 'Alpha Mod-101-1-1.7z',
         'version': '1.1', 'category_id': 1, 'uploaded_timestamp': fake_mo2.T0 + 30 * fake_mo2.DAY}]},
    202: {'files': [
        {'file_id': 2001, 'name': 'Beta Mod', 'file_name': 'Beta Mod-202-2-1.7z',
         'version': '2.1', 'category_id': 1, 'uploaded_timestamp': fake_mo2.T0}]},
}
seen_urls = []
def fake_http(url, headers, timeout):
    seen_urls.append((url, headers.get('apikey')))
    mod_id = int(url.rstrip('/').split('/mods/')[1].split('/')[0])
    if mod_id not in API_PAGES:
        raise urllib_error.HTTPError(url, 404, 'Not Found', {}, None)
    return 200, {'x-rl-daily-remaining': '2400'}, json.dumps(API_PAGES[mod_id])
import json
import urllib.error as urllib_error
fx.organizer.createNexusBridge = lambda: (_ for _ in ()).throw(
    TypeError("C++ type 'QList<ModRepositoryFileInfo*>' is not supported as a signal argument type"))
svc.updater._http_get = fake_http
cred_was = winapi.read_generic_credential
winapi.read_generic_credential = lambda target: 'fake-key' if target == 'ModOrganizer2_APIKEY' else None
try:
    res = get['/updates']({'mod': ['Alpha Mod', 'Beta Mod', 'Delta Mod']})
finally:
    winapi.read_generic_credential = cred_was
by = dict((m['mod'], m) for m in res['mods'])
r.case('путь выбран: api', res['via'], 'api')
r.case('лимит из заголовков', res['quota'].get('daily-remaining'), '2400')
r.case('ключ ушёл заголовком apikey, в ответе его нет',
       (seen_urls[0][1], 'fake-key' in json.dumps(res)), ('fake-key', False))
r.case('адрес: домен игры и modID',
       seen_urls[0][0].endswith('/v1/games/skyrimspecialedition/mods/101/files.json'), True)
r.case('Alpha - UPDATE-AVAILABLE тем же правилом', by['Alpha Mod']['verdict'], 'UPDATE-AVAILABLE')
r.case('Beta - UP-TO-DATE', by['Beta Mod']['verdict'], 'UP-TO-DATE')
r.case('Delta - ERROR HTTP 404', (by['Delta Mod']['verdict'], 'HTTP 404' in by['Delta Mod']['error']),
       ('ERROR', True))
r.case('путь выбирается один раз на сессию', svc.updater._mode, 'api')
winapi.read_generic_credential = lambda target: None
svc2 = services.Services(fx.organizer, run_main=lambda f, timeout=None: f(),
                         docs_path='README.md')
svc2.game_exe = 'NoSuchProcess.exe'.lower()
svc2._self_hwnd = 0
svc2.cfg.values['updates']['delaySec'] = 0
try:
    res = svc2.updates({'mod': ['Alpha Mod']})
finally:
    winapi.read_generic_credential = cred_was
r.case('без ключа - ERROR со словами про подключение к Nexus',
       (res['mods'][0]['verdict'], 'Nexus' in res['mods'][0]['why']), ('ERROR', True))

r.head('/updates: отказ канала прерывает обход, а не повторяется на каждый мод')


def sweep(http, names=('Alpha Mod', 'Beta Mod', 'Delta Mod'), key='fake-key'):
    """Один обход с подставным ответом сети. Возвращает (ответ, сколько раз спросили)."""
    f = make()[0]
    s = services.Services(f.organizer, run_main=lambda fn, timeout=None: fn(),
                          docs_path='README.md')
    s.game_exe = 'NoSuchProcess.exe'.lower()
    s._self_hwnd = 0
    s.cfg.values['updates']['delaySec'] = 0
    f.organizer.createNexusBridge = lambda: (_ for _ in ()).throw(TypeError('нет сигнала'))
    calls = []

    def wrapped(url, headers, timeout):
        calls.append(url)
        return http(url, headers, timeout)

    s.updater._http_get = wrapped
    was = winapi.read_generic_credential
    winapi.read_generic_credential = lambda target: key
    try:
        return s.updates({'mod': list(names)}), len(calls)
    finally:
        winapi.read_generic_credential = was


def raise_http(code, headers=None):
    def http(url, _h, _t):
        raise urllib_error.HTTPError(url, code, 'нет', headers or {}, None)
    return http


# Негодный ключ: раньше каждый из полутора тысяч модов честно шёл в сеть и честно
# получал 401. Теперь первый же отказ прекращает обход.
res, calls = sweep(raise_http(401))
r.case('401 - обход прекращён после первого запроса', calls, 1)
r.case('401 - сказано, почему прекращён', 'HTTP 401' in res['stopped'] or
       'ключ' in res['stopped'], True)
r.case('401 - неспрошенные посчитаны', res['unchecked'], 2)
r.case('401 - мод, на котором споткнулись, в ответе', res['mods'][0]['verdict'], 'ERROR')

# Исчерпанный лимит: продолжать значило бы продлевать запрет каждым запросом.
res, calls = sweep(raise_http(429, {'Retry-After': '600'}))
r.case('429 - обход прекращён после первого запроса', calls, 1)
r.case('429 - Retry-After снят с ОТКАЗА, а не с удачного ответа',
       res['quota'].get('retry-after'), '600')
r.case('429 - в причине названа пауза', '600' in res['stopped'], True)

# Обрыв связи: одна неудача - не беда, пять подряд означают, что канал лёг.
def broken(_url, _h, _t):
    raise OSError('соединение оборвано')


res, calls = sweep(broken, names=('Alpha Mod', 'Beta Mod', 'Delta Mod'))
r.case('обрыв связи: три мода - три попытки, порог не достигнут', calls, 3)
r.case('обрыв связи: обход не прерван, все моды в ответе', len(res['mods']), 3)
r.case('обрыв связи: каждый мод - ERROR',
       sorted(set(m['verdict'] for m in res['mods'])), ['ERROR'])

# Кончающийся запас суточного лимита: ключ общий с самой MO2, и доедать его нельзя.
def near_limit(url, _h, _t):
    mod_id = int(url.rstrip('/').split('/mods/')[1].split('/')[0])
    page = API_PAGES.get(mod_id) or {'files': []}
    return 200, {'x-rl-daily-remaining': '3'}, json.dumps(page)


res, calls = sweep(near_limit)
r.case('запас кончился - обход прекращён после первого мода', calls, 1)
r.case('запас кончился - сказано, сколько осталось', '3' in res['stopped'], True)
r.case('запас кончился - ключ остаётся рабочим для MO2', res['unchecked'], 2)

r.head('/updates: раздел Nexus берётся у MO2, а не подставляется наугад')
fx_d = make()[0]
svc_d = services.Services(fx_d.organizer, run_main=lambda fn, timeout=None: fn(),
                          docs_path='README.md')
# Таблица перекрытий пуста, зато MO2 знает имя своего раздела: спрашивать надо её.
svc_d.cfg.values['nexusDomains'] = {}
svc_d.cfg.values['nexusDomainDefault'] = ''
fx_d.organizer.managedGame().nexus_name = 'fallout4'
r.case('домен спрошен у MO2', svc_d.updater.nexus_domain('ЧужаяИгра'), 'fallout4')
r.case('ссылка собрана по нему',
       svc_d.updater.nexus_url('ЧужаяИгра', 7).endswith('fallout4/mods/7'), True)
# MO2 молчит, перекрытия нет - честный отказ, а не чужой раздел с тем же номером мода.
svc_e = services.Services(make()[0].organizer, run_main=lambda fn, timeout=None: fn(),
                          docs_path='README.md')
svc_e.cfg.values['nexusDomains'] = {}
svc_e.cfg.values['nexusDomainDefault'] = ''
svc_e.o.managedGame().nexus_name = ''
r.case('имени нет - домена нет, а не Skyrim SE', svc_e.updater.nexus_domain('ЧужаяИгра'), '')
r.case('без домена ссылки не будет', svc_e.updater.nexus_url('ЧужаяИгра', 7), '')

r.head('/updates: «скачано, не установлено» считается по странице, а не по одному моду')
fx, svc, get, post, log = make()
svc.cfg.values['updates']['delaySec'] = 0
# На диске появился новейший архив страницы Alpha (fileID 1002), но ни один мод из него
# не собран - DOWNLOADED-NOT-INSTALLED. Как только сосед по странице собран из него,
# патч, собранный из старого архива, перестаёт быть «не установленным».
newest_arc = fx.path('downloads', 'Alpha Mod-101-1-1.7z')
io.open(newest_arc, 'w').write('archive')
os.utime(newest_arc, (fake_mo2.T0 + 30 * fake_mo2.DAY, fake_mo2.T0 + 30 * fake_mo2.DAY))
got_all = {101: {1001: 'Alpha Mod-101-1-0.7z', 1002: 'Alpha Mod-101-1-1.7z'}}
target = {'mod': 'Alpha Mod - Patch', 'nexusId': 101, 'game': 'SkyrimSE',
          'installationFile': 'Alpha Mod-101-1-0.7z', 'version': '1.0',
          'mo2NewestVersion': '', 'ignoredVersion': ''}
dl_root = fx.path('downloads')
alone = svc.updater._check_one(dict(target), got_all, dl_root, 30, {101: ['Alpha Mod-101-1-0.7z']})
r.case('без соседа - DOWNLOADED-NOT-INSTALLED', alone['verdict'], 'DOWNLOADED-NOT-INSTALLED')
mates = svc.updater._check_one(dict(target), got_all, dl_root, 30,
                               {101: ['Alpha Mod-101-1-0.7z', 'Alpha Mod-101-1-1.7z']})
r.case('сосед собран из новейшего - UP-TO-DATE', mates['verdict'], 'UP-TO-DATE')
r.case('дата установки по странице в ответе', mates['installed']['pageTime'],
       fake_mo2.T0 + 30 * fake_mo2.DAY)
os.unlink(newest_arc)

r.head('/updates: чистые правила решения')
decide, D = updates_mod.decide, updates_mod.DAY
def F(name, fid, cat, t, ver='', fn=None):
    return {'name': name, 'fileName': fn or name + '.7z', 'fileId': fid, 'version': ver,
            'category': cat, 'time': t}
r.case('версии не сравниваются: новее по дате той же роли',
       decide([F('Tool SE', 1, 'main', 100, '2.0'), F('Tool SE', 2, 'main', 100 + 2 * D, '1.9')],
              {1}, set(), 100)['verdict'], 'UPDATE-AVAILABLE')
r.case('GOG-сборка - сосед, не обновление',
       decide([F('Tool SE Steam', 1, 'main', 100), F('Tool SE GOG', 2, 'main', 100 + 2 * D)],
              {1}, set(), 100)['verdict'], 'UP-TO-DATE')
r.case('та же версия через минуты - сосед одного выпуска',
       decide([F('Tool', 1, 'main', 100, '1.0'), F('Tool', 2, 'main', 100 + 60, '1.0')],
              {1}, set(), 100)['verdict'], 'UP-TO-DATE')
r.case('наш файл в Old files без замены - CANNOT-MATCH',
       decide([F('Tool', 1, 'old', 100), F('Other Thing', 2, 'main', 100 - D)],
              {1}, set(), 100)['verdict'], 'CANNOT-MATCH')
r.case('все наши в Old, Main новее под другим именем - обновление',
       decide([F('Tool', 1, 'old', 100), F('Tool Redux', 2, 'main', 100 + 3 * D)],
              {1}, set(), 100)['verdict'], 'UPDATE-AVAILABLE')
r.case('ничего не помечено скачанным, Main новее архива - REUPLOADED',
       decide([F('Tool', 5, 'main', 100 + 3 * D)], set(), set(), 100)['verdict'], 'REUPLOADED')
r.case('ничего не помечено и дат нет - CANNOT-MATCH',
       decide([F('Tool', 5, 'main', 100 + 3 * D)], set(), set(), None)['verdict'], 'CANNOT-MATCH')
r.case('свежий архив скачан, мод собран из старого - DOWNLOADED-NOT-INSTALLED',
       decide([F('Tool', 1, 'old', 100), F('Tool', 2, 'main', 100 + 3 * D)],
              {1, 2}, set(), 100)['verdict'], 'DOWNLOADED-NOT-INSTALLED')
r.case('хотфикс в Update - обновление',
       decide([F('Tool', 1, 'main', 100), F('Tool Hotfix', 2, 'update', 100 + D)],
              {1}, set(), 100)['verdict'], 'UPDATE-AVAILABLE')
r.case('файл другой роли в Main - CANNOT-MATCH',
       decide([F('Tool', 1, 'main', 100), F('Tool Extras Pack', 2, 'main', 100 + 2 * D)],
              {1}, set(), 100)['verdict'], 'CANNOT-MATCH')
r.case('момент из имени архива: эпоха', updates_mod.arc_time('x-1234-1-0-1700000000.7z'),
       1700000000)
r.case('момент из имени архива: ISO', updates_mod.arc_time('x 1 2026-09-07T09-14Z y.7z') > 0, True)
r.case('4k не версия', updates_mod.role('Tool 4K v2.1') != updates_mod.role('Tool 2K v2.1'), True)

# ================================================================ подпись карточки
r.head('подпись карточки: op и applied на каждом изменении, reason на отказе')
fx, svc, get, post, log = make()
SIGNED = {
    '/refresh': ({}, 'refresh', True),
    '/toggle': ({'mod': 'Alpha Mod', 'active': True}, 'toggle', True),
    '/plugins/state': ({'set': {'Alpha.esp': False}, 'apply': True}, 'pluginState', True),
    '/plugins/order': ({'order': FULL_ORDER, 'apply': True}, 'pluginOrder', True),
    '/run': (dict({'binary': 'FakeTool'}, **KEY), 'run', True),
    '/mods/priority': (dict({'mod': 'Alpha Mod', 'priority': 1}, **KEY), 'priority', True),
    '/mods/rename': (dict({'mod': 'Gamma Mod', 'newName': 'Gamma Renamed'}, **KEY), 'rename', True),
    '/mods/remove': (dict({'mod': 'Delta Mod'}, **KEY), 'remove', True),
}
for route, (body, op, applied) in SIGNED.items():
    res = post[route](body)
    r.case('%s - op, applied' % route, (res.get('op'), res.get('applied')), (op, applied))
for route, body in (('/mods/priority', {'mod': 'Alpha Mod', 'priority': 1}),
                    ('/mods/rename', {'mod': 'Alpha Mod', 'newName': 'X'}),
                    ('/mods/remove', {'mod': 'Alpha Mod'}),
                    ('/run', {'binary': 'FakeTool'})):
    res = post[route](body)
    r.case('%s без ключа - reason danger, applied False' % route,
           (res.get('reason'), res.get('applied'), res.get('op')),
           ('danger', False, OPS[route][3:]))
res = post['/plugins/state']({'set': {'Alpha.esp': False}})
r.case('предпросмотр подписан, applied False', (res.get('op'), res.get('applied')),
       ('pluginState', False))
res = post['/plugins/order']({'order': FULL_ORDER})
r.case('предпросмотр порядка подписан', (res.get('op'), res.get('applied')),
       ('pluginOrder', False))
res = post['/run'](dict({'binary': 'FakeTool'}, **KEY))
r.case('/run без дескриптора - started False', res.get('started'), False)

r.done()
