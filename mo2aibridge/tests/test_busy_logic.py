# -*- coding: utf-8 -*-
"""Логика занятости, без запущенной MO2.

Регрессия на дефект, из-за которого защита отключалась сама: проверка занятости стирала
собственный учёт запусков, не найдя в системе живого процесса с знакомым именем. Но MO2
сообщает о завершении сама, и пока не сообщила - её замок держится, а значит запись была
не мусором, а единственной оставшейся уликой.

Ловится именно на подставном IOrganizer: так видно логику, а не поведение конкретной сборки
менеджера.
"""
import ctypes  # noqa: F401  - до подмены путей к dll, иначе _ctypes не грузится
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

T = common.T

pkg = common.import_package()
services, i18n = pkg.services, pkg.i18n
i18n.set_language('ru')
r = common.Report('логика занятости')

ЧУЖОЙ = {'n': 1, 'mine': False}   # запуск не наш: MO2 сама сообщит о завершении
СВОЙ = {'n': 1, 'mine': True}     # наш запуск: снимаем с учёта, когда процесс умрёт
ЗАГРУЗЧИК = r'C:\mods\SKSEVR\Root\sksevr_loader.exe'


def make(launched=None, game='НетТакогоПроцесса.exe'):
    svc = services.Services(object(), lambda f, timeout=None: f(), 'docs')
    svc.game_exe = game.lower()
    svc._self_hwnd = 0            # окно MO2 не ищем: менеджер не запущен
    svc.launched = {k: dict(v) for k, v in (launched or {}).items()}
    return svc


r.head('ничего не запущено')
r.case('свободно', make()._busy(), None)

r.head('MO2 не сообщила о завершении - замок держит кто-то безымянный')
busy = make({ЗАГРУЗЧИК: ЧУЖОЙ})._busy()
r.case('занято, а не свободно', busy is not None, True)
if busy:
    r.case('MO2 считает запуск активным', busy['viaMO2'], True)
    r.case('живого процесса не нашли', busy['app'], None)
    r.case('держит неизвестный', busy['heldByUnknown'], True)
    r.case('видно, что запускала MO2', busy['mo2Run'], [ЗАГРУЗЧИК])
    r.case('это не игра', busy['isGame'], False)

r.head('учёт чужого запуска не стирается сам')
svc = make({ЗАГРУЗЧИК: ЧУЖОЙ})
svc._busy()
svc._busy()
r.case('после двух проверок цел', sorted(svc.launched), [ЗАГРУЗЧИК])

r.head('изменяющая операция отказывает')
stop = make({ЗАГРУЗЧИК: ЧУЖОЙ})._blocked_while_busy('op.toggle')
r.case('отказ выдан', stop is not None, True)
if stop:
    r.case('ничего не применено', stop['applied'], False)
    r.case('помечено занятостью', stop['busy'], True)
    r.case('названа операция', stop['blocked'], 'включение или выключение мода')
    r.case('объяснено про безымянного', 'не сообщила о завершении' in stop['why'], True)

r.head('onFinishedRun снимает учёт')
svc = make({ЗАГРУЗЧИК: ЧУЖОЙ})
svc.on_finished_run(ЗАГРУЗЧИК, 0)
r.case('учёт пуст', svc.launched, {})
r.case('снова свободно', svc._busy(), None)

r.head('живая игра ловится перечислением процессов')
# вместо игры подставлен заведомо живой процесс - сам интерпретатор
svc = make(game=os.path.basename(sys.executable))
busy = svc._busy()
r.case('занято', busy is not None, True)
if busy:
    r.case('назван процесс', (busy['app'] or '').lower(),
           os.path.basename(sys.executable).lower())
    r.case('опознан как игра', busy['isGame'], True)
    r.case('MO2 о нём не сообщала', busy['viaMO2'], False)

r.head('свой запуск снимается с учёта, когда процесс умер')
r.case('учёт очистился', make({r'C:\нет\такого.exe': СВОЙ})._busy(), None)
r.case('чужой при том же условии остаётся',
       make({r'C:\нет\такого.exe': ЧУЖОЙ})._busy() is not None, True)

r.done()
