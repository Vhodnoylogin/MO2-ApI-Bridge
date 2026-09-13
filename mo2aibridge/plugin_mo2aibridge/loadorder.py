# -*- coding: utf-8 -*-
"""Порядок плагинов: состояние и порядок загрузки, и запись plugins.txt своими руками.

Оба маршрута умеют предпросмотр: без apply они ничего не меняют и работают даже при занятой
MO2. С apply - обычное изменение со всеми замками.
"""
import io
import os
import shutil

import mobase

from . import i18n
from .base import Domain, flag, stamp


class LoadOrder(Domain):

    def plugins_state(self, body):
        want = body.get('set') or {}
        apply_it = flag(body, 'apply')
        if not isinstance(want, dict) or not want:
            raise ValueError(i18n.t('err.needSet'))

        def f():
            pl = self.o.pluginList()
            known = set(pl.pluginNames())
            plan, missing = [], []
            for name, active in want.items():
                if name not in known:
                    missing.append(name)
                    continue
                now = bool(pl.state(name) & mobase.PluginState.ACTIVE)
                if now != bool(active):
                    plan.append({'plugin': name, 'from': now, 'to': bool(active)})
            if apply_it:
                for p in plan:
                    pl.setState(p['plugin'], mobase.PluginState.ACTIVE if p['to']
                                else mobase.PluginState.INACTIVE)
            res = {'applied': apply_it, 'changes': plan, 'unknown': missing}
            if apply_it and plan:
                res['file'] = self.write_plugins_txt(
                    {p['plugin'].lower(): p['to'] for p in plan})
            return res
        # Предпросмотр доступен всегда: он ничего не меняет.
        if not apply_it:
            return stamp(self.run_main(f), 'pluginState', applied=False)
        return self.change('pluginState', f)

    def write_plugins_txt(self, wanted):
        """Проставить звёздочки в plugins.txt профиля. Зовётся ИЗ главного потока.

        setState меняет список в памяти MO2, а файл она переписывает в свои моменты - обычно
        при выходе. Игра стартует именно с файла, и любой /refresh между правкой и выходом
        читает файл обратно, молча отменяя правку. Стоило это трёх сорванных прогонов: мост
        отвечал applied: true, а плагин в игре оставался выключенным.

        Пишем строго переставляя звёздочку в существующих строках. Набор строк и их порядок
        не трогаем вовсе - это дело MO2, и переписать файл целиком означало бы взять на себя
        и порядок загрузки, и признак esl, и всё остальное, что она туда кладёт.
        """
        path = os.path.join(self.o.profile().absolutePath(), 'plugins.txt')
        try:
            # newline='' обязателен: без него текстовый режим схлопывает CRLF в LF,
            # определение концов строк даёт LF, и файл переписывается чужим форматом
            raw = io.open(path, encoding='utf-8-sig', errors='replace',
                          newline='').read()
        except Exception as exc:
            return {'written': False, 'error': str(exc), 'path': path}
        eol = chr(13) + chr(10) if chr(13) + chr(10) in raw else chr(10)
        out, done = [], set()
        for line in raw.split(chr(10)):
            bare = line.rstrip(chr(13))
            name = bare[1:] if bare.startswith('*') else bare
            key = name.strip().lower()
            if key in wanted and name.strip():
                out.append(('*' if wanted[key] else '') + name)
                done.add(key)
            else:
                out.append(bare)
        while out and not out[-1]:
            out.pop()
        text = eol.join(out) + eol
        # Пишем через черновик и подмену, а не поверх оригинала. plugins.txt - единственный
        # файл пользователя, который мост правит своими руками, и запись поверх означала,
        # что сбой на ней (кончился диск, файл держит антивирус, поток обрывается при
        # закрытии MO2) оставит его усечённым или пустым. Пустой plugins.txt - это профиль
        # со всеми выключенными плагинами, и восстановить его человеку неоткуда.
        #
        # Подмена через os.replace обрыв исключает сама по себе, поэтому копия рядом нужна
        # не от неё, а от НАШЕЙ ошибки: если мост записал неверные состояния, из .bak
        # достаётся то, что было мгновением раньше. Отсюда и обновление на каждую запись -
        # копия недельной давности вернула бы заодно всё, что человек менял с тех пор.
        backup = path + '.bak'
        try:
            shutil.copyfile(path, backup)
            draft = path + '.new'
            io.open(draft, 'w', encoding='utf-8', newline='').write(text)
            os.replace(draft, path)
        except Exception as exc:
            return {'written': False, 'error': str(exc), 'path': path,
                    'backup': backup if os.path.isfile(backup) else ''}
        return {'written': True, 'path': path, 'changed': sorted(done),
                'backup': backup, 'notInFile': sorted(set(wanted) - done)}

    def plugins_order(self, body):
        """Полный порядок загрузки - тем списком, что отдал LOOT.

        Частичный список увёл бы неупомянутые плагины в непредсказуемое место, поэтому
        неполный набор отвергается целиком.
        """
        order = body.get('order') or []
        apply_it = flag(body, 'apply')
        if not isinstance(order, list) or not order:
            raise ValueError(i18n.t('err.needOrder'))
        report = self.limit('orderReportLimit')

        def f():
            pl = self.o.pluginList()
            known = list(pl.pluginNames())
            missing = sorted(set(known) - set(order))
            extra = sorted(set(order) - set(known))
            res = {'applied': False, 'count': len(order),
                   'notListed': missing[:report], 'unknown': extra[:report]}
            if missing or extra:
                res['error'] = i18n.t('err.orderIncomplete',
                                      missing=len(missing), extra=len(extra))
                return res
            before = sorted(known, key=lambda n: pl.loadOrder(n)
                            if pl.loadOrder(n) >= 0 else 10 ** 6)
            # Прежний порядок отдаётся ЦЕЛИКОМ и всегда: короче его не описать, а без него
            # операция необратима - плагинов под сотню, и какой где стоял, знать неоткуда.
            res['before'] = before
            res['undo'] = {'route': '/plugins/order',
                           'body': {'order': before, 'apply': True}}
            if apply_it:
                pl.setLoadOrder(order)
                self.o.refresh(True)
                res['applied'] = True
            return res
        if not apply_it:
            return stamp(self.run_main(f), 'pluginOrder', applied=False)
        return self.change('pluginOrder', f)
