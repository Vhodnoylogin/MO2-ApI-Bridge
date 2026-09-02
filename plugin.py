# -*- coding: utf-8 -*-
"""MO2 AI Link Assistant — локальный HTTP-мост к работающей Mod Organizer 2.

Верхний слой: жизненный цикл плагина и сборка остальных слоёв воедино. Сам ничего не делает
руками — только соединяет.

Слои снизу вверх, каждый знает лишь про тот, что под ним:

    winapi.py    окна и кнопки Windows          — не знает ни про MO2, ни про сеть
    runtime.py   главный поток Qt и HTTP        — не знает, что делают маршруты
    services.py  операции над сборкой           — не знает про HTTP и JSON
    routes.py    какой путь во что отображается — не знает ни про mobase, ни про сокеты
    __init__.py  жизненный цикл плагина

Строки вынесены в i18n.py, переводить можно не притрагиваясь к логике.
"""
import os
import traceback

import mobase

from . import i18n, runtime, routes as routes_mod
from .services import Services

PLUGIN_NAME = 'MO2AILink'
DEFAULT_PORT = 8930
HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(HERE, 'mo2ailink-token.txt')
ERROR_LOG = os.path.join(HERE, 'mo2ailink.log')
DOCS = os.path.join(HERE, 'README.md')


def log(msg):
    """Писать и в лог MO2, и в файл рядом с плагином.

    В файл — потому что уровень логирования MO2 по умолчанию отбрасывает предупреждения
    плагинов, и тогда причина отказа теряется совсем. Именно это однажды стоило часов поиска:
    мост молчал, а исключение проглатывалось.
    """
    line = '[%s] %s' % (PLUGIN_NAME, msg)
    try:
        from PyQt6.QtCore import qCritical
        qCritical(line)
    except Exception:
        pass
    try:
        with open(ERROR_LOG, 'a', encoding='utf-8') as fh:
            fh.write(line + chr(10))
    except Exception:
        pass


class MO2AILink(mobase.IPluginTool):
    def __init__(self):
        super().__init__()
        self._organizer = None
        self._server = None
        self._runner = None
        self._parent = None
        self._svc = None

    # ---- интерфейс MO2 ----------------------------------------------------
    def init(self, organizer):
        self._organizer = organizer
        i18n.set_language(self._setting('language', 'auto'))
        log(i18n.t('init.loaded'))
        try:
            organizer.onUserInterfaceInitialized(self._on_ui_ready)
        except Exception:
            log('onUserInterfaceInitialized: ' + traceback.format_exc())
        return True

    def name(self):
        return PLUGIN_NAME

    def author(self):
        return 'Claude'

    def description(self):
        return i18n.t('plugin.description')

    def version(self):
        return mobase.VersionInfo(2, 0, 0, 0)

    def settings(self):
        return [mobase.PluginSetting('enabled', i18n.t('setting.enabled'), True),
                mobase.PluginSetting('port', i18n.t('setting.port'), DEFAULT_PORT),
                mobase.PluginSetting('language', i18n.t('setting.language'), 'auto')]

    def isActive(self):
        """Мягкая проверка намеренно.

        Галочка «Включено» на панели плагина в MO2 2.5 — собственный выключатель менеджера,
        а не настройка с именем enabled: pluginSetting возвращает по ней None. Строгое
        сравнение `is True` не проходило никогда, и мост молча не поднимался.
        """
        v = self._setting('enabled', True)
        if v is None:
            return True
        if isinstance(v, str):
            return v.strip().lower() not in ('false', '0', 'no', '')
        return bool(v)

    def displayName(self):
        return i18n.t('plugin.displayName')

    def tooltip(self):
        return i18n.t('plugin.tooltip')

    def icon(self):
        from PyQt6.QtGui import QIcon
        return QIcon()

    def setParentWidget(self, widget):
        self._parent = widget

    def display(self):
        """Пункт меню служит и ручным запуском: если автостарт не сработал, мост можно
        поднять отсюда и сразу увидеть настоящую причину отказа."""
        from PyQt6.QtWidgets import QMessageBox
        port = self._setting('port', DEFAULT_PORT)
        if not self._server:
            err = self.start()
            if err:
                QMessageBox.critical(self._parent, i18n.t('plugin.displayName'),
                                     i18n.t('dialog.startFailed', error=err, log=ERROR_LOG))
                return
        # Застрявший учёт запусков виден только здесь и сбрасывается только отсюда:
        # маршрута для этого нет намеренно, иначе замок обходился бы одним запросом.
        if self._svc is not None and self._svc.launched:
            btn = QMessageBox.question(
                self._parent, i18n.t('plugin.displayName'),
                i18n.t('dialog.stuck', run=chr(10).join(sorted(self._svc.launched))),
                QMessageBox.StandardButton.Reset | QMessageBox.StandardButton.Cancel)
            if btn == QMessageBox.StandardButton.Reset:
                self._svc.launched.clear()
                log('учёт запусков сброшен вручную')
        state = i18n.t('dialog.running' if self._server else 'dialog.stopped')
        QMessageBox.information(
            self._parent, i18n.t('plugin.displayName'),
            i18n.t('dialog.listening', port=port, token=TOKEN_FILE, state=state))

    # ---- запуск -----------------------------------------------------------
    def _setting(self, key, default=None):
        try:
            v = self._organizer.pluginSetting(self.name(), key)
            return default if v is None else v
        except Exception:
            return default

    def _subscribe_runs(self, svc):
        """Подписаться на запуски программ через MO2.

        Через эти два вызова видно всё, что запускает менеджер, - и мостом, и кнопкой в
        окне. Без них мост знал бы только о собственных запусках и спокойно переставил бы
        моды под работающей игрой.

        Отказ подписки не смертелен: остаётся перечисление процессов, которое всё равно
        поймает игру. Поэтому пишем в лог и работаем дальше.
        """
        try:
            self._organizer.onAboutToRun(svc.on_about_to_run)
            self._organizer.onFinishedRun(svc.on_finished_run)
        except Exception:
            log('onAboutToRun/onFinishedRun: ' + traceback.format_exc())

    def _on_ui_ready(self, _window=None):
        log(i18n.t('ui.ready'))
        err = self.start()
        if err:
            log('%s: %s' % (i18n.t('start.failed'), err))

    def start(self):
        """Поднять сервер. None при успехе, текст ошибки иначе.

        Молча глотать исключение здесь нельзя: при отказе не остаётся ни файла токена, ни
        порта, ни записи — снаружи это неотличимо от «плагин не загрузился вообще».
        """
        if self._server:
            return None
        if not self.isActive():
            return i18n.t('start.disabled')
        try:
            port = int(self._setting('port', DEFAULT_PORT) or DEFAULT_PORT)
            token = runtime.new_token()
            with open(TOKEN_FILE, 'w', encoding='utf-8') as fh:
                fh.write(token)
            self._runner = runtime.MainThreadRunner()
            svc = self._svc = Services(self._organizer, self._runner.call, DOCS, note=log)
            svc.prime()
            self._subscribe_runs(svc)
            get, post = routes_mod.build(svc)
            self._server = runtime.serve(port, runtime.make_handler(token, get, post))
            log(i18n.t('start.ok', port=port))
            return None
        except Exception as exc:
            self._server = None
            log(i18n.t('start.failed') + ':' + chr(10) + traceback.format_exc())
            return '%s: %s' % (type(exc).__name__, exc)


