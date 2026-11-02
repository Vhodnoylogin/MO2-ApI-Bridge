"""Regression checks for release 2.3.4 over disposable files and mocked native APIs."""
import os
import sys
from importlib import import_module
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_mo2
fake_mo2.install()
import common

services = import_module(common.NAME + '.services')
base = import_module(common.NAME + '.base')
winapi = import_module(common.NAME + '.winapi')
r = common.Report(common.T('patch234.title'))


def check(label, actual, expected=True):
    r.case(common.T('patch234.' + label), actual, expected)


def make():
    fx = fake_mo2.Fixture()
    svc = services.Services(fx.organizer, lambda fn, timeout=None: fn(), 'README.md')
    svc.guard.refusal = lambda *_: None
    return fx, svc


fx, svc = make()
try:
    archive = os.path.join(fx.organizer.downloadsPath(), fx.organizer.modList().getMod('Alpha Mod').installationFile())
    old_archive = open(archive, 'rb').read()
    with patch.object(fx.organizer.modList(), 'removeMod', return_value=False), \
            patch(common.NAME + '.mods.winapi.recycle', return_value=True) as recycle:
        code, result = svc.ctx.operations.invoke('/mods/remove',
            {'mod': 'Alpha Mod', 'withArchive': True, base.DANGER_KEY: base.DANGER_VALUE}, svc.mods_remove)
    check('removeFailure', (code, result['failed'], result['operation']['state']), (500, True, 'failed'))
    check('archiveKept', (recycle.call_count, result['archiveKept'], result['archiveRecycled'],
                          open(archive, 'rb').read() == old_archive), (0, True, False, True))
finally:
    fx.cleanup()

for value, expected in [('false', False), ('0', False), ('off', False), ('no', False),
                        (False, False), (0, False), (True, True), (1, True), ('true', True)]:
    fx, svc = make()
    try:
        result = svc.plugins_state({'set': {'Beta.esp': value}, 'apply': True})
        check('normalizedState', (result['persisted'], bool(fx.organizer.pluginList().state('Beta.esp') & fake_mo2.PluginState.ACTIVE),
              b'*Beta.esp' in fx.read_bytes('profiles', 'Claude', 'plugins.txt')), (True, expected, expected))
    finally:
        fx.cleanup()

for value in [None, [], {}, 2, 'unknown']:
    fx, svc = make()
    try:
        before = fx.read_bytes('profiles', 'Claude', 'plugins.txt')
        try:
            svc.plugins_state({'set': {'Alpha.esp': False, 'Beta.esp': value}, 'apply': True})
            refused = False
        except ValueError:
            refused = True
        check('batchValidated', (refused, bool(fx.organizer.pluginList().state('Alpha.esp') & fake_mo2.PluginState.ACTIVE),
              fx.read_bytes('profiles', 'Claude', 'plugins.txt') == before), (True, True, True))
    finally:
        fx.cleanup()

fx, svc = make()
try:
    svc.plugins_state({'set': {'Beta.esp': True}, 'apply': True})
    filename = fx.plugins_txt()
    before, backup = open(filename, 'rb').read(), open(filename + '.bak', 'rb').read()
    with patch(common.NAME + '.loadorder.os.replace', side_effect=PermissionError('held')):
        failed = svc.plugins_state({'set': {'Alpha.esp': False}, 'apply': True})
    check('failedWriteBackup', (failed['failed'], open(filename, 'rb').read() == before,
          open(filename + '.bak', 'rb').read() == backup), (True, True, True))
    check('failedDraftCleanup', [n for n in os.listdir(os.path.dirname(filename)) if n.startswith('.mo2aibridge-')], [])
    real_replace = os.replace
    def alias_fails(source, destination):
        if destination == filename + '.bak':
            raise PermissionError('backup alias held')
        return real_replace(source, destination)
    with patch(common.NAME + '.loadorder.os.replace', side_effect=alias_fails):
        repaired = svc.plugins_state({'set': {'Alpha.esp': False}, 'apply': True})
    recovery = repaired['file']['backup']
    check('backupAliasFailure', (repaired['persisted'], repaired['file']['backupPublished'],
          open(recovery, 'rb').read() == before, open(filename + '.bak', 'rb').read() == backup), (True, False, True, True))
finally:
    fx.cleanup()

fx, svc = make()
try:
    baseline = svc.analyze({'name': 'Alpha Mod'})
    check('analysisComplete', (baseline['complete'], baseline['conflicts']['complete']), (True, True))
    with patch.object(svc.reader, 'mod', side_effect=RuntimeError('card unavailable')):
        card_error = svc.analyze({'name': 'Alpha Mod'})
    check('cardIncomplete', (card_error['complete'], card_error['partial'], card_error['errorCount'],
          card_error['conflicts']['complete'], card_error['errors'][0]['phase']), (False, True, 1, True, 'card'))
    with patch.object(fx.organizer, 'getFileOrigins', side_effect=RuntimeError('origins unavailable')):
        failed = svc.analyze({'name': 'Alpha Mod'})
        exported = svc.vfsexport({'outDir': fx.path('export')})
    check('originsIncomplete', (failed['complete'], failed['partial'], failed['conflicts']['complete'],
          failed['conflicts']['succeeded'], failed['conflicts']['failed'] > 0), (False, True, False, 0, True))
    check('exportIncomplete', (exported['complete'], exported['partial'], exported['errorCount'] > 0), (False, True, True))
    def unreadable(root, onerror=None):
        onerror(PermissionError('inventory unavailable'))
        return iter([])
    with patch(common.NAME + '.reading.os.walk', side_effect=unreadable):
        inventory = svc.analyze({'name': 'Alpha Mod', 'conflicts': '0'})
    check('inventoryIncomplete', (inventory['inventoryComplete'], inventory['complete'], inventory['errorCount']), (False, False, 1))
    with patch.object(fx.organizer, 'listDirectories', side_effect=RuntimeError('directories unavailable')):
        walked = svc.vfsexport({'outDir': fx.path('walk-export'), 'legacyWalk': True})
    check('fallbackIncomplete', (walked['complete'], walked['errorCount'] > 0), (False, True))
finally:
    fx.cleanup()

fx, svc = make()
try:
    key = svc.ctx.boot_id + ':probe'
    svc.launcher.procs[key] = (None, 123, 'test')
    svc.launcher.identities[key] = {'pid': 123, 'creationTime': 10, 'path': 'test.exe'}
    with patch(common.NAME + '.launch.winapi.identity_alive', return_value=False), \
            patch(common.NAME + '.launch.winapi.windows_of', return_value=[{'hwnd': 456}]) as windows:
        stale = svc.windows({'key': key})
        check('staleKey', (stale['windows'], windows.call_count), ([], 0))
    with patch(common.NAME + '.launch.winapi.identity_alive', side_effect=[True, False]), \
            patch(common.NAME + '.launch.winapi.windows_of', return_value=[{'hwnd': 456}]):
        check('identityChanged', svc.windows({'key': key})['windows'], [])
    with patch(common.NAME + '.launch.winapi.is_window', return_value=True), \
            patch(common.NAME + '.launch.winapi.identity_alive', return_value=True), \
            patch(common.NAME + '.launch.winapi.window_pid', return_value=999), \
            patch(common.NAME + '.launch.winapi.close') as close:
        try:
            svc.window({'key': key, 'hwnd': 456, 'action': 'close'})
            refused = False
        except ValueError:
            refused = True
        check('windowOwner', (refused, close.call_count), (True, 0))
    with patch(common.NAME + '.launch.winapi.windows_of', return_value=[{'hwnd': 456}]):
        check('rawPidCompatible', svc.windows({'pid': '999'})['windows'], [{'hwnd': 456}])
    for action in ['close', 'click']:
        state = {'alive': True}
        posted = []
        def title(_):
            if action == 'close':
                state['alive'] = False
            return 'test'
        def children(_):
            state['alive'] = False
            return [{'hwnd': 789, 'class': 'Button', 'text': 'OK'}]
        with patch.object(winapi, 'is_window', return_value=True), \
                patch.object(winapi, 'identity_alive', side_effect=lambda _: state['alive']), \
                patch.object(winapi, 'window_pid', return_value=123), \
                patch.object(winapi, 'window_text', side_effect=title), \
                patch.object(winapi, 'children', side_effect=children), \
                patch.object(winapi, 'user32', SimpleNamespace(PostMessageW=lambda *args: posted.append(args))):
            try:
                svc.window({'key': key, 'hwnd': 456, 'action': action, 'button': 'OK'})
                refused = False
            except ValueError:
                refused = True
            check('beforePostIdentity', (refused, posted), (True, []))
    posted = []
    with patch.object(winapi, 'is_window', return_value=True), \
            patch.object(winapi, 'identity_alive', return_value=True), \
            patch.object(winapi, 'window_pid', side_effect=lambda hwnd: 123 if hwnd == 456 else 999), \
            patch.object(winapi, 'window_text', return_value='test'), \
            patch.object(winapi, 'children', return_value=[{'hwnd': 789, 'class': 'Button', 'text': 'OK'}]), \
            patch.object(winapi, 'user32', SimpleNamespace(PostMessageW=lambda *args: posted.append(args))):
        try:
            svc.window({'key': key, 'hwnd': 456, 'action': 'click', 'button': 'OK'})
            refused = False
        except ValueError:
            refused = True
        check('childOwner', (refused, posted), (True, []))
finally:
    fx.cleanup()

r.done()
