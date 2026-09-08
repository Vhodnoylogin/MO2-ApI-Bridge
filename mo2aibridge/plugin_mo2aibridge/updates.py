# -*- coding: utf-8 -*-
"""Обновления модов: единственная точка, где сборка спрашивает Nexus, есть ли файл новее.

Мост спрашивает Nexus сам - через `IOrganizer.createNexusBridge()`, то есть тем же путём и тем
же ключом API, которыми пользуется MO2. Чаты и скрипты ничего не сравнивают: они получают от
моста и факты, и решение, а помнить результат - дело индекса.

Правила решения перенесены из прежнего разборщика страниц (`tools\\update-scan.py`) и живут
теперь здесь, в одном месте:

- **Номера версий не сравниваются никогда.** Их печатает автор руками, и они расходятся между
  шапкой страницы, строкой файла и meta.ini. Решают два факта: какие файлы страницы мы
  скачивали (по fileID из `.meta` рядом с архивом в `downloads\\`) и в каком разделе каждый
  файл лежит сейчас. Скачанный файл, уехавший в Old files, устарел по определению; скачанный
  файл в живом разделе актуален, пока страница не предлагает файл новее той же роли.
- **Роль** - имя файла без версии и без шума, но со всем остальным: JContainers SE и
  JContainers VR, PapyrusUtil AE и PapyrusUtil GOG остаются разными ролями и не выдают себя
  за обновления друг друга. Патч и ресурс - другая роль, чем основной файл.
- **Сомнение трактуется как «есть обновление».** Всё, что не сошлось уверенно, попадает
  в CANNOT-MATCH, а не молча объявляется актуальным: короткий список пользователь разберёт сам.

Вердикты - те же слова, что в индексе сборки: UPDATE-AVAILABLE, UP-TO-DATE, CANNOT-MATCH,
REUPLOADED, DOWNLOADED-NOT-INSTALLED, NO-NEXUS-ID, ERROR.
"""
import calendar
import datetime
import io
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

from . import i18n, winapi
from .base import Domain, one, safe

DAY = 86400

# Разделы файлов Nexus по номеру категории, как их отдаёт API.
CATEGORY = {1: 'main', 2: 'update', 3: 'optional', 4: 'old', 5: 'miscellaneous',
            6: 'deleted', 7: 'archived'}
LIVE = ('main', 'update', 'optional', 'miscellaneous')

RE_VER = re.compile(r'\bv?\d+[\d._-]*[a-z]?\b')
RE_RES = re.compile(r'^\d+k$')
RE_PUNCT = re.compile(r'[^a-z0-9Ѐ-ӿ ]+')
RE_NOISE = re.compile(r'\b(main|file|files|version|ver|final|the|of|and|for)\b')

# Два файла, чьи имена различаются только внутри одной из этих групп, - соседи, а не преемники:
# сборка под GOG не обновление к сборке под Steam, тело UNP не обновление к CBBE.
STORE = set(['gog', 'epic', 'steam', 'gamepass', 'ms'])
BODY = set(['cbbe', 'unp', 'unpb', 'uunp', 'bhunp', '3ba', '3bbb', 'himbo', 'sos', 'tng', 'sam'])
# Метки редакции идут в паре со сменой магазина или тела, но сами по себе соседа не делают:
# сборка AE действительно может быть преемницей SE, и это как раз тот случай, о котором надо
# сообщить.
EDITION = set(['se', 'sse', 'ae', 'vr', 'le', 'oldrim', 'special', 'anniversary', 'edition'])

RE_EPOCH = re.compile(r'-(\d{10})\.[0-9a-z]+$', re.I)
RE_ISO = re.compile(r'\b(20\d\d)-(\d\d)-(\d\d)T(\d\d)-(\d\d)Z\b')


# ================================================================ чистые правила
def sibling(a, b):
    """Тот же продукт, другой магазин или другое тело - никогда не обновление друг к другу."""
    d = a ^ b
    return bool(d) and d <= (STORE | BODY | EDITION) and bool(d & (STORE | BODY))


def arc_time(name):
    """Когда был загружен на Nexus архив с таким именем - читается из самого имени.

    MO2 хранит каноническое имя Nexus, оканчивающееся временем загрузки в секундах; загрузки
    через новое именование сайта несут отметку ISO. Так или иначе момент в имени, поэтому
    файл, уже удалённый со страницы, всё равно датируется."""
    m = RE_EPOCH.search(name or '')
    if m:
        return int(m.group(1))
    m = RE_ISO.search(name or '')
    if m:
        return int(calendar.timegm((int(m.group(1)), int(m.group(2)), int(m.group(3)),
                                    int(m.group(4)), int(m.group(5)), 0, 0, 0, 0)))
    return None


def stamp(ts):
    """Дата словами из секунд с эпохи; пусто, если момента нет или он вне диапазона."""
    if not ts:
        return ''
    try:
        return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime('%Y-%m-%d')
    except (OSError, OverflowError, ValueError):
        return ''


def _drop_version(m):
    """4k и 2k - разрешения текстур, а не версии: убрав их, файл 4K и файл 2K стали бы одной
    ролью, и пара обернулась бы призрачным обновлением."""
    t = m.group(0)
    return t if RE_RES.match(t) else ' '


def role(s):
    """Имя файла, сведённое к тому, что это: версия убрана, вариант оставлен."""
    if not s:
        return ''
    s = s.lower()
    s = RE_VER.sub(_drop_version, s)
    s = RE_PUNCT.sub(' ', s)
    s = RE_NOISE.sub(' ', s)
    return ' '.join(sorted(set(s.split())))


def _brief(f):
    return {'name': f['name'], 'fileName': f['fileName'], 'fileId': f['fileId'],
            'version': f['version'], 'category': f['category'], 'time': f['time'],
            'date': stamp(f['time'])}


def decide(files, got_ids, got_names, installed_time, limit=3):
    """Решение по одной странице. Чистая функция: файлы страницы, что из них скачано, когда
    загружен архив, из которого собран мод.

    files          [{name, fileName, fileId, version, category, time}], category - слово
    got_ids        fileID скачанных файлов этой страницы (из .meta в downloads)
    got_names      имена архивов этой страницы, лежащих в downloads
    installed_time момент загрузки архива, из которого собран мод, или None
    """
    for f in files:
        f['got'] = f['fileId'] in got_ids or (f['fileName'] or '') in got_names
    live_main = sorted([f for f in files if f['category'] in ('main', 'update')],
                       key=lambda f: -(f['time'] or 0))
    res = {'items': [], 'extra': [], 'newest': _brief(live_main[0]) if live_main else None}
    got = [f for f in files if f['got']]

    if not got:
        # Ни один файл страницы не помечен скачанным, а мод стоит: строка, с которой качали,
        # исчезла - автор заменил файл, а не убрал в Old files. Момент загрузки архива есть
        # в его имени, так что сравнить можно и с ним.
        res['extra'] = [_brief(f) for f in live_main[:4]]
        if installed_time and live_main and (live_main[0]['time'] or 0) > installed_time + DAY:
            res.update(verdict='REUPLOADED', why='upd.replaced',
                       whyArgs={'mine': stamp(installed_time), 'theirs': stamp(live_main[0]['time'])})
        else:
            res.update(verdict='CANNOT-MATCH', why='upd.unmarked', whyArgs={})
        return res

    # Что у нас есть - по одному на роль, побеждает новейший
    held = {}
    for g in got:
        k = role(g['name'])
        if k not in held or (g['time'] or 0) > (held[k]['time'] or 0):
            held[k] = g

    for k, mine in sorted(held.items()):
        cands = [f for f in files
                 if f['category'] in LIVE and role(f['name']) == k
                 and (f['time'] or 0) > (mine['time'] or 0) and not f['got']
                 # та же версия, загруженная через минуты, - сосед одного выпуска, а не новее
                 and not (f['version'] and f['version'] == mine['version']
                          and (f['time'] - (mine['time'] or 0)) < DAY)]
        cands.sort(key=lambda f: -(f['time'] or 0))
        item = {'role': k, 'mine': _brief(mine), 'newer': [_brief(f) for f in cands[:limit]]}
        if cands:
            item['state'] = 'UPDATE-AVAILABLE'
        elif mine['category'] == 'old':
            # Наш файл убран в Old files, и ничего с тем же именем его не заменило: автор
            # переименовал или слил файлы, страницу надо смотреть глазами
            item['state'] = 'CANNOT-MATCH'
            item['newer'] = [_brief(f) for f in live_main[:limit]]
        else:
            item['state'] = 'UP-TO-DATE'
        res['items'].append(item)

    newest_held = max([(g['time'] or 0) for g in got])
    taken = set((n['name'], n['time']) for it in res['items'] for n in it['newer'])
    held_tok = [set(role(g['name']).split()) for g in got]
    for f in files:
        if f['category'] not in ('main', 'update') or f['got']:
            continue
        if (f['time'] or 0) <= newest_held or (f['name'], f['time']) in taken:
            continue
        if f['version'] and any(f['version'] == g['version'] and (f['time'] - (g['time'] or 0)) < DAY
                                for g in got):
            continue                      # сосед того же выпуска, загружен рядом
        # сравнивается с каждым нашим файлом отдельно: страница может держать несколько, и
        # объединение их слов спрятало бы тот, чьим соседом кандидат является
        ft = set(role(f['name']).split())
        if any(sibling(ht, ft) for ht in held_tok):
            continue                      # сборка под GOG или другое тело, а не новее
        res['extra'].append(_brief(f))
    res['extra'].sort(key=lambda f: -(f['time'] or 0))
    hotfix = [f for f in res['extra'] if f['category'] == 'update']

    states = set(i['state'] for i in res['items'])
    newest_main = max([(f['time'] or 0) for f in live_main] or [0])

    # Новейший файл Main уже помечен скачанным: что бы ни лежало в Old files, это история
    # загрузок. Чего это не доказывает - что файл установлен: MO2 помнит, из какого архива
    # собран мод, и момент его загрузки есть в имени, так что их можно сравнить.
    top = [f for f in live_main if (f['time'] or 0) == newest_main]
    if top and any(f['got'] for f in top) and 'UPDATE-AVAILABLE' not in states:
        res['items'] = [i for i in res['items'] if i['state'] == 'UPDATE-AVAILABLE']
        if installed_time and installed_time < newest_main - DAY:
            res.update(verdict='DOWNLOADED-NOT-INSTALLED', why='upd.downloadedNotInstalled',
                       whyArgs={'theirs': stamp(newest_main), 'mine': stamp(installed_time)})
            res['extra'] = [_brief(f) for f in top]
        elif hotfix:
            res.update(verdict='UPDATE-AVAILABLE', why='upd.hotfix', whyArgs={})
        else:
            res.update(verdict='UP-TO-DATE', why='upd.current', whyArgs={})
            res['extra'] = []
        return res

    # Все наши файлы в Old files, а Main ушёл дальше них: автор переименовал файл между
    # выпусками, и это всё ещё обновление, а не сомнение
    all_old = all(i['mine']['category'] == 'old' for i in res['items'])
    if 'UPDATE-AVAILABLE' in states:
        res.update(verdict='UPDATE-AVAILABLE', why='upd.newer', whyArgs={})
    elif hotfix:
        res.update(verdict='UPDATE-AVAILABLE', why='upd.hotfix', whyArgs={})
    elif 'CANNOT-MATCH' in states and all_old and newest_main > newest_held + DAY:
        res.update(verdict='UPDATE-AVAILABLE', why='upd.renamed', whyArgs={})
    elif 'CANNOT-MATCH' in states:
        res.update(verdict='CANNOT-MATCH', why='upd.retired', whyArgs={})
    elif res['extra']:
        res.update(verdict='CANNOT-MATCH', why='upd.otherRole', whyArgs={})
    else:
        res.update(verdict='UP-TO-DATE', why='upd.current', whyArgs={})
    return res


# ================================================================ область
class Updates(Domain):

    def __init__(self, ctx, guard):
        super().__init__(ctx, guard)
        self._bridge = None
        self._how = ''
        # Каким путём спрашивается Nexus: '' - ещё не выяснено, 'bridge' - через мост MO2,
        # 'api' - напрямую, ключом MO2 из хранилища учётных данных Windows.
        self._mode = ''
        self._api_key = None
        self._quota = {}
        self._pending = {}
        self._lock = threading.Lock()
        self._seq = 0
        self._last_request = 0.0

    # ---- мост MO2 к Nexus ------------------------------------------------
    def _ensure_bridge(self):
        """Создать мост к Nexus один раз и подписаться на его ответы. Зовётся ИЗ главного потока.

        В MO2 2.5.2 подписка на filesAvailable из Python невозможна: сигнал несёт
        QList<ModRepositoryFileInfo*>, а PyQt такой тип не знает и отказывает на connect.
        Тогда мост MO2 не используется вовсе, и Nexus спрашивается напрямую (см. _api_files).
        """
        if self._bridge is not None:
            return self._bridge
        bridge = self.o.createNexusBridge()
        self._how = '%s/%s' % (_connect(bridge, 'filesAvailable', self._on_files),
                               _connect(bridge, 'requestFailed', self._on_failed))
        self._bridge = bridge
        self.note('nexus bridge: %s' % self._how)
        return bridge

    def _choose_mode(self, timeout):
        """Один раз на сессию: годится ли мост MO2, иначе - прямой запрос ключом MO2."""
        if self._mode:
            return self._mode
        try:
            self.run_main(self._ensure_bridge, timeout=timeout)
            self._mode = 'bridge'
        except Exception as exc:
            self.note('nexus bridge unusable (%s), asking the API directly' % exc)
            self._mode = 'api'
        return self._mode

    # ---- прямой запрос к API ---------------------------------------------
    def _key(self):
        """Ключ API Nexus - тот же, что хранит MO2, из хранилища учётных данных Windows.

        Читается один раз и живёт только в памяти этого объекта: наружу мост его не отдаёт
        ни одним маршрутом и в лог не пишет.
        """
        if self._api_key is None:
            target = self.cfg.get('updates', {}).get('credentialTarget') or ''
            self._api_key = winapi.read_generic_credential(target) or ''
        return self._api_key

    def _http_get(self, url, headers, timeout):
        """GET с заголовками; вынесен отдельно, чтобы проверки могли подставить свой ответ."""
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read().decode('utf-8', 'replace')

    def _api_files(self, game, mod_id, timeout):
        key = self._key()
        if not key:
            return None, i18n.t('upd.noApiKey')
        upd = self.cfg.get('updates', {})
        domains = self.cfg.get('nexusDomains') or {}
        domain = domains.get(game or '', self.cfg.get('nexusDomainDefault'))
        url = '%s/v1/games/%s/mods/%d/files.json' % (
            (upd.get('apiHost') or 'https://api.nexusmods.com').rstrip('/'), domain, int(mod_id))
        headers = {'apikey': key, 'Accept': 'application/json',
                   'Application-Name': 'MO2AIBridge',
                   'Application-Version': upd.get('appVersion') or ''}
        try:
            status, hdrs, body = self._http_get(url, headers, timeout)
        except urllib.error.HTTPError as e:
            return None, 'HTTP %d %s' % (e.code, (e.reason or '')[:80])
        except Exception as exc:
            return None, str(exc)[:200]
        low = {k.lower(): v for k, v in hdrs.items()}
        for h in ('x-rl-daily-remaining', 'x-rl-hourly-remaining', 'x-rl-daily-limit'):
            if h in low:
                self._quota[h[5:]] = low[h]
        try:
            data = json.loads(body)
        except Exception as exc:
            return None, 'bad json: %s' % exc
        out = []
        for f in data.get('files') or []:
            cat = f.get('category_id')
            try:
                cat = int(cat)
            except Exception:
                cat = 0
            out.append({'name': f.get('name') or '', 'fileName': f.get('file_name') or '',
                        'fileId': int(f.get('file_id') or 0),
                        'version': str(f.get('version') or ''),
                        'category': CATEGORY.get(cat, str(cat)),
                        'time': int(f.get('uploaded_timestamp') or 0)})
        return out, ''

    def _take(self, user_data, game=None, mod_id=None):
        with self._lock:
            slot = self._pending.pop(str(user_data), None)
            if slot is None and mod_id is not None:
                # Подпись не совпала - ищем по паре игра/мод: это тот же ответ
                for key, cand in list(self._pending.items()):
                    if cand['modId'] == int(mod_id):
                        return self._pending.pop(key)
            return slot

    def _on_files(self, game, mod_id, user_data, files):
        slot = self._take(user_data, game, mod_id)
        if slot is None:
            return
        slot['files'] = [_file_record(f) for f in (files or [])]
        slot['event'].set()

    def _on_failed(self, game, mod_id, *rest):
        # Сигнатуры различаются между сборками MO2: (game, modId, fileId, userData, error)
        # или (game, modId, userData, error). Сообщение об ошибке - всегда последнее.
        error = str(rest[-1]) if rest else ''
        user_data = rest[-2] if len(rest) >= 2 else None
        slot = self._take(user_data, game, mod_id)
        if slot is None:
            return
        slot['error'] = error or 'request failed'
        slot['event'].set()

    def _fetch(self, game, mod_id, timeout):
        """Список файлов страницы или ошибка.

        Через мост MO2 ответ приходит сигналом в главном потоке, а поток сервера ждёт его
        событием; напрямую - обычный HTTP из потока сервера. Пауза между запросами в обоих
        случаях: у API Nexus дневной лимит.
        """
        delay = float(self.cfg.get('updates', {}).get('delaySec') or 0)
        wait_for = self._last_request + delay - time.time()
        if wait_for > 0:
            time.sleep(wait_for)
        self._last_request = time.time()
        if self._choose_mode(timeout) == 'api':
            return self._api_files(game, mod_id, timeout)
        self._seq += 1
        token = 'upd%d' % self._seq
        slot = {'event': threading.Event(), 'files': None, 'error': '', 'modId': int(mod_id)}
        with self._lock:
            self._pending[token] = slot
        try:
            self.run_main(lambda: self._ensure_bridge().requestFiles(game, int(mod_id), token),
                          timeout=timeout)
        except Exception as exc:
            with self._lock:
                self._pending.pop(token, None)
            return None, str(exc)
        if not slot['event'].wait(timeout):
            with self._lock:
                self._pending.pop(token, None)
            return None, i18n.t('upd.timeout', sec=timeout)
        return slot['files'], slot['error']

    # ---- местные факты ---------------------------------------------------
    def _downloads_meta(self):
        """Что мы скачивали: по .meta рядом с архивами. modID -> {fileID: имя архива}."""
        root = safe(self.o.downloadsPath, '') or ''
        got = {}
        try:
            names = os.listdir(root)
        except Exception:
            return got, root
        for name in names:
            if not name.lower().endswith('.meta'):
                continue
            mod_id = file_id = None
            try:
                with io.open(os.path.join(root, name), encoding='utf-8', errors='replace') as fh:
                    for line in fh:
                        k, _, v = line.strip().partition('=')
                        if k == 'modID':
                            mod_id = int(v or 0)
                        elif k == 'fileID':
                            file_id = int(v or 0)
                        if mod_id is not None and file_id is not None:
                            break
            except Exception:
                continue
            if mod_id:
                got.setdefault(mod_id, {})[file_id or 0] = name[:-5]
        return got, root

    def _installed_time(self, arc, root):
        if not arc:
            return None
        t = arc_time(arc)
        if t:
            return t
        try:
            return int(os.path.getmtime(os.path.join(root, arc)))
        except OSError:
            return None

    def _targets(self, names, want_all, offset, limit):
        """Карточки модов для проверки. Зовётся В главном потоке."""
        ml = self.o.modList()
        if want_all:
            names = [n for n in ml.allModsByProfilePriority()]
        out, total = [], 0
        for n in names:
            m = ml.getMod(n)
            if m is None:
                out.append({'mod': n, 'missing': True})
                continue
            if want_all and safe(m.isSeparator, False):
                continue
            total += 1
            if want_all and (total <= offset or len(out) >= limit):
                continue
            out.append({'mod': n,
                        'nexusId': int(safe(m.nexusId, 0) or 0),
                        'game': safe(m.gameName, '') or safe(
                            lambda: self.o.managedGame().gameShortName(), ''),
                        'installationFile': safe(m.installationFile, '') or '',
                        'version': safe(lambda: m.version().displayString(), ''),
                        'mo2NewestVersion': safe(lambda: m.newestVersion().displayString(), ''),
                        'ignoredVersion': safe(lambda: m.ignoredVersion().displayString(), '')})
        return out, total

    # ---- маршрут --------------------------------------------------------
    def updates(self, q):
        """GET /updates?mod=A&mod=B  или  /updates?all=1&offset=0&limit=50

        Проверка по живому Nexus через MO2. Работает и при занятой MO2: это чтение.
        """
        names = (q or {}).get('mod') or []
        if isinstance(names, str):
            names = [names]
        want_all = one(q, 'all', '') not in ('', '0', 'false', 'no')
        if not names and not want_all:
            raise ValueError(i18n.t('err.needModOrAll'))
        upd = self.cfg.get('updates', {})
        limit = int(one(q, 'limit', str(upd.get('maxPerCall') or 50)))
        offset = int(one(q, 'offset', '0') or 0)
        timeout = float(one(q, 'timeout', str(upd.get('timeoutSec') or 30)))
        started = time.time()

        targets, total = self.run_main(lambda: self._targets(names, want_all, offset, limit))
        got_all, dl_root = self._downloads_meta()
        rows = []
        for t in targets:
            rows.append(self._check_one(t, got_all, dl_root, timeout))
        res = {'count': len(rows), 'mods': rows, 'elapsedSec': round(time.time() - started, 1),
               'checked': sum(1 for r in rows if r.get('checked')),
               'rule': 'files-and-dates, versions never compared',
               # Каким путём спрошен Nexus и сколько запросов осталось по лимиту API
               'via': self._mode or 'none', 'quota': dict(self._quota)}
        if want_all:
            res.update(offset=offset, limit=limit, total=total,
                       more=offset + len(rows) < total)
        return res

    def _check_one(self, t, got_all, dl_root, timeout):
        row = dict(t)
        row['checked'] = False
        if t.get('missing'):
            row.update(verdict='ERROR', why=i18n.t('err.noSuchMod', mod=t['mod']))
            return row
        if not t['nexusId']:
            row.update(verdict='NO-NEXUS-ID', why=i18n.t('upd.noNexusId'))
            return row
        files, error = self._fetch(t['game'], t['nexusId'], timeout)
        if files is None:
            row.update(verdict='ERROR', why=i18n.t('upd.failed', error=error), error=error)
            return row
        got = got_all.get(t['nexusId'], {})
        got_names = set(got.values())
        inst_time = self._installed_time(t['installationFile'], dl_root)
        inst_id = next((fid for fid, arc in got.items() if arc == t['installationFile']), 0)
        d = decide(files, set(got), got_names, inst_time,
                   limit=int(self.cfg.get('updates', {}).get('newerLimit') or 3))
        row.update(checked=True, verdict=d['verdict'],
                   why=i18n.t(d['why'], **d['whyArgs']),
                   installed={'file': t['installationFile'], 'fileId': inst_id,
                              'time': inst_time, 'date': stamp(inst_time)},
                   downloaded=sorted(got.values()),
                   newest=d['newest'], items=d['items'], extra=d['extra'],
                   files=len(files))
        return row


# ================================================================ обвязка mobase
def _connect(bridge, name, callback):
    """Подписаться на ответ моста тем способом, какой есть у этой сборки MO2.

    В одних сборках это метод onFilesAvailable(callback), в других - сигнал Qt
    filesAvailable или filesAvailable_ с .connect(). Пробуются все, по порядку.
    """
    cap = name[0].upper() + name[1:]
    reg = getattr(bridge, 'on' + cap, None)
    if callable(reg):
        reg(callback)
        return 'on' + cap
    for attr in (name, name + '_'):
        sig = getattr(bridge, attr, None)
        connect = getattr(sig, 'connect', None)
        if callable(connect):
            connect(callback)
            return attr
    raise RuntimeError('nexus bridge has no %s' % name)


def _secs(value):
    """QDateTime, число или строка -> секунды с эпохи, либо 0."""
    if value is None:
        return 0
    f = getattr(value, 'toSecsSinceEpoch', None)
    if callable(f):
        return int(safe(f, 0) or 0)
    try:
        return int(value)
    except Exception:
        return 0


def _text(value):
    f = getattr(value, 'displayString', None)
    if callable(f):
        return safe(f, '') or ''
    return '' if value is None else str(value)


def _file_record(f):
    """Плоская запись файла из ModRepositoryFileInfo: только то, что нужно решению."""
    g = lambda attr, default=None: safe(lambda: getattr(f, attr), default)
    cat = g('fileCategory', 0)
    try:
        cat = int(cat)
    except Exception:
        cat = 0
    return {'name': _text(g('name', '')) or '',
            'fileName': _text(g('fileName', '')) or '',
            'fileId': int(g('fileID', 0) or 0),
            'version': _text(g('version', '')),
            'category': CATEGORY.get(cat, str(cat)),
            'time': _secs(g('fileTime'))}
