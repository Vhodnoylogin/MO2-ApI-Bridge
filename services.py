# -*- coding: utf-8 -*-
"""Предметный слой: всё, что делается с MO2.

Знает про mobase и про сборку. Не знает ни про HTTP, ни про JSON, ни про токены - принимает
обычные аргументы и возвращает обычные словари. Поэтому эти функции можно звать из чего угодно:
из маршрута, из пункта меню, из теста.

Каждый метод оборачивает работу в run_main: mobase живёт в главном потоке Qt, и обращаться
к нему из потока сервера нельзя.
"""
import io
import os
import shutil
import subprocess

import mobase

from . import i18n, winapi

DANGER_KEY = 'iUnderstandTheRisk'
DANGER_VALUE = 'yes-I-read-the-docs-and-accept-irreversible-changes'


class Services(object):
    def __init__(self, organizer, run_main, docs_path, note=None):
        # note - куда писать след о запусках; без него плагин работает молча
        self.note = note or (lambda _msg: None)
        self.o = organizer
        self.run_main = run_main
        self.docs = docs_path
        self.procs = {}
        self.seq = 0
        # Имя исполняемого файла игры. Спрашивается один раз и запоминается: проверка
        # занятости обязана работать и тогда, когда главный поток MO2 не отвечает.
        self.game_exe = None
        # Главное окно MO2, найденное один раз: по его включённости видно её собственный замок
        self._self_hwnd = None
        # Что MO2 сейчас держит запущенным: путь -> {'n': сколько раз, 'mine': наш ли запуск}.
        # Заполняется её же обратными вызовами, поэтому видно и то, что запустил мост, и то,
        # что пользователь запустил кнопкой в окне.
        self.launched = {}
        # Поднят на время своего startApplication, чтобы отличить свой запуск от чужого
        self._starting = False

    # ================================================== занятость MO2
    def on_about_to_run(self, path, *rest):
        """MO2 сообщает, что запускает программу. Вернуть True - иначе запуск отменится.

        Сигнатур у обратного вызова две: старая принимает один путь, новая ещё рабочий
        каталог и аргументы. Какую подставит конкретная сборка MO2, заранее неизвестно,
        поэтому лишнее принимается в *rest, а не угадывается по версии.
        """
        key = str(path)
        rec = self.launched.setdefault(key, {'n': 0, 'mine': False})
        rec['n'] += 1
        rec['mine'] = rec['mine'] or self._starting
        self.note('MO2 запускает: %s%s' % (key, ' (наш запуск)' if self._starting else ''))
        return True

    def on_finished_run(self, path, exit_code=0):
        """Программа завершилась - снять учёт."""
        key = str(path)
        rec = self.launched.get(key)
        if rec and rec['n'] > 1:
            rec['n'] -= 1
        else:
            self.launched.pop(key, None)
        self.note('MO2 закончила: %s (код %s), осталось активных: %d'
                  % (key, exit_code, len(self.launched)))

    def prime(self):
        """Запомнить имя исполняемого файла игры. Зовётся ИЗ главного потока, напрямую."""
        try:
            self.game_exe = os.path.basename(
                (self.o.managedGame().binaryName() or '').strip()).lower()
        except Exception:
            self.game_exe = ''
        return self.game_exe

    def _game_binary(self):
        """Имя игры из кэша. Если запомнить не успели - спросить, но недолго."""
        if self.game_exe is None:
            try:
                name = self.run_main(lambda: self.o.managedGame().binaryName(), timeout=10)
                self.game_exe = os.path.basename((name or '').strip()).lower()
            except Exception:
                return ''
        return self.game_exe

    def _busy(self):
        """Кто держит MO2 прямо сейчас, или None.

        Две проверки, а не одна. Обратные вызовы MO2 знают о запусках через неё, но не
        переживут пропущенного onFinishedRun; перечисление процессов знает правду о живых
        программах, но само по себе не отличит запуск через MO2 от запуска мимо неё.
        Вместе они дают и то, и другое, и учёт лечится от застрявших записей.
        """
        game = self._game_binary()
        names = {os.path.basename(k).lower() for k in self.launched}
        if game:
            names.add(game)
        alive = winapi.pids_by_exe(names) if names else {}
        # Свои запуски снимаем с учёта сами: MO2 не ждёт их и о завершении не сообщает, так
        # что запись висела бы вечно. Чужие держим до её слова - именно в них вся защита.
        gone = [k for k, r in self.launched.items()
                if r.get('mine') and os.path.basename(k).lower() not in
                {v.lower() for v in alive.values()}]
        for k in gone:
            self.launched.pop(k, None)
            self.note('свой запуск закончился, снимаю с учёта: %s' % k)
        locked = self._window_locked()
        if not alive and not self.launched and not locked:
            return None
        exe = sorted(alive.values())[0] if alive else ''
        # viaMO2 отвечает на вопрос «MO2 сейчас считает запуск активным», а не «этот самый
        # файл запущен ею». Разница настоящая: игру MO2 стартует через sksevr_loader.exe,
        # тот сразу завершается, и живым остаётся SkyrimVR.exe - имена не совпадают, хотя
        # запуск идёт именно через MO2. Что именно она запустила, видно в mo2Run.
        return {'app': exe or None,
                'pids': sorted(alive),
                'isGame': bool(exe) and exe.lower() == game,
                'viaMO2': bool(self.launched),
                'mo2Run': sorted(self.launched),
                'windowLocked': locked,
                # MO2 не сообщила о завершении запуска, а знакомых процессов в системе нет:
                # замок держит тот, о ком она не говорила. Так остаётся висеть служба,
                # поднятая плагином ИЗНУТРИ игры: MO2 её не запускала, но держит в задании.
                'heldByUnknown': not alive and (bool(self.launched) or locked)}

    def _window_locked(self):
        """Выключено ли главное окно MO2. Прямой признак её собственного замка.

        Третий источник правды, и самый близкий к тому, что видит человек: пока MO2 ждёт
        запущенную программу, она показывает модальный диалог «заблокирован, пока приложение
        запущено», и её окно перестаёт принимать ввод. Учёт запусков и перечисление процессов
        оба могут промахнуться - это не может.
        """
        try:
            if self._self_hwnd is None:
                self._self_hwnd = 0
                for w in winapi.windows_of(os.getpid()):
                    if w.get('visible') and w.get('class', '').startswith('Qt'):
                        self._self_hwnd = w['hwnd']
                        break
            return bool(self._self_hwnd) and not winapi.is_enabled(self._self_hwnd)
        except Exception:
            return False

    def _blocked_while_busy(self, op_key):
        """Отказ, если MO2 занята запущенной программой; None, если работать можно.

        Причина не в вежливости к окну MO2, а в устройстве USVFS: виртуальная Data уже
        смонтирована в чужой процесс. Правка модов, порядка или плагинов на ходу означает,
        что запущенная программа видит одну сборку, а файлы на диске описывают другую.
        """
        busy = self._busy()
        if not busy:
            return None
        return {'applied': False, 'busy': True, 'running': busy,
                'blocked': i18n.t(op_key),
                'why': i18n.t('busy.why', app=busy['app']) if busy['app']
                else i18n.t('busy.whyUnknown', run=', '.join(busy['mo2Run']) or '-')}

    # ================================================== чтение состояния
    def ping(self, _=None):
        def f():
            return {'ok': True,
                    'profile': self.o.profile().name(),
                    'game': self.o.managedGame().gameShortName(),
                    'mo2Version': self.o.appVersion().displayString(),
                    'modsPath': self.o.modsPath(),
                    'overwrite': self.o.overwritePath(),
                    'downloads': self.o.downloadsPath()}
        # Занятость выясняется первой и без главного потока: именно этот вызов нужен,
        # когда MO2 не отвечает, и ответить он обязан быстро.
        res = {'busy': self._busy()}
        try:
            res.update(self.run_main(f, timeout=15))
            res['mainThread'] = 'ok'
        except Exception as exc:
            # Не отвечающий главный поток - это ответ, а не ошибка: значит MO2 чем-то занята.
            res['ok'] = False
            res['mainThread'] = str(exc)
        return res

    def mods(self, _=None):
        def f():
            ml = self.o.modList()
            out = []
            for name in ml.allModsByProfilePriority():
                st = ml.state(name)
                out.append({'mod': name,
                            'active': bool(st & mobase.ModState.ACTIVE),
                            'essential': bool(st & mobase.ModState.ESSENTIAL),
                            'priority': ml.priority(name)})
            return {'count': len(out), 'mods': out}
        return self.run_main(f)

    def profiles(self, _=None):
        def f():
            root = os.path.dirname(self.o.profile().absolutePath())
            names = sorted(x for x in os.listdir(root) if os.path.isdir(os.path.join(root, x)))
            return {'current': self.o.profile().name(), 'profiles': names, 'path': root}
        return self.run_main(f)

    def plugins(self, _=None):
        """Порядок загрузки и всё, что MO2 знает о плагинах.

        Иначе это сшивается вручную из plugins.txt, loadorder.txt и заголовков TES4, причём
        esl-флаг и признак «нет записей» в файлах не лежат вовсе.
        """
        def f():
            pl = self.o.pluginList()
            out = []
            for n in pl.pluginNames():
                st = pl.state(n)
                out.append({'plugin': n,
                            'active': bool(st & mobase.PluginState.ACTIVE),
                            'priority': pl.priority(n),
                            'loadOrder': pl.loadOrder(n),
                            'origin': pl.origin(n),
                            # isMaster объявлен устаревшим и пишет предупреждение
                            # в лог MO2 на каждый плагин при каждом вызове /plugins
                            'master': pl.isMasterFlagged(n) or pl.hasMasterExtension(n),
                            'esl': pl.isLightFlagged(n) or pl.hasLightExtension(n),
                            'empty': pl.hasNoRecords(n),
                            'masters': list(pl.masters(n))})
            out.sort(key=lambda x: x['loadOrder'] if x['loadOrder'] >= 0 else 10 ** 6)
            return {'count': len(out), 'plugins': out}
        return self.run_main(f)

    # ================================================== виртуальная Data
    def vfs(self, q):
        path = _one(q, 'path', '.')
        filt = _one(q, 'filter', '*')

        def f():
            return {'path': path, 'filter': filt,
                    'files': list(self.o.findFiles(path, filt))}
        return self.run_main(f)

    def origins(self, q):
        rel = _one(q, 'path', '')

        def f():
            # порядок важен: первый в списке - тот, чей файл реально видит игра
            return {'path': rel, 'origins': list(self.o.getFileOrigins(rel))}
        return self.run_main(f)

    def dirs(self, q):
        path = _one(q, 'path', '.')

        def f():
            return {'path': path, 'dirs': list(self.o.listDirectories(path))}
        return self.run_main(f)

    def resolve(self, q):
        p = _one(q, 'path', '')
        if not p:
            raise ValueError(i18n.t('err.needPath'))

        def f():
            return {'path': p, 'real': self.o.resolvePath(p)}
        return self.run_main(f)

    def vfsexport(self, body):
        """Выгрузка всей виртуальной Data со всеми поставщиками каждого файла.

        Дерево берётся у самой MO2 (virtualFileTree). Обход вширь оставлен запасным путём и
        включается legacyWalk: он появился лишь потому, что listDirectories('') отдаёт пусто
        и корень казался недостижимым.
        """
        out_dir = body.get('outDir') or os.path.join(self.o.basePath(), 'vfs-export')
        timeout = float(body.get('timeout') or 1800)
        how = 'walk' if body.get('legacyWalk') else 'virtualFileTree'

        def by_tree():
            rows = []

            def visit(path, entry):
                if not entry.isDir():
                    rel = (path or '') + entry.name()
                    try:
                        org = list(self.o.getFileOrigins(rel))
                    except Exception:
                        org = []
                    rows.append((rel, org[0] if org else '', '', '|'.join(org)))
                return mobase.IFileTree.WalkReturn.CONTINUE

            self.o.virtualFileTree().walk(visit, chr(92))
            return self.o.profile().name(), rows

        if how == 'walk':
            profile, rows = self.run_main(_walk_factory(self.o), timeout=timeout)
        else:
            try:
                profile, rows = self.run_main(by_tree, timeout=timeout)
            except Exception as exc:
                how = 'walk (fallback)'
                i18n.t('vfs.fallback', error=exc)
                profile, rows = self.run_main(_walk_factory(self.o), timeout=timeout)

        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        path = os.path.join(out_dir, 'vfs-%s.csv' % profile)
        with open(path, 'w', encoding='utf-8', newline='') as fh:
            fh.write('rel;winner;archive;providers' + chr(10))
            for r in rows:
                fh.write(';'.join(x.replace(';', ',') for x in r) + chr(10))
        return {'profile': profile, 'files': len(rows), 'how': how,
                'fromArchive': sum(1 for r in rows if r[2]),
                'contested': sum(1 for r in rows if '|' in r[3]),
                'csv': path}

    # ================================================== карточка и разбор
    def mod(self, q):
        """Всё, что MO2 знает о моде. Три поля не лежат в meta.ini вовсе:
        isSeparator (иначе определяется по суффиксу имени, то есть по соглашению),
        categories (свои категории MO2 хранит отдельно) и ignoredVersion с endorsed
        (какие обновления пользователь уже отклонил).
        """
        name = _one(q, 'name', '')
        if not name:
            raise ValueError(i18n.t('err.needName'))

        def f():
            ml = self.o.modList()
            m = ml.getMod(name)
            if m is None:
                raise ValueError(i18n.t('err.noSuchMod', mod=name))
            st = ml.state(name)
            nid = _safe(m.nexusId, 0)
            game = _safe(m.gameName, '') or ''
            own_url = _safe(m.url, '') or ''
            # Ссылку собираем сами, когда известен nexusId: m.url() у части модов отдаёт
            # голый домен без /mods/<id>, и получается ссылка в никуда.
            url = own_url
            if nid:
                dom = {'SkyrimSE': 'skyrimspecialedition', 'SkyrimVR': 'skyrimspecialedition',
                       'Skyrim': 'skyrim', 'Fallout4': 'fallout4',
                       'Fallout4VR': 'fallout4'}.get(game, 'skyrimspecialedition')
                url = 'https://www.nexusmods.com/%s/mods/%d' % (dom, nid)
            return {'mod': name,
                    'displayName': _safe(lambda: ml.displayName(name), name),
                    'path': _safe(m.absolutePath, ''),
                    'active': bool(st & mobase.ModState.ACTIVE),
                    'essential': bool(st & mobase.ModState.ESSENTIAL),
                    'priority': ml.priority(name),
                    'isSeparator': _safe(m.isSeparator, False),
                    'isForeign': _safe(m.isForeign, False),
                    'isBackup': _safe(m.isBackup, False),
                    'isOverwrite': _safe(m.isOverwrite, False),
                    'nexusId': nid, 'url': url, 'urlFromMO2': own_url, 'gameName': game,
                    'version': _safe(lambda: m.version().displayString(), ''),
                    'newestVersion': _safe(lambda: m.newestVersion().displayString(), ''),
                    'ignoredVersion': _safe(lambda: m.ignoredVersion().displayString(), ''),
                    'installationFile': _safe(m.installationFile, ''),
                    'categories': list(_safe(m.categories, []) or []),
                    'primaryCategory': _safe(m.primaryCategory, 0),
                    'notes': _safe(m.notes, ''), 'comments': _safe(m.comments, ''),
                    'endorsed': str(_safe(m.endorsedState, '')),
                    'tracked': str(_safe(m.trackedState, ''))}
        return self.run_main(f)

    def analyze(self, q):
        """Разбор мода одним вызовом: карточка, файлы, плагины и кто с кем спорит.

        В отличие от расчёта по списку модов это реальный порядок активного профиля.
        conflicts=0 отключает самую дорогую часть - проверку каждого файла.
        """
        name = _one(q, 'name', '')
        if not name:
            raise ValueError(i18n.t('err.needName'))
        want_conf = _one(q, 'conflicts', '1') != '0'
        limit = int(_one(q, 'limit', '80'))

        def f():
            ml = self.o.modList()
            m = ml.getMod(name)
            if m is None:
                raise ValueError(i18n.t('err.noSuchMod', mod=name))
            root = m.absolutePath()
            files = []
            for dp, _dn, fs in os.walk(root):
                rel_dir = os.path.relpath(dp, root)
                for x in fs:
                    if x.lower() == 'meta.ini' and rel_dir == '.':
                        continue
                    files.append(x if rel_dir == '.' else os.path.join(rel_dir, x))
            wins, loses, contested = [], [], 0
            if want_conf:
                for rel in files:
                    try:
                        org = list(self.o.getFileOrigins(rel))
                    except Exception:
                        continue
                    if len(org) < 2:
                        continue
                    contested += 1
                    (wins if org[0] == name else loses).append({'file': rel, 'providers': org})
            return {'mod': name, 'path': root, 'files': len(files),
                    'plugins': [x for x in files if x.lower().endswith(('.esp', '.esm', '.esl'))],
                    'sample': sorted(files)[:limit],
                    'conflicts': {'checked': want_conf, 'contested': contested,
                                  'winsCount': len(wins), 'losesCount': len(loses),
                                  'wins': wins[:limit], 'loses': loses[:limit]}}
        res = self.run_main(f, timeout=600.0)
        # карточку берём отдельным вызовом: вкладывать один run_main в другой нельзя,
        # главный поток уже занят и получится взаимная блокировка
        try:
            res['card'] = self.mod({'name': [name]})
        except Exception as exc:
            res['card'] = {'error': str(exc)}
        return res

    # ================================================== изменения
    def toggle(self, body):
        stop = self._blocked_while_busy('op.toggle')
        if stop:
            return stop
        mod = body.get('mod')
        want = body.get('active')
        if not mod or want is None:
            raise ValueError(i18n.t('err.needMod'))

        def f():
            ml = self.o.modList()
            if ml.getMod(mod) is None:
                raise ValueError(i18n.t('err.noSuchMod', mod=mod))
            ml.setActive(mod, bool(want))
            return {'mod': mod, 'active': bool(want)}
        return self.run_main(f)

    def install(self, body):
        """Установка мода без единого диалога: createMod плюс собственная распаковка.

        installMod намеренно не используется. Он запускает установщик MO2, а тот всегда
        спрашивает имя, при совпадении имён спрашивает «заменить или слить», а на архиве с
        fomod/ModuleConfig.xml открывает мастера. Автоматизировать эти окна нельзя: MO2 на Qt,
        её виджеты нарисованы внутри одного HWND, нативных кнопок для нажатия по подписи нет.

        createMod же заводит зарегистрированную пустую папку, а файлы кладу я сам - ровно те,
        что нужны. Для FOMOD это и так единственный верный путь: выбор опций разбирается
        чтением ModuleConfig.xml, а не кликами.

        body: {archive, name, paths: ["подпапка в архиве", ...]}
        """
        stop = self._blocked_while_busy('op.install')
        if stop:
            return stop
        arc = body.get('archive')
        name = (body.get('name') or '').strip()
        paths = body.get('paths') or ['']
        if not arc or not os.path.isfile(arc):
            raise ValueError(i18n.t('err.noArchive', archive=arc))
        if not name:
            raise ValueError(i18n.t('err.needName'))

        mods_root = self.run_main(lambda: self.o.modsPath())
        if os.path.isdir(os.path.join(mods_root, name)):
            raise ValueError(i18n.t('err.modExists', mod=name))

        def mk():
            m = self.o.createMod(mobase.GuessedString(name))
            return None if m is None else m.absolutePath()
        target = self.run_main(mk)
        if not target:
            raise RuntimeError(i18n.t('err.createFailed'))

        tmp = os.path.join(os.environ.get('TEMP', target), 'mo2ailink-unpack')
        if os.path.isdir(tmp):
            shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        seven = _seven_zip()
        if not seven:
            raise RuntimeError(i18n.t('err.no7z'))
        r = subprocess.run([seven, 'x', arc, '-o' + tmp, '-y'],
                           capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            raise RuntimeError(i18n.t('err.unpack', detail=(r.stdout or r.stderr)[-300:]))

        copied, skipped_fomod = 0, False
        for sub in paths:
            root = os.path.join(tmp, sub) if sub else tmp
            if not os.path.isdir(root):
                raise ValueError(i18n.t('err.noPathInArchive', path=sub))
            for dp, _dn, fs in os.walk(root):
                rel = os.path.relpath(dp, root)
                # fomod - описание мастера установки, игре оно не нужно
                if rel.lower().split(os.sep)[0] == 'fomod':
                    skipped_fomod = True
                    continue
                dst = target if rel == '.' else os.path.join(target, rel)
                if not os.path.isdir(dst):
                    os.makedirs(dst)
                for f in fs:
                    if f.lower() == 'meta.ini' and rel == '.':
                        continue
                    shutil.copy2(os.path.join(dp, f), os.path.join(dst, f))
                    copied += 1
        shutil.rmtree(tmp, ignore_errors=True)
        self.run_main(lambda: (self.o.refresh(True), True)[1])
        return {'mod': name, 'path': target, 'files': copied, 'created': True,
                'fomodSkipped': skipped_fomod, 'archive': os.path.basename(arc)}

    def refresh(self, _=None):
        """Перечитать mods\\ и профиль.

        Именно это снимает необходимость закрывать MO2: папка мода, созданная мимо менеджера,
        после refresh попадает в список и дальше включается обычным toggle.
        """
        stop = self._blocked_while_busy('op.refresh')
        if stop:
            return stop

        def f():
            self.o.refresh(True)
            return {'refreshed': True}
        return self.run_main(f)

    def plugins_state(self, body):
        want = body.get('set') or {}
        apply_it = bool(body.get('apply'))
        if not isinstance(want, dict) or not want:
            raise ValueError(i18n.t('err.needSet'))
        # Предпросмотр доступен всегда: он ничего не меняет.
        if apply_it:
            stop = self._blocked_while_busy('op.pluginState')
            if stop:
                return stop

        def f():
            pl = self.o.pluginList()
            known = set(pl.pluginNames())
            plan, missing = [], []
            for name, active in want.items():
                if name not in known:
                    missing.append(name)
                    continue
                now = bool(pl.state(name) & mobase.PluginState.ACTIVE)
                if now != bool(active):
                    plan.append({'plugin': name, 'from': now, 'to': bool(active)})
            if apply_it:
                for p in plan:
                    pl.setState(p['plugin'], mobase.PluginState.ACTIVE if p['to']
                                else mobase.PluginState.INACTIVE)
            res = {'applied': apply_it, 'changes': plan, 'unknown': missing}
            if apply_it and plan:
                res['file'] = self._write_plugins_txt(
                    {p['plugin'].lower(): p['to'] for p in plan})
            return res
        return self.run_main(f)

    def _write_plugins_txt(self, wanted):
        """Проставить звёздочки в plugins.txt профиля. Зовётся ИЗ главного потока.

        setState меняет список в памяти MO2, а файл она переписывает в свои моменты - обычно
        при выходе. Игра стартует именно с файла, и любой /refresh между правкой и выходом
        читает файл обратно, молча отменяя правку. Стоило это трёх сорванных прогонов: мост
        отвечал applied: true, а плагин в игре оставался выключенным.

        Пишем строго переставляя звёздочку в существующих строках. Набор строк и их порядок
        не трогаем вовсе - это дело MO2, и переписать файл целиком означало бы взять на себя
        и порядок загрузки, и признак esl, и всё остальное, что она туда кладёт.
        """
        path = os.path.join(self.o.profile().absolutePath(), 'plugins.txt')
        try:
            # newline='' обязателен: без него текстовый режим схлопывает CRLF в LF,
            # определение концов строк даёт LF, и файл переписывается чужим форматом
            raw = io.open(path, encoding='utf-8-sig', errors='replace',
                          newline='').read()
        except Exception as exc:
            return {'written': False, 'error': str(exc), 'path': path}
        eol = chr(13) + chr(10) if chr(13) + chr(10) in raw else chr(10)
        out, done = [], set()
        for line in raw.split(chr(10)):
            bare = line.rstrip(chr(13))
            name = bare[1:] if bare.startswith('*') else bare
            key = name.strip().lower()
            if key in wanted and name.strip():
                out.append(('*' if wanted[key] else '') + name)
                done.add(key)
            else:
                out.append(bare)
        while out and not out[-1]:
            out.pop()
        io.open(path, 'w', encoding='utf-8', newline='').write(eol.join(out) + eol)
        return {'written': True, 'path': path, 'changed': sorted(done),
                'notInFile': sorted(set(wanted) - done)}

    def plugins_order(self, body):
        """Полный порядок загрузки - тем списком, что отдал LOOT.

        Частичный список увёл бы неупомянутые плагины в непредсказуемое место, поэтому
        неполный набор отвергается целиком.
        """
        order = body.get('order') or []
        apply_it = bool(body.get('apply'))
        if not isinstance(order, list) or not order:
            raise ValueError(i18n.t('err.needOrder'))
        if apply_it:
            stop = self._blocked_while_busy('op.pluginOrder')
            if stop:
                return stop

        def f():
            pl = self.o.pluginList()
            known = list(pl.pluginNames())
            missing = sorted(set(known) - set(order))
            extra = sorted(set(order) - set(known))
            res = {'applied': False, 'count': len(order),
                   'notListed': missing[:20], 'unknown': extra[:20]}
            if missing or extra:
                res['error'] = i18n.t('err.orderIncomplete',
                                      missing=len(missing), extra=len(extra))
                return res
            if apply_it:
                pl.setLoadOrder(order)
                self.o.refresh(True)
                res['applied'] = True
            return res
        return self.run_main(f)

    # ================================================== необратимое
    def _danger(self, body, what_key):
        if (body.get(DANGER_KEY) or '') != DANGER_VALUE:
            return {'applied': False,
                    'blocked': i18n.t(what_key),
                    'why': i18n.t('danger.why', key=DANGER_KEY),
                    'docs': self.docs}
        return None

    def mods_priority(self, body):
        stop = self._blocked_while_busy('op.priority')
        if stop:
            return stop
        mod, prio = body.get('mod'), body.get('priority')
        if not mod or prio is None:
            raise ValueError(i18n.t('err.needMod'))
        stop = self._danger(body, 'op.priority')

        def f():
            ml = self.o.modList()
            if ml.getMod(mod) is None:
                raise ValueError(i18n.t('err.noSuchMod', mod=mod))
            was = ml.priority(mod)
            if stop:
                stop.update({'mod': mod, 'from': was, 'to': int(prio)})
                return stop
            ok = ml.setPriority(mod, int(prio))
            self.o.refresh(True)
            return {'applied': bool(ok), 'mod': mod, 'from': was, 'to': ml.priority(mod)}
        return self.run_main(f)

    def mods_rename(self, body):
        stop = self._blocked_while_busy('op.rename')
        if stop:
            return stop
        mod, new = body.get('mod'), body.get('newName')
        if not mod or not new:
            raise ValueError(i18n.t('err.needMod'))
        stop = self._danger(body, 'op.rename')
        if stop:
            stop.update({'mod': mod, 'newName': new})
            return stop

        def f():
            ml = self.o.modList()
            m = ml.getMod(mod)
            if m is None:
                raise ValueError(i18n.t('err.noSuchMod', mod=mod))
            res = ml.renameMod(m, new)
            self.o.refresh(True)
            return {'applied': res is not None, 'mod': mod, 'newName': new}
        return self.run_main(f)

    def mods_remove(self, body):
        stop = self._blocked_while_busy('op.remove')
        if stop:
            return stop
        mod = body.get('mod')
        if not mod:
            raise ValueError(i18n.t('err.needMod'))
        stop = self._danger(body, 'op.remove')

        def f():
            ml = self.o.modList()
            m = ml.getMod(mod)
            if m is None:
                raise ValueError(i18n.t('err.noSuchMod', mod=mod))
            path = m.absolutePath()
            if stop:
                stop.update({'mod': mod, 'path': path})
                return stop
            ok = ml.removeMod(m)
            self.o.refresh(True)
            return {'applied': bool(ok), 'mod': mod, 'path': path}
        return self.run_main(f)

    # ================================================== процессы и окна
    def run(self, body):
        """Запуск утилиты внутри VFS.

        binary - ЗАРЕГИСТРИРОВАННОЕ в MO2 имя исполняемого файла, а не путь: с полным путём
        startApplication молча не создаёт процесс.
        """
        stop = self._blocked_while_busy('op.run')
        if stop:
            return stop
        binary = body.get('binary')
        if not binary:
            raise ValueError(i18n.t('err.needBinary'))
        args = body.get('args') or []
        cwd = body.get('cwd') or ''
        wait = bool(body.get('wait'))

        def start():
            # Флаг живёт внутри одного задания главного потока: onAboutToRun MO2 зовёт
            # синхронно из startApplication, а задания выполняются по одному.
            self._starting = True
            try:
                return self.o.startApplication(binary, args, cwd)
            finally:
                self._starting = False
        handle = self.run_main(start)
        self.seq += 1
        key = 'p%d' % self.seq
        pid = winapi.process_id(handle) if handle else 0
        self.procs[key] = (handle, pid, os.path.basename(str(binary)))
        res = {'key': key, 'pid': pid, 'binary': binary, 'args': args}
        if wait:
            # Ждём сами, а не через waitForApplication: тот не отпускает GIL и останавливает
            # весь интерпретатор вместе с сервером - мост замолкает целиком.
            res['exit'] = winapi.wait_process(handle, float(body.get('timeout') or 3600))
            res['waitedBy'] = 'WaitForSingleObject'
        return res

    def procs_list(self, _=None):
        return {'procs': [{'key': k, 'pid': v[1], 'what': v[2]} for k, v in self.procs.items()],
                'launchedByMO2': sorted(self.launched),
                'busy': self._busy()}

    def windows(self, q):
        pid = int(_one(q, 'pid', '0') or 0)
        if not pid:
            key = _one(q, 'key', '')
            pid = self.procs.get(key, (0, 0, ''))[1]
        if not pid:
            raise ValueError(i18n.t('err.needPidOrKey'))
        return {'pid': pid, 'windows': winapi.windows_of(pid)}

    def window(self, body):
        hwnd = int(body.get('hwnd') or 0)
        action = body.get('action')
        if not hwnd or not winapi.is_window(hwnd):
            raise ValueError(i18n.t('err.noSuchWindow'))
        title = winapi.window_text(hwnd)
        if action == 'close':
            winapi.close(hwnd)
            return {'hwnd': hwnd, 'title': title, 'did': 'close'}
        if action == 'click':
            caption = body.get('button') or ''
            if not caption.strip():
                raise ValueError(i18n.t('err.needButton'))
            hit = winapi.click_by_caption(hwnd, caption)
            if hit is None:
                raise ValueError(i18n.t('err.noButton', button=caption))
            return {'hwnd': hwnd, 'title': title, 'did': 'click', 'button': hit}
        raise ValueError(i18n.t('err.action'))

    # ================================================== самоописание
    def api(self, _=None):
        """Что на самом деле умеет эта сборка MO2 - без догадок по документации."""
        def f():
            out = {}
            for label, obj in (('IOrganizer', self.o), ('IModList', self.o.modList()),
                               ('IPluginList', self.o.pluginList()),
                               ('IProfile', self.o.profile())):
                try:
                    out[label] = sorted(x for x in dir(obj) if not x.startswith('_'))
                except Exception as exc:
                    out[label] = ['error: %s' % exc]
            return out
        return self.run_main(f)


# ------------------------------------------------------------------ мелочи
def _one(q, key, default):
    v = (q or {}).get(key)
    return v[0] if isinstance(v, list) and v else (v if isinstance(v, str) else default)


def _seven_zip():
    """Путь к 7z.exe. Ищем по нескольким местам, а не по PATH: установщик 7-Zip туда не пишет."""
    for p in (r'C:\Program Files-Zipz.exe',
              r'C:\Program Files (x86)-Zipz.exe'):
        if os.path.isfile(p):
            return p
    from shutil import which
    return which('7z')


def _safe(fn, default=None):
    try:
        v = fn()
        return v if v is not None else default
    except Exception:
        return default


def _walk_factory(o):
    """Запасной обход вширь: корень собирается из деревьев самих модов."""
    def walk():
        ml = o.modList()
        roots = set(['meshes', 'textures', 'scripts', 'sound', 'music', 'interface', 'seq',
                     'strings', 'video', 'grass', 'lodsettings', 'shadersfx', 'skse', 'source'])
        for name in ml.allModsByProfilePriority():
            if not (ml.state(name) & mobase.ModState.ACTIVE):
                continue
            try:
                for entry in ml.getMod(name).fileTree():
                    if entry.isDir():
                        roots.add(entry.name().lower())
            except Exception:
                pass
        rows, queue, seen = [], ['.'] + sorted(roots), set()
        while queue:
            d = queue.pop()
            if d.lower() in seen:
                continue
            seen.add(d.lower())
            at_root = (d == '.')
            try:
                for sub in o.listDirectories(d):
                    queue.append(sub if at_root else d + chr(92) + sub)
            except Exception:
                pass
            try:
                infos = o.findFileInfos(d, lambda fi: True)
            except Exception:
                continue
            for fi in infos:
                org = list(fi.origins) if fi.origins else []
                base = os.path.basename(fi.filePath)
                rel = base if at_root else d + chr(92) + base
                rows.append((rel, org[0] if org else '', fi.archive or '', '|'.join(org)))
        return o.profile().name(), rows
    return walk
