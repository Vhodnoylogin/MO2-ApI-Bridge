# -*- coding: utf-8 -*-
"""The operating-system layer: windows and buttons.

Below every other layer. It knows nothing of MO2 or of HTTP - only of Windows. So it can be
exercised on its own, with neither the manager nor the server running.
"""
import ctypes

from . import i18n

# The load is wrapped deliberately. A WinDLL at module level brings the whole import down,
# and the entire plugin with it - meaning two window routes would take the other twenty-two
# along. Here the failure stays local: windows are unavailable, everything else works.
try:
    from ctypes import wintypes
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    shell32 = ctypes.WinDLL('shell32', use_last_error=True)
    advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
except Exception:
    wintypes = None
    user32 = None
    kernel32 = None
    shell32 = None
    advapi32 = None

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
    """A process's top-level windows together with the captions of their buttons.

    Qt windows are marked separately. Qt draws its widgets itself and has no native child
    windows, so an empty button list there means "cannot see", not "there are none" - a
    material difference: such a window says nothing about whether there is anything to press.
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
    """A process's main visible window, without reading its title.

    Materially different from windows_of: GetWindowTextW is never called here. It sends
    WM_GETTEXT to the window and waits for ITS thread to answer, so it hangs when that thread
    is busy. For our own process that is a deadlock: the server thread waits on the UI
    thread, which at that very moment is not dispatching anything.

    GetClassNameW, IsWindowVisible and IsWindowEnabled read the window structure and wait for
    nobody.
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
    """Does the window accept input. A disabled top-level window means a modal sits over it.

    This is how MO2's own lock becomes visible: while it waits for a launched program its
    window is disabled. Qt draws its widgets inside a single HWND, but being enabled is a
    property of the window itself, and unlike the buttons it can be read from outside.
    """
    _require()
    return bool(user32.IsWindowEnabled(hwnd))


def is_window(hwnd):
    _require()
    return bool(user32.IsWindow(hwnd))


def close(hwnd):
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def click_by_caption(hwnd, caption):
    """Press a button by its caption. Returns the caption pressed, or None.

    By text only and never by coordinates or order: in generator dialogs such as DynDOLOD the
    option buttons sit side by side, and a miss silently ruins the result.
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


# ---------------------------------------------------------------- call signatures
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
        """Declare what the arguments and results actually are.

        The ctypes default is a 32-bit int for everything, while a handle on x64 is eight
        bytes. As long as the high bytes are zero this goes unpunished; when they are not, a
        truncated value is handed to another process. Declaring is cheaper than working that
        out later.
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
                (shell32, 'SHFileOperationW', (ctypes.c_void_p,), I),
                (advapi32, 'CredReadW', (wintypes.LPCWSTR, D, D, ctypes.c_void_p), B),
                (advapi32, 'CredFree', (ctypes.c_void_p,), None)):
            fn = getattr(lib, name)
            fn.argtypes = list(args)
            fn.restype = ret

    _declare()


# ---------------------------------------------------------------- processes
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
    """A process id from its handle. 0 when it cannot be determined.

    GetProcessId lives in kernel32, not in user32 - calling it through user32 silently came
    back as zero, and /run reported pid 0 the whole way along.
    """
    if kernel32 is None:
        return 0
    try:
        return int(kernel32.GetProcessId(wintypes.HANDLE(int(handle))))
    except Exception:
        return 0


def wait_process(handle, timeout_sec=None):
    """Wait for a process to end, by handle. The exit code, or None once the time is up.

    We wait here rather than through IOrganizer.waitForApplication, deliberately. That call
    goes into C++ and never releases the GIL, so the whole embedded interpreter stops along
    with MO2's main thread: the HTTP server stops accepting even /ping. Observed in practice -
    the bridge went quiet for exactly as long as the tool ran, and came back the second it
    closed.
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
FOF_NOERRORUI = 0x0400


def recycle(path):
    """Send a file to the Recycle Bin. True if it worked.

    To the Bin and not past it: deleting an archive is the one thing the bridge does to a
    file irrevocably, so let the person keep a way to change their mind. The path is
    terminated with a double null - SHFileOperation separates its list of paths with nulls
    and ends it with one more.
    """
    if shell32 is None:
        _require()
    op = SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    op.pFrom = path + chr(0) + chr(0)
    # FOF_NOERRORUI is mandatory: FOF_SILENT only hides the progress window, the error window
    # stays - and pops up modal, without an owner, on Qt's main thread. A file in use (an
    # archive open in 7-Zip, being read by an antivirus, sitting on a dropped network drive)
    # hung the whole of MO2 that way until a button was pressed, and the window could well be
    # behind MO2's own. With this flag the failure comes back as a code, not as a window.
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    return shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not op.fAnyOperationsAborted


def pids_by_exe(names):
    """Ids of live processes with these executable names (case does not matter).

    We ask the system, not only our own launch bookkeeping: a program may have been started
    by the user with a button in MO2 rather than by the bridge, in which case our lists do
    not have it. A failure to enumerate is treated as "found nothing": a silent "do not know"
    is safer here than an exception, because the caller checks its own bookkeeping anyway.
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


# ---------------------------------------------------------------- credential store
CRED_TYPE_GENERIC = 1

if wintypes is not None:
    class CREDENTIALW(ctypes.Structure):
        _fields_ = [('Flags', wintypes.DWORD),
                    ('Type', wintypes.DWORD),
                    ('TargetName', wintypes.LPWSTR),
                    ('Comment', wintypes.LPWSTR),
                    ('LastWritten', wintypes.FILETIME),
                    ('CredentialBlobSize', wintypes.DWORD),
                    ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)),
                    ('Persist', wintypes.DWORD),
                    ('AttributeCount', wintypes.DWORD),
                    ('Attributes', ctypes.c_void_p),
                    ('TargetAlias', wintypes.LPWSTR),
                    ('UserName', wintypes.LPWSTR)]


def read_generic_credential(target):
    """A secret from the Windows credential store by entry name, or None.

    It reads the entry of the same user the process runs as - that is, exactly what the
    owning program put there. The string returned is never logged anywhere: that is the
    caller's concern, this is a read and nothing else. MO2 stores the key as UTF-16, but
    UTF-8 is accepted just in case.
    """
    if advapi32 is None or not target:
        return None
    pcred = ctypes.c_void_p()
    if not advapi32.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)):
        return None
    try:
        cred = ctypes.cast(pcred, ctypes.POINTER(CREDENTIALW)).contents
        n = int(cred.CredentialBlobSize)
        raw = bytes(bytearray(cred.CredentialBlob[i] for i in range(n))) if n else b''
    finally:
        advapi32.CredFree(pcred)
    for enc in ('utf-16-le', 'utf-8'):
        try:
            text = raw.decode(enc).strip('\x00').strip()
        except UnicodeDecodeError:
            continue
        if text and all(32 <= ord(ch) < 127 for ch in text):
            return text
    return None
