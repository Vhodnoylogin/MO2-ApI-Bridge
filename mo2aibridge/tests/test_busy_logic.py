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
r = common.Report(T('busy.title'))

ЧУЖОЙ = {'n': 1, 'mine': False}   # запуск не наш: MO2 сама сообщит о завершении
СВОЙ = {'n': 1, 'mine': True}     # наш запуск: снимаем с учёта, когда процесс умрёт
ЗАГРУЗЧИК = r'C:\mods\SKSEVR\Root\sksevr_loader.exe'


def make(launched=None, game='НетТакогоПроцесса.exe'):
    svc = services.Services(object(), lambda f, timeout=None: f(), 'docs')
    svc.game_exe = game.lower()
    svc._self_hwnd = 0            # окно MO2 не ищем: менеджер не запущен
    svc.launched = {k: dict(v) for k, v in (launched or {}).items()}
    return svc


r.head(T('busy.nothingRunning'))
r.case(T('busy.free'), make()._busy(), None)

r.head(T('busy.heldByNameless'))
busy = make({ЗАГРУЗЧИК: ЧУЖОЙ})._busy()
r.case(T('busy.busyNotFree'), busy is not None, True)
if busy:
    r.case(T('busy.mo2ThinksActive'), busy['viaMO2'], True)
    r.case(T('busy.noLiveProcess'), busy['app'], None)
    r.case(T('busy.heldByUnknown'), busy['heldByUnknown'], True)
    r.case(T('busy.mo2LaunchVisible'), busy['mo2Run'], [ЗАГРУЗЧИК])
    r.case(T('busy.notTheGame'), busy['isGame'], False)

r.head(T('busy.foreignRecordKept'))
svc = make({ЗАГРУЗЧИК: ЧУЖОЙ})
svc._busy()
svc._busy()
r.case(T('busy.intactAfterTwo'), sorted(svc.launched), [ЗАГРУЗЧИК])

r.head(T('busy.writeRefuses'))
stop = make({ЗАГРУЗЧИК: ЧУЖОЙ})._blocked_while_busy('op.toggle')
r.case(T('busy.refusalIssued'), stop is not None, True)
if stop:
    r.case(T('busy.nothingApplied'), stop['applied'], False)
    r.case(T('busy.markedBusy'), stop['busy'], True)
    r.case(T('busy.operationNamed'), stop['blocked'], 'включение или выключение мода')
    r.case(T('busy.namelessExplained'), 'не сообщила о завершении' in stop['why'], True)

r.head(T('busy.finishedClears'))
svc = make({ЗАГРУЗЧИК: ЧУЖОЙ})
svc.on_finished_run(ЗАГРУЗЧИК, 0)
r.case(T('busy.recordEmpty'), svc.launched, {})
r.case(T('busy.freeAgain'), svc._busy(), None)

r.head(T('busy.gameCaughtByEnum'))
# вместо игры подставлен заведомо живой процесс - сам интерпретатор
svc = make(game=os.path.basename(sys.executable))
busy = svc._busy()
r.case(T('busy.busy'), busy is not None, True)
if busy:
    r.case(T('busy.processNamed'), (busy['app'] or '').lower(),
           os.path.basename(sys.executable).lower())
    r.case(T('busy.recognisedAsGame'), busy['isGame'], True)
    r.case(T('busy.mo2SilentAboutIt'), busy['viaMO2'], False)

r.head(T('busy.ownRunRetired'))
r.case(T('busy.recordCleared'), make({r'C:\нет\такого.exe': СВОЙ})._busy(), None)
r.case(T('busy.foreignStays'),
       make({r'C:\нет\такого.exe': ЧУЖОЙ})._busy() is not None, True)

r.done()
