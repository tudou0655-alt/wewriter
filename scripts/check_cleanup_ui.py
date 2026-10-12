"""Exercise cleanup/search callbacks on isolated synthetic data only."""
import sys
import tempfile
from pathlib import Path
import tkinter as tk
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from wewriter.db import Database
from wewriter.secrets import Vault
from wewriter.ui import App

base=Path('.local-data/cleanup-ui');base.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=base) as directory:
    p=Path(directory);db=Database(p/'test.sqlite')
    for i in range(61):
        j=db.job('test:'+str(i),db.config())
        db.save_draft(j['id'],'稿件'+str(i),'正文 needle' if i==3 else '普通正文',[],'报告')
        db.update_job(j['id'],status='已完成')
    root=tk.Tk();app=App(root,db,Vault(p/'keys'),p,demo=True)
    errors=[];root.report_callback_exception=lambda *a:errors.append(str(a[1]))
    def widgets(w):
        for child in w.winfo_children():
            yield child
            yield from widgets(child)
    def button(label):
        for w in widgets(app.content):
            if w.winfo_class() == 'TButton' and w.cget('text') == label:
                return w
            if w.winfo_class() == 'Menu':
                for i in range((w.index('end') or 0) + 1):
                    if w.type(i) == 'command' and w.entrycget(i, 'label') == label:
                        class Action:
                            def invoke(self, menu=w, index=i): return menu.invoke(index)
                        return Action()
        raise AssertionError('Missing action: ' + label)
    def tree():
        return next(w for w in widgets(app.content) if w.winfo_class()=='Treeview')
    try:
        app.show('稿件工作台');root.update()
        assert len(tree().get_children())==50
        button('下一页').invoke();root.update()
        assert len(tree().get_children())==11
        entry=next(w for w in widgets(app.content) if w.winfo_class()=='TEntry')
        entry.insert(0,'needle');button('搜索标题 / 正文').invoke();root.update()
        assert len(tree().get_children())==1
        with patch('wewriter.ui.messagebox.askyesno',return_value=False):
            button('清理所选稿件').invoke();root.update()
        assert len(db.rows('SELECT id FROM drafts WHERE cleared=0'))==61
        with patch('wewriter.ui.messagebox.askyesno',return_value=True):
            button('清理所选稿件').invoke();root.update()
        assert len(db.rows('SELECT id FROM drafts WHERE cleared=0'))==60
        assert app.draft_id is None
        with patch('wewriter.ui.messagebox.askyesno',return_value=True):
            button('一键清空稿件').invoke();root.update()
        assert not db.rows('SELECT id FROM drafts WHERE cleared=0')
        assert not db.rows('SELECT * FROM draft_versions')
        for i in range(3):
            db.save_article({'source_key':str(i),'account_name':'测试','title':'文章'+str(i),'url':'https://mp.weixin.qq.com/s/'+str(i),'published_at':'2026-10-10T00:00:00+00:00'})
        app.show('文章资料库');root.update()
        tree().selection_set(tree().get_children()[:2])
        with patch('wewriter.ui.messagebox.askyesno',return_value=True):
            button('清理所选文章').invoke();root.update()
        assert len(tree().get_children())==1
        with patch('wewriter.ui.messagebox.askyesno',return_value=True):
            button('一键清空资料库').invoke();root.update()
        assert not tree().get_children()
        assert not errors,errors
        print('PASS: 61 drafts pagination, body search, cancel, selected/all cleanup; article multiselect/all cleanup')
    finally:
        root.destroy()
