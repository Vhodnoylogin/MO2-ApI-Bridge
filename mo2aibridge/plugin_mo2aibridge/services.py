# -*- coding: utf-8 -*-
"""Предметный слой: всё, что делается с MO2. Здесь - фасад над областями.

Знает про mobase и про сборку. Не знает ни про HTTP, ни про JSON, ни про токены - принимает
обычные аргументы и возвращает обычные словари. Поэтому эти методы можно звать из чего угодно:
из маршрута, из пункта меню, из теста.

Сама логика разложена по областям, каждая в своём файле:

    busy.py       BusyGuard   занятость MO2: учёт запусков и три источника замка
    reading.py    Reader      чтение состояния и виртуальной Data
    install.py    Installer   установка мода: свежая, слияние, замена
    mods.py       ModOps      включить, выключить, перечитать; приоритет, имя, удаление
    loadorder.py  LoadOrder   состояние и порядок плагинов, запись plugins.txt
    launch.py     Launcher    запуск программ и их окна
    base.py       Domain      контекст, общий приём изменения, форма карточки

`Services` только собирает их и отдаёт наружу те же самые методы с теми же именами: имена -
контракт, на них ссылаются routes.py, plugin.py и проверки. Атрибуты учёта (`launched`,
`game_exe`, `_self_hwnd`, `procs`) тоже остаются здесь - проверки логики занятости ставят
их напрямую.
"""
from . import base, busy, config, install, launch, loadorder, mods, reading
from .base import DANGER_KEY, DANGER_VALUE, one as _one, safe as _safe  # noqa: F401
from .install import tree_files as _tree_files  # noqa: F401
from .reading import walk_factory as _walk_factory  # noqa: F401


def _seven_zip():
    """Прежнее имя: путь к 7z.exe по умолчаниям настроек."""
    return config.Config().seven_zip()


class Services(object):
    def __init__(self, organizer, run_main, docs_path, note=None, cfg=None):
        # note - куда писать след о запусках; без него плагин работает молча.
        # cfg - настройки; без них берутся встроенные умолчания, файл не читается.
        ctx = base.Context(organizer, run_main, docs_path,
                           note or (lambda _msg: None), cfg or config.Config())
        self.ctx = ctx
        self.o = organizer
        self.run_main = run_main
        self.docs = docs_path
        self.note = ctx.note
        self.cfg = ctx.cfg
        self.guard = busy.BusyGuard(ctx)
        self.reader = reading.Reader(ctx, self.guard)
        self.installer = install.Installer(ctx, self.guard)
        self.modops = mods.ModOps(ctx, self.guard)
        self.loadorder = loadorder.LoadOrder(ctx, self.guard)
        self.launcher = launch.Launcher(ctx, self.guard)

    # ================================================== занятость MO2
    def on_about_to_run(self, path, *rest):
        return self.guard.on_about_to_run(path, *rest)

    def on_finished_run(self, path, exit_code=0):
        return self.guard.on_finished_run(path, exit_code)

    def prime(self):
        return self.guard.prime()

    def _busy(self):
        return self.guard.busy()

    def _blocked_while_busy(self, op_key):
        return self.guard.refusal(op_key)

    def _window_locked(self):
        return self.guard.window_locked()

    def _game_binary(self):
        return self.guard.game_binary()

    # Учёт живёт в замке, а снаружи виден под прежними именами: plugin.py читает и
    # сбрасывает `launched`, проверки логики занятости ставят `game_exe` и `_self_hwnd`.
    launched = property(lambda s: s.guard.launched,
                        lambda s, v: setattr(s.guard, 'launched', v))
    game_exe = property(lambda s: s.guard.game_exe,
                        lambda s, v: setattr(s.guard, 'game_exe', v))
    _self_hwnd = property(lambda s: s.guard._self_hwnd,
                          lambda s, v: setattr(s.guard, '_self_hwnd', v))
    _starting = property(lambda s: s.guard._starting,
                         lambda s, v: setattr(s.guard, '_starting', v))
    procs = property(lambda s: s.launcher.procs,
                     lambda s, v: setattr(s.launcher, 'procs', v))
    seq = property(lambda s: s.launcher.seq,
                   lambda s, v: setattr(s.launcher, 'seq', v))

    # ================================================== чтение
    def ping(self, q=None):
        return self.reader.ping(q)

    def api(self, q=None):
        return self.reader.api(q)

    def mods(self, q=None):
        return self.reader.mods(q)

    def profiles(self, q=None):
        return self.reader.profiles(q)

    def plugins(self, q=None):
        return self.reader.plugins(q)

    def mod(self, q):
        return self.reader.mod(q)

    def analyze(self, q):
        return self.reader.analyze(q)

    def vfs(self, q):
        return self.reader.vfs(q)

    def origins(self, q):
        return self.reader.origins(q)

    def dirs(self, q):
        return self.reader.dirs(q)

    def resolve(self, q):
        return self.reader.resolve(q)

    def vfsexport(self, body):
        return self.reader.vfsexport(body)

    # ================================================== изменения
    def toggle(self, body):
        return self.modops.toggle(body)

    def refresh(self, body=None):
        return self.modops.refresh(body)

    def install(self, body):
        return self.installer.install(body)

    def plugins_state(self, body):
        return self.loadorder.plugins_state(body)

    def plugins_order(self, body):
        return self.loadorder.plugins_order(body)

    def _write_plugins_txt(self, wanted):
        return self.loadorder.write_plugins_txt(wanted)

    # ================================================== необратимое
    def _danger(self, body, what_key):
        # Прежняя сигнатура принимала ключ i18n целиком ('op.remove'); новая - имя операции.
        return self.modops.danger(body, what_key[3:] if what_key.startswith('op.') else what_key)

    def mods_priority(self, body):
        return self.modops.mods_priority(body)

    def mods_rename(self, body):
        return self.modops.mods_rename(body)

    def mods_remove(self, body):
        return self.modops.mods_remove(body)

    def _removal_card(self, m, ml):
        return self.modops.removal_card(m, ml)

    # ================================================== процессы и окна
    def run(self, body):
        return self.launcher.run(body)

    def procs_list(self, q=None):
        return self.launcher.procs_list(q)

    def windows(self, q):
        return self.launcher.windows(q)

    def window(self, body):
        return self.launcher.window(body)
