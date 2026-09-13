# -*- coding: utf-8 -*-
"""Подставная MO2: модуль mobase и IOrganizer, которых хватает всем маршрутам без менеджера.

Зачем. Три живых набора проверок требуют запущенной MO2, и потому их прогоняют редко и
поздно: договор маршрутов - какие ключи в ответе, что отдаётся при отказе, чем обращается
изменение - проверялся только тогда, когда кто-то поднимал менеджер. Переделка services.py
между такими прогонами могла тихо переименовать поле, и узналось бы это в чужом чате.

Здесь MO2 подменяется целиком, но не пустышкой, а копией её поведения на настоящих файлах:
mods\\ с папками модов, профиль с plugins.txt и modlist.txt, downloads\\ с архивами, overwrite\\.
Всё лежит во временной папке (tempfile.mkdtemp), поэтому маршруты, которые пишут на диск -
/install, /plugins/state с apply, /mods/remove, /vfsexport, - делают настоящий ввод-вывод, и
его можно сверить чтением файлов. Виртуальная Data собирается из активных модов по приоритету
ровно так, как это делает USVFS: побеждает мод с большим приоритетом, поставщики отдаются
победителем вперёд.

Что здесь есть из mobase: ModState и PluginState (целые флаги), GuessedString, IFileTree с
WalkReturn, а также VersionInfo, PluginSetting и IPluginTool - последние трое лишь для того,
чтобы plugin.py можно было импортировать, если понадобится.

Как пользоваться:

    import fake_mo2
    fake_mo2.install()                  # ДО импорта пакета: services связывает mobase на импорте
    fx = fake_mo2.Fixture()             # временная сборка на диске
    svc = services.Services(fx.organizer, run_main=lambda f, timeout=None: f(), ...)
    ...
    fx.cleanup()

Путей к конкретной машине нет: всё внутри временной папки, 7-Zip ищется по PATH и по
стандартным местам установки.
"""
import configparser
import fnmatch
import io
import os
import shutil
import subprocess
import sys
import tempfile
import types


# ================================================================== mobase
class ModState(object):
    """Значения те же, что у настоящей MO2: services их складывает и маскирует."""
    EXISTS = 1
    ACTIVE = 2
    ESSENTIAL = 4
    EMPTY = 8
    ENDORSED = 16
    VALID = 32
    ALTERNATE = 64


class PluginState(object):
    MISSING = 0
    INACTIVE = 1
    ACTIVE = 2


class GuessedString(object):
    """Имя с вариантами. createMod принимает именно его, а не строку."""

    def __init__(self, value=''):
        self._value = str(value)

    def value(self):
        return self._value

    def __str__(self):
        return self._value

    def __repr__(self):
        return 'GuessedString(%r)' % self._value


class IFileTree(object):
    class WalkReturn(object):
        CONTINUE = 0
        STOP = 1
        SKIP = 2


class VersionInfo(object):
    def __init__(self, *parts):
        self._parts = parts

    def displayString(self, _forced=0):
        return '.'.join(str(p) for p in self._parts) if self._parts else ''


class PluginSetting(object):
    def __init__(self, key, description, default):
        self.key, self.description, self.default_value = key, description, default


class IPluginTool(object):
    def __init__(self):
        pass


_EXPORTS = ('ModState', 'PluginState', 'GuessedString', 'IFileTree', 'VersionInfo',
            'PluginSetting', 'IPluginTool')


def install():
    """Подставить mobase в sys.modules.

    Если пустышку уже завёл common.import_package(), она наполняется на месте: services
    связал имя mobase с тем самым объектом модуля, и заменять его в sys.modules поздно.
    """
    mod = sys.modules.get('mobase')
    if mod is None:
        mod = types.ModuleType('mobase')
        sys.modules['mobase'] = mod
    for name in _EXPORTS:
        setattr(mod, name, globals()[name])
    mod.__fake__ = True
    return mod


# ================================================================== дерево файлов
class Entry(object):
    """Запись дерева: имя и признак папки - всё, что читают services."""

    def __init__(self, name, is_dir, children=None):
        self._name = name
        self._dir = is_dir
        self._children = children if children is not None else {}

    def name(self):
        return self._name

    def isDir(self):
        return self._dir

    def isFile(self):
        return not self._dir

    def __iter__(self):
        return iter(self._children.values())

    def __repr__(self):
        return 'Entry(%r, dir=%s)' % (self._name, self._dir)


def _tree_from_paths(rel_paths):
    """Дерево из относительных путей (через любой разделитель). Регистр имён сохраняется."""
    root = Entry('', True)
    for rel in rel_paths:
        parts = [p for p in rel.replace('/', chr(92)).split(chr(92)) if p]
        node = root
        for i, part in enumerate(parts):
            key = part.lower()
            if key not in node._children:
                node._children[key] = Entry(part, i < len(parts) - 1)
            node = node._children[key]
    return root


class FileTree(object):
    """IFileTree: перебор верхнего уровня и обход в глубину с visitor(путь, запись)."""

    def __init__(self, root_entry):
        self._root = root_entry

    def __iter__(self):
        return iter(self._root)

    def walk(self, visitor, sep=chr(92)):
        """Как у MO2: путь - это префикс родителя с завершающим разделителем, '' для корня."""
        def go(node, prefix):
            for child in node:
                rv = visitor(prefix, child)
                if rv == IFileTree.WalkReturn.STOP:
                    return False
                if child.isDir() and rv != IFileTree.WalkReturn.SKIP:
                    if not go(child, prefix + child.name() + sep):
                        return False
            return True
        go(self._root, '')


# ================================================================== мод
class _Version(object):
    def __init__(self, text):
        self._text = text or ''

    def displayString(self, _forced=0):
        return self._text


class FakeMod(object):
    """IModInterface поверх папки в mods\\ и её meta.ini."""

    def __init__(self, organizer, name):
        self._o = organizer
        self._name = name

    def _meta(self):
        cp = configparser.ConfigParser(interpolation=None)
        try:
            cp.read(os.path.join(self.absolutePath(), 'meta.ini'), encoding='utf-8')
        except Exception:
            pass
        return cp['General'] if cp.has_section('General') else {}

    def name(self):
        return self._name

    def absolutePath(self):
        return os.path.join(self._o.modsPath(), self._name)

    def nexusId(self):
        try:
            return int(self._meta().get('modid', 0) or 0)
        except ValueError:
            return 0

    def gameName(self):
        return self._meta().get('gamename', '') or self._o.managedGame().gameShortName()

    def url(self):
        return self._meta().get('url', '') or ''

    def version(self):
        return _Version(self._meta().get('version', ''))

    def newestVersion(self):
        return _Version(self._meta().get('newestversion', ''))

    def ignoredVersion(self):
        return _Version(self._meta().get('ignoredversion', ''))

    def installationFile(self):
        return self._meta().get('installationfile', '') or ''

    def categories(self):
        raw = self._meta().get('category', '') or ''
        return [x for x in raw.split(',') if x]

    def primaryCategory(self):
        cats = self.categories()
        try:
            return int(cats[0]) if cats else 0
        except ValueError:
            return 0

    def notes(self):
        return self._meta().get('notes', '') or ''

    def comments(self):
        return self._meta().get('comments', '') or ''

    def endorsedState(self):
        return 'EndorsedState.ENDORSED_UNKNOWN'

    def trackedState(self):
        return 'TrackedState.TRACKED_UNKNOWN'

    def isSeparator(self):
        return self._name.endswith('_separator')

    def isForeign(self):
        return False

    def isBackup(self):
        return False

    def isOverwrite(self):
        return False

    def fileTree(self):
        return FileTree(_tree_from_paths(_files_under(self.absolutePath())))


def tree_files(root):
    """Пути всех файлов папки относительно неё, в нижнем регистре, meta.ini включительно -
    то же «было и стало», которым /install считает added и overwritten."""
    out = set()
    for dp, _dn, fs in os.walk(root):
        rel = os.path.relpath(dp, root)
        for f in fs:
            out.add((f if rel == '.' else os.path.join(rel, f)).lower())
    return out


def _files_under(root):
    """Относительные пути всех файлов папки, кроме корневого meta.ini - он не часть Data."""
    out = []
    for dp, _dn, fs in os.walk(root):
        rel = os.path.relpath(dp, root)
        for f in fs:
            if rel == '.' and f.lower() == 'meta.ini':
                continue
            out.append(f if rel == '.' else os.path.join(rel, f))
    return out


# ================================================================== список модов
class FakeModList(object):
    """IModList: порядок - это приоритет, окно MO2 сверху вниз, 0 - самый слабый."""

    def __init__(self, organizer):
        self._o = organizer
        self._order = []      # имена по возрастанию приоритета
        self._active = {}     # имя -> включён ли
        self._essential = set()

    # --- чтение
    def allModsByProfilePriority(self, _profile=None):
        return list(self._order)

    def allMods(self):
        return list(self._order)

    def state(self, name):
        if name not in self._active:
            return 0
        st = ModState.EXISTS | ModState.VALID
        if self._active[name]:
            st |= ModState.ACTIVE
        if name in self._essential:
            st |= ModState.ESSENTIAL
        return st

    def priority(self, name):
        return self._order.index(name) if name in self._order else -1

    def getMod(self, name):
        return FakeMod(self._o, name) if name in self._active else None

    def displayName(self, name):
        return name

    # --- изменения
    def setActive(self, name, active):
        if name not in self._active:
            return False
        self._active[name] = bool(active)
        self._o._save_modlist()
        self._o._rebuild()
        return True

    def setPriority(self, name, new_priority):
        if name not in self._order:
            return False
        self._order.remove(name)
        new_priority = max(0, min(int(new_priority), len(self._order)))
        self._order.insert(new_priority, name)
        self._o._save_modlist()
        self._o._rebuild()
        return True

    def renameMod(self, mod, new_name):
        old = mod.name()
        if old not in self._active or not new_name or new_name in self._active:
            return None
        os.rename(os.path.join(self._o.modsPath(), old),
                  os.path.join(self._o.modsPath(), new_name))
        self._order[self._order.index(old)] = new_name
        self._active[new_name] = self._active.pop(old)
        if old in self._essential:
            self._essential.discard(old)
            self._essential.add(new_name)
        self._o._save_modlist()
        self._o._rebuild()
        return FakeMod(self._o, new_name)

    def removeMod(self, mod):
        name = mod.name()
        if name not in self._active:
            return False
        shutil.rmtree(os.path.join(self._o.modsPath(), name), ignore_errors=True)
        self._order.remove(name)
        self._active.pop(name, None)
        self._essential.discard(name)
        self._o._save_modlist()
        self._o._rebuild()
        return True


# ================================================================== список плагинов
class FakePluginList(object):
    """IPluginList: плагины из корней активных модов, порядок и звёздочки из plugins.txt.

    setState и setLoadOrder меняют только память - как в MO2, которая переписывает
    plugins.txt в свои моменты. Ровно из-за этого у services есть _write_plugins_txt.
    """

    def __init__(self, organizer):
        self._o = organizer
        self._order = []        # все известные плагины, и скрытые тоже: место и состояние помнятся
        self._visible = []      # те, чей мод сейчас включён, - их и отдаёт pluginNames
        self._active = {}
        self._origin = {}
        self._masters = {}

    def _rebuild(self):
        """Собрать список заново: новые плагины дописываются в конец выключенными,
        плагины выключенных модов скрываются, но место и состояние за ними остаются -
        иначе выключить и включить мод значило бы потерять его плагин в конце списка."""
        ml = self._o.modList()
        found, origin = [], {}
        for name in ml.allModsByProfilePriority():
            if not ml.state(name) & ModState.ACTIVE:
                continue
            root = os.path.join(self._o.modsPath(), name)
            try:
                names = sorted(os.listdir(root))
            except OSError:
                continue
            for f in names:
                if f.lower().endswith(('.esp', '.esm', '.esl')) and \
                        os.path.isfile(os.path.join(root, f)):
                    if f not in found:
                        found.append(f)
                    origin[f] = name      # больший приоритет перекрывает
        for p in found:
            if p not in self._order:
                self._order.append(p)
                self._active[p] = False
        self._visible = [p for p in self._order if p in found]
        self._origin = origin

    def _load_file(self):
        """Первое чтение plugins.txt профиля: порядок строк и звёздочки."""
        path = os.path.join(self._o.profile().absolutePath(), 'plugins.txt')
        try:
            raw = io.open(path, encoding='utf-8-sig', newline='').read()
        except OSError:
            return
        order, active = [], {}
        for line in raw.split(chr(10)):
            bare = line.rstrip(chr(13)).strip()
            if not bare or bare.startswith('#'):
                continue
            name = bare[1:] if bare.startswith('*') else bare
            order.append(name)
            active[name] = bare.startswith('*')
        self._order = order
        self._active = active
        self._rebuild()

    # --- чтение
    def pluginNames(self):
        return list(self._visible)

    def state(self, name):
        if name not in self._visible:
            return PluginState.MISSING
        return PluginState.ACTIVE if self._active[name] else PluginState.INACTIVE

    def priority(self, name):
        return self._visible.index(name) if name in self._visible else -1

    def loadOrder(self, name):
        return self._visible.index(name) if name in self._visible else -1

    def origin(self, name):
        return self._origin.get(name, '')

    def isMasterFlagged(self, name):
        return name.lower().endswith('.esm')

    def hasMasterExtension(self, name):
        return name.lower().endswith('.esm')

    def isLightFlagged(self, name):
        return name.lower().endswith('.esl')

    def hasLightExtension(self, name):
        return name.lower().endswith('.esl')

    def hasNoRecords(self, name):
        return False

    def masters(self, name):
        return list(self._masters.get(name, []))

    # --- изменения
    def setState(self, name, state):
        if name in self._visible:
            self._active[name] = bool(state & PluginState.ACTIVE)

    def setLoadOrder(self, order):
        """Новый порядок видимых; скрытые остаются позади в прежнем взаимном порядке."""
        visible = [p for p in order if p in self._visible] + \
                  [p for p in self._visible if p not in order]
        hidden = [p for p in self._order if p not in self._visible]
        self._order = visible + hidden
        self._visible = visible


# ================================================================== IOrganizer
class _Profile(object):
    def __init__(self, name, path):
        self._name, self._path = name, path

    def name(self):
        return self._name

    def absolutePath(self):
        return self._path

    def localSavesEnabled(self):
        return False

    def localSettingsEnabled(self):
        return False


class _Game(object):
    def __init__(self, short_name, binary, nexus_name=None):
        self._short, self._binary = short_name, binary
        # Раздел Nexus, под которым игра там живёт. У настоящей MO2 это gameNexusName(),
        # и для Skyrim VR он отдаёт раздел SSE - своего у VR нет. Метод обязан быть и
        # здесь: подделка, более мягкая, чем настоящий API, даёт ложную уверенность.
        self.nexus_name = short_name.lower() if nexus_name is None else nexus_name

    def gameNexusName(self):
        return self.nexus_name

    def gameShortName(self):
        return self._short

    def gameName(self):
        return self._short

    def binaryName(self):
        return self._binary


class _FileInfo(object):
    def __init__(self, file_path, origins, archive=''):
        self.filePath = file_path
        self.origins = list(origins)
        self.archive = archive


class FakeOrganizer(object):
    """IOrganizer над временной папкой. Виртуальная Data пересобирается на refresh."""

    def __init__(self, root, profile='Claude', game='SkyrimVR', binary='SkyrimVR.exe',
                 version='2.5.2-fake'):
        self.root = root
        self._profile = profile
        self._game = _Game(game, binary)
        self._version = version
        self._mods = FakeModList(self)
        self._plugins = FakePluginList(self)
        self._vfs = {}          # rel в нижнем регистре -> (список поставщиков, путь победителя)
        self._display = {}      # rel в нижнем регистре -> rel как в папке победителя
        self._about_to_run = []
        self._finished_run = []
        self.started = []       # что просили запустить: (binary, args, cwd)
        self.start_result = None    # что отдаёт startApplication: None - процесс не создан
        self.refreshed = 0

    # --- пути
    def basePath(self):
        return self.root

    def modsPath(self):
        return os.path.join(self.root, 'mods')

    def overwritePath(self):
        return os.path.join(self.root, 'overwrite')

    def downloadsPath(self):
        return os.path.join(self.root, 'downloads')

    def profilePath(self):
        return os.path.join(self.root, 'profiles', self._profile)

    def profile(self):
        return _Profile(self._profile, self.profilePath())

    def profileName(self):
        return self._profile

    def managedGame(self):
        return self._game

    def appVersion(self):
        return _Version(self._version)

    def version(self):
        return self.appVersion()

    def modList(self):
        return self._mods

    def pluginList(self):
        return self._plugins

    # --- загрузка состояния с диска
    def _load(self):
        """Прочитать modlist.txt (первая строка - самый сильный) и plugins.txt."""
        ml = self._mods
        path = os.path.join(self.profilePath(), 'modlist.txt')
        ordered = []
        try:
            raw = io.open(path, encoding='utf-8-sig').read()
        except OSError:
            raw = ''
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            name = line[1:]
            if not os.path.isdir(os.path.join(self.modsPath(), name)):
                continue
            ordered.append((name, line.startswith('+')))
        # в файле порядок обратный окну: первая строка - наибольший приоритет
        ordered.reverse()
        ml._order = [n for n, _a in ordered]
        ml._active = {n: a for n, a in ordered}
        self._adopt_new_folders()
        self._rebuild()
        self._plugins._load_file()

    def _adopt_new_folders(self):
        """Папки в mods\\, которых нет в профиле, появляются выключенными в конце -
        так MO2 подхватывает мод, разложенный мимо неё, после refresh."""
        ml = self._mods
        try:
            names = sorted(os.listdir(self.modsPath()))
        except OSError:
            names = []
        for n in names:
            if os.path.isdir(os.path.join(self.modsPath(), n)) and n not in ml._active:
                ml._order.append(n)
                ml._active[n] = False
        for n in list(ml._active):
            if not os.path.isdir(os.path.join(self.modsPath(), n)):
                ml._order.remove(n)
                ml._active.pop(n, None)

    def _save_modlist(self):
        ml = self._mods
        lines = ['# This file was automatically generated by Mod Organizer.']
        for name in reversed(ml._order):
            lines.append(('+' if ml._active[name] else '-') + name)
        with io.open(os.path.join(self.profilePath(), 'modlist.txt'), 'w',
                     encoding='utf-8', newline='') as fh:
            fh.write(chr(13).join('') + (chr(13) + chr(10)).join(lines) + chr(13) + chr(10))

    def _rebuild(self):
        """Собрать виртуальную Data из активных модов: больший приоритет побеждает."""
        ml = self._mods
        vfs, display = {}, {}
        for name in ml.allModsByProfilePriority():
            if not ml.state(name) & ModState.ACTIVE:
                continue
            root = os.path.join(self.modsPath(), name)
            for rel in _files_under(root):
                key = rel.replace('/', chr(92)).lower()
                providers = vfs.get(key, ([], ''))[0]
                # поставщики хранятся победителем вперёд, как отдаёт getFileOrigins
                vfs[key] = ([name] + providers, os.path.join(root, rel))
                display[key] = rel.replace('/', chr(92))
        self._vfs, self._display = vfs, display
        self._plugins._rebuild()

    def refresh(self, _save_changes=True):
        self.refreshed += 1
        self._adopt_new_folders()
        self._rebuild()
        return True

    # --- виртуальная Data
    @staticmethod
    def _norm(path):
        p = (path or '').replace('/', chr(92)).strip(chr(92))
        return '' if p in ('', '.') else p.lower()

    def findFiles(self, path, pattern='*'):
        """Настоящие пути файлов-победителей в виртуальной папке, без рекурсии."""
        d = self._norm(path)
        pats = pattern if isinstance(pattern, (list, tuple)) else [pattern or '*']
        out = []
        for key, (_prov, real) in sorted(self._vfs.items()):
            parent, _sep, base = key.rpartition(chr(92))
            if parent != d:
                continue
            if any(fnmatch.fnmatch(base, p.lower()) for p in pats):
                out.append(real)
        return out

    def getFileOrigins(self, rel):
        return list(self._vfs.get(self._norm(rel), ([], ''))[0])

    def listDirectories(self, path):
        d = self._norm(path)
        out = {}
        for key in self._vfs:
            parent = key.rpartition(chr(92))[0]
            if d and not (parent == d or parent.startswith(d + chr(92))):
                continue
            rest = parent[len(d):].lstrip(chr(92)) if d else parent
            if rest:
                first = rest.split(chr(92))[0]
                disp = self._display[key].split(chr(92))[len(d.split(chr(92))) if d else 0]
                out.setdefault(first, disp)
        return [out[k] for k in sorted(out)]

    def resolvePath(self, rel):
        return self._vfs.get(self._norm(rel), ([], ''))[1]

    def virtualFileTree(self):
        return FileTree(_tree_from_paths(self._display[k] for k in sorted(self._display)))

    def findFileInfos(self, path, predicate=None):
        d = self._norm(path)
        out = []
        for key, (prov, real) in sorted(self._vfs.items()):
            if key.rpartition(chr(92))[0] != d:
                continue
            fi = _FileInfo(real, prov, '')
            if predicate is None or predicate(fi):
                out.append(fi)
        return out

    # --- изменения
    def createMod(self, guessed):
        name = str(guessed.value() if hasattr(guessed, 'value') else guessed).strip()
        if not name:
            return None
        path = os.path.join(self.modsPath(), name)
        if os.path.isdir(path):
            return FakeMod(self, name) if name in self._mods._active else None
        os.makedirs(path)
        with io.open(os.path.join(path, 'meta.ini'), 'w', encoding='utf-8') as fh:
            fh.write('[General]' + chr(10) + 'modid=0' + chr(10) + 'version=' + chr(10))
        self._mods._order.append(name)
        self._mods._active[name] = False
        self._save_modlist()
        return FakeMod(self, name)

    def startApplication(self, binary, args=None, cwd='', profile='', overwrite='',
                         ignore_overwrite=False):
        """Ничего не запускает. Зовёт onAboutToRun, как MO2, и отдаёт start_result."""
        self.started.append((binary, list(args or []), cwd))
        for cb in self._about_to_run:
            try:
                cb(binary, cwd, ' '.join(args or []))
            except TypeError:
                cb(binary)
        return self.start_result

    def waitForApplication(self, _handle, _refresh=True):
        return (False, -1)

    # --- обратные вызовы и настройки
    def onAboutToRun(self, callback):
        self._about_to_run.append(callback)
        return True

    def onFinishedRun(self, callback):
        self._finished_run.append(callback)
        return True

    def onUserInterfaceInitialized(self, callback):
        return True

    def finish_run(self, binary, exit_code=0):
        """Сообщить подписчикам о завершении - то, что MO2 делает сама."""
        for cb in self._finished_run:
            cb(binary, exit_code)

    def pluginSetting(self, _plugin, _key):
        return None


# ================================================================== сборка на диске
# Четыре мода: окно MO2 сверху вниз, приоритет 0..3. Delta выключен, поэтому его файлов
# в виртуальной Data нет, а его плагин неизвестен списку плагинов.
MODS = [
    ('Alpha Mod', {'modid': 101, 'version': '1.0', 'installationFile': 'Alpha Mod-101-1-0.7z',
                   'category': '3,', 'notes': 'alpha note', 'comments': 'alpha comment',
                   'active': True,
                   'files': ['Alpha.esp', 'textures/alpha.dds', 'meshes/shared.nif']}),
    ('Beta Mod', {'modid': 202, 'version': '2.1', 'installationFile': 'Beta Mod-202-2-1.7z',
                  'category': '5,', 'notes': '', 'comments': '',
                  'active': True,
                  'files': ['Beta.esp', 'meshes/shared.nif', 'scripts/beta.pex']}),
    ('Gamma Mod', {'modid': 0, 'version': '0.3', 'installationFile': 'Gamma Mod-0-0-3.7z',
                   'category': '', 'notes': '', 'comments': '',
                   'active': True,
                   'files': ['Gamma.esl', 'textures/gamma.dds']}),
    ('Delta Mod', {'modid': 404, 'version': '4.0', 'installationFile': '',
                   'category': '', 'notes': '', 'comments': '',
                   'active': False,
                   'files': ['Delta.esp', 'meshes/shared.nif']}),
]
PLUGINS_TXT = ['# This file was automatically generated by Mod Organizer.',
               '*Alpha.esp', 'Beta.esp', '*Gamma.esl']
# Архивы в downloads: у Gamma архива на диске нет - для archiveOnDisk: false
DOWNLOADS = ['Alpha Mod-101-1-0.7z', 'Beta Mod-202-2-1.7z']
PROFILES = ['Claude', 'Second']
CRLF = chr(13) + chr(10)


def _write(path, text=''):
    d = os.path.dirname(path)
    if not os.path.isdir(d):
        os.makedirs(d)
    with io.open(path, 'w', encoding='utf-8', newline='') as fh:
        fh.write(text)


class Fixture(object):
    """Временная сборка: папки на диске плюс IOrganizer над ними."""

    def __init__(self, profile='Claude'):
        self.root = tempfile.mkdtemp(prefix='mo2aibridge-fake-')
        self.profile = profile
        self._build()
        self.organizer = FakeOrganizer(self.root, profile=profile)
        self.organizer._load()

    def _build(self):
        mods = os.path.join(self.root, 'mods')
        for name, spec in MODS:
            root = os.path.join(mods, name)
            _write(os.path.join(root, 'meta.ini'),
                   '[General]' + chr(10) +
                   'modid=%d' % spec['modid'] + chr(10) +
                   'version=%s' % spec['version'] + chr(10) +
                   'installationFile=%s' % spec['installationFile'] + chr(10) +
                   'category=%s' % spec['category'] + chr(10) +
                   'notes=%s' % spec['notes'] + chr(10) +
                   'comments=%s' % spec['comments'] + chr(10))
            for rel in spec['files']:
                _write(os.path.join(root, rel.replace('/', os.sep)), 'content of ' + rel + chr(10))
        for prof in PROFILES:
            os.makedirs(os.path.join(self.root, 'profiles', prof))
        prof_dir = os.path.join(self.root, 'profiles', self.profile)
        _write(os.path.join(prof_dir, 'plugins.txt'), CRLF.join(PLUGINS_TXT) + CRLF)
        # первая строка modlist.txt - самый сильный мод, то есть нижний в окне
        lines = ['# This file was automatically generated by Mod Organizer.']
        for name, spec in reversed(MODS):
            lines.append(('+' if spec['active'] else '-') + name)
        _write(os.path.join(prof_dir, 'modlist.txt'), CRLF.join(lines) + CRLF)
        os.makedirs(os.path.join(self.root, 'overwrite'))
        for arc in DOWNLOADS:
            full = os.path.join(self.root, 'downloads', arc)
            _write(full, 'not a real archive' + chr(10))
            # В имени архива нет отметки загрузки, поэтому момент берётся с диска: ставим его
            # равным времени нашего файла на странице, как у настоящей загрузки
            os.utime(full, (T0, T0))
            meta = DOWNLOAD_META.get(arc)
            if meta:
                _write(full + '.meta', '[General]' + chr(10) +
                       'modID=%d' % meta[0] + chr(10) + 'fileID=%d' % meta[1] + chr(10) +
                       'installed=true' + chr(10))

    # --- удобства для проверок
    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def mod_path(self, name):
        return os.path.join(self.root, 'mods', name)

    def plugins_txt(self):
        return self.path('profiles', self.profile, 'plugins.txt')

    def read_bytes(self, *parts):
        with io.open(self.path(*parts), 'rb') as fh:
            return fh.read()

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


# ================================================================== 7-Zip и архив под /install
def seven_zip():
    """7z.exe по PATH или по обычным местам установки; None, если нет нигде."""
    found = shutil.which('7z')
    if found:
        return found
    for base in (os.environ.get('ProgramFiles', ''), os.environ.get('ProgramFiles(x86)', ''),
                 os.environ.get('ProgramW6432', '')):
        if base:
            cand = os.path.join(base, '7-Zip', '7z.exe')
            if os.path.isfile(cand):
                return cand
    return None


# Архив с двумя вариантами, fomod и корневым meta.ini - всё, что /install умеет пропускать
ARCHIVE_FILES = {
    '00 Core/Zeta.esp': 'zeta plugin',
    '00 Core/textures/zeta.dds': 'zeta texture',
    '10 Extra/meshes/zeta.nif': 'zeta mesh',
    'fomod/ModuleConfig.xml': '<config/>',
    'meta.ini': '[General]',
}


def build_archive(dest, files=None):
    """Собрать .7z из словаря {относительный путь: текст}. None, если 7-Zip не найден."""
    seven = seven_zip()
    if not seven:
        return None
    stage = tempfile.mkdtemp(prefix='mo2aibridge-stage-')
    try:
        for rel, text in (files or ARCHIVE_FILES).items():
            _write(os.path.join(stage, rel.replace('/', os.sep)), text + chr(10))
        if os.path.isfile(dest):
            os.remove(dest)
        r = subprocess.run([seven, 'a', '-t7z', '-y', '-bso0', '-bsp0', dest, '*'],
                           cwd=stage, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError('7z a: %s' % (r.stdout or r.stderr)[-300:])
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return dest


# ================================================================== Nexus: подставной мост
# Момент загрузки нашего файла на страницу; от него отсчитываются остальные.
T0 = 1700000000
DAY = 86400
# Что мы скачивали: архив -> (modID, fileID). Отсюда мост узнаёт, какие строки страницы наши.
DOWNLOAD_META = {'Alpha Mod-101-1-0.7z': (101, 1001), 'Beta Mod-202-2-1.7z': (202, 2001)}
# Страницы Nexus по modID. Строка вместо списка - отказ запроса с таким текстом.
NEXUS_FILES = {
    # Alpha: наш файл уехал в Old files, в Main лежит файл той же роли новее - обновление
    101: [dict(name='Alpha Mod', fileName='Alpha Mod-101-1-0.7z', fileID=1001, version='1.0',
               fileCategory=4, fileTime=T0),
          dict(name='Alpha Mod', fileName='Alpha Mod-101-1-1.7z', fileID=1002, version='1.1',
               fileCategory=1, fileTime=T0 + 30 * DAY)],
    # Beta: наш файл - новейший Main; рядом патч другой роли в Optional - актуально
    202: [dict(name='Beta Mod', fileName='Beta Mod-202-2-1.7z', fileID=2001, version='2.1',
               fileCategory=1, fileTime=T0),
          dict(name='Beta Mod - Farming Patch', fileName='Beta Mod Farming Patch-202-1-0.7z',
               fileID=2002, version='1.0', fileCategory=3, fileTime=T0 + 5 * DAY)],
    # Delta: страница на модерации, запрос отказан
    404: 'mod is under moderation',
}


class _When(object):
    """QDateTime в одном методе: столько мосту и нужно."""

    def __init__(self, secs):
        self.secs = secs

    def toSecsSinceEpoch(self):
        return self.secs


class FakeFileInfo(object):
    """ModRepositoryFileInfo: атрибуты, которые читает мост."""

    def __init__(self, name, fileName, fileID, version, fileCategory, fileTime):
        self.name = name
        self.fileName = fileName
        self.fileID = fileID
        self.version = _Version(version)
        self.fileCategory = fileCategory
        self.fileTime = _When(fileTime)
        self.modID = 0
        self.fileSize = 1
        self.description = ''


class FakeBridge(object):
    """IModRepositoryBridge: ответ приходит сразу же, из того же вызова.

    Настоящий мост отвечает позже и из главного потока; подставному это не нужно - ждущий
    Event уже выставлен к моменту, когда мост станет ждать, и ожидание не блокирует."""

    def __init__(self):
        self.requests = []
        self._files = None
        self._failed = None

    def onFilesAvailable(self, callback):
        self._files = callback

    def onRequestFailed(self, callback):
        self._failed = callback

    def requestFiles(self, game, mod_id, user_data):
        self.requests.append((game, int(mod_id), user_data))
        spec = NEXUS_FILES.get(int(mod_id))
        if isinstance(spec, str):
            self._failed(game, int(mod_id), 0, user_data, spec)
        else:
            self._files(game, int(mod_id), user_data,
                        [FakeFileInfo(**d) for d in (spec or [])])


def _create_nexus_bridge(self):
    self.bridges = getattr(self, 'bridges', [])
    b = FakeBridge()
    self.bridges.append(b)
    return b


FakeOrganizer.createNexusBridge = _create_nexus_bridge
