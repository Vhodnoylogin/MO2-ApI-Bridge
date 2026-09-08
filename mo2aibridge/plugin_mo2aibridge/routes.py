# -*- coding: utf-8 -*-
"""Слой маршрутизации: какой путь какую операцию вызывает.

Знает про имена путей и ничего больше. Ни mobase, ни сокетов, ни JSON здесь нет - только
таблицы соответствий. Добавить маршрут значит дописать строку, а не трогать транспорт
или предметный слой.
"""


def build(svc):
    """Собрать две таблицы: чтение и изменения. Разделение не косметическое - транспорт
    по нему решает, брать ли параметры из строки запроса или из тела."""
    get = {
        '/ping': svc.ping,
        '/api': svc.api,
        '/mods': svc.mods,
        '/mod': svc.mod,
        '/analyze': svc.analyze,
        '/profiles': svc.profiles,
        '/plugins': svc.plugins,
        '/vfs': svc.vfs,
        '/origins': svc.origins,
        '/resolve': svc.resolve,
        '/dirs': svc.dirs,
        '/procs': svc.procs_list,
        '/windows': svc.windows,
        # обновления по живому Nexus - чтение, работает и при занятой MO2
        '/updates': svc.updates,
    }
    post = {
        '/refresh': svc.refresh,
        '/install': svc.install,
        '/toggle': svc.toggle,
        '/plugins/state': svc.plugins_state,
        '/plugins/order': svc.plugins_order,
        '/vfsexport': svc.vfsexport,
        '/run': svc.run,
        '/window': svc.window,
        # необратимое: без ключа из документации эти маршруты ничего не делают
        '/mods/priority': svc.mods_priority,
        '/mods/rename': svc.mods_rename,
        '/mods/remove': svc.mods_remove,
    }
    return get, post
