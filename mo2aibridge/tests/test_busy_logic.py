# -*- coding: utf-8 -*-
"""The busy logic, without a running MO2.

A regression check for a defect that switched the protection off by itself: the busy check
cleared its own launch bookkeeping when it found no live process of a familiar name in the
system. But MO2 reports completion itself, and until it has, its lock still holds - so the
record was not rubbish but the only remaining evidence.

It is caught on a fake IOrganizer precisely so that the logic is visible rather than the
behaviour of one particular build of the manager.
"""
import ctypes  # noqa: F401  - before the dll paths are stubbed, or _ctypes will not load
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

T = common.T

pkg = common.import_package()
services, i18n = pkg.services, pkg.i18n
i18n.set_language('ru')
r = common.Report(T('busy.title'))

FOREIGN = {'n': 1, 'mine': False}  # not our launch: MO2 will report its completion
OURS = {'n': 1, 'mine': True}      # our launch: retired from the books once it dies
LOADER = r'C:\mods\SKSEVR\Root\sksevr_loader.exe'


def make(launched=None, game='NoSuchProcess.exe'):
    svc = services.Services(object(), lambda f, timeout=None: f(), 'docs')
    svc.game_exe = game.lower()
    svc._self_hwnd = 0            # no MO2 window to look for: the manager is not running
    svc.launched = {k: dict(v) for k, v in (launched or {}).items()}
    return svc


r.head(T('busy.nothingRunning'))
r.case(T('busy.free'), make()._busy(), None)

r.head(T('busy.heldByNameless'))
busy = make({LOADER: FOREIGN})._busy()
r.case(T('busy.busyNotFree'), busy is not None, True)
if busy:
    r.case(T('busy.mo2ThinksActive'), busy['viaMO2'], True)
    r.case(T('busy.noLiveProcess'), busy['app'], None)
    r.case(T('busy.heldByUnknown'), busy['heldByUnknown'], True)
    r.case(T('busy.mo2LaunchVisible'), busy['mo2Run'], [LOADER])
    r.case(T('busy.notTheGame'), busy['isGame'], False)

r.head(T('busy.foreignRecordKept'))
svc = make({LOADER: FOREIGN})
svc._busy()
svc._busy()
r.case(T('busy.intactAfterTwo'), sorted(svc.launched), [LOADER])

r.head(T('busy.writeRefuses'))
stop = make({LOADER: FOREIGN})._blocked_while_busy('op.toggle')
r.case(T('busy.refusalIssued'), stop is not None, True)
if stop:
    r.case(T('busy.nothingApplied'), stop['applied'], False)
    r.case(T('busy.markedBusy'), stop['busy'], True)
    # Compared against the key, not against a phrase: a verbatim text breaks the
    # moment the language changes.
    r.case(T('busy.operationNamed'), stop['blocked'], i18n.t('op.toggle'))
    r.case(T('busy.namelessExplained'),
           stop['why'] == i18n.t('busy.whyUnknown', run=LOADER), True)

r.head(T('busy.finishedClears'))
svc = make({LOADER: FOREIGN})
svc.on_finished_run(LOADER, 0)
r.case(T('busy.recordEmpty'), svc.launched, {})
r.case(T('busy.freeAgain'), svc._busy(), None)

r.head(T('busy.gameCaughtByEnum'))
# a certainly-live process stands in for the game - the interpreter itself
svc = make(game=os.path.basename(sys.executable))
busy = svc._busy()
r.case(T('busy.busy'), busy is not None, True)
if busy:
    r.case(T('busy.processNamed'), (busy['app'] or '').lower(),
           os.path.basename(sys.executable).lower())
    r.case(T('busy.recognisedAsGame'), busy['isGame'], True)
    r.case(T('busy.mo2SilentAboutIt'), busy['viaMO2'], False)

r.head(T('busy.ownRunRetired'))
r.case(T('busy.recordCleared'), make({r'C:\no\such.exe': OURS})._busy(), None)
r.case(T('busy.foreignStays'),
       make({r'C:\no\such.exe': FOREIGN})._busy() is not None, True)

r.done()
