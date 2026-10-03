# -*- coding: utf-8 -*-
"""Run every check in turn.

Six suites run without MO2: source hygiene, the busy logic, the strings, the update rules,
the contract of every route against a fake mobase, and the transport on a real loopback
socket. The other four need a running manager and are skipped when the bridge does not
answer: a skip is not a failure but an honest "there was nothing to check against".

The offline suites come first on purpose: they see the code lying on disk, while the
acceptance ones see the code MO2 loaded when it started. Until the manager is restarted
those are different versions.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common  # noqa: E402
SUITES = ['test_sources.py', 'test_busy_logic.py', 'test_i18n.py', 'test_policy.py',
          'test_contract_offline.py', 'test_transport_offline.py',
          'test_routes.py', 'test_plugins_txt.py', 'test_install_modes.py',
          'test_busy_live.py']
SKIPPED = 77

bad = skipped = 0
for name in SUITES:
    print()
    print('=' * 78)
    code = subprocess.call([sys.executable, os.path.join(HERE, name)])
    if code == SKIPPED:
        skipped += 1
    elif code:
        bad += 1

print()
print('=' * 78)
print(common.T('report.suites', total=len(SUITES), bad=bad, skipped=skipped))
sys.exit(1 if bad else 0)
