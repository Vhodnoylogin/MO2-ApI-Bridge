# -*- coding: utf-8 -*-
"""Общее для предметных классов: контекст, обвязка изменяющей операции, форма карточки.

Предметный слой разобран на классы по областям - занятость, чтение, установка, состав модов,
порядок плагинов, запуск. Всем им нужно одно и то же: `IOrganizer`, способ попасть в главный
поток, куда писать след, настройки. Это и есть `Context`. `Domain` - база класса области:
знает контекст, замок занятости и умеет один общий приём для любого изменения.

Приём такой, и он один на все девять изменяющих маршрутов:

    отказать, если MO2 занята  ->  проверить входные данные  ->  выполнить  ->  подписать карточку

Подпись - это два ключа, которые добавляются к любому ответу об изменении: `op` (какая
операция) и `applied` (сделано ли), а к любому отказу - ещё `reason` (`busy` или `danger`).
Прежние ключи остаются на месте: форма ответа меняется только добавлением, иначе тихо ломаются
чужие вызовы.
"""
from . import i18n

DANGER_KEY = 'iUnderstandTheRisk'
DANGER_VALUE = 'yes-I-read-the-docs-and-accept-irreversible-changes'


def one(q, key, default):
    """Один параметр из строки запроса: parse_qs отдаёт списки, а нужна строка."""
    v = (q or {}).get(key)
    return v[0] if isinstance(v, list) and v else (v if isinstance(v, str) else default)


def safe(fn, default=None):
    """Значение вызова или умолчание: у части модов методы карточки бросают исключение."""
    try:
        v = fn()
        return v if v is not None else default
    except Exception:
        return default


def stamp(res, op, applied=True):
    """Подписать карточку операцией. Только добавляет: существующие ключи не трогаются."""
    if isinstance(res, dict):
        res.setdefault('op', op)
        res.setdefault('applied', applied)
    return res


class Context(object):
    """Всё, что нужно любой области: MO2, главный поток, след, документация, настройки."""

    def __init__(self, organizer, run_main, docs, note, cfg):
        self.o = organizer
        self.run_main = run_main
        self.docs = docs
        self.note = note
        self.cfg = cfg


class Domain(object):
    """База предметной области. Держит контекст и замок занятости."""

    def __init__(self, ctx, guard):
        self.ctx = ctx
        self.o = ctx.o
        self.run_main = ctx.run_main
        self.note = ctx.note
        self.cfg = ctx.cfg
        self.guard = guard

    # ---- общий приём изменения --------------------------------------------
    def change(self, op, fn, timeout=None, on_main=True, prepare=None):
        """Выполнить изменяющую операцию по общему приёму.

        op       имя операции без префикса: 'toggle', 'install', ... - ключ i18n 'op.<op>'
        fn       сама работа; по умолчанию идёт в главном потоке MO2
        timeout  секунды ожидания главного потока; None - умолчание транспорта
        on_main  False, если работа сама ходит в главный поток по мере надобности
                 (установка: распаковка и копирование главному потоку не нужны)
        prepare  проверка входных данных в потоке сервера до главного; может бросить
                 ValueError или вернуть готовый отказ (например, замка необратимого)

        Занятость проверяется ПЕРВОЙ, до разбора аргументов: отказ «занята» обязан приходить
        и на заведомо негодный запрос, иначе по ответу не понять, что менеджер занят.
        """
        stop = self.guard.refusal('op.' + op)
        if stop:
            return self._refusal(stop, op, 'busy')
        if prepare is not None:
            stop = prepare()
            if stop:
                return self._refusal(stop, op, 'danger')
        if on_main:
            res = self.run_main(fn, timeout=timeout) if timeout else self.run_main(fn)
        else:
            res = fn()
        return stamp(res, op)

    def danger(self, body, op):
        """Отказ замка необратимого, или None, если ключ передан верно."""
        if (body.get(DANGER_KEY) or '') != DANGER_VALUE:
            return self._refusal({'applied': False,
                                  'blocked': i18n.t('op.' + op),
                                  'why': i18n.t('danger.why', key=DANGER_KEY),
                                  'docs': self.ctx.docs}, op, 'danger')
        return None

    @staticmethod
    def _refusal(res, op, reason):
        res.setdefault('reason', reason)
        return stamp(res, op, applied=False)

    # ---- мелочи, нужные нескольким областям ---------------------------------
    def timeout(self, name):
        return self.cfg.timeout(name)

    def limit(self, name='listLimit'):
        return int(self.cfg.get(name) or 0)

    def nexus_url(self, game, nid):
        """Ссылка на страницу мода. Собирается по nexusId: m.url() у части модов отдаёт голый
        домен без /mods/<id>, и получается ссылка в никуда."""
        if not nid or nid <= 0:
            return ''
        domains = self.cfg.get('nexusDomains') or {}
        dom = domains.get(game or '', self.cfg.get('nexusDomainDefault'))
        return 'https://www.nexusmods.com/%s/mods/%d' % (dom, nid)
