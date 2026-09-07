# -*- coding: utf-8 -*-
"""Занятость MO2: кто держит менеджер прямо сейчас и можно ли менять сборку.

Центральная идея модуля. Пока под MO2 работает игра или утилита, виртуальная Data уже
смонтирована в чужой процесс, и правка модов, порядка или плагинов на ходу означает, что
работающая программа видит одну сборку, а файлы на диске описывают другую. Поэтому все
изменяющие маршруты отказывают, а чтение продолжает работать.

Занятость определяется тремя независимыми источниками, и это не избыточность:

1. обратные вызовы MO2 (onAboutToRun / onFinishedRun) - знают о запусках через менеджер, но
   не переживают пропущенного onFinishedRun;
2. перечисление процессов - знает правду о живых программах, но не отличает запуск через MO2
   от запуска мимо неё. Игру ловит только этот источник: MO2 стартует её через
   sksevr_loader.exe, тот сразу завершается, а живым остаётся SkyrimVR.exe;
3. включённость главного окна MO2 - самый близкий к тому, что видит человек: пока менеджер
   ждёт программу, он показывает модальный диалог и окно перестаёт принимать ввод.

Занятость объявляется, если сработал любой из трёх.
"""
import os

from . import i18n, winapi


class BusyGuard(object):
    """Учёт запусков и три источника занятости."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.o = ctx.o
        self.run_main = ctx.run_main
        self.note = ctx.note
        # Имя исполняемого файла игры. Спрашивается один раз и запоминается: проверка
        # занятости обязана работать и тогда, когда главный поток MO2 не отвечает.
        self.game_exe = None
        # Главное окно MO2, найденное один раз: по его включённости видно её собственный замок
        self._self_hwnd = None
        # Что MO2 сейчас держит запущенным: путь -> {'n': сколько раз, 'mine': наш ли запуск}.
        # Заполняется её же обратными вызовами, поэтому видно и то, что запустил мост, и то,
        # что пользователь запустил кнопкой в окне.
        self.launched = {}
        # Поднят на время своего startApplication, чтобы отличить свой запуск от чужого
        self._starting = False

    # ================================================== обратные вызовы MO2
    def on_about_to_run(self, path, *rest):
        """MO2 сообщает, что запускает программу. Вернуть True - иначе запуск отменится.

        Сигнатур у обратного вызова две: старая принимает один путь, новая ещё рабочий
        каталог и аргументы. Какую подставит конкретная сборка MO2, заранее неизвестно,
        поэтому лишнее принимается в *rest, а не угадывается по версии.
        """
        key = str(path)
        rec = self.launched.setdefault(key, {'n': 0, 'mine': False})
        rec['n'] += 1
        rec['mine'] = rec['mine'] or self._starting
        self.note('MO2 запускает: %s%s' % (key, ' (наш запуск)' if self._starting else ''))
        return True

    def on_finished_run(self, path, exit_code=0):
        """Программа завершилась - снять учёт."""
        key = str(path)
        rec = self.launched.get(key)
        if rec and rec['n'] > 1:
            rec['n'] -= 1
        else:
            self.launched.pop(key, None)
        self.note('MO2 закончила: %s (код %s), осталось активных: %d'
                  % (key, exit_code, len(self.launched)))

    def prime(self):
        """Запомнить имя исполняемого файла игры. Зовётся ИЗ главного потока, напрямую."""
        try:
            self.game_exe = os.path.basename(
                (self.o.managedGame().binaryName() or '').strip()).lower()
        except Exception:
            self.game_exe = ''
        # Заодно запоминаем окно: мы в главном потоке, спешить некуда
        if self._self_hwnd is None:
            try:
                self._self_hwnd = winapi.main_window(os.getpid())
            except Exception:
                self._self_hwnd = 0
        return self.game_exe

    class Starting(object):
        """Флаг «это наш запуск» на время одного startApplication.

        Живёт внутри одного задания главного потока: onAboutToRun MO2 зовёт синхронно из
        startApplication, а задания выполняются по одному, поэтому флаг не перепутает
        свой запуск с чужим.
        """

        def __init__(self, guard):
            self.guard = guard

        def __enter__(self):
            self.guard._starting = True
            return self

        def __exit__(self, *_exc):
            self.guard._starting = False
            return False

    def starting(self):
        return BusyGuard.Starting(self)

    # ================================================== три источника
    def game_binary(self):
        """Имя игры из кэша. Если запомнить не успели - спросить, но недолго."""
        if self.game_exe is None:
            try:
                name = self.run_main(lambda: self.o.managedGame().binaryName(),
                                     timeout=self.ctx.cfg.timeout('gameName'))
                self.game_exe = os.path.basename((name or '').strip()).lower()
            except Exception:
                return ''
        return self.game_exe

    def busy(self):
        """Кто держит MO2 прямо сейчас, или None.

        Обратные вызовы MO2 знают о запусках через неё, но не переживут пропущенного
        onFinishedRun; перечисление процессов знает правду о живых программах, но само по
        себе не отличит запуск через MO2 от запуска мимо неё. Вместе они дают и то, и другое,
        и учёт лечится от застрявших записей.
        """
        game = self.game_binary()
        names = {os.path.basename(k).lower() for k in self.launched}
        if game:
            names.add(game)
        alive = winapi.pids_by_exe(names) if names else {}
        # Свои запуски снимаем с учёта сами: MO2 не ждёт их и о завершении не сообщает, так
        # что запись висела бы вечно. Чужие держим до её слова - именно в них вся защита.
        gone = [k for k, r in self.launched.items()
                if r.get('mine') and os.path.basename(k).lower() not in
                {v.lower() for v in alive.values()}]
        for k in gone:
            self.launched.pop(k, None)
            self.note('свой запуск закончился, снимаю с учёта: %s' % k)
        locked = self.window_locked()
        if not alive and not self.launched and not locked:
            return None
        exe = sorted(alive.values())[0] if alive else ''
        # viaMO2 отвечает на вопрос «MO2 сейчас считает запуск активным», а не «этот самый
        # файл запущен ею». Разница настоящая: игру MO2 стартует через sksevr_loader.exe,
        # тот сразу завершается, и живым остаётся SkyrimVR.exe - имена не совпадают, хотя
        # запуск идёт именно через MO2. Что именно она запустила, видно в mo2Run.
        return {'app': exe or None,
                'pids': sorted(alive),
                'isGame': bool(exe) and exe.lower() == game,
                'viaMO2': bool(self.launched),
                'mo2Run': sorted(self.launched),
                'windowLocked': locked,
                # MO2 не сообщила о завершении запуска, а знакомых процессов в системе нет:
                # замок держит тот, о ком она не говорила. Так остаётся висеть служба,
                # поднятая плагином ИЗНУТРИ игры: MO2 её не запускала, но держит в задании.
                'heldByUnknown': not alive and (bool(self.launched) or locked)}

    def window_locked(self):
        """Выключено ли главное окно MO2. Прямой признак её собственного замка.

        Третий источник правды, и самый близкий к тому, что видит человек: пока MO2 ждёт
        запущенную программу, она показывает модальный диалог «заблокирован, пока приложение
        запущено», и её окно перестаёт принимать ввод. Учёт запусков и перечисление процессов
        оба могут промахнуться - это не может.

        Окно ищется без чтения заголовка. GetWindowTextW шлёт WM_GETTEXT и ждёт поток окна,
        то есть повис бы ровно в том случае, ради которого проверка и написана.
        """
        try:
            hwnd = self._self_hwnd
            # Дескриптор проверяется, а не берётся на веру. Запомненное при старте окно к
            # этому времени может быть уничтожено - на старте видна заставка, а не главное
            # окно, - и IsWindowEnabled на мёртвом дескрипторе отвечает "выключено". Мост
            # тогда считал MO2 навсегда занятой и отказывал во всех изменениях.
            if not hwnd or not winapi.is_window(hwnd):
                hwnd = self._self_hwnd = winapi.main_window(os.getpid())
            return bool(hwnd) and not winapi.is_enabled(hwnd)
        except Exception:
            return False

    def refusal(self, op_key):
        """Отказ, если MO2 занята запущенной программой; None, если работать можно.

        op_key - ключ i18n имени операции, например 'op.toggle'.
        """
        busy = self.busy()
        if not busy:
            return None
        return {'applied': False, 'busy': True, 'running': busy,
                'blocked': i18n.t(op_key),
                'why': i18n.t('busy.why', app=busy['app']) if busy['app']
                else i18n.t('busy.whyUnknown', run=', '.join(busy['mo2Run']) or '-')}
