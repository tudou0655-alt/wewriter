import argparse
import json
import os
from pathlib import Path
import sys
import tkinter as tk
from tkinter import messagebox
from datetime import datetime, timedelta, timezone
from wewriter.db import Database
from wewriter.secrets import Vault
from wewriter.ui import App
from wewriter.windows import set_app_id, single_instance


def demo_data(db):
    if db.accounts():
        return
    from wewriter.api import digest
    db.add_account("示例 · 科技观察", "demo_technology")
    db.add_account("示例 · 产品笔记", "demo_product")
    sources = []
    for i, (name, title, text) in enumerate([
        ("示例 · 科技观察", "从信息收集到内容创作：让资料真正用起来", "这是一段演示素材，不代表真实公众号内容。整理信息时，应先记录来源，再提炼事实与观点。"),
        ("示例 · 产品笔记", "一个好的写作工具，应该保留哪些人工判断", "这是一段演示素材，不代表真实公众号内容。工具可以帮助整理资料，但选题判断和事实审核仍需要编辑参与。")]):
        url = f"https://mp.weixin.qq.com/s/demo-{i}"
        article = db.save_article({"source_key": digest(url), "account_name": name, "title": title, "url": url,
                                   "published_at": (datetime.now(timezone.utc) - timedelta(hours=i + 1)).isoformat()})
        db.execute("UPDATE articles SET content=?,content_hash=? WHERE id=?", (text, digest(text), article))
        sources.append({"article_id": article, "source_id": f"S{article}", "title": title, "url": url})
    job = db.job("demo:sample", db.config())
    db.save_draft(job["id"], "演示稿件｜让每日阅读变成可追溯的创作素材",
                  "> 本文为界面演示样例，未调用 API，也不是实际公众号文章。\n\n"
                  "## 从收集开始，保留来源\n\n每天的信息很多，真正有用的是能回到出处的信息。把文章标题、发布时间与原文链接一起保存，后续写作才有可靠的材料入口。[S1]\n\n"
                  "## 让工具整理，让编辑判断\n\n摘要可以帮助我们快速理解材料。选题是否成立、证据是否足够，仍需要编辑判断。[S2]\n\n"
                  "## 在工作台完成最后一步\n\n这里可以编辑标题与正文，保存版本，查看素材报告，并导出稿件。",
                  sources, "# 演示素材报告\n\n所有内容都是本地样例，用于检查界面。\n\n[S1] 示例 · 科技观察\n[S2] 示例 · 产品笔记\n\n未发生真实 API 调用。")
    db.update_job(job["id"], status="已完成", stage="完成", message="演示样例，未调用 API")


def smoke(app, root, output):
    results = []
    try:
        for name in ("概览", "公众号管理", "文章资料库", "稿件工作台", "运行记录", "设置"):
            app.show(name)
            root.update_idletasks()
            root.update()
            assert app.content.winfo_children(), name
            results.append(name)
        app.show("概览")
        Path(output).write_text(json.dumps({"ok": True, "pages": results, "database": str(app.db.path)}, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        Path(output).write_text(json.dumps({"ok": False, "pages": results, "error": str(exc)}, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    finally:
        root.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true", help="使用独立演示数据库，不调用 API")
    parser.add_argument("--minimized", action="store_true")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--smoke-test", type=Path, help="检查全部界面并写出检查结果，随后退出")
    parser.add_argument("--clear-data", action="store_true", help="备份后清空业务数据库，保留密钥、登录和设置")
    args = parser.parse_args()
    set_app_id()
    root = tk.Tk()
    root.withdraw()
    if not single_instance("WeWriter.Demo" if args.demo else "WeWriter.Desktop"):
        messagebox.showinfo("公众号工作台", "软件已经在运行，请查看任务栏或系统托盘。", parent=root)
        root.destroy()
        return
    base = args.data_dir or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / ("WeWriterDemo" if args.demo else "WeWriter")
    from wewriter import logs
    logs.setup(base)
    from wewriter import __version__
    logs.logger.info("启动 WeWriter %s，数据目录 %s", __version__, base)
    db = Database(base / "workspace.sqlite")
    db.recover()
    try:
        from wewriter.weread import cleanup_stale_profiles
        removed = cleanup_stale_profiles(base / "weread-edge-profile", older_than=600)
        if removed:
            logs.logger.info("已清理 %d 个残留 Edge 临时配置目录", removed)
    except Exception:
        logs.logger.exception("清理残留 Edge 配置目录失败")
    if args.clear_data:
        confirmed = messagebox.askyesno(
            "清空数据库",
            "将清空公众号、文章、稿件、任务记录和分析缓存。\n\n"
            "API 密钥、微信读书登录状态和软件设置会保留；自动任务会暂停。\n"
            "执行前会自动备份数据库。是否继续？",
            parent=root)
        if confirmed:
            backup = base / ("backup-before-clear-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".sqlite")
            db.backup(backup)
            removed = db.clear_all()
            total = sum(removed.values())
            messagebox.showinfo("清理完成", f"已清空 {total} 条业务记录。\n自动备份：\n{backup}", parent=root)
        root.destroy()
        return
    vault = Vault(base / "keys.dpapi.json")
    if args.demo:
        demo_data(db)
    app = App(root, db, vault, base, demo=args.demo)
    if args.smoke_test:
        root.after(50, lambda: smoke(app, root, args.smoke_test))
    elif args.minimized:
        root.after(50, app.minimize)
    else:
        root.deiconify()
    root.mainloop()


if __name__ == "__main__":
    main()
