# -*- coding: utf-8 -*-
"""Запуск программ внутри VFS и работа с их окнами.

Запуск - самая опасная операция моста: под MO2 поднимается чужой процесс с виртуальной
Data. Он закрыт замком занятости, как и остальные изменения. Окна нужны ради утилит на WinAPI
(диалоги DynDOLOD и TexGen): их закрывают тем же мостом, и именно это снимает занятость.
"""
import os

from . import i18n, winapi
from .base import Domain, one, safe


class Launcher(Domain):

    def __init__(self, ctx, guard):
        super().__init__(ctx, guard)
        # Что мост запускал за эту сессию: ключ -> (дескриптор, pid, имя)
        self.procs = {}
        self.seq = 0

    def run(self, body):
        """Запуск утилиты внутри VFS.

        binary - ЗАРЕГИСТРИРОВАННОЕ в MO2 имя исполняемого файла, а не путь: с полным путём
        startApplication молча не создаёт процесс.
        """
        def prepare():
            if not body.get('binary'):
                raise ValueError(i18n.t('err.needBinary'))

        return self.change('run', lambda: self._run(body), on_main=False, prepare=prepare)

    def _run(self, body):
        binary = body.get('binary')
        args = body.get('args') or []
        cwd = body.get('cwd') or ''
        wait = bool(body.get('wait'))

        def start():
            # Флаг живёт внутри одного задания главного потока: onAboutToRun MO2 зовёт
            # синхронно из startApplication, а задания выполняются по одному.
            with self.guard.starting():
                return self.o.startApplication(binary, args, cwd)
        handle = self.run_main(start)
        self.seq += 1
        key = 'p%d' % self.seq
        pid = winapi.process_id(handle) if handle else 0
        self.procs[key] = (handle, pid, os.path.basename(str(binary)))
        # started говорит, дал ли MO2 дескриптор вообще: с полным путём вместо имени
        # startApplication молча возвращает пусто, и раньше это выглядело как запуск.
        res = {'key': key, 'pid': pid, 'binary': binary, 'args': args,
               'started': bool(handle)}
        if wait and handle:
            # Ждём сами, а не через waitForApplication: тот не отпускает GIL и останавливает
            # весь интерпретатор вместе с сервером - мост замолкает целиком.
            res['exit'] = winapi.wait_process(
                handle, float(body.get('timeout') or self.timeout('runWait')))
            res['waitedBy'] = 'WaitForSingleObject'
        return res

    def procs_list(self, _=None):
        """Что мост запускал за эту сессию - с признаком, жив ли процесс до сих пор.

        Список копится с первого запуска и сам не чистится: о завершении своих запусков MO2
        не сообщает, ждать её слова тут нечего. Пока признака не было, три давно закрытых
        TexGen из проверок выглядели как три работающие программы и подняли ложную тревогу
        в соседнем чате. Поэтому живость спрашивается у системы на каждый вызов.
        """
        out = []
        for k, (handle, pid, what) in self.procs.items():
            code = (safe(lambda h=handle: winapi.wait_process(h, 0), 0)
                    if handle else 0)
            out.append({'key': k, 'pid': pid, 'what': what,
                        'alive': code is None, 'exit': code})
        return {'procs': out,
                'running': sum(1 for x in out if x['alive']),
                'launchedByMO2': sorted(self.guard.launched),
                'busy': self.guard.busy()}

    def windows(self, q):
        pid = int(one(q, 'pid', '0') or 0)
        if not pid:
            key = one(q, 'key', '')
            pid = self.procs.get(key, (0, 0, ''))[1]
        if not pid:
            raise ValueError(i18n.t('err.needPidOrKey'))
        return {'pid': pid, 'windows': winapi.windows_of(pid)}

    def window(self, body):
        hwnd = int(body.get('hwnd') or 0)
        action = body.get('action')
        if not hwnd or not winapi.is_window(hwnd):
            raise ValueError(i18n.t('err.noSuchWindow'))
        title = winapi.window_text(hwnd)
        if action == 'close':
            winapi.close(hwnd)
            return {'hwnd': hwnd, 'title': title, 'did': 'close'}
        if action == 'click':
            caption = body.get('button') or ''
            if not caption.strip():
                raise ValueError(i18n.t('err.needButton'))
            hit = winapi.click_by_caption(hwnd, caption)
            if hit is None:
                raise ValueError(i18n.t('err.noButton', button=caption))
            return {'hwnd': hwnd, 'title': title, 'did': 'click', 'button': hit}
        raise ValueError(i18n.t('err.action'))
