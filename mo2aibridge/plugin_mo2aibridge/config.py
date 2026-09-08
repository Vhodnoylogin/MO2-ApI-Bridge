# -*- coding: utf-8 -*-
"""Настраиваемые значения плагина: таймауты, лимиты списков, где искать 7-Zip.

Всё, что раньше было константой в коде, живёт здесь одним словарём умолчаний. Файл
`<PLUGIN_ID>-config.json` рядом с плагином создаётся при первом запуске из этих умолчаний,
а дальше читается поверх них: чего в файле нет, берётся из умолчаний, так что старый файл
переживает добавление новых ключей.

Абсолютных путей здесь нет. Кандидаты на 7z.exe складываются из переменных окружения
`ProgramFiles` и `ProgramFiles(x86)`, а не пишутся руками, - и дополняются поиском по PATH.
"""
import io
import json
import os
import shutil

from . import i18n


def _seven_zip_candidates():
    """Где обычно лежит 7z.exe. Установщик 7-Zip в PATH не пишет, поэтому смотрим сами."""
    out = []
    for var in ('ProgramFiles', 'ProgramW6432', 'ProgramFiles(x86)'):
        base = os.environ.get(var)
        if base:
            cand = os.path.join(base, '7-Zip', '7z.exe')
            if cand not in out:
                out.append(cand)
    return out


DEFAULTS = {
    # Секунды ожидания главного потока MO2 по видам работы. Ping короткий намеренно:
    # именно он нужен, когда MO2 не отвечает, и ответить он обязан быстро.
    'timeouts': {
        'ping': 15,
        'gameName': 10,
        'analyze': 600,
        'unpack': 1800,
        'vfsExport': 1800,
        'runWait': 3600,
    },
    # Сколько строк отдавать в списках added/overwritten/sample, чтобы ответ не разрастался.
    'listLimit': 200,
    'analyzeLimit': 80,
    'orderReportLimit': 20,
    # Хвост вывода 7-Zip, попадающий в текст ошибки распаковки.
    'unpackErrorTail': 300,
    # Пути к 7z.exe по порядку; пустой список означает «только поиск по PATH».
    'sevenZip': _seven_zip_candidates(),
    # Имя папки распаковки внутри %TEMP%; к нему добавляется уникальный хвост на каждый вызов.
    'unpackDirName': 'mo2aibridge-unpack',
    # Домен Nexus по имени игры MO2 - для ссылки на страницу мода.
    'nexusDomains': {
        'SkyrimSE': 'skyrimspecialedition',
        'SkyrimVR': 'skyrimspecialedition',
        'Skyrim': 'skyrim',
        'Fallout4': 'fallout4',
        'Fallout4VR': 'fallout4',
    },
    'nexusDomainDefault': 'skyrimspecialedition',
    # Корневые папки Data для запасного обхода VFS вширь.
    'walkRoots': ['meshes', 'textures', 'scripts', 'sound', 'music', 'interface', 'seq',
                  'strings', 'video', 'grass', 'lodsettings', 'shadersfx', 'skse', 'source'],
    # Проверка обновлений через Nexus: пауза между запросами бережёт дневной лимит API,
    # maxPerCall ограничивает один вызов /updates?all=1, newerLimit - сколько файлов новее
    # называть по каждой роли.
    'updates': {
        'delaySec': 0.5,
        'timeoutSec': 30,
        'maxPerCall': 50,
        'newerLimit': 3,
        # Прямой путь к API, когда мост MO2 из Python недоступен: ключ берётся из хранилища
        # учётных данных Windows под тем именем, под которым его держит сама MO2.
        'credentialTarget': 'ModOrganizer2_APIKEY',
        'apiHost': 'https://api.nexusmods.com',
        'appVersion': '2.1.0',
    },
}


def _merge(base, over):
    """Словарь поверх словаря: вложенные словари сливаются, остальное заменяется."""
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config(object):
    """Значения по ключам с умолчаниями. Экземпляр без файла - просто умолчания."""

    def __init__(self, values=None, path=None):
        self.path = path
        self.values = _merge(DEFAULTS, values)

    @classmethod
    def load(cls, path, note=None):
        """Прочитать файл; нет файла - записать умолчания и работать по ним.

        Испорченный файл не роняет плагин: сообщается через note, берутся умолчания.
        """
        note = note or (lambda _m: None)
        if not os.path.isfile(path):
            try:
                with io.open(path, 'w', encoding='utf-8') as fh:
                    json.dump(DEFAULTS, fh, ensure_ascii=False, indent=1)
            except Exception as exc:
                note(i18n.t('config.writeFailed', path=path, error=exc))
            return cls(None, path)
        try:
            with io.open(path, encoding='utf-8-sig') as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError(i18n.t('config.notObject'))
            return cls(data, path)
        except Exception as exc:
            note(i18n.t('config.unreadable', path=path, error=exc))
            return cls(None, path)

    def get(self, key, default=None):
        return self.values.get(key, default)

    def timeout(self, name):
        return float(self.values['timeouts'].get(name) or DEFAULTS['timeouts'][name])

    def seven_zip(self):
        """Путь к 7z.exe: сначала настроенные кандидаты, затем PATH. None, если нет нигде."""
        for p in self.values.get('sevenZip') or []:
            if p and os.path.isfile(p):
                return p
        return shutil.which('7z')
