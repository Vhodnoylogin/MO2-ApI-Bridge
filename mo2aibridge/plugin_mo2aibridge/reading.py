# -*- coding: utf-8 -*-
"""Чтение состояния и виртуальной Data. Ничего не меняет и работает всегда, даже когда MO2
занята запущенной программой.

Здесь всё, что отвечает на вопрос «что сейчас есть»: карточка и разбор мода, списки модов,
профилей и плагинов, файлы виртуальной Data и их поставщики, выгрузка VFS целиком.
"""
import os

import mobase

from . import i18n
from .base import Domain, one, safe


class Reader(Domain):

    # ================================================== состояние
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
        res = {'busy': self.guard.busy()}
        try:
            res.update(self.run_main(f, timeout=self.timeout('ping')))
            res['mainThread'] = 'ok'
        except Exception as exc:
            # Не отвечающий главный поток - это ответ, а не ошибка: значит MO2 чем-то занята.
            res['ok'] = False
            res['mainThread'] = str(exc)
        return res

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

    # ================================================== карточка и разбор
    def mod(self, q):
        """Всё, что MO2 знает о моде. Три поля не лежат в meta.ini вовсе:
        isSeparator (иначе определяется по суффиксу имени, то есть по соглашению),
        categories (свои категории MO2 хранит отдельно) и ignoredVersion с endorsed
        (какие обновления пользователь уже отклонил).
        """
        name = one(q, 'name', '')
        if not name:
            raise ValueError(i18n.t('err.needName'))

        def f():
            ml = self.o.modList()
            m = ml.getMod(name)
            if m is None:
                raise ValueError(i18n.t('err.noSuchMod', mod=name))
            st = ml.state(name)
            nid = safe(m.nexusId, 0)
            game = safe(m.gameName, '') or ''
            own_url = safe(m.url, '') or ''
            url = self.nexus_url(game, nid) or own_url
            return {'mod': name,
                    'displayName': safe(lambda: ml.displayName(name), name),
                    'path': safe(m.absolutePath, ''),
                    'active': bool(st & mobase.ModState.ACTIVE),
                    'essential': bool(st & mobase.ModState.ESSENTIAL),
                    'priority': ml.priority(name),
                    'isSeparator': safe(m.isSeparator, False),
                    'isForeign': safe(m.isForeign, False),
                    'isBackup': safe(m.isBackup, False),
                    'isOverwrite': safe(m.isOverwrite, False),
                    'nexusId': nid, 'url': url, 'urlFromMO2': own_url, 'gameName': game,
                    'version': safe(lambda: m.version().displayString(), ''),
                    'newestVersion': safe(lambda: m.newestVersion().displayString(), ''),
                    'ignoredVersion': safe(lambda: m.ignoredVersion().displayString(), ''),
                    'installationFile': safe(m.installationFile, ''),
                    'categories': list(safe(m.categories, []) or []),
                    'primaryCategory': safe(m.primaryCategory, 0),
                    'notes': safe(m.notes, ''), 'comments': safe(m.comments, ''),
                    'endorsed': str(safe(m.endorsedState, '')),
                    'tracked': str(safe(m.trackedState, ''))}
        return self.run_main(f)

    def analyze(self, q):
        """Разбор мода одним вызовом: карточка, файлы, плагины и кто с кем спорит.

        В отличие от расчёта по списку модов это реальный порядок активного профиля.
        conflicts=0 отключает самую дорогую часть - проверку каждого файла.
        """
        name = one(q, 'name', '')
        if not name:
            raise ValueError(i18n.t('err.needName'))
        want_conf = one(q, 'conflicts', '1') != '0'
        limit = int(one(q, 'limit', str(self.limit('analyzeLimit'))))

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
        res = self.run_main(f, timeout=self.timeout('analyze'))
        # карточку берём отдельным вызовом: вкладывать один run_main в другой нельзя,
        # главный поток уже занят и получится взаимная блокировка
        try:
            res['card'] = self.mod({'name': [name]})
        except Exception as exc:
            res['card'] = {'error': str(exc)}
        return res

    # ================================================== виртуальная Data
    def vfs(self, q):
        path = one(q, 'path', '.')
        filt = one(q, 'filter', '*')

        def f():
            return {'path': path, 'filter': filt,
                    'files': list(self.o.findFiles(path, filt))}
        return self.run_main(f)

    def origins(self, q):
        rel = one(q, 'path', '')

        def f():
            # порядок важен: первый в списке - тот, чей файл реально видит игра
            return {'path': rel, 'origins': list(self.o.getFileOrigins(rel))}
        return self.run_main(f)

    def dirs(self, q):
        path = one(q, 'path', '.')

        def f():
            return {'path': path, 'dirs': list(self.o.listDirectories(path))}
        return self.run_main(f)

    def resolve(self, q):
        p = one(q, 'path', '')
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
        timeout = float(body.get('timeout') or self.timeout('vfsExport'))
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

        walk = walk_factory(self.o, self.cfg.get('walkRoots') or [])
        if how == 'walk':
            profile, rows = self.run_main(walk, timeout=timeout)
        else:
            try:
                profile, rows = self.run_main(by_tree, timeout=timeout)
            except Exception as exc:
                how = 'walk (fallback)'
                self.note(i18n.t('vfs.fallback', error=exc))
                profile, rows = self.run_main(walk, timeout=timeout)

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


def walk_factory(o, roots):
    """Запасной обход вширь: корень собирается из деревьев самих модов."""
    def walk():
        ml = o.modList()
        seen_roots = set(x.lower() for x in roots)
        for name in ml.allModsByProfilePriority():
            if not (ml.state(name) & mobase.ModState.ACTIVE):
                continue
            try:
                for entry in ml.getMod(name).fileTree():
                    if entry.isDir():
                        seen_roots.add(entry.name().lower())
            except Exception:
                pass
        rows, queue, seen = [], ['.'] + sorted(seen_roots), set()
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
