# -*- coding: utf-8 -*-
"""The plugin's strings and their translations.

Why a layer of its own. While the messages were scattered through the code, the only person
who could translate the plugin was one willing to edit Python and risk breaking the logic
with a misplaced quote. Here the text is separated from the code completely: a translator
copies a dictionary, replaces the right-hand sides and drops the file in place - touching
the logic is neither necessary nor possible.

A key is a short Latin label, not an English phrase. A phrase changes during proofreading,
and then every translation comes unstuck from the code at once; a label never changes.

Adding a language:
    1. copy the EN dictionary into a new one, DE for instance
    2. translate the right-hand sides, leave the left-hand sides alone
    3. add the language to LANGS
    4. the language is picked by the plugin setting `language`; 'auto' follows MO2's own UI

An untranslated key does not break anything: English is used, and failing that, the label
itself.
"""


RU = {
    # --- the plugin as it introduces itself to MO2 ---
    'plugin.description': ('Локальный HTTP-мост к работающей MO2: список модов, чтение '
                           'виртуальной Data, порядок плагинов, запуск утилит внутри VFS.'),
    'plugin.displayName': 'MO2 ApI Bridge',
    'plugin.tooltip': 'Показать адрес и токен локального моста',
    'setting.enabled': 'запускать мост вместе с MO2',
    'setting.port': 'порт на 127.0.0.1',
    'setting.language': "язык сообщений: auto, ru, en",

    # --- startup and state ---
    'start.ok': 'мост поднят на 127.0.0.1:%(port)s',
    'start.otherPort': ('порт %(wanted)s был занят - скорее всего вторым экземпляром MO2; '
                        'встал на %(port)s, токен в %(token)s'),
    'start.failed': 'НЕ УДАЛОСЬ поднять мост',
    'stop.ok': 'мост опущен, файл токена убран',
    'start.disabled': 'плагин выключен в настройках MO2',
    'init.loaded': 'плагин загружен, жду инициализации интерфейса',
    'ui.ready': 'интерфейс готов, поднимаю мост',
    'dialog.running': 'работает',
    'dialog.stopped': 'НЕ запущен',
    'dialog.stopButton': 'Опустить мост',
    'dialog.listening': 'Слушает http://127.0.0.1:%(port)s\nТокен лежит в:\n%(token)s\n\n%(state)s',
    'dialog.startFailed': 'Мост НЕ запустился.\n\n%(error)s\n\nПодробности: %(log)s',

    # --- request errors ---
    'err.token': 'нужен заголовок X-Token',
    'err.noRoute': 'нет такого пути',
    'err.needName': 'нужен name',
    'err.needMod': 'нужен mod',
    'err.needPath': 'нужен path',
    'err.needBinary': 'нужен binary - ЗАРЕГИСТРИРОВАННОЕ в MO2 имя утилиты, не путь',
    'err.noSuchMod': 'нет такого мода: %(mod)s',
    'err.noSuchWindow': 'нет такого окна',
    'err.windowsOnly': 'работа с окнами возможна только в Windows',
    'err.needButton': 'нужна подпись кнопки - вслепую не жму',
    'err.noButton': 'кнопка "%(button)s" в этом окне не найдена',
    'err.action': 'action: close | click',
    'err.needPidOrKey': 'нужен pid или key',
    'err.needSet': 'нужен set: {"плагин.esp": true/false, ...}',
    'err.needOrder': 'нужен order: ["Skyrim.esm", ...] - ПОЛНЫЙ список',
    'err.orderIncomplete': ('список неполный или содержит неизвестные плагины - не применяю: '
                            'пропущено %(missing)d, лишних %(extra)d'),
    'err.mainThread': 'главный поток MO2 не ответил за %(sec).0f с',
    'err.needModOrAll': 'нужен mod (можно несколько) или all=1',

    'err.noArchive': 'нет архива: %(archive)s',
    'err.modExists': ('мод "%(mod)s" уже существует. Молча поверх не кладу: укажите mode - '
                      'merge, чтобы положить выбранное поверх прежнего, или replace, чтобы '
                      'сперва убрать прежнее содержимое в Корзину'),
    'err.createFailed': 'createMod вернул пусто - имя занято или отказано',
    'err.no7z': 'не найден 7z.exe',
    'err.unpack': 'распаковка не удалась: %(detail)s',
    'err.noPathInArchive': 'нет пути в архиве: %(path)s',
    'err.badMode': "mode: merge (положить поверх) или replace (заменить), а не %(mode)s",
    'err.badName': ('имя мода должно быть именем папки, без разделителей пути и двоеточия: '
                    '%(name)s'),
    'err.badFlag': ('%(key)s: нужно true или false, а не %(value)r - догадываться о значении '
                    'на выключателе нельзя'),
    'err.recycle': ('не удалось отправить в Корзину: %(path)s; до этого места ушло записей: '
                    '%(done)d'),

    # --- names of the write operations, shared by both locks ---
    'op.refresh': 'перечитывание списков MO2',
    'op.install': 'установка мода',
    'op.installReplace': 'установка С ЗАМЕНОЙ: прежнее содержимое мода уйдёт в Корзину',
    'op.toggle': 'включение или выключение мода',
    'op.pluginState': 'изменение состояния плагинов',
    'op.pluginOrder': 'изменение порядка загрузки',
    'op.run': 'запуск ещё одной программы',
    'op.priority': 'смена приоритета мода',
    'op.rename': 'переименование мода',
    'op.remove': 'УДАЛЕНИЕ мода с диска',

    # --- MO2 is busy with a running program ---
    'busy.why': ('Сейчас работает %(app)s, и MO2 занята ею. Пока программа не закрыта, '
                 'состав сборки менять нельзя: виртуальная Data уже смонтирована в её '
                 'процесс, и правка модов, порядка или плагинов на ходу означает, что '
                 'программа видит одну сборку, а файлы на диске описывают другую. Ничего '
                 'не сделано. Чтение доступно полностью.'),

    'busy.whyUnknown': ('MO2 не сообщила о завершении запуска (%(run)s), то есть её замок '
                        'ещё держится - скорее всего живёт процесс, который она не запускала '
                        'сама, а получила по наследству от запущенного. Пока замок держится, '
                        'состав сборки менять нельзя. Ничего не сделано. Чтение доступно '
                        'полностью.'),
    'dialog.stuck': ('MO2 считает запуск активным:\n%(run)s\n\nПока это так, мост не даёт '
                     'менять состав сборки. Если окно MO2 уже разблокировано и ничего не '
                     'работает, учёт можно сбросить.'),

    # --- irreversible operations ---
    'danger.why': ('Необратимая операция над сборкой. Ничего не сделано. Прочитай раздел '
                   '"Необратимые операции" в документации, пойми последствия и передай '
                   'поле %(key)s с указанным там значением.'),

    # --- export ---
    'vfs.fallback': 'virtualFileTree не сработал (%(error)s), иду обходом вширь',

    # --- updates: why this verdict was reached ---
    'upd.noNexusId': 'у мода нет nexusId - с Nexus он не связан',
    'upd.noApiKey': ('ключ API Nexus не найден в хранилище учётных данных Windows - MO2 не '
                     'подключена к Nexus (Настройки - Nexus - Connect)'),
    'upd.failed': 'Nexus не ответил: %(error)s',
    'upd.timeout': 'Nexus не ответил за %(sec).0f с',
    'upd.noDomain': ('не знаю раздел Nexus для игры %(game)s - MO2 его не назвала; впиши '
                     'соответствие в nexusDomains настроек, иначе спрашивать будет некого'),
    'upd.badKey': ('Nexus отверг ключ (HTTP %(code)d) - обход прекращён: с негодным ключом '
                   'провалится и каждый следующий запрос'),
    'upd.rateLimited': ('лимит запросов к Nexus исчерпан, повторить можно через %(retry)s - '
                        'обход прекращён, иначе каждая попытка только продлевала бы запрет'),
    'upd.networkDown': ('связь с Nexus оборвалась: %(fails)d отказа подряд, последний - '
                        '%(error)s; обход прекращён'),
    'upd.quotaLow': ('запас суточного лимита Nexus кончается (осталось %(left)s) - обход '
                     'прекращён, чтобы ключ остался рабочим для самой MO2'),
    'upd.replaced': ('твоего файла на странице больше нет - автор заменил его; скачан %(mine)s, '
                     'в Main лежит файл от %(theirs)s'),
    'upd.unmarked': 'ни один файл страницы не помечен как скачанный',
    'upd.newer': 'на странице есть файл новее той же роли',
    'upd.hotfix': 'в разделе Update лежит не скачанный хотфикс к твоей версии',
    'upd.renamed': 'твой файл в Old files, а в Main лежит более новый под другим именем',
    'upd.retired': 'скачанный файл переехал в Old files, замены с тем же именем нет',
    'upd.otherRole': 'основной файл на месте, но в Main появился более свежий файл другой роли',
    'upd.downloadedNotInstalled': ('свежий файл от %(theirs)s уже лежит в downloads, а мод собран '
                                   'из архива от %(mine)s'),
    'upd.current': 'скачанный файл - новейший в своём разделе',

    # --- the trail left in the log ---
    'log.stuckReset': 'учёт запусков сброшен вручную',
    'log.mo2Starting': 'MO2 запускает: %(path)s%(mine)s',
    'log.oursSuffix': ' (наш запуск)',
    'log.mo2Finished': 'MO2 закончила: %(path)s (код %(code)s), осталось активных: %(left)d',
    'log.ownRunEnded': 'свой запуск закончился, снимаю с учёта: %(path)s',
    'log.nexusBridge': 'мост MO2 к Nexus: %(how)s',
    'log.nexusDirect': 'мост MO2 к Nexus непригоден (%(error)s), спрашиваю API напрямую',
    'log.sweepStopped': 'Nexus: обход прекращён - %(why)s',
    'config.writeFailed': 'настройки: не удалось записать умолчания в %(path)s: %(error)s',
    'config.unreadable': 'настройки: %(path)s не прочитан (%(error)s), работаю по умолчаниям',
    'config.notObject': 'ожидался объект JSON',
}

EN = {
    'plugin.description': ('Local HTTP bridge to a running MO2: mod list, virtual Data reads, '
                           'plugin load order, launching tools inside the VFS.'),
    'plugin.displayName': 'MO2 ApI Bridge',
    'plugin.tooltip': 'Show the local bridge address and token',
    'setting.enabled': 'start the bridge together with MO2',
    'setting.port': 'port on 127.0.0.1',
    'setting.language': 'message language: auto, ru, en',

    'start.ok': 'bridge listening on 127.0.0.1:%(port)s',
    'start.otherPort': ('port %(wanted)s was taken - most likely by a second MO2 instance; '
                        'listening on %(port)s instead, token in %(token)s'),
    'start.failed': 'FAILED to start the bridge',
    'stop.ok': 'bridge stopped, token file removed',
    'start.disabled': 'plugin disabled in MO2 settings',
    'init.loaded': 'plugin loaded, waiting for the user interface',
    'ui.ready': 'interface ready, starting the bridge',
    'dialog.running': 'running',
    'dialog.stopped': 'NOT running',
    'dialog.stopButton': 'Stop the bridge',
    'dialog.listening': ('Listening on http://127.0.0.1:%(port)s\n'
                         'Token file:\n%(token)s\n\n%(state)s'),
    'dialog.startFailed': 'The bridge did NOT start.\n\n%(error)s\n\nDetails: %(log)s',

    'err.token': 'X-Token header required',
    'err.noRoute': 'no such route',
    'err.needName': 'name is required',
    'err.needMod': 'mod is required',
    'err.needPath': 'path is required',
    'err.needBinary': 'binary is required - a name REGISTERED in MO2, not a path',
    'err.noSuchMod': 'no such mod: %(mod)s',
    'err.noSuchWindow': 'no such window',
    'err.windowsOnly': 'window automation is available on Windows only',
    'err.needButton': 'button caption required - refusing to click blindly',
    'err.noButton': 'button "%(button)s" not found in this window',
    'err.action': 'action: close | click',
    'err.needPidOrKey': 'pid or key required',
    'err.needSet': 'set required: {"plugin.esp": true/false, ...}',
    'err.needOrder': 'order required: ["Skyrim.esm", ...] - the FULL list',
    'err.orderIncomplete': ('the list is incomplete or contains unknown plugins - not applied: '
                            '%(missing)d missing, %(extra)d unknown'),
    'err.mainThread': "MO2's main thread did not answer within %(sec).0f s",
    'err.needModOrAll': 'mod (one or more) or all=1 is required',

    'err.noArchive': 'archive not found: %(archive)s',
    'err.modExists': ('mod "%(mod)s" already exists. Nothing is written over it silently: '
                      'pass mode - merge to overlay the selection, or replace to send the '
                      'previous contents to the Recycle Bin first'),
    'err.createFailed': 'createMod returned nothing - the name is taken or was refused',
    'err.no7z': '7z.exe not found',
    'err.unpack': 'extraction failed: %(detail)s',
    'err.noPathInArchive': 'path not found in archive: %(path)s',
    'err.badMode': 'mode: merge (overlay) or replace (wipe first), not %(mode)s',
    'err.badName': ('the mod name must be a plain folder name, with no path separators '
                    'and no colon: %(name)s'),
    'err.badFlag': ('%(key)s: expected true or false, not %(value)r - a switch is no place '
                    'to guess'),
    'err.recycle': ('could not send to the Recycle Bin: %(path)s; entries already sent '
                    'before this one: %(done)d'),

    'op.refresh': 'refreshing the MO2 lists',
    'op.install': 'installing a mod',
    'op.installReplace': 'installing WITH REPLACE: the mod\'s previous contents go to the '
                         'Recycle Bin',
    'op.toggle': 'enabling or disabling a mod',
    'op.pluginState': 'changing plugin states',
    'op.pluginOrder': 'changing the load order',
    'op.run': 'launching another program',
    'op.priority': 'changing mod priority',
    'op.rename': 'renaming a mod',
    'op.remove': 'DELETING a mod from disk',

    'busy.why': ('%(app)s is running and MO2 is busy with it. Until that program exits the '
                 'mod setup cannot be changed: the virtual Data is already mounted into its '
                 'process, so editing mods, order or plugins now would mean the program sees '
                 'one setup while the files on disk describe another. Nothing was done. '
                 'Reads remain fully available.'),
    'busy.whyUnknown': ('MO2 never reported the run (%(run)s) as finished, so its lock is '
                        'still held - most likely by a process it did not start itself but '
                        'inherited from one it did. While the lock is held the mod setup '
                        'cannot be changed. Nothing was done. Reads remain fully available.'),
    'dialog.stuck': ('MO2 still considers a run active:\n%(run)s\n\nWhile that holds, the '
                     'bridge refuses to change the mod setup. If the MO2 window is already '
                     'unlocked and nothing is running, the bookkeeping can be reset.'),

    'danger.why': ('Irreversible change to the mod setup. Nothing was done. Read the '
                   '"Irreversible operations" section of the documentation, understand the '
                   'consequences, then pass the %(key)s field with the value given there.'),

    'vfs.fallback': 'virtualFileTree failed (%(error)s), falling back to the breadth-first walk',

    'upd.noNexusId': 'the mod has no nexusId - it is not linked to Nexus',
    'upd.noApiKey': ('no Nexus API key in the Windows credential store - MO2 is not connected '
                     'to Nexus (Settings - Nexus - Connect)'),
    'upd.failed': 'Nexus did not answer: %(error)s',
    'upd.timeout': 'Nexus did not answer within %(sec).0f s',
    'upd.noDomain': ('no Nexus section known for the game %(game)s - MO2 did not name one; '
                     'add a mapping to nexusDomains in the settings, or there is nobody '
                     'to ask'),
    'upd.badKey': ('Nexus rejected the key (HTTP %(code)d) - the sweep stopped: with a bad '
                   'key every further request would fail the same way'),
    'upd.rateLimited': ('the Nexus request limit is used up, retry in %(retry)s - the sweep '
                        'stopped, since each further attempt would only extend the block'),
    'upd.networkDown': ('the connection to Nexus broke: %(fails)d failures in a row, the '
                        'last one %(error)s; the sweep stopped'),
    'upd.quotaLow': ('the Nexus daily allowance is running out (%(left)s left) - the sweep '
                     'stopped so the key keeps working for MO2 itself'),
    'upd.replaced': ('your file is no longer on the page - the author replaced it; downloaded '
                     '%(mine)s, Main now holds a file from %(theirs)s'),
    'upd.unmarked': 'no file on the page is marked as downloaded',
    'upd.newer': 'the page holds a newer file of the same role',
    'upd.hotfix': 'the Update section holds a hotfix for your version that was not downloaded',
    'upd.renamed': 'your file is in Old files and Main holds a newer one under another name',
    'upd.retired': 'the downloaded file moved to Old files and nothing of the same name replaced it',
    'upd.otherRole': 'the main file is in place, but Main gained a newer file of another role',
    'upd.downloadedNotInstalled': ('a fresh file from %(theirs)s is already in downloads, but the '
                                   'mod was built from an archive dated %(mine)s'),
    'upd.current': 'the downloaded file is the newest in its section',

    'log.stuckReset': 'run bookkeeping reset by hand',
    'log.mo2Starting': 'MO2 is starting: %(path)s%(mine)s',
    'log.oursSuffix': ' (our launch)',
    'log.mo2Finished': 'MO2 finished: %(path)s (code %(code)s), still active: %(left)d',
    'log.ownRunEnded': 'our own launch ended, dropping it from the bookkeeping: %(path)s',
    'log.nexusBridge': 'MO2 Nexus bridge: %(how)s',
    'log.nexusDirect': 'MO2 Nexus bridge unusable (%(error)s), asking the API directly',
    'log.sweepStopped': 'Nexus: sweep stopped - %(why)s',
    'config.writeFailed': 'settings: could not write the defaults to %(path)s: %(error)s',
    'config.unreadable': 'settings: %(path)s not read (%(error)s), using the defaults',
    'config.notObject': 'a JSON object was expected',
}

LANGS = {'ru': RU, 'en': EN}
DEFAULT = 'en'

_current = DEFAULT


def set_language(code):
    """Pick the language. 'auto' and any unknown code fall back to English."""
    global _current
    code = (code or '').strip().lower()
    if code in ('auto', ''):
        code = _detect()
    _current = code if code in LANGS else DEFAULT
    return _current


def _detect():
    """MO2's interface language. We ask Qt rather than the system: the user may have
    changed it inside MO2."""
    try:
        from PyQt6.QtCore import QLocale
        return (QLocale().name() or '').split('_')[0].lower()
    except Exception:
        return DEFAULT


def t(key, /, **kw):
    """A string by its label. No translation - use English; none of that either - the label.

    The label is positional-only on purpose. Without the slash, a substitution named `key`
    collides with the parameter itself and the call dies with "t() got multiple values for
    argument 'key'" - which is exactly what happened to the irreversible-operations lock:
    instead of a clear refusal the route returned a traceback.
    """
    table = LANGS.get(_current) or {}
    text = table.get(key) or EN.get(key) or key
    if kw:
        try:
            return text % kw
        except Exception:
            return text
    return text
