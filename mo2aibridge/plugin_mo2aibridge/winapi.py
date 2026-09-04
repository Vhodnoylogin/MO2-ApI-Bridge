# -*- coding: utf-8 -*-
"""Слой операционной системы: окна и кнопки.

Ниже всех остальных слоёв. Ничего не знает ни про MO2, ни про HTTP - только про Windows.
Поэтому его можно проверять отдельно, не поднимая ни менеджер, ни сервер.
"""
import ctypes

from . import i18n

# Загрузка обёрнута намеренно. WinDLL на уровне модуля роняет импорт целиком, а вместе с ним
# и весь плагин - то есть из-за двух маршрутов работы с окнами отвалились бы остальные
# двадцать два. Здесь же отказ остаётся локальным: окна недоступны, всё прочее работает.
try:
    from ctypes import wintypes
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    shell32 = ctypes.WinDLL('shell32', use_last_error=True)
except Exception:
    wintypes = None
    user32 = None
    kernel32 = None
    shell32 = None

WM_CLOSE = 0x0010
BM_CLICK = 0x00F5
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE = -1


def available():
    return user32 is not None


def _require():
    if user32 is None:
        raise RuntimeError(i18n.t('err.windowsOnly'))


def window_text(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ''
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def class_name(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def children(hwnd):
    out = []

    def cb(h, _):
        out.append({'hwnd': int(h), 'class': class_name(h), 'text': window_text(h)})
        return True
    user32.EnumChildWindows(hwnd, WNDENUMPROC(cb), 0)
    return out


def windows_of(pid):
    """Верхнеуровневые окна процесса вместе с подписями их кнопок.

    Отдельно помечаются окна Qt. Qt рисует виджеты сам, нативных дочерних окон у него нет,
    и пустой список кнопок там означает "не вижу", а не "кнопок нет" - разница принципиальная:
    по такому окну нельзя заключить, что нажимать нечего.
    """
    _require()
    found = []

    def cb(hwnd, _):
        wpid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value == pid:
            kids = children(hwnd)
            btns = [k for k in kids if k['class'].lower().startswith('button') and k['text']]
            cn = class_name(hwnd)
            rec = {'hwnd': int(hwnd), 'title': window_text(hwnd), 'class': cn,
                   'visible': bool(user32.IsWindowVisible(hwnd)),
                   'children': len(kids), 'buttons': btns}
            if not kids and cn.startswith('Qt'):
                rec['qtWindow'] = True
            found.append(rec)
        return True
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def main_window(pid):
    """Главное видимое окно процесса, без чтения его заголовка.

    Отличается от windows_of принципиально: здесь не вызывается GetWindowTextW. Он шлёт
    окну WM_GETTEXT и ждёт ответа от ЕГО потока, а значит зависает, если тот занят. Для
    своего же процесса это тупик: поток сервера ждёт поток интерфейса, который в этот
    момент как раз ничего не разбирает.

    GetClassNameW, IsWindowVisible и IsWindowEnabled читают структуру окна и не ждут никого.
    """
    _require()
    found = []

    def visit(hwnd, _l):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            if class_name(hwnd).startswith('Qt'):
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(WNDENUMPROC(visit), 0)
    return found[0] if found else 0


def is_enabled(hwnd):
    """Принимает ли окно ввод. Выключенное верхнее окно означает, что поверх него модальное.

    Так видно замок самой MO2: пока она ждёт запущенную программу, её окно выключено. Свои
    виджеты Qt рисует внутри одного HWND, но включённость - свойство именно окна, и оно
    читается снаружи, в отличие от кнопок.
    """
    _require()
    return bool(user32.IsWindowEnabled(hwnd))


def is_window(hwnd):
    _require()
    return bool(user32.IsWindow(hwnd))


def close(hwnd):
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def click_by_caption(hwnd, caption):
    """Нажать кнопку по её подписи. Возвращает подпись нажатой или None.

    Только по тексту и никогда по координатам или порядку: в диалогах генераторов вроде
    DynDOLOD рядом стоят кнопки выбора параметров, и промах молча испортит результат.
    """
    want = (caption or '').strip().lower()
    if not want:
        return None
    for k in children(hwnd):
        if k['class'].lower().startswith('button') and \
                k['text'].replace('&', '').strip().lower() == want:
            user32.PostMessageW(k['hwnd'], BM_CLICK, 0, 0)
            return k['text']
    return None


# ---------------------------------------------------------------- типы вызовов
if wintypes is not None:
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [('hwnd', wintypes.HWND),
                    ('wFunc', wintypes.UINT),
                    ('pFrom', wintypes.LPCWSTR),
                    ('pTo', wintypes.LPCWSTR),
                    ('fFlags', ctypes.c_uint16),
                    ('fAnyOperationsAborted', wintypes.BOOL),
                    ('hNameMappings', ctypes.c_void_p),
                    ('lpszProgressTitle', wintypes.LPCWSTR)]

    def _declare():
        """Объявить, чем на самом деле являются аргументы и результаты.

        Умолчание ctypes - 32-битный int на всё, а дескриптор на x64 восьмибайтовый.
        Пока старшие байты нулевые, это сходит с рук; когда нет - в чужой процесс уходит
        обрезанное значение. Дешевле объявить, чем разбираться потом.
        """
        H, D, B, I = wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, ctypes.c_int
        HWND, LP = wintypes.HWND, wintypes.LPARAM
        for lib, name, args, ret in (
                (user32, 'GetWindowTextLengthW', (HWND,), I),
                (user32, 'GetWindowTextW', (HWND, wintypes.LPWSTR, I), I),
                (user32, 'GetClassNameW', (HWND, wintypes.LPWSTR, I), I),
                (user32, 'EnumWindows', (WNDENUMPROC, LP), B),
                (user32, 'EnumChildWindows', (HWND, WNDENUMPROC, LP), B),
                (user32, 'GetWindowThreadProcessId', (HWND, ctypes.POINTER(D)), D),
                (user32, 'IsWindowVisible', (HWND,), B),
                (user32, 'IsWindowEnabled', (HWND,), B),
                (user32, 'IsWindow', (HWND,), B),
                (user32, 'PostMessageW', (HWND, wintypes.UINT, wintypes.WPARAM, LP), B),
                (kernel32, 'CreateToolhelp32Snapshot', (D, D), H),
                (kernel32, 'Process32FirstW', (H, ctypes.c_void_p), B),
                (kernel32, 'Process32NextW', (H, ctypes.c_void_p), B),
                (kernel32, 'CloseHandle', (H,), B),
                (kernel32, 'WaitForSingleObject', (H, D), D),
                (kernel32, 'GetExitCodeProcess', (H, ctypes.POINTER(D)), B),
                (kernel32, 'GetProcessId', (H,), D),
                (shell32, 'SHFileOperationW', (ctypes.c_void_p,), I)):
            fn = getattr(lib, name)
            fn.argtypes = list(args)
            fn.restype = ret

    _declare()


# ---------------------------------------------------------------- процессы
if wintypes is not None:
    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD),
                    ('cntUsage', wintypes.DWORD),
                    ('th32ProcessID', wintypes.DWORD),
                    ('th32DefaultHeapID', ctypes.POINTER(ctypes.c_ulong)),
                    ('th32ModuleID', wintypes.DWORD),
                    ('cntThreads', wintypes.DWORD),
                    ('th32ParentProcessID', wintypes.DWORD),
                    ('pcPriClassBase', ctypes.c_long),
                    ('dwFlags', wintypes.DWORD),
                    ('szExeFile', ctypes.c_wchar * 260)]


WAIT_TIMEOUT = 0x00000102
INFINITE = 0xFFFFFFFF


def process_id(handle):
    """Идентификатор процесса по его дескриптору. 0, если узнать не вышло.

    GetProcessId живёт в kernel32, а не в user32 - обращение к нему через user32 молча
    оборачивалось нулём, и /run всю дорогу отдавал pid 0.
    """
    if kernel32 is None:
        return 0
    try:
        return int(kernel32.GetProcessId(wintypes.HANDLE(int(handle))))
    except Exception:
        return 0


def wait_process(handle, timeout_sec=None):
    """Дождаться конца процесса по его дескриптору. Код возврата, или None по истечении срока.

    Ждём здесь, а не через IOrganizer.waitForApplication, намеренно. Тот вызов уходит в C++
    и не отпускает GIL, поэтому вместе с главным потоком MO2 останавливается весь встроенный
    интерпретатор: HTTP-сервер перестаёт принимать даже /ping. Проверено - мост молчал ровно
    столько, сколько работала утилита, и ожил в ту же секунду, когда она закрылась.
    """
    if kernel32 is None:
        _require()
    ms = INFINITE if timeout_sec is None else max(0, int(float(timeout_sec) * 1000))
    r = kernel32.WaitForSingleObject(wintypes.HANDLE(int(handle)), wintypes.DWORD(ms))
    if r == WAIT_TIMEOUT:
        return None
    code = wintypes.DWORD()
    kernel32.GetExitCodeProcess(wintypes.HANDLE(int(handle)), ctypes.byref(code))
    return int(code.value)


FO_DELETE = 0x0003
FOF_ALLOWUNDO = 0x0040
FOF_NOCONFIRMATION = 0x0010
FOF_SILENT = 0x0004


def recycle(path):
    """Отправить файл в Корзину. True, если получилось.

    Именно в Корзину, а не мимо: удаление архива - единственное, что мост делает с файлом
    безвозвратно, и пусть у человека остаётся способ передумать. Файл окончен двойным нулём -
    список путей в SHFileOperation разделяется нулями и завершается ещё одним.
    """
    if shell32 is None:
        _require()
    op = SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    op.pFrom = path + chr(0) + chr(0)
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
    return shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not op.fAnyOperationsAborted


def pids_by_exe(names):
    """Идентификаторы живых процессов с такими именами файлов (регистр не важен).

    Спрашиваем систему, а не только собственный учёт запусков: программу мог запустить сам
    пользователь кнопкой в MO2, а не мост, - и тогда в наших списках её нет. Ошибка обхода
    трактуется как "ничего не найдено": молчаливое "не знаю" здесь безопаснее исключения,
    потому что вызывающий и так проверит собственный учёт.
    """
    want = {str(n).lower() for n in names if n}
    if not want or kernel32 is None:
        return {}
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE:
        return {}
    found = {}
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        more = kernel32.Process32FirstW(snap, ctypes.byref(e))
        while more:
            if e.szExeFile.lower() in want:
                found[int(e.th32ProcessID)] = e.szExeFile
            more = kernel32.Process32NextW(snap, ctypes.byref(e))
    except Exception:
        return found
    finally:
        kernel32.CloseHandle(snap)
    return found
