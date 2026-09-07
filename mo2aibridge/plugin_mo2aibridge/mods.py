# -*- coding: utf-8 -*-
"""Состав модов: включить, выключить, перечитать, и необратимое - приоритет, имя, удаление.

Необратимая тройка требует в теле запроса ключ из документации. Без него маршрут ничего не
делает и возвращает объяснение и путь к README: узнать значение можно только прочитав раздел
о последствиях. Замок занятости проверяется раньше замка необратимого.
"""
import os

import mobase

from . import i18n, winapi
from .base import Domain, safe


class ModOps(Domain):

    def toggle(self, body):
        def prepare():
            if not body.get('mod') or body.get('active') is None:
                raise ValueError(i18n.t('err.needMod'))

        def f():
            mod, want = body.get('mod'), body.get('active')
            ml = self.o.modList()
            if ml.getMod(mod) is None:
                raise ValueError(i18n.t('err.noSuchMod', mod=mod))
            # Прежнее состояние снимается ДО правки: обратить операцию больше нечем,
            # запоминать его мост не станет - это дело вызывающего.
            was = bool(ml.state(mod) & mobase.ModState.ACTIVE)
            ml.setActive(mod, bool(want))
            return {'mod': mod, 'active': bool(want), 'was': was,
                    'changed': was != bool(want),
                    'undo': {'route': '/toggle', 'body': {'mod': mod, 'active': was}}}
        return self.change('toggle', f, prepare=prepare)

    def refresh(self, _=None):
        """Перечитать mods\\ и профиль.

        Именно это снимает необходимость закрывать MO2: папка мода, созданная мимо менеджера,
        после refresh попадает в список и дальше включается обычным toggle.
        """
        def f():
            self.o.refresh(True)
            return {'refreshed': True}
        return self.change('refresh', f)

    # ================================================== необратимое
    def mods_priority(self, body):
        def prepare():
            if not body.get('mod') or body.get('priority') is None:
                raise ValueError(i18n.t('err.needMod'))

        mod, prio = body.get('mod'), body.get('priority')

        def f():
            ml = self.o.modList()
            if ml.getMod(mod) is None:
                raise ValueError(i18n.t('err.noSuchMod', mod=mod))
            was = ml.priority(mod)
            # Отказ замка необратимого отдаётся с тем, что было бы сделано: откуда и куда.
            stop = self.danger(body, 'priority')
            if stop:
                stop.update({'mod': mod, 'from': was, 'to': int(prio)})
                return stop
            ok = ml.setPriority(mod, int(prio))
            self.o.refresh(True)
            return {'applied': bool(ok), 'mod': mod, 'from': was, 'to': ml.priority(mod)}
        return self.change('priority', f, prepare=prepare)

    def mods_rename(self, body):
        mod, new = body.get('mod'), body.get('newName')

        def prepare():
            if not mod or not new:
                raise ValueError(i18n.t('err.needMod'))
            stop = self.danger(body, 'rename')
            if stop:
                stop.update({'mod': mod, 'newName': new})
            return stop

        def f():
            ml = self.o.modList()
            m = ml.getMod(mod)
            if m is None:
                raise ValueError(i18n.t('err.noSuchMod', mod=mod))
            was_path = safe(m.absolutePath, '')
            nid = safe(m.nexusId, 0)
            res = ml.renameMod(m, new)
            self.o.refresh(True)
            got = ml.getMod(new)
            # Имя папки - это то, чем мод опознают снаружи, поэтому вместе с путями
            # отдаём nexusId: по нему мод узнаётся, даже если имя уже потеряно.
            return {'applied': res is not None, 'mod': mod, 'newName': new,
                    'fromPath': was_path,
                    'toPath': safe(got.absolutePath, '') if got else '',
                    'nexusId': nid,
                    'undo': {'route': '/mods/rename',
                             'body': {'mod': new, 'newName': mod}}}
        return self.change('rename', f, prepare=prepare)

    def mods_remove(self, body):
        mod = body.get('mod')
        with_archive = bool(body.get('withArchive'))

        def prepare():
            if not mod:
                raise ValueError(i18n.t('err.needMod'))

        def f():
            ml = self.o.modList()
            m = ml.getMod(mod)
            if m is None:
                raise ValueError(i18n.t('err.noSuchMod', mod=mod))
            # Карточка снимается ДО сноса и отдаётся целиком: после удаления спросить будет
            # некого, а чтобы мод вернуть, нужно знать всё - из какого архива он собран,
            # какой версии, где стоял и был ли включён.
            card = self.removal_card(m, ml)
            stop = self.danger(body, 'remove')
            if stop:
                stop.update({'mod': mod, 'card': card, 'withArchive': with_archive})
                return stop
            ok = ml.removeMod(m)
            self.o.refresh(True)
            out = {'applied': bool(ok), 'mod': mod, 'path': card['path'],
                   'card': card, 'archiveKept': True, 'archiveRecycled': False}
            if with_archive and card.get('archivePath'):
                # Архив - единственное, что мост удаляет сам, поэтому только в Корзину.
                out['archiveRecycled'] = bool(safe(
                    lambda: winapi.recycle(card['archivePath']), False))
                out['archiveKept'] = not out['archiveRecycled']
            out['undo'] = ({'route': '/install',
                            'body': {'archive': card.get('archivePath'), 'name': mod}}
                           if out['archiveKept'] and card.get('archivePath') else None)
            return out
        return self.change('remove', f, prepare=prepare)

    def removal_card(self, m, ml):
        """Всё, что понадобится, чтобы вернуть мод. Снимается до удаления, из главного потока."""
        name = m.name()
        arc = safe(m.installationFile, '') or ''
        arc_path = ''
        if arc:
            cand = os.path.join(safe(self.o.downloadsPath, '') or '', arc)
            arc_path = cand if os.path.isfile(cand) else ''
        st = ml.state(name)
        nid = safe(m.nexusId, 0)
        return {'mod': name,
                'path': safe(m.absolutePath, ''),
                'version': safe(lambda: m.version().displayString(), ''),
                'nexusId': nid,
                'url': self.nexus_url(safe(m.gameName, '') or '', nid),
                'categories': list(safe(m.categories, []) or []),
                'notes': safe(m.comments, '') or '',
                'installationFile': arc,
                'archivePath': arc_path,
                'archiveOnDisk': bool(arc_path),
                'priority': ml.priority(name),
                'active': bool(st & mobase.ModState.ACTIVE),
                'files': safe(lambda: sum(len(fs) for _r, _d, fs in
                                          os.walk(m.absolutePath())), -1)}
