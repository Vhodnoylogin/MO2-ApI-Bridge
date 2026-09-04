# -*- coding: utf-8 -*-
"""Прогнать все проверки подряд.

Логика занятости идёт без MO2. Остальные три требуют запущенного менеджера и пропускаются,
если мост не отвечает: пропуск - это не сбой, а честное «проверить было нечем».
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SUITES = ['test_busy_logic.py', 'test_routes.py', 'test_plugins_txt.py',
           'test_install_modes.py', 'test_busy_live.py']
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
print('наборов: %d, со сбоями: %d, пропущено: %d' % (len(SUITES), bad, skipped))
sys.exit(1 if bad else 0)
