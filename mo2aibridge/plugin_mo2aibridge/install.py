# -*- coding: utf-8 -*-
"""Установка мода без единого диалога: createMod плюс собственная распаковка.

installMod намеренно не используется. Он запускает установщик MO2, а тот всегда спрашивает
имя, при совпадении имён спрашивает «заменить или слить», а на архиве с fomod/ModuleConfig.xml
открывает мастера. Автоматизировать эти окна нельзя: MO2 на Qt, её виджеты нарисованы внутри
одного HWND, нативных кнопок для нажатия по подписи нет.

createMod же заводит зарегистрированную пустую папку, а файлы кладёт мост сам - ровно те,
что нужны. Для FOMOD это и так единственный верный путь: выбор опций разбирается чтением
ModuleConfig.xml, а не кликами.
"""
import os
import shutil
import subprocess
import tempfile

import mobase

from . import i18n, winapi
from .base import Domain, safe


def tree_files(root):
    """Пути всех файлов папки относительно неё самой, в нижнем регистре.

    Нужны для честного «было и стало»: по разнице видно, что добавилось, а по пересечению -
    что перекрыто. Регистр снят, потому что Windows его не различает, а архивы приносят
    и то, и другое написание.
    """
    out = set()
    for dp, _dn, fs in os.walk(root):
        rel = os.path.relpath(dp, root)
        for f in fs:
            out.add((f if rel == '.' else os.path.join(rel, f)).lower())
    return out


class Installer(Domain):

    def install(self, body):
        """body: {archive, name, paths: ["подпапка в архиве", ...], mode}

        mode нужен только когда папка мода уже занята, и повторяет то, что установщик MO2
        спрашивает диалогом:

            merge    положить выбранное поверх прежнего содержимого
            replace  убрать прежнее содержимое и положить выбранное

        Без mode занятая папка - отказ: перезаписать чужую работу молча нельзя. При замене
        прежнее содержимое уходит В КОРЗИНУ, а meta.ini остаётся: в нём nexusId, категория и
        имя архива, то есть опознание мода помимо имени папки.
        """
        def prepare():
            arc = body.get('archive')
            if not arc or not os.path.isfile(arc):
                raise ValueError(i18n.t('err.noArchive', archive=arc))
            if not (body.get('name') or '').strip():
                raise ValueError(i18n.t('err.needName'))
            mode = (body.get('mode') or '').strip().lower()
            if mode and mode not in ('merge', 'replace'):
                raise ValueError(i18n.t('err.badMode', mode=mode))
            return None

        # Распаковка и копирование главному потоку не нужны, он зовётся точечно изнутри.
        return self.change('install', lambda: self._install(body), on_main=False,
                           prepare=prepare)

    def _install(self, body):
        arc = body.get('archive')
        name = (body.get('name') or '').strip()
        paths = body.get('paths') or ['']
        mode = (body.get('mode') or '').strip().lower()

        mods_root = self.run_main(lambda: self.o.modsPath())
        existed = os.path.isdir(os.path.join(mods_root, name))
        if existed and not mode:
            raise ValueError(i18n.t('err.modExists', mod=name))

        if existed:
            target = os.path.join(mods_root, name)
            before = tree_files(target)
        else:
            def mk():
                m = self.o.createMod(mobase.GuessedString(name))
                return None if m is None else m.absolutePath()
            target = self.run_main(mk)
            if not target:
                raise RuntimeError(i18n.t('err.createFailed'))
            before = set()

        removed = 0
        if existed and mode == 'replace':
            removed = self._wipe(target)

        tmp = self._unpack(arc)
        try:
            copied, skipped_fomod = self._copy(tmp, paths, target)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.run_main(lambda: (self.o.refresh(True), True)[1])
        after = tree_files(target)
        limit = self.limit()
        # «Что было и что стало» - целиком: слияние молча перекрывает прежние файлы, и без
        # списка перекрытых вызывающий не узнает, что именно потерял.
        overwritten = sorted(before & after) if mode == 'merge' else []
        return {'mod': name, 'path': target, 'files': copied,
                'created': not existed, 'existed': existed, 'mode': mode or 'new',
                'fomodSkipped': skipped_fomod, 'archive': os.path.basename(arc),
                'filesBefore': len(before), 'filesAfter': len(after),
                'added': sorted(after - before)[:limit], 'addedCount': len(after - before),
                'overwritten': overwritten[:limit], 'overwrittenCount': len(overwritten),
                'removedToRecycleBin': removed,
                'undo': ({'route': '/mods/remove', 'body': {'mod': name}}
                         if not existed else None)}

    def _wipe(self, target):
        """Убрать прежнее содержимое В КОРЗИНУ, а не мимо: замена - единственная установка,
        которая что-то теряет, и терять её надо обратимо. meta.ini не трогаем: он принадлежит
        MO2 и хранит опознание мода помимо имени папки."""
        removed = 0
        for entry in sorted(os.listdir(target)):
            if entry.lower() == 'meta.ini':
                continue
            full = os.path.join(target, entry)
            if safe(lambda p=full: winapi.recycle(p), False):
                removed += 1
            else:
                raise RuntimeError(i18n.t('err.recycle', path=full))
        return removed

    def _unpack(self, arc):
        """Распаковать архив в свою временную папку. Папка у каждого вызова своя: две
        установки подряд не должны затирать друг друга."""
        seven = self.cfg.seven_zip()
        if not seven:
            raise RuntimeError(i18n.t('err.no7z'))
        tmp = tempfile.mkdtemp(prefix=self.cfg.get('unpackDirName') + '-')
        r = subprocess.run([seven, 'x', arc, '-o' + tmp, '-y'],
                           capture_output=True, text=True, timeout=self.timeout('unpack'))
        if r.returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            tail = int(self.cfg.get('unpackErrorTail') or 300)
            raise RuntimeError(i18n.t('err.unpack', detail=(r.stdout or r.stderr)[-tail:]))
        return tmp

    @staticmethod
    def _copy(tmp, paths, target):
        copied, skipped_fomod = 0, False
        for sub in paths:
            root = os.path.join(tmp, sub) if sub else tmp
            if not os.path.isdir(root):
                raise ValueError(i18n.t('err.noPathInArchive', path=sub))
            for dp, _dn, fs in os.walk(root):
                rel = os.path.relpath(dp, root)
                # fomod - описание мастера установки, игре оно не нужно
                if rel.lower().split(os.sep)[0] == 'fomod':
                    skipped_fomod = True
                    continue
                dst = target if rel == '.' else os.path.join(target, rel)
                if not os.path.isdir(dst):
                    os.makedirs(dst)
                for f in fs:
                    if f.lower() == 'meta.ini' and rel == '.':
                        continue
                    shutil.copy2(os.path.join(dp, f), os.path.join(dst, f))
                    copied += 1
        return copied, skipped_fomod
