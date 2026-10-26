import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import time

_mutex = None


def set_app_id():
    if os.name == "nt":
        ctypes.WinDLL("shell32").SetCurrentProcessExplicitAppUserModelID("WeWriter.Desktop")


def set_dpi_aware():
    """Render at the screen's real resolution instead of letting Windows stretch a 96-DPI bitmap (blurry at 125%/150%).

    Must run before the first window is created; later calls cannot change the process mode.
    """
    if os.name != "nt":
        return
    user32 = ctypes.WinDLL("user32")
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # per-monitor v2, Windows 10 1703+
            return
    except AttributeError:
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()


def single_instance(name="WeWriter.Desktop"):
    global _mutex
    if os.name != "nt":
        return True
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    _mutex = kernel.CreateMutexW(None, False, "Local\\" + name)
    if not _mutex:
        raise OSError("无法创建单实例锁")
    return ctypes.get_last_error() != 183


def startup_enabled():
    if os.name != "nt":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            winreg.QueryValueEx(key, "WeWriter")
            return True
    except OSError:
        return False


def set_startup(enabled):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
        if enabled:
            if getattr(sys, "frozen", False):
                command = [sys.executable, "--minimized"]
            else:
                pythonw = Path(sys.executable).with_name("pythonw.exe")
                command = [str(pythonw), str(Path(__file__).resolve().parents[1] / "main.py"), "--minimized"]
            winreg.SetValueEx(key, "WeWriter", 0, winreg.REG_SZ, subprocess.list2cmdline(command))
        else:
            try:
                winreg.DeleteValue(key, "WeWriter")
            except FileNotFoundError:
                pass


def cf_html(fragment):
    """Windows "HTML Format" clipboard payload: a header of UTF-8 byte offsets around the fragment."""
    head = 'Version:0.9\r\nStartHTML:{:010d}\r\nEndHTML:{:010d}\r\nStartFragment:{:010d}\r\nEndFragment:{:010d}\r\n'
    pre = '<html><head><meta charset="utf-8"></head><body><!--StartFragment-->'
    post = '<!--EndFragment--></body></html>'
    start_html = len(head.format(0, 0, 0, 0))
    start_fragment = start_html + len(pre.encode('utf-8'))
    end_fragment = start_fragment + len(fragment.encode('utf-8'))
    end_html = end_fragment + len(post.encode('utf-8'))
    return (head.format(start_html, end_html, start_fragment, end_fragment) + pre + fragment + post).encode('utf-8') + b'\0'


def copy_to_clipboard(text, html=None):
    """Put text (and HTML, when given) on the Windows clipboard.

    Returns False off Windows or while another program holds the clipboard, so the page can fall back.
    """
    if os.name != "nt":
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel.GlobalAlloc.restype = wintypes.HANDLE
    kernel.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = [wintypes.HANDLE]
    kernel.GlobalFree.argtypes = [wintypes.HANDLE]
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterClipboardFormatW.restype = wintypes.UINT

    def put(fmt, data):
        handle = kernel.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE; the clipboard owns it once set
        if not handle:
            raise OSError("GlobalAlloc failed")
        pointer = kernel.GlobalLock(handle)
        if not pointer:
            kernel.GlobalFree(handle)
            raise OSError("GlobalLock failed")
        ctypes.memmove(pointer, data, len(data))
        kernel.GlobalUnlock(handle)
        if not user32.SetClipboardData(fmt, handle):
            kernel.GlobalFree(handle)
            raise OSError("SetClipboardData failed")

    for _ in range(10):  # another program may be holding the clipboard for a moment
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return False
    try:
        user32.EmptyClipboard()
        put(13, text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-16-le") + b"\0\0")  # CF_UNICODETEXT
        if html:
            put(user32.RegisterClipboardFormatW("HTML Format"), cf_html(html))
        return True
    except OSError:
        return False
    finally:
        user32.CloseClipboard()
