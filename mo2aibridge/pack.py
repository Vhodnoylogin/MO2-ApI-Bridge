# -*- coding: utf-8 -*-
r"""Собрать архив плагина для выкладки.

Зачем отдельный шаг. Раскладка для разработки - связь или `xcopy /E` всей папки пакета,
и для выкладки она не годится: рядом с кодом живут файл токена, лог и настройки, которые
плагин создаёт при каждом запуске, плюс `__pycache__` от каждого импорта. Рекурсивное
копирование их не различает, и в архив уезжают чужой токен, чужой лог с путями машины
автора и замороженные настройки - в том числе пути к 7-Zip, которых у другого человека
может не быть. `.gitignore` от этого не спасает: он про git, а не про копирование файлов.

Поэтому здесь перечислено то, что уезжает, а не то, что исключается. Забытый файл тогда
означает «не попал в архив», а не «попал чужой»: цена первой ошибки - сообщение о поломке,
цена второй - утечка.

Папка внутри архива называется именем, под которым плагин известен MO2 (PLUGIN_ID), а не
так, как зовётся в репозитории: Python берёт имя пакета из имени папки, и `plugin_mo2aibridge`
дал бы пакет, которого MO2 не ищет.

    python pack.py [куда]

Без аргумента архив кладётся в `dist\` рядом с модулем. Путей к конкретной машине здесь нет:
корень выводится от расположения этого файла, версия и имя - у самого плагина.
"""
import io
import os
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))

# Что уезжает. Всё остальное - не уезжает, каким бы нужным ни выглядело.
SHIP_SUFFIX = ('.py',)
SHIP_EXACT = ('README.md', 'README.en.md', 'LICENSE')


def find_package():
    """Папка пакета - соседняя, та, где лежит __init__.py. Ищем, а не складываем из имени:
    имя папки в репозитории и имя пакета в MO2 намеренно разные."""
    for name in sorted(os.listdir(ROOT)):
        if os.path.isfile(os.path.join(ROOT, name, '__init__.py')):
            return os.path.join(ROOT, name)
    raise SystemExit('рядом с pack.py нет папки с __init__.py - где плагин?')


def plugin_id_and_version(pkg):
    """Имя для MO2 и номер версии - у самого плагина, чтением __init__.py.

    Именно чтением, а не импортом: импорт потянет PyQt6 и mobase, которых вне процесса
    менеджера нет, и сборка стала бы возможна только внутри MO2.
    """
    text = io.open(os.path.join(pkg, '__init__.py'), encoding='utf-8').read()
    out = {}
    for line in text.split(chr(10)):
        for key in ('PLUGIN_ID', '__version__'):
            if line.startswith(key):
                out[key] = line.split('=', 1)[1].strip().strip('"').strip("'")
    if not out.get('PLUGIN_ID') or not out.get('__version__'):
        raise SystemExit('в __init__.py не нашлись PLUGIN_ID и __version__')
    return out['PLUGIN_ID'], out['__version__']


def collect(pkg):
    """Файлы пакета, которые уезжают. Возвращает (берём, оставляем)."""
    take, leave = [], []
    for name in sorted(os.listdir(pkg)):
        full = os.path.join(pkg, name)
        if os.path.isdir(full):
            leave.append(name + os.sep)
            continue
        if name in SHIP_EXACT or name.endswith(SHIP_SUFFIX):
            take.append(name)
        else:
            leave.append(name)
    return take, leave


def main(out_dir):
    pkg = find_package()
    plugin_id, version = plugin_id_and_version(pkg)
    take, leave = collect(pkg)

    stage = os.path.join(out_dir, plugin_id)
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    os.makedirs(stage)
    for name in take:
        shutil.copy2(os.path.join(pkg, name), os.path.join(stage, name))

    archive = os.path.join(out_dir, '%s-%s.zip' % (plugin_id, version))
    if os.path.isfile(archive):
        os.remove(archive)
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name in take:
            zf.write(os.path.join(stage, name), os.path.join(plugin_id, name))

    print('архив: %s' % archive)
    print('папка внутри архива: %s%s  (имя, под которым плагин известен MO2)'
          % (plugin_id, os.sep))
    print()
    print('взято (%d):' % len(take))
    for name in take:
        print('  %s' % name)
    if leave:
        print()
        print('оставлено на месте (%d) - это создаётся на машине пользователя:' % len(leave))
        for name in leave:
            print('  %s' % name)
    return 0


if __name__ == '__main__':
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'dist')
    target = os.path.abspath(target)
    os.makedirs(target, exist_ok=True)
    raise SystemExit(main(target))
