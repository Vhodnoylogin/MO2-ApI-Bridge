# -*- coding: utf-8 -*-
"""Общее для проверок: где лежит плагин, как до него достучаться, как отчитаться.

Ни одного пути к конкретной машине здесь нет. Корень модуля выводится от расположения этого
файла, папка плагина ищется рядом по наличию __init__.py, имя файла с токеном - от имени этой
папки, порт берётся из окружения. Поэтому проверки едут вместе с модом и работают у того, кто
его склонировал.
"""
import io
import json
import os
import sys
import types
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _find_package():
    """Папка самого плагина - соседняя с tests, та, где лежит __init__.py.

    Ищем, а не складываем из имени: корень модуля и пакет называются одинаково лишь по
    соглашению, а проверки не должны зависеть от соглашений."""
    for name in sorted(os.listdir(ROOT)):
        if os.path.isfile(os.path.join(ROOT, name, '__init__.py')):
            return os.path.join(ROOT, name)
    raise SystemExit('рядом с tests нет папки с __init__.py - где плагин?')


PKG = _find_package()
NAME = os.path.basename(PKG)
TOKEN_FILE = os.path.join(PKG, NAME + '-token.txt')
PORT = int(os.environ.get('MO2AIBRIDGE_PORT') or 8930)
BASE = 'http://127.0.0.1:%d' % PORT


def token():
    """Токен запущенного плагина, или None, если MO2 не поднята."""
    try:
        return io.open(TOKEN_FILE, encoding='utf-8').read().strip() or None
    except Exception:
        return None


def call(method, route, body=None, timeout=120):
    """Запрос к мосту. Возвращает (код, разобранный ответ)."""
    data = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib.request.Request(BASE + route, data=data, method=method,
                                 headers={'X-Token': token() or '',
                                          'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode('utf-8'))


def alive():
    """Отвечает ли мост прямо сейчас."""
    if not token():
        return False
    try:
        return call('GET', '/ping', timeout=20)[1].get('ok') is True
    except Exception:
        return False


def need_live():
    """Прекратить с внятным словом, если MO2 не запущена."""
    if not alive():
        print('  ПРОПУСК: мост не отвечает на %s - MO2 не запущена?' % BASE)
        raise SystemExit(77)


def import_package(with_mobase=False):
    """Импортировать сам плагин.

    mobase живёт только внутри процесса MO2. Модулям нижних слоёв он не нужен вовсе, а
    services упоминает его лишь на уровне импорта, поэтому для проверки логики достаточно
    пустышки - и тогда проверка идёт без запущенного менеджера.
    """
    if not with_mobase and 'mobase' not in sys.modules:
        sys.modules['mobase'] = types.ModuleType('mobase')
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    return __import__(NAME, fromlist=['services', 'i18n', 'winapi'])


class Report(object):
    """Счёт проверок и печать в одном виде для всех наборов."""

    def __init__(self, title):
        self.ok = self.bad = 0
        print('=== %s ===' % title)

    def head(self, text):
        print()
        print('--- %s' % text)

    def case(self, label, got, want):
        good = got == want
        self.ok, self.bad = self.ok + int(good), self.bad + int(not good)
        print('  %-4s %-50s %s' % ('OK' if good else 'СБОЙ', label,
                                   got if good else 'получено %r, ждали %r' % (got, want)))
        return good

    def note(self, label, value):
        print('  %-4s %-50s %s' % ('', label, value))

    def done(self):
        print()
        print('ИТОГО: успешно %d, сбоев %d' % (self.ok, self.bad))
        raise SystemExit(1 if self.bad else 0)
