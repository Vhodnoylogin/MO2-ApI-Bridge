# -*- coding: utf-8 -*-
"""Failure and retry contracts over scratch files; no live MO2 is contacted."""
import os
import sys
from importlib import import_module
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_mo2
fake_mo2.install()
import common
services = import_module(common.NAME + '.services')
i18n = import_module(common.NAME + '.i18n')
base = import_module(common.NAME + '.base')
r = common.Report(common.T('mutation.title'))


def check(key, actual, expected=True):
    r.case(common.T('mutation.' + key), actual, expected)


def make():
    fx = fake_mo2.Fixture()
    svc = services.Services(fx.organizer, run_main=lambda f, timeout=None: f(),
                            docs_path='README.md')
    svc.guard.refusal = lambda *args: None
    return fx, svc


fx, svc = make()
try:
    original = fx.read_bytes('profiles', 'Claude', 'plugins.txt')
    body = {'set': {'Beta.esp': True}, 'apply': True,
            'expectedServerBootId': svc.ctx.boot_id, 'idempotencyKey': 'locked'}
    with patch(common.NAME + '.loadorder.os.replace', side_effect=PermissionError('held')):
        code, result = svc.ctx.operations.invoke('/plugins/state', body, svc.plugins_state)
        code2, retry = svc.ctx.operations.invoke('/plugins/state', body, svc.plugins_state)
    check('persistenceFailed', (code, result['applied'], result['operation']['state']),
          (500, False, 'failed'))
    check('partialDisclosed', (result['partial'], result['persisted'],
                              result['memoryApplied'], result['reconciliationRequired']),
          (True, False, True, True))
    check('originalIntact', fx.read_bytes('profiles', 'Claude', 'plugins.txt'), original)
    check('retrySameFailure', (code2, retry['operation']['id']), (500, result['operation']['id']))
    check('draftRemoved', [p for p in os.listdir(os.path.dirname(fx.plugins_txt()))
                           if p.startswith('.mo2aibridge-')], [])
    healed = svc.plugins_state({'set': {'Beta.esp': True}, 'apply': True})
    check('retryReconciles', (healed['changes'], healed['persisted'],
                             b'*Beta.esp' in fx.read_bytes('profiles', 'Claude', 'plugins.txt')),
          ([], True, True))
    backup_bytes = open(fx.plugins_txt() + '.bak', 'rb').read()
    converged = svc.plugins_state({'set': {'Beta.esp': True}, 'apply': True})
    check('backupPreserved', (converged['file']['unchanged'],
                             open(fx.plugins_txt() + '.bak', 'rb').read() == backup_bytes),
          (True, True))
    with open(fx.plugins_txt(), 'wb') as out:
        out.write(b'*Alpha.esp\r\n*Gamma.esl\r\n')
    missing = svc.plugins_state({'set': {'Beta.esp': True}, 'apply': True})
    check('missingNotSuccess', (missing['applied'], missing['failed'], missing['file']['notInFile']),
          (False, True, ['beta.esp']))
finally:
    fx.cleanup()


def stage(fx):
    root = fx.path('staging')
    os.makedirs(root)
    with open(os.path.join(root, 'payload.txt'), 'wb') as out:
        out.write(b'new payload')
    return root


fx, svc = make()
try:
    unpacked = stage(fx)
    dummy = fx.path('dummy.7z')
    with open(dummy, 'wb') as out:
        out.write(b'fixture')
    svc.installer._unpack = lambda arc: unpacked
    refreshes = []
    original_refresh = fx.organizer.refresh
    def refresh(*args):
        refreshes.append(args)
        return original_refresh(*args)
    fx.organizer.refresh = refresh
    def broken_copy(tmp, paths, target, progress=None):
        with open(os.path.join(target, 'partial.txt'), 'wb') as out:
            out.write(b'first file')
        raise PermissionError('second file held')
    svc.installer._copy = broken_copy
    code, res = svc.ctx.operations.invoke('/install',
        {'archive': dummy, 'name': 'Alpha Mod', 'mode': 'merge'}, svc.install)
    check('copyFailureReported', (code, res['failed'], res['partial'], res['applied']),
          (500, True, True, False))
    check('partialRefreshed', (len(refreshes), res['refreshRequested']), (1, True))
    check('partialEvidence', (os.path.isfile(os.path.join(fx.mod_path('Alpha Mod'), 'partial.txt')),
                              res['addedCount'] > 0), (True, True))
finally:
    fx.cleanup()

for target, mode in [('Alpha Mod', 'replace'), ('New Mod', '')]:
    fx, svc = make()
    try:
        unpacked = stage(fx)
        dummy = fx.path('dummy.7z')
        with open(dummy, 'wb') as out:
            out.write(b'fixture')
        svc.installer._unpack = lambda arc: unpacked
        before = sorted(fake_mo2.tree_files(fx.mod_path('Alpha Mod')))
        with patch.object(svc.installer, '_wipe') as wipe:
            try:
                svc.install({'archive': dummy, 'name': target, 'mode': mode,
                             'paths': ['', 'missing'], base.DANGER_KEY: base.DANGER_VALUE})
                refused = False
            except ValueError as exc:
                refused = str(exc) == i18n.t('err.noPathInArchive', path='missing')
        check('selectionBeforeWrites', (refused, wipe.call_count,
               sorted(fake_mo2.tree_files(fx.mod_path('Alpha Mod'))) == before,
               os.path.exists(fx.mod_path('New Mod'))), (True, 0, True, False))
    finally:
        fx.cleanup()

fx, svc = make()
try:
    checks = []
    busy = {'value': False}
    def refusal(*args):
        checks.append(busy['value'])
        return {'applied': False, 'busy': True} if busy['value'] else None
    svc.guard.refusal = refusal
    def dispatch(fn, timeout=None):
        busy['value'] = True
        return fn()
    svc.modops.run_main = dispatch
    res = svc.toggle({'mod': 'Alpha Mod', 'active': False})
    check('dequeueRechecks', (checks, res['applied'],
                             bool(fx.organizer.modList().state('Alpha Mod') & 2)),
          ([False, True], False, True))
finally:
    fx.cleanup()

for nested in (False, True):
    for target, mode in [('Alpha Mod', 'replace'), ('New Mod', '')]:
        fx, svc = make()
        try:
            unpacked = stage(fx)
            denied = os.path.join(unpacked, 'nested') if nested else unpacked
            if nested:
                os.mkdir(denied)
            dummy = fx.path('dummy.7z')
            with open(dummy, 'wb') as out:
                out.write(b'fixture')
            svc.installer._unpack = lambda arc: unpacked
            scandir = os.scandir
            def denied_scan(path):
                if os.path.normcase(os.fspath(path)) == os.path.normcase(denied):
                    raise PermissionError('unreadable selection')
                return scandir(path)
            with patch('os.scandir', side_effect=denied_scan), patch.object(svc.installer, '_wipe') as wipe:
                try:
                    svc.install({'archive': dummy, 'name': target, 'mode': mode,
                                 base.DANGER_KEY: base.DANGER_VALUE})
                    refused = False
                except PermissionError:
                    refused = True
            check('unreadableSelection', (refused, wipe.call_count,
                                          os.path.exists(fx.mod_path('New Mod'))),
                  (True, 0, False))
        finally:
            fx.cleanup()

fx, svc = make()
try:
    unpacked = stage(fx)
    dummy = fx.path('dummy.7z')
    with open(dummy, 'wb') as out:
        out.write(b'fixture')
    svc.installer._unpack = lambda arc: unpacked
    original_tree = import_module(common.NAME + '.install').tree_files
    copied = {'value': False}
    def broken_copy(tmp, paths, target, progress=None):
        copied['value'] = True
        raise PermissionError('original copy error')
    def broken_inventory(path):
        if copied['value']:
            raise PermissionError('inventory unavailable')
        return original_tree(path)
    svc.installer._copy = broken_copy
    with patch(common.NAME + '.install.tree_files', side_effect=broken_inventory):
        res = svc.install({'archive': dummy, 'name': 'Alpha Mod', 'mode': 'merge'})
    check('inventoryFailureRetained', (res['error'], res['inventoryKnown'], res['filesAfter'],
                                       res['partial'], res['refreshRequested']),
          ('original copy error', False, None, True, True))
finally:
    fx.cleanup()

for fail_at in (1, 2):
    fx, svc = make()
    try:
        unpacked = stage(fx)
        os.remove(os.path.join(unpacked, 'payload.txt'))
        os.mkdir(os.path.join(unpacked, 'textures'))
        for rel in ['Alpha.esp', os.path.join('textures', 'alpha.dds'), 'meta.ini']:
            with open(os.path.join(unpacked, rel), 'wb') as out:
                out.write(b'new bytes')
        dummy = fx.path('dummy.7z')
        with open(dummy, 'wb') as out:
            out.write(b'fixture')
        svc.installer._unpack = lambda arc: unpacked
        import shutil
        copy = shutil.copy2
        calls = []
        def held_copy(src, dst):
            calls.append(dst)
            if len(calls) == fail_at:
                raise PermissionError('held destination')
            return copy(src, dst)
        with patch(common.NAME + '.install.shutil.copy2', side_effect=held_copy):
            res = svc.install({'archive': dummy, 'name': 'Alpha Mod', 'mode': 'merge'})
        expected = ([], ['alpha.esp']) if fail_at == 1 else (['alpha.esp'], [os.path.join('textures', 'alpha.dds')])
        check('overwriteEvidence', (res['overwritten'], res['possibleOverwritten']), expected)
    finally:
        fx.cleanup()

r.done()
