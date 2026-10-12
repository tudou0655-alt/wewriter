"""Render only this application's demo windows; never capture the desktop."""
import ctypes
import json
import os
from pathlib import Path
import sys
import time
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main import demo_data
from wewriter.db import Database
from wewriter.secrets import Vault
from wewriter.ui import App
from PIL import ImageGrab

target = Path(__file__).resolve().parents[1] / ".local-data" / "ui-review"
target.mkdir(parents=True, exist_ok=True)
db = Database(target / "demo.sqlite")
demo_data(db)
root = tk.Tk()
app = App(root, db, Vault(target / "keys.json"), target, demo=True)
if os.environ.get('WEWRITER_COMPACT_UI') == '1':
    root.geometry('1024x600')
failures = []
root.report_callback_exception = lambda *args: failures.append(str(args[1]))
try:
    root.update()
    user32 = ctypes.WinDLL("user32")
    user32.GetParent.argtypes = [ctypes.c_void_p]
    user32.GetParent.restype = ctypes.c_void_p
    handle = user32.GetParent(root.winfo_id()) or root.winfo_id()
    for index, page in enumerate(("概览", "公众号管理", "文章资料库", "稿件工作台", "运行记录", "设置")):
        app.show(page)
        root.update_idletasks()
        root.update()
        time.sleep(0.1)
        ImageGrab.grab(window=handle).save(target / f"{index + 1}.png")
    # Test actual editor/save callbacks with synthetic local demo data.
    app.show("稿件工作台")
    root.update()
    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)
    app.show('设置')
    root.update()
    notebook = next(w for w in descendants(app.content) if w.winfo_class() == 'TNotebook')
    assert len(notebook.tabs()) == 4
    for n, tab in enumerate(notebook.tabs()):
        notebook.select(tab)
        root.update()
        ImageGrab.grab(window=handle).save(target / f'settings-{n}.png')
    app.show('稿件工作台')
    root.update()
    texts = [w for w in descendants(app.content) if isinstance(w, tk.Text)]
    texts[0].insert("end", "\n\n界面交互测试。")
    root.update()
    assert app.editor_dirty
    save = next(w for w in descendants(app.content) if w.winfo_class() == "TButton" and w.cget("text") == "保存版本")
    save.invoke()
    root.update()
    assert not app.editor_dirty
    assert db.rows("SELECT revision FROM drafts")[0]["revision"] >= 2
    if failures:
        raise AssertionError(failures)
    (target / "result.json").write_text(json.dumps({"ok": True, "pages": 6, "editor_save": True}, indent=2), encoding="utf-8")
finally:
    root.destroy()
