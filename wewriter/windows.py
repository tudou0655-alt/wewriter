import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys

_mutex = None


def set_app_id():
    if os.name == "nt":
        ctypes.WinDLL("shell32").SetCurrentProcessExplicitAppUserModelID("WeWriter.Desktop")


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
