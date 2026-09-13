# -*- coding: utf-8 -*-
"""MO2 AI Bridge — локальный HTTP-мост к работающей Mod Organizer 2.

Верхний слой: жизненный цикл плагина и сборка остальных слоёв воедино. Сам ничего не делает
руками — только соединяет.

Слои снизу вверх, каждый знает лишь про тот, что под ним:

    winapi.py    окна и кнопки Windows          — не знает ни про MO2, ни про сеть
    runtime.py   главный поток Qt и HTTP        — не знает, что делают маршруты
    services.py  операции над сборкой, фасад    — не знает про HTTP и JSON
    routes.py    какой путь во что отображается — не знает ни про mobase, ни про сокеты
    plugin.py    жизненный цикл плагина         — этот файл

Строки вынесены в i18n.py, настраиваемые значения — в config.py.
"""
import os
import traceback

import mobase

from . import i18n, runtime, routes as routes_mod
from .config import Config
from .services import Services

from . import PLUGIN_ID, __version__

PLUGIN_NAME = 'MO2AIBridge'
DEFAULT_PORT = 8930
# Сколько портов подряд пробовать, если заказанный занят. Занимает его обычно второй
# экземпляр MO2 - и это не беда, а обычный день: Skyrim и Fallout держат открытыми оба.
PORT_TRIES = 10
HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(HERE, PLUGIN_ID + '-token.txt')
ERROR_LOG = os.path.join(HERE, PLUGIN_ID + '.log')
CONFIG_FILE = os.path.join(HERE, PLUGIN_ID + '-config.json')
DOCS = os.path.join(HERE, 'README.md')


def token_file(port, wanted):
    """Куда писать токен для моста, вставшего на порт `port` при заказанном `wanted`.

    Досталcя заказанный порт - имя привычное, и у кого один экземпляр MO2, для того не
    меняется ничего, каким бы порт ни был настроен. Второй экземпляр встаёт на следующий
    свободный и получает собственный файл: один общий они перетирали друг другу, и клиент,
    взявший токен последним, стучался им в чужой мост. Порт вынесен в имя намеренно - по
    нему клиент и узнаёт, куда стучаться, не спрашивая ни у кого.
    """
    if int(port) == int(wanted):
        return TOKEN_FILE
    return os.path.join(HERE, '%s-token-%d.txt' % (PLUGIN_ID, int(port)))


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


def _version_info():
    """VersionInfo из __version__: номер задаётся один раз, в __init__.py."""
    parts = [int(x) for x in __version__.split('.')[:3]]
    while len(parts) < 3:
        parts.append(0)
    return mobase.VersionInfo(parts[0], parts[1], parts[2], 0)


class MO2AIBridge(mobase.IPluginTool):
    def __init__(self):
        super().__init__()
        self._organizer = None
        self._server = None
        self._runner = None
        self._parent = None
        self._svc = None
        self._port = None
        self._token_file = None
        self._quit_hooked = False

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
        # Имя владельца, как оно видно на Nexus. Лицензия - MIT, файл LICENSE рядом.
        return 'Vhodnoylogin'

    def description(self):
        return i18n.t('plugin.description')

    def version(self):
        return _version_info()

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
                log(i18n.t('log.stuckReset'))
        # Порт показывается тот, на котором мост реально встал, а не тот, что заказан
        # настройкой: при занятом заказанном он не совпадает, и человек, читая окно,
        # должен видеть правду, а не намерение.
        state = i18n.t('dialog.running' if self._server else 'dialog.stopped')
        box = QMessageBox(self._parent)
        box.setWindowTitle(i18n.t('plugin.displayName'))
        box.setText(i18n.t('dialog.listening', port=self._port or DEFAULT_PORT,
                           token=self._token_file or TOKEN_FILE, state=state))
        # Кнопка остановки - единственный способ опустить мост, не выходя из MO2: галочка
        # «Включено» читается только при запуске, и снять её на ходу мало.
        stop_btn = (box.addButton(i18n.t('dialog.stopButton'),
                                  QMessageBox.ButtonRole.DestructiveRole)
                    if self._server else None)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if stop_btn is not None and box.clickedButton() is stop_btn:
            self.stop()

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
            wanted = int(self._setting('port', DEFAULT_PORT) or DEFAULT_PORT)
            token = runtime.new_token()
            # Настройки читаются один раз на запуск; файла нет - он создаётся из умолчаний.
            cfg = Config.load(CONFIG_FILE, note=log)
            self._runner = runtime.MainThreadRunner()
            svc = self._svc = Services(self._organizer, self._runner.call, DOCS,
                                       note=log, cfg=cfg)
            svc.prime()
            self._subscribe_runs(svc)
            get, post = routes_mod.build(svc)
            # Сервер поднимается ДО записи токена: при отказе привязки файла не остаётся,
            # и клиент не получит ключ к мосту, которого нет.
            self._server, self._port = runtime.serve(
                wanted, runtime.make_handler(token, get, post), tries=PORT_TRIES)
            self._token_file = token_file(self._port, wanted)
            with open(self._token_file, 'w', encoding='utf-8') as fh:
                fh.write(token)
            self._hook_quit()
            log(i18n.t('start.ok', port=self._port))
            if self._port != wanted:
                log(i18n.t('start.otherPort', wanted=wanted, port=self._port,
                           token=self._token_file))
            return None
        except Exception as exc:
            self.stop()
            log(i18n.t('start.failed') + ':' + chr(10) + traceback.format_exc())
            return '%s: %s' % (type(exc).__name__, exc)

    def _hook_quit(self):
        """Опустить мост при выходе из MO2.

        Отдельного вызова выгрузки у плагина в mobase 2.5.2 нет, а сигнал приложения есть
        и приходит до финализации встроенного интерпретатора. Без него поток сервера
        обрывался финализацией прямо внутри ожидания, а файл токена оставался лежать -
        ключом к мосту, которого уже нет.
        """
        if self._quit_hooked:
            return
        try:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(self.stop)
                self._quit_hooked = True
        except Exception:
            log('aboutToQuit: ' + traceback.format_exc())

    def stop(self):
        """Опустить мост: закрыть сокет, дождаться потока, убрать файл токена.

        Зовётся из трёх мест: при выходе из MO2, кнопкой в своём окне и при неудачном
        запуске - чтобы половина поднятого не осталась висеть. Каждый шаг отдельно и
        молча: остановка обязана доработать до конца, чем бы ни кончился предыдущий шаг.
        """
        srv, self._server = self._server, None
        if srv is not None:
            try:
                srv.shutdown()
            except Exception:
                pass
            try:
                srv.server_close()
            except Exception:
                pass
        try:
            if self._token_file and os.path.isfile(self._token_file):
                os.remove(self._token_file)
        except Exception:
            pass
        self._token_file = None
        self._svc = None
        self._runner = None
        if srv is not None:
            log(i18n.t('stop.ok'))
