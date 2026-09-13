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
r = common.Report(T('contract.title'))

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
r.head(T('contract.routeTables'))
fx, svc, get, post, log = make()
r.case(T('contract.readsDocumented'), sorted(get), sorted(GET_ROUTES))
r.case(T('contract.writesDocumented'), sorted(post), sorted(POST_ROUTES))
r.case(T('contract.pathsDisjoint'), sorted(set(get) & set(post)), [])
r.case(T('contract.everyRouteCallable'),
       all(callable(f) for f in list(get.values()) + list(post.values())), True)

# ================================================================ чтение
r.head(T('contract.hPing'))
res = get['/ping']({})
r.case(T('contract.pingKeys'), has(res, PING_KEYS), PING_KEYS)
r.case(T('contract.pingOk'), res['ok'], True)
r.case(T('contract.pingBusyNone'), res['busy'], None)
r.case(T('contract.pingMainThread'), res['mainThread'], 'ok')
r.case(T('contract.pingProfile'), res['profile'], 'Claude')
r.case(T('contract.pingGame'), res['game'], 'SkyrimVR')
r.case(T('contract.pingModsPath'), res['modsPath'], fx.path('mods'))
r.case(T('contract.pingOverwrite'), res['overwrite'], fx.path('overwrite'))
r.case(T('contract.pingDownloads'), res['downloads'], fx.path('downloads'))
r.case(T('contract.pingVersionString'), isinstance(res['mo2Version'], str), True)

r.head(T('contract.hMods'))
res = get['/mods']({})
r.case(T('contract.modsKeys'), has(res, ['count', 'mods']), ['count', 'mods'])
r.case(T('contract.modsCountMatches'), res['count'], len(res['mods']))
r.case(T('contract.modsFourByPriority'), [m['mod'] for m in res['mods']],
       ['Alpha Mod', 'Beta Mod', 'Gamma Mod', 'Delta Mod'])
r.case(T('contract.modsEntryKeys'), has(res['mods'][0], ['active', 'essential', 'mod', 'priority']), ['active', 'essential', 'mod', 'priority'])
r.case(T('contract.modsPriorities'), [m['priority'] for m in res['mods']], [0, 1, 2, 3])
r.case(T('contract.modsDeltaOff'), [m['active'] for m in res['mods']], [True, True, True, False])
r.case(T('contract.modsEssentialBool'), all(m['essential'] is False for m in res['mods']), True)

r.head(T('contract.hModCard'))
res = get['/mod']({'name': ['Alpha Mod']})
r.case(T('contract.modKeys'), has(res, CARD_KEYS), CARD_KEYS)
r.case(T('contract.modName'), res['mod'], 'Alpha Mod')
r.case(T('contract.modPath'), res['path'], fx.mod_path('Alpha Mod'))
r.case(T('contract.modActive'), res['active'], True)
r.case(T('contract.modPriority'), res['priority'], 0)
r.case(T('contract.modNexusId'), res['nexusId'], 101)
r.case(T('contract.modUrlBuilt'), res['url'],
       'https://www.nexusmods.com/skyrimspecialedition/mods/101')
r.case(T('contract.modUrlFromMo2'), res['urlFromMO2'], '')
r.case(T('contract.modVersion'), res['version'], '1.0')
r.case(T('contract.modInstallationFile'), res['installationFile'], 'Alpha Mod-101-1-0.7z')
r.case(T('contract.modCategoriesList'), res['categories'], ['3'])
r.case(T('contract.modPrimaryCategory'), res['primaryCategory'], 3)
r.case(T('contract.modNotesVsComments'), (res['notes'], res['comments']),
       ('alpha note', 'alpha comment'))
r.case(T('contract.modIsSeparator'), res['isSeparator'], False)
r.case(T('contract.modEndorsedString'), isinstance(res['endorsed'], str), True)
res = get['/mod']({'name': ['Gamma Mod']})
r.case(T('contract.modNoNexusIdNoUrl'), res['url'], '')
r.case(T('contract.modNeedsName'), is_clean_error(raised(get['/mod'], {}), 'err.needName'), True)
r.case(T('contract.modNoSuchMod'),
       is_clean_error(raised(get['/mod'], {'name': ['Nope']}), 'err.noSuchMod', mod='Nope'),
       True)
r.case(T('contract.modNameAsString'), get['/mod']({'name': 'Beta Mod'})['mod'], 'Beta Mod')

r.head(T('contract.hAnalyze'))
res = get['/analyze']({'name': ['Alpha Mod']})
r.case(T('contract.analyzeKeys'), has(res, ANALYZE_KEYS), ANALYZE_KEYS)
r.case(T('contract.analyzeConflictKeys'), has(res['conflicts'], CONFLICT_KEYS), CONFLICT_KEYS)
r.case(T('contract.analyzeCardSame'), has(res['card'], CARD_KEYS), CARD_KEYS)
r.case(T('contract.analyzeFilesNoMeta'), res['files'], 3)
r.case(T('contract.analyzePlugins'), res['plugins'], ['Alpha.esp'])
r.case(T('contract.analyzeSampleSorted'), res['sample'], sorted(res['sample']))
c = res['conflicts']
r.case(T('contract.analyzeChecked'), c['checked'], True)
r.case(T('contract.analyzeOneContested'), c['contested'], 1)
r.case(T('contract.analyzeAlphaLoses'), (c['winsCount'], c['losesCount']), (0, 1))
r.case(T('contract.analyzeConflictEntryKeys'), has(c['loses'][0], ['file', 'providers']), ['file', 'providers'])
r.case(T('contract.analyzeWinnerFirst'), c['loses'][0]['providers'][0], 'Beta Mod')
r.case(T('contract.analyzeBothProviders'), c['loses'][0]['providers'],
       ['Beta Mod', 'Alpha Mod'])
res = get['/analyze']({'name': ['Beta Mod']})
r.case(T('contract.analyzeBetaWins'), (res['conflicts']['winsCount'], res['conflicts']['losesCount']),
       (1, 0))
res = get['/analyze']({'name': ['Alpha Mod'], 'conflicts': ['0'], 'limit': ['1']})
r.case(T('contract.analyzeOffNotChecked'), res['conflicts']['checked'], False)
r.case(T('contract.analyzeOffZeroCounts'), (res['conflicts']['contested'],
                                       res['conflicts']['winsCount']), (0, 0))
r.case(T('contract.analyzeLimitCutsSample'), len(res['sample']), 1)
r.case(T('contract.analyzeNeedsName'), is_clean_error(raised(get['/analyze'], {}), 'err.needName'),
       True)
r.case(T('contract.analyzeNoSuchMod'),
       isinstance(raised(get['/analyze'], {'name': ['Nope']}), ValueError), True)

r.head(T('contract.hProfiles'))
res = get['/profiles']({})
r.case(T('contract.profilesKeys'), has(res, ['current', 'path', 'profiles']), ['current', 'path', 'profiles'])
r.case(T('contract.profilesCurrent'), res['current'], 'Claude')
r.case(T('contract.profilesAllFolders'), res['profiles'], ['Claude', 'Second'])
r.case(T('contract.profilesPath'), res['path'], fx.path('profiles'))

r.head(T('contract.hPlugins'))
res = get['/plugins']({})
r.case(T('contract.pluginsKeys'), has(res, ['count', 'plugins']), ['count', 'plugins'])
r.case(T('contract.pluginsCount'), res['count'], 3)
r.case(T('contract.pluginsEntryKeys'), has(res['plugins'][0], PLUGIN_KEYS), PLUGIN_KEYS)
r.case(T('contract.pluginsOrderFromFile'), [p['plugin'] for p in res['plugins']], FULL_ORDER)
r.case(T('contract.pluginsAsterisksFromFile'), [p['active'] for p in res['plugins']], [True, False, True])
r.case(T('contract.pluginsLoadOrder'), [p['loadOrder'] for p in res['plugins']], [0, 1, 2])
r.case(T('contract.pluginsOrigin'), [p['origin'] for p in res['plugins']],
       ['Alpha Mod', 'Beta Mod', 'Gamma Mod'])
r.case(T('contract.pluginsEslByExtension'), [p['esl'] for p in res['plugins']], [False, False, True])
r.case(T('contract.pluginsMastersList'), all(isinstance(p['masters'], list) for p in res['plugins']), True)
r.case(T('contract.pluginsOfDisabledModHidden'),
       'Delta.esp' in [p['plugin'] for p in res['plugins']], False)

r.head(T('contract.hVfsGroup'))
res = get['/vfs']({'path': ['meshes'], 'filter': ['*.nif']})
r.case(T('contract.vfsKeys'), has(res, ['files', 'filter', 'path']), ['files', 'filter', 'path'])
r.case(T('contract.vfsPathAndFilter'), (res['path'], res['filter']), ('meshes', '*.nif'))
r.case(T('contract.vfsWinnerFromBeta'), res['files'], [fx.mod_path('Beta Mod') + os.sep + 'meshes'
                                                 + os.sep + 'shared.nif'])
res = get['/vfs']({})
r.case(T('contract.vfsDefaults'), (res['path'], res['filter']), ('.', '*'))
r.case(T('contract.vfsThreePluginsAtRoot'), sorted(os.path.basename(x) for x in res['files']), FULL_ORDER)
res = get['/origins']({'path': ['meshes' + chr(92) + 'shared.nif']})
r.case(T('contract.originsKeys'), has(res, ['origins', 'path']), ['origins', 'path'])
r.case(T('contract.originsFirstWins'), res['origins'], ['Beta Mod', 'Alpha Mod'])
r.case(T('contract.originsUnknownEmpty'), get['/origins']({'path': ['nope.esp']})['origins'], [])
res = get['/dirs']({})
r.case(T('contract.dirsKeys'), has(res, ['dirs', 'path']), ['dirs', 'path'])
r.case(T('contract.dirsRootSubfolders'), sorted(res['dirs']), ['meshes', 'scripts', 'textures'])
res = get['/resolve']({'path': ['textures' + chr(92) + 'gamma.dds']})
r.case(T('contract.resolveKeys'), has(res, ['path', 'real']), ['path', 'real'])
r.case(T('contract.resolveRealPath'), res['real'], os.path.join(fx.mod_path('Gamma Mod'), 'textures',
                                                    'gamma.dds'))
r.case(T('contract.resolveMissingEmpty'), get['/resolve']({'path': ['nope']})['real'], '')
r.case(T('contract.resolveNeedsPath'), is_clean_error(raised(get['/resolve'], {}), 'err.needPath'),
       True)

r.head(T('contract.hApi'))
res = get['/api']({})
r.case(T('contract.apiKeys'), has(res, ['IModList', 'IOrganizer', 'IPluginList', 'IProfile']), ['IModList', 'IOrganizer', 'IPluginList', 'IProfile'])
r.case(T('contract.apiNamesNoDunder'), all(isinstance(v, list) and not any(x.startswith('_')
                                                                          for x in v)
                                         for v in res.values()), True)
r.case(T('contract.apiModListVisible'), 'modList' in res['IOrganizer'], True)

r.head(T('contract.hProcsWindows'))
res = get['/procs']({})
r.case(T('contract.procsKeys'), has(res, ['busy', 'launchedByMO2', 'procs', 'running']), ['busy', 'launchedByMO2', 'procs', 'running'])
r.case(T('contract.procsEmptyBeforeRuns'), (res['procs'], res['running'], res['launchedByMO2']), ([], 0, []))
r.case(T('contract.procsBusyNone'), res['busy'], None)
r.case(T('contract.windowsNeedsPid'),
       is_clean_error(raised(get['/windows'], {}), 'err.needPidOrKey'), True)
r.case(T('contract.windowsUnknownKey'),
       isinstance(raised(get['/windows'], {'key': ['p99']}), ValueError), True)
res = get['/windows']({'pid': [str(os.getpid())]})
r.case(T('contract.windowsKeys'), has(res, ['pid', 'windows']), ['pid', 'windows'])
r.case(T('contract.windowsPidIsNumber'), res['pid'], os.getpid())
r.case(T('contract.windowsIsList'), isinstance(res['windows'], list), True)

# ================================================================ изменения
r.head(T('contract.hRefresh'))
before = fx.organizer.refreshed
res = post['/refresh']({})
r.case(T('contract.refreshFlag'), res.get('refreshed'), True)
r.case(T('contract.refreshActuallyReread'), fx.organizer.refreshed, before + 1)

r.head(T('contract.hToggle'))
res = post['/toggle']({'mod': 'Alpha Mod', 'active': False})
r.case(T('contract.toggleKeys'), has(res, ['active', 'changed', 'mod', 'undo', 'was']), ['active', 'changed', 'mod', 'undo', 'was'])
r.case(T('contract.toggleWasOn'), res['was'], True)
r.case(T('contract.toggleNowOff'), res['active'], False)
r.case(T('contract.toggleChanged'), res['changed'], True)
r.case(T('contract.toggleUndoReady'), res['undo'],
       {'route': '/toggle', 'body': {'mod': 'Alpha Mod', 'active': True}})
r.case(T('contract.toggleMo2SeesOff'),
       [m['active'] for m in get['/mods']({})['mods'] if m['mod'] == 'Alpha Mod'], [False])
r.case(T('contract.togglePluginGone'),
       [p['plugin'] for p in get['/plugins']({})['plugins']], ['Beta.esp', 'Gamma.esl'])
res = post['/toggle'](res['undo']['body'])
r.case(T('contract.toggleUndoRestores'), (res['active'], res['was'], res['changed']),
       (True, False, True))
res = post['/toggle']({'mod': 'Alpha Mod', 'active': True})
r.case(T('contract.toggleRepeatNoChange'), (res['changed'], res['was']), (False, True))
r.case(T('contract.toggleNeedsMod'),
       is_clean_error(raised(post['/toggle'], {'active': True}), 'err.needMod'), True)
r.case(T('contract.toggleNeedsActive'),
       is_clean_error(raised(post['/toggle'], {'mod': 'Alpha Mod'}), 'err.needMod'), True)
r.case(T('contract.toggleNoSuchMod'),
       is_clean_error(raised(post['/toggle'], {'mod': 'Nope', 'active': True}),
                      'err.noSuchMod', mod='Nope'), True)

# Булевы поля разбираются строго. bool("false") в Python - True, и раньше запрос
# {"active": "false"} ВКЛЮЧАЛ мод, возвращая changed: true, то есть делал обратное
# заказанному и объявлял это успехом. Клиенты на shell и curl шлют строки постоянно.
for word, want in (('false', False), ('true', True), ('0', False), ('1', True),
                   ('no', False), ('yes', True), ('OFF', False)):
    post['/toggle']({'mod': 'Alpha Mod', 'active': not want})
    res = post['/toggle']({'mod': 'Alpha Mod', 'active': word})
    r.case(T('contract.activeUnderstoodAs', word=word, want=want), res['active'], want)
for junk in ('maybe', '', 'дa', 2, [], {}):
    r.case(T('contract.activeRejected', junk=junk),
           isinstance(raised(post['/toggle'], {'mod': 'Alpha Mod', 'active': junk}),
                      ValueError), True)
r.case(T('contract.withArchiveStrict'),
       isinstance(raised(post['/mods/remove'],
                         dict(KEY, mod='Alpha Mod', withArchive='наверное')),
                  ValueError), True)
r.case(T('contract.applyStrict'),
       isinstance(raised(post['/plugins/state'],
                         {'set': {'alpha.esp': True}, 'apply': 'наверное'}), ValueError), True)
post['/toggle']({'mod': 'Alpha Mod', 'active': True})

r.head(T('contract.hStatePreview'))
raw_before = fx.read_bytes('profiles', 'Claude', 'plugins.txt')
res = post['/plugins/state']({'set': {'Alpha.esp': False, 'Beta.esp': True,
                                      'Gamma.esl': True, 'Delta.esp': True}})
r.case(T('contract.statePreviewKeys'), has(res, ['applied', 'changes', 'unknown']), ['applied', 'changes', 'unknown'])
r.case(T('contract.statePreviewNotApplied'), res['applied'], False)
r.case(T('contract.stateChangesOnlyReal'), res['changes'],
       [{'plugin': 'Alpha.esp', 'from': True, 'to': False},
        {'plugin': 'Beta.esp', 'from': False, 'to': True}])
r.case(T('contract.stateUnknownPlugin'), res['unknown'], ['Delta.esp'])
r.case(T('contract.stateFileUntouched'), fx.read_bytes('profiles', 'Claude', 'plugins.txt'), raw_before)
r.case(T('contract.stateMemoryUntouched'), [p['active'] for p in get['/plugins']({})['plugins']],
       [True, False, True])
r.case(T('contract.stateNeedsSet'),
       is_clean_error(raised(post['/plugins/state'], {}), 'err.needSet'), True)
r.case(T('contract.stateSetNotDict'),
       isinstance(raised(post['/plugins/state'], {'set': ['Alpha.esp']}), ValueError), True)

r.head(T('contract.hStateApply'))
res = post['/plugins/state']({'set': {'Alpha.esp': False, 'Beta.esp': True}, 'apply': True})
r.case(T('contract.stateKeysWithFile'), has(res, ['applied', 'changes', 'file', 'unknown']), ['applied', 'changes', 'file', 'unknown'])
r.case(T('contract.stateApplied'), res['applied'], True)
r.case(T('contract.stateFileKeys'), has(res['file'], ['changed', 'notInFile', 'path', 'written']), ['changed', 'notInFile', 'path', 'written'])
r.case(T('contract.stateWritten'), res['file']['written'], True)
r.case(T('contract.statePathIsProfileFile'), res['file']['path'], fx.plugins_txt())
r.case(T('contract.stateChangedLowercase'), res['file']['changed'], ['alpha.esp', 'beta.esp'])
r.case(T('contract.stateNotInFileEmpty'), res['file']['notInFile'], [])
raw_after = fx.read_bytes('profiles', 'Claude', 'plugins.txt')
r.case(T('contract.stateFileChanged'), raw_after != raw_before, True)
r.case(T('contract.stateCrlfKept'), raw_after.count(b'\r\n'), raw_before.count(b'\r\n'))
r.case(T('contract.stateNoBareLf'), raw_after.count(b'\n'), raw_after.count(b'\r\n'))
lines = raw_after.decode('utf-8').split(fake_mo2.CRLF)
r.case(T('contract.stateLinesMovedPointwise'), lines,
       ['# This file was automatically generated by Mod Organizer.',
        'Alpha.esp', '*Beta.esp', '*Gamma.esl', ''])
r.case(T('contract.stateMemoryToo'), [p['active'] for p in get['/plugins']({})['plugins']],
       [False, True, True])
r.case(T('contract.stateReverseFromChanges'),
       post['/plugins/state']({'set': {c['plugin']: c['from'] for c in res['changes']},
                               'apply': True})['file']['changed'], ['alpha.esp', 'beta.esp'])
r.case(T('contract.stateFileBackToOriginal'), fx.read_bytes('profiles', 'Claude', 'plugins.txt'),
       raw_before)
res = post['/plugins/state']({'set': {'Alpha.esp': True}, 'apply': True})
r.case(T('contract.stateNoChangeNoFileKey'), has(res, ['applied', 'changes', 'unknown']), ['applied', 'changes', 'unknown'])
r.case(T('contract.stateNoChangeStillApplied'), (res['applied'], res['changes']), (True, []))

r.head(T('contract.hOrder'))
res = post['/plugins/order']({'order': ['Alpha.esp']})
r.case(T('contract.orderPartialKeys'), has(res, ['applied', 'count', 'error', 'notListed', 'unknown']), ['applied', 'count', 'error', 'notListed', 'unknown'])
r.case(T('contract.orderPartialNotApplied'), res['applied'], False)
r.case(T('contract.orderNotListed'), res['notListed'], ['Beta.esp', 'Gamma.esl'])
r.case(T('contract.orderErrorIsI18n'), res['error'], i18n.t('err.orderIncomplete', missing=2, extra=0))
res = post['/plugins/order']({'order': FULL_ORDER + ['Nope.esp']})
r.case(T('contract.orderUnknownPlugin'), (res['unknown'], 'error' in res), (['Nope.esp'], True))
new_order = ['Gamma.esl', 'Alpha.esp', 'Beta.esp']
res = post['/plugins/order']({'order': new_order})
r.case(T('contract.orderFullNoApplyKeys'), has(res, ['applied', 'before', 'count', 'notListed', 'undo', 'unknown']), ['applied', 'before', 'count', 'notListed', 'undo', 'unknown'])
r.case(T('contract.orderPreviewNotApplied'), res['applied'], False)
r.case(T('contract.orderBeforeIsWholeOrder'), res['before'], FULL_ORDER)
r.case(T('contract.orderUndoReady'), res['undo'],
       {'route': '/plugins/order', 'body': {'order': FULL_ORDER, 'apply': True}})
r.case(T('contract.orderMo2Untouched'), [p['plugin'] for p in get['/plugins']({})['plugins']],
       FULL_ORDER)
res = post['/plugins/order']({'order': new_order, 'apply': True})
r.case(T('contract.orderApplied'), res['applied'], True)
r.case(T('contract.orderBeforeKept'), res['before'], FULL_ORDER)
r.case(T('contract.orderMo2Changed'), [p['plugin'] for p in get['/plugins']({})['plugins']],
       new_order)
res = post['/plugins/order'](res['undo']['body'])
r.case(T('contract.orderUndoRestores'), (res['applied'], [p['plugin'] for p in get['/plugins']({})['plugins']]),
       (True, FULL_ORDER))
r.case(T('contract.orderNeedsOrder'),
       is_clean_error(raised(post['/plugins/order'], {}), 'err.needOrder'), True)

r.head(T('contract.hVfsExport'))
res = post['/vfsexport']({'outDir': fx.path('export')})
r.case(T('contract.exportKeys'), has(res, ['contested', 'csv', 'files', 'fromArchive', 'how', 'profile']), ['contested', 'csv', 'files', 'fromArchive', 'how', 'profile'])
r.case(T('contract.exportProfile'), res['profile'], 'Claude')
r.case(T('contract.exportViaTree'), res['how'], 'virtualFileTree')
r.case(T('contract.exportAllActiveFiles'), res['files'], 7)
r.case(T('contract.exportOneContested'), res['contested'], 1)
r.case(T('contract.exportNoneFromArchives'), res['fromArchive'], 0)
r.case(T('contract.exportCsvInOutDir'), res['csv'], fx.path('export', 'vfs-Claude.csv'))
r.case(T('contract.exportCsvExists'), os.path.isfile(res['csv']), True)
csv = read_text(res['csv']).split(chr(10))
r.case(T('contract.exportCsvHeader'), csv[0], 'rel;winner;archive;providers')
r.case(T('contract.exportRowsMatchFiles'), len([x for x in csv[1:] if x]), res['files'])
row = [x for x in csv if x.lower().startswith('meshes' + chr(92) + 'shared.nif')]
r.case(T('contract.exportContestedRow'), row,
       ['meshes' + chr(92) + 'shared.nif;Beta Mod;;Beta Mod|Alpha Mod'])
res = post['/vfsexport']({'outDir': fx.path('export'), 'legacyWalk': True})
r.case(T('contract.hLegacyWalk'), res['how'], 'walk')
r.case(T('contract.walkSameFiles'), res['files'], 7)
res = post['/vfsexport']({})
r.case(T('contract.exportDefaultOutDir'), res['csv'],
       os.path.join(fx.root, 'vfs-export', 'vfs-Claude.csv'))

# ================================================================ /install
r.head(T('contract.hInstallValidation'))
r.case(T('contract.installNeedsArchive'),
       is_clean_error(raised(post['/install'], {'name': 'X'}), 'err.noArchive', archive=None),
       True)
r.case(T('contract.installArchiveMissing'),
       is_clean_error(raised(post['/install'], {'archive': fx.path('nope.7z'), 'name': 'X'}),
                      'err.noArchive', archive=fx.path('nope.7z')), True)
dummy = fx.path('downloads', 'Alpha Mod-101-1-0.7z')
r.case(T('contract.installNeedsName'),
       is_clean_error(raised(post['/install'], {'archive': dummy}), 'err.needName'), True)
r.case(T('contract.installBadMode'),
       is_clean_error(raised(post['/install'], {'archive': dummy, 'name': 'X', 'mode': 'ovr'}),
                      'err.badMode', mode='ovr'), True)
r.case(T('contract.installTakenNoMode'),
       is_clean_error(raised(post['/install'], {'archive': dummy, 'name': 'Alpha Mod'}),
                      'err.modExists', mod='Alpha Mod'), True)
r.case(T('contract.installRefusalCreatedNothing'), os.path.isdir(fx.mod_path('X')), False)

r.head(T('contract.hInstallModes'))
archive = fake_mo2.build_archive(fx.path('downloads', 'Zeta Mod-999-1-0.7z'))
if archive is None:
    r.note(T('contract.installSkipNo7z'), '7-Zip не найден - установка из архива не проверяется')
elif not shutil.which('7z'):
    # Пакет ищет 7z.exe по-своему; PATH - единственное место, за которое отвечаем мы.
    # Добавляется только в окружение этого процесса, на диске ничего не меняется.
    os.environ['PATH'] = os.path.dirname(fake_mo2.seven_zip()) + os.pathsep + \
        os.environ.get('PATH', '')
    r.note(T('contract.install7zOnPath'), os.path.dirname(fake_mo2.seven_zip()))
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
            r.note(T('contract.installSkipNoTemp'), 'пакет не нашёл 7z.exe - установка не проверяется')
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
        r.case(T('contract.installKeys'), has(res, INSTALL_KEYS), INSTALL_KEYS)
        r.case(T('contract.installCreatedModeNew'), (res['created'], res['existed'], res['mode']),
               (True, False, 'new'))
        r.case(T('contract.installVariantCopied'), res['files'], 2)
        r.case(T('contract.installNoFomodSeen'), res['fomodSkipped'], False)
        r.case(T('contract.installArchiveName'), res['archive'], 'Zeta Mod-999-1-0.7z')
        r.case(T('contract.installPathIsModFolder'), res['path'], fx.mod_path('Zeta Mod'))
        r.case(T('contract.installUndoRemoves'), res['undo'],
               {'route': '/mods/remove', 'body': {'mod': 'Zeta Mod'}})
        r.case(T('contract.installFilesBeforeZero'), res['filesBefore'], 0)
        # meta.ini в added - его пишет createMod до копирования, и «было» для новой папки пусто
        r.case(T('contract.installAddedAtRootLower'), res['added'],
               sorted(['zeta.esp', os.path.join('textures', 'zeta.dds'), 'meta.ini']))
        r.case(T('contract.installAddedCount'), res['addedCount'], 3)
        r.case(T('contract.installOverwrittenEmpty'), (res['overwritten'], res['overwrittenCount']), ([], 0))
        r.case(T('contract.installNothingRecycled'), res['removedToRecycleBin'], 0)
        r.case(T('contract.installDiskMatchesAdded'), sorted(fake_mo2.tree_files(fx.mod_path('Zeta Mod'))),
               res['added'])
        r.case(T('contract.installModVisibleAfterRefresh'),
               'Zeta Mod' in [m['mod'] for m in get['/mods']({})['mods']], True)
        r.case(T('contract.installModDisabledAfter'),
               get['/mod']({'name': ['Zeta Mod']})['active'], False)

        # Весь архив целиком: подпапки вариантов ложатся как есть, fomod и корневой
        # meta.ini пропускаются
        res = post['/install']({'archive': archive, 'name': 'Zeta Whole', 'paths': ['']})
        r.case(T('contract.installWholeFomodSkipped'), res['fomodSkipped'], True)
        r.case(T('contract.installWholeFileCount'), res['files'], 3)
        r.case(T('contract.installWholeSubfolders'), res['added'],
               sorted([os.path.join('00 core', 'zeta.esp'),
                       os.path.join('00 core', 'textures', 'zeta.dds'),
                       os.path.join('10 extra', 'meshes', 'zeta.nif'), 'meta.ini']))
        r.case(T('contract.installWholeNoFomodOnDisk'),
               os.path.isdir(fx.path('mods', 'Zeta Whole', 'fomod')), False)
        r.case(T('contract.installWholeMetaKept'),
               'modid=0' in read_text(fx.path('mods', 'Zeta Whole', 'meta.ini')), True)

        # слияние: только 00 Core поверх Zeta Mod - два файла перекрыты
        res = post['/install']({'archive': archive, 'name': 'Zeta Mod', 'paths': ['00 Core'],
                                'mode': 'merge'})
        r.case(T('contract.mergeSameKeys'), has(res, INSTALL_KEYS), INSTALL_KEYS)
        r.case(T('contract.mergeExistedMode'), (res['created'], res['existed'], res['mode']),
               (False, True, 'merge'))
        # overwritten считается как пересечение «было» и «стало», то есть в него попадает
        # и то, что лежало в папке и не перезаписывалось (meta.ini). Договор здесь - что
        # реально перекрытые файлы в списке есть и счётчик равен длине списка.
        r.case(T('contract.mergeOverwrittenListed'),
               {'zeta.esp', os.path.join('textures', 'zeta.dds')} <= set(res['overwritten']),
               True)
        r.case(T('contract.mergeOverwrittenCount'), res['overwrittenCount'],
               len(res['overwritten']))
        r.case(T('contract.mergeNothingAdded'), (res['added'], res['addedCount']), ([], 0))
        r.case(T('contract.mergeUndoNone'), res['undo'], None)
        r.case(T('contract.mergeFileCountSame'), res['filesBefore'], res['filesAfter'])
        r.case(T('contract.mergeFomodUntouched'), res['fomodSkipped'], False)
        r.case(T('contract.mergeFilesInPlace'), sorted(fake_mo2.tree_files(fx.mod_path('Zeta Mod'))),
               sorted(['meta.ini', 'zeta.esp', os.path.join('textures', 'zeta.dds')]))

        # замена без ключа: она уносит прежнее содержимое в Корзину, то есть теряет чужую
        # работу, и потому закрыта замком необратимого наравне с удалением. Слияние и
        # обычная установка ключа не требуют - они ничего не теряют.
        res = post['/install']({'archive': archive, 'name': 'Zeta Mod', 'paths': ['10 Extra'],
                                'mode': 'replace'})
        r.case(T('contract.replaceNoKeyRefused'), (res['applied'], res['reason']),
               (False, 'danger'))
        r.case(T('contract.replaceNoKeyFilesKept'),
               sorted(fake_mo2.tree_files(fx.mod_path('Zeta Mod'))),
               sorted(['meta.ini', 'zeta.esp', os.path.join('textures', 'zeta.dds')]))

        # замена: прежнее в Корзину, meta.ini остаётся, кладётся другой вариант
        res = post['/install'](dict(KEY, archive=archive, name='Zeta Mod',
                                    paths=['10 Extra'], mode='replace'))
        r.case(T('contract.replaceSameKeys'), has(res, INSTALL_KEYS), INSTALL_KEYS)
        r.case(T('contract.replaceMode'), res['mode'], 'replace')
        r.case(T('contract.replaceTwoRecycled'),
               res['removedToRecycleBin'], 2)
        r.case(T('contract.replaceDiskOnlyNewAndMeta'),
               sorted(fake_mo2.tree_files(fx.mod_path('Zeta Mod'))),
               sorted(['meta.ini', os.path.join('meshes', 'zeta.nif')]))
        r.case(T('contract.replaceFileCounts'), (res['filesBefore'], res['filesAfter']),
               (3, 2))
        r.case(T('contract.replaceAddedNew'),
               (res['added'], res['addedCount']), ([os.path.join('meshes', 'zeta.nif')], 1))
        r.case(T('contract.replaceOverwrittenEmpty'), (res['overwritten'], res['overwrittenCount']),
               ([], 0))
        r.case(T('contract.replaceUndoNone'), res['undo'], None)
        r.case(T('contract.replaceMetaSurvived'),
               'modid=0' in read_text(fx.path('mods', 'Zeta Mod', 'meta.ini')), True)
        r.case(T('contract.installTempCleaned'), os.listdir(fx.path('temp')), [])
        r.case(T('contract.installNoPathInArchive'),
               is_clean_error(raised(post['/install'], {'archive': archive, 'name': 'Zeta Mod',
                                                        'paths': ['99 Nope'], 'mode': 'merge'}),
                              'err.noPathInArchive', path='99 Nope'), True)
        r.case(T('contract.installPathRefusalPlacedNothing'),
               sorted(fake_mo2.tree_files(fx.mod_path('Zeta Mod'))),
               sorted(['meta.ini', os.path.join('meshes', 'zeta.nif')]))
        r.note(T('contract.installTempAfterRefusal'), os.listdir(fx.path('temp')))

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
            r.case(T('contract.nameOutsideModsRejected', name=bad),
                   is_clean_error(raised(post['/install'],
                                         dict(KEY, archive=archive, name=bad,
                                              mode='replace')), 'err.badName', name=bad),
                   True)
        r.case(T('contract.installOutsideFolderIntact'),
               sorted(os.listdir(outside)), ['важное.txt'])
    finally:
        winapi.recycle = recycle_was
        if temp_was is None:
            os.environ.pop('TEMP', None)
        else:
            os.environ['TEMP'] = temp_was

# ================================================================ необратимое
r.head(T('contract.hPriority'))
res = post['/mods/priority']({'mod': 'Alpha Mod', 'priority': 2})
r.case(T('contract.priorityNoKeyKeys'), has(res, ['applied', 'blocked', 'docs', 'from', 'mod', 'to', 'why']), ['applied', 'blocked', 'docs', 'from', 'mod', 'to', 'why'])
r.case(T('contract.priorityNoKeyNotApplied'), res['applied'], False)
r.case(T('contract.priorityBlockedNamesOp'), res['blocked'], i18n.t('op.priority'))
r.case(T('contract.priorityWhyMentionsKey'), res['why'], i18n.t('danger.why', key='iUnderstandTheRisk'))
r.case(T('contract.priorityDocsPath'), res['docs'], 'README.md')
r.case(T('contract.priorityFromToShown'), (res['from'], res['to']), (0, 2))
r.case(T('contract.priorityUntouched'), get['/mod']({'name': ['Alpha Mod']})['priority'], 0)
res = post['/mods/priority'](dict({'mod': 'Alpha Mod', 'priority': 2}, **KEY))
r.case(T('contract.priorityWithKeyKeys'), has(res, ['applied', 'from', 'mod', 'to']), ['applied', 'from', 'mod', 'to'])
r.case(T('contract.priorityApplied'), res['applied'], True)
r.case(T('contract.priorityFromTo'), (res['from'], res['to']), (0, 2))
r.case(T('contract.priorityOrderChanged'), [m['mod'] for m in get['/mods']({})['mods']][:3],
       ['Beta Mod', 'Gamma Mod', 'Alpha Mod'])
r.case(T('contract.priorityReverseFromFrom'),
       post['/mods/priority'](dict({'mod': 'Alpha Mod', 'priority': res['from']}, **KEY))['to'],
       0)
r.case(T('contract.priorityNeedsMod'),
       is_clean_error(raised(post['/mods/priority'], {'priority': 1}), 'err.needMod'), True)
r.case(T('contract.priorityNeedsPriority'),
       is_clean_error(raised(post['/mods/priority'], {'mod': 'Alpha Mod'}), 'err.needMod'),
       True)
r.case(T('contract.priorityNoSuchModEvenNoKey'),
       is_clean_error(raised(post['/mods/priority'], {'mod': 'Nope', 'priority': 1}),
                      'err.noSuchMod', mod='Nope'), True)

r.head(T('contract.hRename'))
res = post['/mods/rename']({'mod': 'Gamma Mod', 'newName': 'Gamma Renamed'})
r.case(T('contract.renameNoKeyKeys'), has(res, ['applied', 'blocked', 'docs', 'mod', 'newName', 'why']), ['applied', 'blocked', 'docs', 'mod', 'newName', 'why'])
r.case(T('contract.renameNoKeyNotApplied'), res['applied'], False)
r.case(T('contract.renameBlocked'), res['blocked'], i18n.t('op.rename'))
r.case(T('contract.renameFolderInPlace'), os.path.isdir(fx.mod_path('Gamma Mod')), True)
res = post['/mods/rename'](dict({'mod': 'Gamma Mod', 'newName': 'Gamma Renamed'}, **KEY))
r.case(T('contract.renameWithKeyKeys'), has(res, ['applied', 'fromPath', 'mod', 'newName', 'nexusId', 'toPath', 'undo']), ['applied', 'fromPath', 'mod', 'newName', 'nexusId', 'toPath', 'undo'])
r.case(T('contract.renameApplied'), res['applied'], True)
r.case(T('contract.renameFromToPath'), (res['fromPath'], res['toPath']),
       (fx.mod_path('Gamma Mod'), fx.mod_path('Gamma Renamed')))
r.case(T('contract.renameNexusIdKept'), res['nexusId'], 0)
r.case(T('contract.renameUndoReverse'), res['undo'],
       {'route': '/mods/rename', 'body': {'mod': 'Gamma Renamed', 'newName': 'Gamma Mod'}})
r.case(T('contract.renameFolderRenamed'), (os.path.isdir(fx.mod_path('Gamma Mod')),
                               os.path.isdir(fx.mod_path('Gamma Renamed'))), (False, True))
r.case(T('contract.renameMo2KnowsNewName'), get['/mod']({'name': ['Gamma Renamed']})['mod'],
       'Gamma Renamed')
res = post['/mods/rename'](dict(res['undo']['body'], **KEY))
r.case(T('contract.renameUndoRestores'), (res['applied'], os.path.isdir(fx.mod_path('Gamma Mod'))),
       (True, True))
r.case(T('contract.renameNeedsNewName'),
       is_clean_error(raised(post['/mods/rename'], {'mod': 'Gamma Mod'}), 'err.needMod'), True)
r.case(T('contract.renameNoSuchModWithKey'),
       is_clean_error(raised(post['/mods/rename'],
                             dict({'mod': 'Nope', 'newName': 'N'}, **KEY)),
                      'err.noSuchMod', mod='Nope'), True)
r.case(T('contract.renameNoSuchModNoKey'),
       is_clean_error(raised(post['/mods/rename'], {'mod': 'Nope', 'newName': 'N'}),
                      'err.noSuchMod', mod='Nope'), True)
res = post['/mods/rename']({'mod': 'Gamma Mod', 'newName': 'Gamma Renamed'})
r.case(T('contract.renameRefusalCarriesPathAndId'), has(res, ['fromPath', 'nexusId']),
       ['fromPath', 'nexusId'])

r.head(T('contract.hRemove'))
res = post['/mods/remove']({'mod': 'Alpha Mod'})
r.case(T('contract.removeNoKeyKeys'), has(res, ['applied', 'blocked', 'card', 'docs', 'mod', 'why', 'withArchive']), ['applied', 'blocked', 'card', 'docs', 'mod', 'why', 'withArchive'])
r.case(T('contract.removeNoKeyNotApplied'), res['applied'], False)
r.case(T('contract.removeBlocked'), res['blocked'], i18n.t('op.remove'))
r.case(T('contract.removeWithArchiveDefaultFalse'), res['withArchive'], False)
card = res['card']
r.case(T('contract.cardKeys'), has(card, REMOVAL_CARD_KEYS), REMOVAL_CARD_KEYS)
r.case(T('contract.cardIdentity'), (card['mod'], card['nexusId'], card['version']),
       ('Alpha Mod', 101, '1.0'))
r.case(T('contract.cardUrl'), card['url'], 'https://www.nexusmods.com/skyrimspecialedition/mods/101')
r.case(T('contract.cardArchiveFound'),
       (card['installationFile'], card['archivePath'], card['archiveOnDisk']),
       ('Alpha Mod-101-1-0.7z', fx.path('downloads', 'Alpha Mod-101-1-0.7z'), True))
r.case(T('contract.cardWhereAndActive'), (card['priority'], card['active']), (0, True))
r.case(T('contract.cardFileCount'), card['files'], 4)
r.case(T('contract.cardCategoriesList'), card['categories'], ['3'])
r.case(T('contract.removeFolderIntact'), os.path.isdir(fx.mod_path('Alpha Mod')), True)
card = post['/mods/remove']({'mod': 'Gamma Mod'})['card']
r.case(T('contract.cardArchiveNotOnDisk'),
       (card['archivePath'], card['archiveOnDisk'], card['url']), ('', False, ''))

res = post['/mods/remove'](dict({'mod': 'Beta Mod'}, **KEY))
r.case(T('contract.removeWithKeyKeys'), has(res, ['applied', 'archiveKept', 'archiveRecycled', 'card', 'mod', 'path', 'undo']), ['applied', 'archiveKept', 'archiveRecycled', 'card', 'mod', 'path', 'undo'])
r.case(T('contract.removeApplied'), res['applied'], True)
r.case(T('contract.removePathBeforeRemoval'), res['path'], fx.mod_path('Beta Mod'))
r.case(T('contract.removeCardTakenBefore'), (has(res['card'], REMOVAL_CARD_KEYS), res['card']['files']),
       (REMOVAL_CARD_KEYS, 4))
r.case(T('contract.removeArchiveKept'), (res['archiveKept'], res['archiveRecycled']), (True, False))
r.case(T('contract.removeUndoReinstall'), res['undo'],
       {'route': '/install', 'body': {'archive': fx.path('downloads', 'Beta Mod-202-2-1.7z'),
                                      'name': 'Beta Mod'}})
r.case(T('contract.removeFolderGone'), os.path.isdir(fx.mod_path('Beta Mod')), False)
r.case(T('contract.removeArchiveInPlace'), os.path.isfile(fx.path('downloads', 'Beta Mod-202-2-1.7z')), True)
r.case(T('contract.removeMo2Forgot'),
       'Beta Mod' in [m['mod'] for m in get['/mods']({})['mods']], False)
r.case(T('contract.removePluginGone'), 'Beta.esp' in [p['plugin'] for p in get['/plugins']({})['plugins']],
       False)

recycle_was = winapi.recycle
winapi.recycle = fake_recycle
try:
    res = post['/mods/remove'](dict({'mod': 'Alpha Mod', 'withArchive': True}, **KEY))
finally:
    winapi.recycle = recycle_was
r.case(T('contract.removeArchiveRecycled'), (res['archiveKept'], res['archiveRecycled']),
       (False, True))
r.case(T('contract.removeArchiveNotOnDisk'), os.path.isfile(fx.path('downloads', 'Alpha Mod-101-1-0.7z')),
       False)
r.case(T('contract.removeArchiveUndoNone'), res['undo'], None)
r.case(T('contract.removeNoArchiveKeptUndoNone'),
       (lambda x: (x['archiveKept'], x['archiveRecycled'], x['undo']))(
           post['/mods/remove'](dict({'mod': 'Gamma Mod', 'withArchive': True}, **KEY))),
       (True, False, None))
r.case(T('contract.removeNeedsMod'),
       is_clean_error(raised(post['/mods/remove'], {}), 'err.needMod'), True)
r.case(T('contract.removeNoSuchModNoKey'),
       is_clean_error(raised(post['/mods/remove'], {'mod': 'Nope'}), 'err.noSuchMod',
                      mod='Nope'), True)

# ================================================================ занятость
r.head(T('contract.hBusyRefuses'))
fx, svc, get, post, log = make()
svc.launched = {k: dict(v) for k, v in ЧУЖОЙ_ЗАПУСК.items()}
plugins_before = fx.read_bytes('profiles', 'Claude', 'plugins.txt')
modlist_before = fx.read_bytes('profiles', 'Claude', 'modlist.txt')
res = get['/ping']({})
r.case(T('contract.busyPingShows'), has(res['busy'], BUSY_KEYS), BUSY_KEYS)
r.case(T('contract.busyPingNameless'), (res['busy']['app'], res['busy']['viaMO2'],
                                     res['busy']['heldByUnknown']), (None, True, True))
r.case(T('contract.busyPingMo2Run'), res['busy']['mo2Run'], sorted(ЧУЖОЙ_ЗАПУСК))
r.case(T('contract.busyProcsSame'),
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
r.case(T('contract.busyAllNineListed'), sorted(set(MUTATING) - set(post)), [])
why_expected = i18n.t('busy.whyUnknown', run=', '.join(sorted(ЧУЖОЙ_ЗАПУСК)))
for route in MUTATING:
    res = post[route](BODIES[route])
    r.case(T('contract.busyRefusalKeys', route=route), has(res, REFUSAL_KEYS), REFUSAL_KEYS)
    r.case('%s - applied False, busy True' % route, (res['applied'], res['busy']),
           (False, True))
    r.case('%s - running' % route, has(res['running'], BUSY_KEYS), BUSY_KEYS)
    r.case('%s - blocked' % route, res['blocked'], i18n.t(OPS[route]))
    r.case('%s - why' % route, res['why'], why_expected)
    r.case(T('contract.busyStamp', route=route), (res.get('reason'), res.get('op')),
           ('busy', OPS[route][3:]))
r.case(T('contract.busyWhyNotTrace'), 'Traceback' in why_expected or 'multiple values' in why_expected,
       False)
r.case(T('contract.busyForeignRecordIntact'), sorted(svc.launched), sorted(ЧУЖОЙ_ЗАПУСК))
r.case(T('contract.busyPluginsTxtUntouched'), fx.read_bytes('profiles', 'Claude', 'plugins.txt'),
       plugins_before)
r.case(T('contract.busyModlistUntouched'), fx.read_bytes('profiles', 'Claude', 'modlist.txt'),
       modlist_before)
r.case(T('contract.busyModFoldersIntact'), all(os.path.isdir(fx.mod_path(n)) for n, _s in fake_mo2.MODS), True)
r.case(T('contract.busyNewFolderNotCreated'), os.path.isdir(fx.mod_path('New')), False)
r.case(T('contract.busyStartAppNotCalled'), fx.organizer.started, [])
r.case(T('contract.busyRefreshNotAsked'), fx.organizer.refreshed, 0)

r.head(T('contract.hBusyReads'))
r.case(T('contract.busyModsAnswers'), get['/mods']({})['count'], 4)
r.case(T('contract.busyPluginsAnswers'), get['/plugins']({})['count'], 3)
r.case(T('contract.busyAnalyzeAnswers'), get['/analyze']({'name': ['Alpha Mod']})['files'], 3)
res = post['/plugins/state']({'set': {'Alpha.esp': False}})
r.case(T('contract.busyStatePreview'), has(res, ['applied', 'changes', 'unknown']), ['applied', 'changes', 'unknown'])
r.case(T('contract.busyStateHasChanges'),
       (res['applied'], len(res['changes'])), (False, 1))
res = post['/plugins/order']({'order': ['Gamma.esl', 'Alpha.esp', 'Beta.esp']})
r.case(T('contract.busyOrderPreview'), has(res, ['applied', 'before', 'count', 'notListed', 'undo', 'unknown']), ['applied', 'before', 'count', 'notListed', 'undo', 'unknown'])
r.case(T('contract.busyOrderBefore'), (res['applied'], res['before']), (False, FULL_ORDER))
r.case(T('contract.busyExportNotLocked'), post['/vfsexport']({'outDir': fx.path('export')})['files'], 7)
r.case(T('contract.busyWindowsNotLocked'), keys(get['/windows']({'pid': [str(os.getpid())]})),
       ['pid', 'windows'])
r.case(T('contract.busyStateValidatesFirst'),
       is_clean_error(raised(post['/plugins/state'], {'apply': True}), 'err.needSet'), True)
r.case(T('contract.busyOrderValidatesFirst'),
       is_clean_error(raised(post['/plugins/order'], {'apply': True}), 'err.needOrder'), True)

r.head(T('contract.hBusyReleased'))
svc.on_finished_run(sorted(ЧУЖОЙ_ЗАПУСК)[0], 0)
r.case(T('contract.releasedBusyNone'), get['/ping']({})['busy'], None)
r.case(T('contract.releasedRefreshWorks'), post['/refresh']({}).get('refreshed'), True)

# ================================================================ /run и /window
r.head(T('contract.hRun'))
fx, svc, get, post, log = make()
r.case(T('contract.runNeedsBinary'),
       is_clean_error(raised(post['/run'], {}), 'err.needBinary'), True)
res = post['/run'](dict({'binary': 'FakeTool', 'args': ['-x']}, **KEY))
r.case(T('contract.runKeys'), has(res, ['args', 'binary', 'key', 'pid']), ['args', 'binary', 'key', 'pid'])
r.case(T('contract.runKeyP1'), res['key'], 'p1')
r.case(T('contract.runPidZero'), res['pid'], 0)
r.case(T('contract.runBinaryAndArgs'), (res['binary'], res['args']), ('FakeTool', ['-x']))
r.case(T('contract.runMo2AskedExactly'), fx.organizer.started, [('FakeTool', ['-x'], '')])
r.case(T('contract.runSecondIsP2'), post['/run'](dict({'binary': 'FakeTool'}, **KEY))['key'], 'p2')
res = get['/procs']({})
r.case(T('contract.runProcsSeesBoth'), (len(res['procs']), res['running']), (2, 0))
r.case(T('contract.runProcsEntryKeys'), has(res['procs'][0], ['alive', 'exit', 'key', 'pid', 'what']), ['alive', 'exit', 'key', 'pid', 'what'])
r.case(T('contract.runWhatIsFileName'), res['procs'][0]['what'], 'FakeTool')
r.case(T('contract.runWindowsByKeyPidZero'),
       is_clean_error(raised(get['/windows'], {'key': ['p1']}), 'err.needPidOrKey'), True)

r.head(T('contract.hOwnRun'))
fx.organizer.onAboutToRun(svc.on_about_to_run)
post['/run'](dict({'binary': 'FakeTool'}, **KEY))
r.case(T('contract.ownRunBooked'), sorted(svc.launched), ['FakeTool'])
r.case(T('contract.ownRunMarkedMine'), svc.launched['FakeTool']['mine'], True)
r.case(T('contract.ownRunRetiredFree'), (get['/ping']({})['busy'], svc.launched),
       (None, {}))
r.case(T('contract.ownRunNoteLeft'), any('FakeTool' in x for x in log), True)

r.head(T('contract.hWindow'))
r.case(T('contract.windowNeedsHwnd'),
       is_clean_error(raised(post['/window'], {}), 'err.noSuchWindow'), True)
r.case(T('contract.windowDeadHwnd'),
       is_clean_error(raised(post['/window'], {'hwnd': 1, 'action': 'close'}),
                      'err.noSuchWindow'), True)

# ================================================================ тексты ошибок
r.head(T('contract.hI18nTexts'))
for key in ('op.toggle', 'op.remove', 'busy.whyUnknown', 'danger.why', 'err.noSuchMod',
            'err.orderIncomplete', 'err.modExists'):
    text = i18n.t(key, mod='m', run='r', key='k', missing=1, extra=2)
    r.case(T('contract.substituted', key=key), '%(' in text or 'multiple values' in text, False)
r.case(T('contract.tAcceptsKeyKwarg'), i18n.t('danger.why', key='X').count('X') >= 1, True)

for fx in fixtures:
    fx.cleanup()
# ================================================================ обновления
r.head(T('contract.hUpdates'))
updates_mod = importlib.import_module(pkg.__name__ + '.updates')
fx, svc, get, post, log = make()
svc.cfg.values['updates']['delaySec'] = 0
r.case(T('contract.updNeedsModOrAll'),
       is_clean_error(raised(get['/updates'], {}), 'err.needModOrAll'), True)
res = get['/updates']({'mod': ['Alpha Mod', 'Beta Mod', 'Gamma Mod', 'Delta Mod', 'Nope']})
r.case(T('contract.updKeys'), has(res, ['count', 'mods', 'checked', 'elapsedSec', 'rule']),
       ['count', 'mods', 'checked', 'elapsedSec', 'rule'])
r.case(T('contract.updOneRowPerMod'), res['count'], 5)
by = dict((m['mod'], m) for m in res['mods'])
r.case(T('contract.updRowKeys'), has(by['Alpha Mod'], ['mod', 'nexusId', 'checked', 'verdict', 'why',
                                              'installed', 'downloaded', 'newest', 'items',
                                              'extra', 'files']),
       ['mod', 'nexusId', 'checked', 'verdict', 'why', 'installed', 'downloaded', 'newest',
        'items', 'extra', 'files'])
r.case(T('contract.updAlphaAvailable'), by['Alpha Mod']['verdict'], 'UPDATE-AVAILABLE')
r.case(T('contract.updAlphaMatchedByMeta'),
       (by['Alpha Mod']['installed']['fileId'], by['Alpha Mod']['items'][0]['mine']['fileId']),
       (1001, 1001))
r.case(T('contract.updAlphaNewerNamed'), by['Alpha Mod']['items'][0]['newer'][0]['fileId'], 1002)
r.case(T('contract.updAlphaWhyInWords'), by['Alpha Mod']['why'], i18n.t('upd.newer'))
r.case(T('contract.updBetaUpToDate'), by['Beta Mod']['verdict'],
       'UP-TO-DATE')
r.case(T('contract.updGammaNoNexusId'), (by['Gamma Mod']['verdict'], by['Gamma Mod']['checked']),
       ('NO-NEXUS-ID', False))
r.case(T('contract.updDeltaError'),
       (by['Delta Mod']['verdict'], 'moderation' in (by['Delta Mod'].get('error') or '')),
       ('ERROR', True))
r.case(T('contract.updNopeNoSuchMod'), by['Nope']['verdict'], 'ERROR')
r.case(T('contract.updThreeChecked'), res['checked'], 2)
r.case(T('contract.updBridgeCreatedOnce'), len(fx.organizer.bridges), 1)
r.case(T('contract.updRequestsByNexusId'), sorted(q[1] for q in fx.organizer.bridges[0].requests),
       [101, 202, 404])
res = get['/updates']({'all': ['1'], 'limit': ['2']})
r.case(T('contract.updAllPageOfTwo'), (res['count'], res['total'], res['more']), (2, 4, True))
res = get['/updates']({'all': ['1'], 'offset': ['2']})
r.case(T('contract.updAllOffsetToEnd'), (res['count'], res['more']), (2, False))
svc.launched = {k: dict(v) for k, v in ЧУЖОЙ_ЗАПУСК.items()}
r.case(T('contract.updWorksWhileBusy'), get['/updates']({'mod': ['Beta Mod']})['mods'][0]['verdict'],
       'UP-TO-DATE')

r.head(T('contract.hUpdApi'))
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
r.case(T('contract.apiModeChosen'), res['via'], 'api')
r.case(T('contract.apiQuotaFromHeaders'), res['quota'].get('daily-remaining'), '2400')
r.case(T('contract.apiKeyInHeaderNotInReply'),
       (seen_urls[0][1], 'fake-key' in json.dumps(res)), ('fake-key', False))
r.case(T('contract.apiUrlHasDomainAndId'),
       seen_urls[0][0].endswith('/v1/games/skyrimspecialedition/mods/101/files.json'), True)
r.case(T('contract.apiAlphaAvailable'), by['Alpha Mod']['verdict'], 'UPDATE-AVAILABLE')
r.case(T('contract.apiBetaUpToDate'), by['Beta Mod']['verdict'], 'UP-TO-DATE')
r.case(T('contract.apiDelta404'), (by['Delta Mod']['verdict'], 'HTTP 404' in by['Delta Mod']['error']),
       ('ERROR', True))
r.case(T('contract.apiModeChosenOnce'), svc.updater._mode, 'api')
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
r.case(T('contract.apiNoKeyError'),
       (res['mods'][0]['verdict'], 'Nexus' in res['mods'][0]['why']), ('ERROR', True))

r.head(T('contract.hSweepStop'))


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
r.case(T('contract.stop401AfterFirst'), calls, 1)
r.case(T('contract.stop401Explained'), 'HTTP 401' in res['stopped'] or
       'ключ' in res['stopped'], True)
r.case(T('contract.stop401UncheckedCounted'), res['unchecked'], 2)
r.case(T('contract.stop401ModInReply'), res['mods'][0]['verdict'], 'ERROR')

# Исчерпанный лимит: продолжать значило бы продлевать запрет каждым запросом.
res, calls = sweep(raise_http(429, {'Retry-After': '600'}))
r.case(T('contract.stop429AfterFirst'), calls, 1)
r.case(T('contract.stop429RetryAfterFromError'),
       res['quota'].get('retry-after'), '600')
r.case(T('contract.stop429PauseNamed'), '600' in res['stopped'], True)

# Обрыв связи: одна неудача - не беда, пять подряд означают, что канал лёг.
def broken(_url, _h, _t):
    raise OSError('соединение оборвано')


res, calls = sweep(broken, names=('Alpha Mod', 'Beta Mod', 'Delta Mod'))
r.case(T('contract.netThreeTries'), calls, 3)
r.case(T('contract.netSweepContinues'), len(res['mods']), 3)
r.case(T('contract.netEveryModError'),
       sorted(set(m['verdict'] for m in res['mods'])), ['ERROR'])

# Кончающийся запас суточного лимита: ключ общий с самой MO2, и доедать его нельзя.
def near_limit(url, _h, _t):
    mod_id = int(url.rstrip('/').split('/mods/')[1].split('/')[0])
    page = API_PAGES.get(mod_id) or {'files': []}
    return 200, {'x-rl-daily-remaining': '3'}, json.dumps(page)


res, calls = sweep(near_limit)
r.case(T('contract.quotaStopsAfterFirst'), calls, 1)
r.case(T('contract.quotaSaysHowMuchLeft'), '3' in res['stopped'], True)
r.case(T('contract.quotaKeyStaysUsable'), res['unchecked'], 2)

r.head(T('contract.hDomain'))
fx_d = make()[0]
svc_d = services.Services(fx_d.organizer, run_main=lambda fn, timeout=None: fn(),
                          docs_path='README.md')
# Таблица перекрытий пуста, зато MO2 знает имя своего раздела: спрашивать надо её.
svc_d.cfg.values['nexusDomains'] = {}
svc_d.cfg.values['nexusDomainDefault'] = ''
fx_d.organizer.managedGame().nexus_name = 'fallout4'
r.case(T('contract.domainAskedOfMo2'), svc_d.updater.nexus_domain('ЧужаяИгра'), 'fallout4')
r.case(T('contract.domainUrlBuiltFromIt'),
       svc_d.updater.nexus_url('ЧужаяИгра', 7).endswith('fallout4/mods/7'), True)
# MO2 молчит, перекрытия нет - честный отказ, а не чужой раздел с тем же номером мода.
svc_e = services.Services(make()[0].organizer, run_main=lambda fn, timeout=None: fn(),
                          docs_path='README.md')
svc_e.cfg.values['nexusDomains'] = {}
svc_e.cfg.values['nexusDomainDefault'] = ''
svc_e.o.managedGame().nexus_name = ''
r.case(T('contract.domainNoNameNoDomain'), svc_e.updater.nexus_domain('ЧужаяИгра'), '')
r.case(T('contract.domainNoUrlWithoutDomain'), svc_e.updater.nexus_url('ЧужаяИгра', 7), '')

r.head(T('contract.hPageWide'))
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
r.case(T('contract.pageNoNeighbourNotInstalled'), alone['verdict'], 'DOWNLOADED-NOT-INSTALLED')
mates = svc.updater._check_one(dict(target), got_all, dl_root, 30,
                               {101: ['Alpha Mod-101-1-0.7z', 'Alpha Mod-101-1-1.7z']})
r.case(T('contract.pageNeighbourFromNewest'), mates['verdict'], 'UP-TO-DATE')
r.case(T('contract.pageInstalledDateInReply'), mates['installed']['pageTime'],
       fake_mo2.T0 + 30 * fake_mo2.DAY)
os.unlink(newest_arc)

r.head(T('contract.hPureRules'))
decide, D = updates_mod.decide, updates_mod.DAY
def F(name, fid, cat, t, ver='', fn=None):
    return {'name': name, 'fileName': fn or name + '.7z', 'fileId': fid, 'version': ver,
            'category': cat, 'time': t}
r.case(T('contract.ruleNoVersionCompare'),
       decide([F('Tool SE', 1, 'main', 100, '2.0'), F('Tool SE', 2, 'main', 100 + 2 * D, '1.9')],
              {1}, set(), 100)['verdict'], 'UPDATE-AVAILABLE')
r.case(T('contract.ruleGogIsSibling'),
       decide([F('Tool SE Steam', 1, 'main', 100), F('Tool SE GOG', 2, 'main', 100 + 2 * D)],
              {1}, set(), 100)['verdict'], 'UP-TO-DATE')
r.case(T('contract.ruleSameVersionMinutesApart'),
       decide([F('Tool', 1, 'main', 100, '1.0'), F('Tool', 2, 'main', 100 + 60, '1.0')],
              {1}, set(), 100)['verdict'], 'UP-TO-DATE')
r.case(T('contract.ruleOursInOldNoReplacement'),
       decide([F('Tool', 1, 'old', 100), F('Other Thing', 2, 'main', 100 - D)],
              {1}, set(), 100)['verdict'], 'CANNOT-MATCH')
r.case(T('contract.ruleAllOldMainRenamed'),
       decide([F('Tool', 1, 'old', 100), F('Tool Redux', 2, 'main', 100 + 3 * D)],
              {1}, set(), 100)['verdict'], 'UPDATE-AVAILABLE')
r.case(T('contract.ruleNothingMarkedMainNewer'),
       decide([F('Tool', 5, 'main', 100 + 3 * D)], set(), set(), 100)['verdict'], 'REUPLOADED')
r.case(T('contract.ruleNothingMarkedNoDates'),
       decide([F('Tool', 5, 'main', 100 + 3 * D)], set(), set(), None)['verdict'], 'CANNOT-MATCH')
r.case(T('contract.ruleFreshArchiveOldBuild'),
       decide([F('Tool', 1, 'old', 100), F('Tool', 2, 'main', 100 + 3 * D)],
              {1, 2}, set(), 100)['verdict'], 'DOWNLOADED-NOT-INSTALLED')
r.case(T('contract.ruleHotfixInUpdate'),
       decide([F('Tool', 1, 'main', 100), F('Tool Hotfix', 2, 'update', 100 + D)],
              {1}, set(), 100)['verdict'], 'UPDATE-AVAILABLE')
r.case(T('contract.ruleOtherRoleInMain'),
       decide([F('Tool', 1, 'main', 100), F('Tool Extras Pack', 2, 'main', 100 + 2 * D)],
              {1}, set(), 100)['verdict'], 'CANNOT-MATCH')
r.case(T('contract.timeFromNameEpoch'), updates_mod.arc_time('x-1234-1-0-1700000000.7z'),
       1700000000)
r.case(T('contract.timeFromNameIso'), updates_mod.arc_time('x 1 2026-09-07T09-14Z y.7z') > 0, True)
r.case(T('contract.resolutionIsNotVersion'), updates_mod.role('Tool 4K v2.1') != updates_mod.role('Tool 2K v2.1'), True)

# ================================================================ подпись карточки
r.head(T('contract.hStamp'))
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
    r.case(T('contract.dangerRefusalStamp', route=route),
           (res.get('reason'), res.get('applied'), res.get('op')),
           ('danger', False, OPS[route][3:]))
res = post['/plugins/state']({'set': {'Alpha.esp': False}})
r.case(T('contract.stampPreviewSigned'), (res.get('op'), res.get('applied')),
       ('pluginState', False))
res = post['/plugins/order']({'order': FULL_ORDER})
r.case(T('contract.stampOrderPreviewSigned'), (res.get('op'), res.get('applied')),
       ('pluginOrder', False))
res = post['/run'](dict({'binary': 'FakeTool'}, **KEY))
r.case(T('contract.runNoHandleNotStarted'), res.get('started'), False)

r.done()
