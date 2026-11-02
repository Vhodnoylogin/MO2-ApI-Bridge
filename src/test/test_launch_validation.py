# -*- coding: utf-8 -*-
"""Launch validation over fake MO2 and mocked process APIs; starts no programs."""
import os
import sys
from contextlib import ExitStack
from importlib import import_module
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_mo2
fake_mo2.install()
import common

services = import_module(common.NAME + '.services')
base = import_module(common.NAME + '.base')
winapi = import_module(common.NAME + '.winapi')
r = common.Report(common.T('launch.title'))


def check(key, actual, expected=True):
    r.case(common.T('launch.' + key), actual, expected)


fx = fake_mo2.Fixture()
try:
    svc = services.Services(fx.organizer, lambda fn, timeout=None: fn(), 'README.md')
    svc.guard.refusal = lambda *args: None
    svc.guard.game_exe = 'game.exe'
    fx.organizer.start_result = 123
    with ExitStack() as mocks:
        mocks.enter_context(patch.object(winapi, 'duplicate_process_handle', return_value=456))
        mocks.enter_context(patch.object(winapi, 'process_id', return_value=42))
        mocks.enter_context(patch.object(winapi, 'process_identity', return_value=None))
        close = mocks.enter_context(patch.object(winapi, 'close_process_handle'))
        wait = mocks.enter_context(patch.object(winapi, 'wait_process', return_value=0))
        template = {'binary': 'FakeTool.exe', 'wait': True,
                    base.DANGER_KEY: base.DANGER_VALUE}
        for timeout in ('invalid', ['invalid'], {'invalid': True}, float('nan'),
                        float('inf'), '-inf', 1e308, ((1 << 32) - 1) / 1000):
            before = len(fx.organizer.started)
            code, result = svc.ctx.operations.invoke('/run', dict(template, timeout=timeout), svc.run)
            check('invalidBeforeStart', (code, result['code'], len(fx.organizer.started)),
                  (400, 'badRequest', before))

        svc.guard.refusal = lambda *args: {'applied': False, 'busy': True}
        code, result = svc.ctx.operations.invoke('/run', dict(template, timeout='invalid'), svc.run)
        check('busyFirst', (code, result['reason'], len(fx.organizer.started)), (200, 'busy', 0))
        svc.guard.refusal = lambda *args: None
        code, result = svc.ctx.operations.invoke('/run', {'binary': 'FakeTool.exe', 'wait': True}, svc.run)
        check('dangerBeforeStart', (code, result['reason'], len(fx.organizer.started)), (200, 'danger', 0))
        code, result = svc.ctx.operations.invoke('/run', dict(template, expectedProfile='Other'), svc.run)
        check('expectedBeforeStart', (code, result['code'], len(fx.organizer.started)), (400, 'badRequest', 0))

        checks = iter((None, {'applied': False, 'busy': True}))
        svc.guard.refusal = lambda *args: next(checks)
        code, result = svc.ctx.operations.invoke('/run', template, svc.run)
        check('dequeueBusy', (code, result['reason'], len(fx.organizer.started)), (200, 'busy', 0))
        svc.guard.refusal = lambda *args: None

        for timeout, expected in ((None, 3600.0), (0, 3600.0), ('2.5', 2.5),
                                  (-1, 0.0), (-1e308, 0.0),
                                  (((1 << 32) - 2) / 1000, ((1 << 32) - 2) / 1000)):
            code, result = svc.ctx.operations.invoke('/run', dict(template, timeout=timeout), svc.run)
            check('waitSemantics', (code, wait.call_args.args[1], result['exit']), (200, expected, 0))
        wait.reset_mock()
        code, result = svc.ctx.operations.invoke('/run', dict(template, wait=False, timeout='invalid'), svc.run)
        check('noWaitIgnored', (code, result['started'], wait.called), (200, True, False))

        body = dict(template, timeout='0', argv=['path with spaces', '', 'quote"value'],
                    idempotencyKey='single-start', expectedServerBootId=svc.ctx.boot_id)
        before = len(fx.organizer.started)
        code, first = svc.ctx.operations.invoke('/run', body, svc.run)
        again, second = svc.ctx.operations.invoke('/run', body, svc.run)
        check('retrySingleStart', (code, again, len(fx.organizer.started) - before,
                                  first['operation']['id'] == second['operation']['id']),
              (200, 200, 1, True))
        check('argvPreserved', (first['argv'], fx.organizer.started[-1][1]),
              (body['argv'], services.launch.Launcher.arguments(body)))
        svc.launcher.close()
        check('handleOwned', close.call_args.args, (456,))
finally:
    fx.cleanup()

r.done()
