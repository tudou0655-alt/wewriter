import json
import os
from pathlib import Path
import queue
import re
import sqlite3
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from tkinter import font as tkfont
from tkinter.scrolledtext import ScrolledText
import uuid
import webbrowser
from datetime import datetime
from zoneinfo import ZoneInfo
from .api import ApiError, Model, Transport
from .config import due_key, validate
from .engine import Engine
from .logs import logger
from .windows import startup_enabled, set_startup

ERROR_LABELS = {'material': '素材不可用', 'no_accounts': '未添加公众号', 'collect': '采集失败',
                'auth': '登录/密钥', 'network': '网络', 'model_output': '模型输出', 'api': '接口错误',
                'config': '设置问题', 'cancelled': '已停止', 'internal': '程序异常'}
ERROR_HINTS = {
    'material': '这篇文章无法提供可用正文（已删除、验证页或图片为主）。批量任务会自动由下一篇补位；也可扩大时间范围后新建任务。',
    'no_accounts': '到“公众号管理”同步书架或启用公众号。',
    'collect': '所有公众号都没能读取列表。先在“公众号管理”点“测试采集”，失败时重新扫码登录。',
    'auth': '微信读书登录失效或 API Key/余额/权限有问题。微信读书类错误请重新扫码；HTTP 401/402/403 请检查设置中的 Key 和账户余额。',
    'network': '网络连接失败或超时。检查网络后重试，已完成的步骤不会重复计费。',
    'model_output': '模型两次输出都没通过格式或来源校验。直接重试通常即可；反复出现可换写作模型。',
    'api': '接口返回错误，详情见结果说明；可稍后重试。',
    'config': '请按结果说明调整设置后重试。',
    'internal': '程序内部异常，详情已写入数据目录 logs\\wewriter.log。可以先重试，反复出现请把日志发给开发者。',
}
BG = "#F6F7FC"
PANEL = "#FFFFFF"
INK = "#20243A"
MUTED = "#73788C"
GREEN = "#FF6B4A"
SIDEBAR = "#28304A"
PURPLE = "#6C63FF"
TEAL = "#20A486"
YELLOW = "#F4B942"
BORDER = "#E8EAF2"


def asset_path(name):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base / "assets" / name


def local_time(value):
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%m-%d %H:%M")
    except (ValueError, TypeError):
        return str(value)


def export_document(title, body, sources, markdown=True):
    """Create a reader-facing export without internal S/W citation markers."""
    headings = list(re.finditer(r'(?im)^#{1,6}\s*(?:参考来源|参考资料|资料来源|引用来源)\s*$', body))
    if headings:
        body = body[:headings[-1].start()].rstrip()
    cited = []
    for source_id in re.findall(r'\[([SW]\d+)\]', body):
        if source_id not in cited:
            cited.append(source_id)
    body = re.sub(r'\s*\[(?:S|W)\d+\]', '', body)
    body = re.sub(r'[ \t]+([，。！？；：,.!?;:])', r'\1', body)
    body = re.sub(r'\n{3,}', '\n\n', body).strip()
    by_id = {str(s.get('source_id')): s for s in sources if isinstance(s, dict)}
    selected = [by_id[sid] for sid in cited if sid in by_id]
    if not selected:
        selected = [s for s in sources if isinstance(s, dict) and s.get('url')]
    unique, seen = [], set()
    for source in selected:
        url = str(source.get('url') or '').strip()
        if not url or url in seen:
            continue
        seen.add(url)
        unique.append(source)
    if markdown:
        text = f"# {title.strip()}\n\n{body}"
        if unique:
            text += "\n\n## 参考资料网站\n\n" + "\n".join(
                f"- [{s.get('title') or s.get('account_name') or '资料来源'}]({s['url']})" for s in unique)
    else:
        text = f"{title.strip()}\n\n{body}"
        if unique:
            text += "\n\n参考资料网站\n\n" + "\n".join(
                f"- {s.get('title') or s.get('account_name') or '资料来源'}：{s['url']}" for s in unique)
    return text.strip() + "\n"


def source_website_text(body, sources):
    exported = export_document('', body, sources, markdown=False)
    marker = '\n参考资料网站\n\n'
    return exported.split(marker, 1)[1].strip() if marker in exported else '当前正文没有可显示的参考网站。'


class App:
    def __init__(self, root, db, vault, data_dir, demo=False):
        self.root, self.db, self.vault, self.data_dir, self.demo = root, db, vault, Path(data_dir), demo
        self.events = queue.Queue()
        self.engine = Engine(db, vault, lambda msg: self.events.put(("progress", msg)),
                             profile_dir=self.data_dir / 'weread-edge-profile')
        self.busy = False
        self.closed = False
        self.tray = None
        self.page = "概览"
        self.draft_id = None
        self.editor_dirty = False
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        self.compact = (screen_width < 1180 or screen_height < 800
                        or os.environ.get('WEWRITER_COMPACT_UI') == '1')
        self.root.title("公众号工作台" + (" · 演示数据" if demo else ""))
        try:
            self.window_icon = tk.PhotoImage(file=str(asset_path("wewriter-cat.png")))
            self.root.iconphoto(True, self.window_icon)
        except (tk.TclError, OSError):
            self.window_icon = None
        width = max(760, min(1220, screen_width - 40))
        height = max(540, min(820, screen_height - 80))
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(min(900, screen_width - 20), min(600, screen_height - 50))
        self.root.configure(bg=BG)
        tkfont.nametofont("TkDefaultFont").configure(family="Microsoft YaHei UI", size=10)
        self.style()
        self.build_shell()
        self.show("概览")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.report_callback_exception = self.report_callback_exception
        self.root.after(150, self.poll)
        self.root.after(2000, self.schedule_tick)

    def style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=INK, font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 21 if self.compact else 26, "bold"))
        style.configure("Sub.TLabel", foreground=MUTED, font=("Microsoft YaHei UI", 9 if self.compact else 10))
        style.configure("Eyebrow.TLabel", foreground=GREEN,
                        font=("Microsoft YaHei UI", 9 if self.compact else 10, "bold"))
        style.configure("Section.TLabel", foreground=INK,
                        font=("Microsoft YaHei UI", 12 if self.compact else 14, "bold"))
        style.configure("Card.TLabel", background=PANEL)
        style.configure("Number.TLabel", background=PANEL, foreground=GREEN,
                        font=("Microsoft YaHei UI", 24 if self.compact else 31, "bold"))
        style.configure("TButton", padding=(13 if self.compact else 17, 8 if self.compact else 10),
                        font=("Microsoft YaHei UI", 9 if self.compact else 10, "bold"),
                        background=PANEL, foreground=INK, borderwidth=1, relief="flat")
        style.map("TButton", background=[("active", "#FFF0EC"), ("pressed", "#FFE4DD")],
                  foreground=[("active", GREEN)])
        style.configure("Primary.TButton", background=GREEN, foreground="white", borderwidth=0)
        style.map("Primary.TButton", background=[("active", "#E95738"), ("pressed", "#D94A2C"),
                                                   ("disabled", "#E8AAA0")],
                  foreground=[("active", "white")])
        style.configure("TEntry", padding=9, fieldbackground="white", bordercolor=BORDER,
                        lightcolor=BORDER, darkcolor=BORDER)
        style.configure("TCombobox", padding=8, fieldbackground="white", bordercolor=BORDER,
                        lightcolor=BORDER, darkcolor=BORDER)
        style.configure("Treeview", background="white", fieldbackground="white", foreground=INK,
                        rowheight=36 if self.compact else 45, borderwidth=0,
                        font=("Microsoft YaHei UI", 9 if self.compact else 10))
        style.configure("Treeview.Heading", background="#ECEEFA", foreground=INK,
                        padding=10, relief="flat", font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Treeview.Heading", background=[("active", "#E1E3F5")])
        style.map("Treeview", background=[("selected", "#FFE4DC")], foreground=[("selected", INK)])
        style.configure("TLabelframe", background=PANEL, bordercolor=BORDER, relief="solid")
        style.configure("TLabelframe.Label", background=PANEL, foreground=INK,
                        font=("Microsoft YaHei UI", 11 if self.compact else 12, "bold"))
        style.configure("TCheckbutton", background=BG, padding=(0, 6))
        style.configure("TMenubutton", padding=(14, 9), background=PANEL, foreground=MUTED, borderwidth=0)
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(14 if self.compact else 22, 9 if self.compact else 12),
                        background='#ECEEFA', foreground=MUTED, font=("Microsoft YaHei UI", 10, "bold"))
        style.map("TNotebook.Tab", background=[('selected', PANEL), ('active', '#F7EDEA')],
                  foreground=[('selected', GREEN), ('active', INK)])
        style.configure("Horizontal.TProgressbar", background=GREEN, troughcolor="#E8EAF2", borderwidth=0)

    def build_shell(self):
        sidebar_width = 154 if self.compact else 200
        sidebar = tk.Frame(self.root, bg=SIDEBAR, width=sidebar_width)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Label(sidebar, text="灵感\n工作台", justify="left", bg=SIDEBAR, fg="white",
                 font=("Microsoft YaHei UI", 19 if self.compact else 24, "bold")).pack(
                     anchor="w", padx=18 if self.compact else 25, pady=(20 if self.compact else 32, 6))
        tk.Label(sidebar, text="发现热点  写出新意", bg=SIDEBAR, fg="#B9BEE0",
                 font=("Microsoft YaHei UI", 9 if self.compact else 10)).pack(
                     anchor="w", padx=18 if self.compact else 25, pady=(0, 26))
        self.nav = {}
        items = (("概览", "⌂"), ("公众号管理", "◎"), ("文章资料库", "▤"),
                 ("稿件工作台", "✎"), ("运行记录", "◷"), ("设置", "⚙"))
        for title, icon in items:
            button = tk.Button(sidebar, text=f"{icon}   {title}", anchor="w", relief="flat", bd=0,
                               padx=18 if self.compact else 22, pady=12, bg=SIDEBAR, fg="#E6E8F4",
                               activebackground=GREEN, activeforeground="white",
                               font=("Microsoft YaHei UI", 10, "bold"), command=lambda p=title: self.show(p))
            button.pack(fill="x", padx=10, pady=3)
            self.nav[title] = button
        tk.Label(sidebar, text="本地数据 · 云端智能\nv0.10.1  试用版", justify="left", bg=SIDEBAR,
                 fg="#AEB4D3", font=("Microsoft YaHei UI", 9)).pack(
                     side="bottom", anchor="w", padx=18 if self.compact else 25, pady=25)
        right = ttk.Frame(self.root)
        right.pack(side="left", fill="both", expand=True)
        self.content = ttk.Frame(right, padding=(16, 14, 16, 8) if self.compact else (28, 25, 28, 12))
        self.content.pack(fill="both", expand=True)
        footer = ttk.Frame(right, padding=(14, 7) if self.compact else (22, 10))
        footer.pack(fill="x", side="bottom", before=self.content)
        self.status = tk.StringVar(value="准备就绪。先添加公众号，再配置 API。" if not self.demo else "演示模式：仅本地样例，不调用 API。")
        ttk.Label(footer, textvariable=self.status, style="Sub.TLabel", wraplength=700).pack(side="left")
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=120)
        self.progress.pack(side="right")

    def heading(self, title, subtitle):
        # Dense work pages keep the primary action visible on 600 px-high screens.
        if not self.compact or self.page not in ("公众号管理", "稿件工作台"):
            ttk.Label(self.content, text="WEWRITER · 内容创作助手", style="Eyebrow.TLabel").pack(anchor="w")
        ttk.Label(self.content, text=title, style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.content, text=subtitle, style="Sub.TLabel", wraplength=880).pack(
            anchor="w", pady=(4, 12) if self.compact else (7, 22))

    def show(self, page):
        if self.editor_dirty and not messagebox.askyesno("未保存修改", "离开后将丢弃未保存的稿件修改。是否离开？", parent=self.root):
            return
        self.editor_dirty = False
        self.page = page
        for child in self.content.winfo_children():
            child.destroy()
        for name, button in self.nav.items():
            button.configure(bg=GREEN if name == page else SIDEBAR)
        {"概览": self.overview, "公众号管理": self.accounts, "文章资料库": self.articles,
         "稿件工作台": self.drafts, "运行记录": self.jobs, "设置": self.settings}[page]()

    def button(self, parent, text, action, primary=False):
        secondary = {
            '停止任务', '配置接口与定时', '删除关注', '测试所选公众号采集',
            '全选列表', '清理所选文章', '一键清空资料库', '全选本页', '清理所选稿件', '一键清空稿件',
            '历史版本', '重新生成（API 计费）', '参考网站', '停止运行', '刷新',
            '测试 OpenAI（API 计费）', '测试 DeepSeek（API 计费）', '恢复数据库', '打开数据目录',
            '查看正文与摘要', '打开原文', '补采所选图片'}
        if text in secondary or text.startswith('清除 '):
            if not hasattr(parent, '_action_menu'):
                b = tk.Menubutton(parent, text='•••  更多', width=10, anchor='center',
                                  bg=PANEL, fg=MUTED, activebackground='#FFF0EC',
                                  activeforeground=GREEN, relief='flat', bd=0,
                                  padx=10, pady=7, font=('Microsoft YaHei UI', 9 if self.compact else 10))
                menu = tk.Menu(b, tearoff=False, bg=PANEL, fg=INK,
                               activebackground='#FFF0EC', activeforeground=GREEN,
                               font=('Microsoft YaHei UI', 10), relief='flat', bd=1)
                b.configure(menu=menu)
                b.pack(side='right', padx=(8, 0), pady=4)
                parent._action_menu = menu
            menu = parent._action_menu
            label = '测试所选公众号采集' if text == '测试所选公众号采集' else text
            menu.add_command(label=label, command=lambda: self.safe(action))
            return menu
        b = ttk.Button(parent, text=text, command=lambda: self.safe(action), style="Primary.TButton" if primary else "TButton")
        b.pack(side="left", padx=(0, 8), pady=4)
        return b

    def safe(self, action):
        try:
            return action()
        except sqlite3.IntegrityError:
            messagebox.showerror("无法保存", "该公众号标识已存在，请勿重复添加。", parent=self.root)
        except Exception as exc:
            # UI validation errors are local, network adapters never include raw server responses.
            if not isinstance(exc, (ValueError, ApiError)):
                logger.exception("界面操作异常")
            messagebox.showerror("操作未完成", str(exc) or type(exc).__name__, parent=self.root)

    def require_idle(self):
        if self.busy:
            raise ValueError("任务运行中，请等待完成或停止任务后再操作")

    def require_live(self):
        if self.demo:
            raise ValueError("这是演示模式，请打开正常版配置 API 后使用")

    def table(self, parent, columns, height=10):
        box = ttk.Frame(parent)
        box.pack(fill="both", expand=True, pady=(10, 0))
        if self.compact:
            height = min(height, 4)
        tree = ttk.Treeview(box, columns=[c[0] for c in columns], show="headings", height=height, selectmode="browse")
        tree.tag_configure("even", background="#FAFAFE")
        tree.tag_configure("odd", background="#FFFFFF")
        for key, label, width in columns:
            tree.heading(key, text=label)
            tree.column(key, width=width, minwidth=45, stretch=True)
        scroll = ttk.Scrollbar(box, orient="vertical", command=tree.yview)
        horizontal = ttk.Scrollbar(box, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        return tree

    def selection(self, tree):
        selected = tree.selection()
        if not selected:
            raise ValueError("请先选择一条记录")
        return int(selected[0])

    @staticmethod
    def stripe_rows(tree):
        for index, item in enumerate(tree.get_children()):
            tree.item(item, tags=("even" if index % 2 == 0 else "odd",))

    def overview(self):
        self.heading("今天，写点有影响力的内容", "从关注的公众号里发现热点，补充网络资料，再生成可编辑、可追溯的新稿件。")
        cards = ttk.Frame(self.content)
        cards.pack(fill="x")
        counts = [self.db.rows("SELECT COUNT(*) n FROM " + name + (" WHERE cleared=0" if name != 'accounts' else ''))[0]["n"] for name in ("accounts", "articles", "drafts")]
        card_specs = (("关注公众号", counts[0], "◎", "#EEF0FF", PURPLE),
                      ("今日素材库", counts[1], "▤", "#EAF8F4", TEAL),
                      ("待打磨稿件", counts[2], "✎", "#FFF2EC", GREEN))
        for column, (label, count, icon, fill, accent) in enumerate(card_specs):
            cards.columnconfigure(column, weight=1)
            card = tk.Frame(cards, bg=fill, padx=14 if self.compact else 20,
                            pady=12 if self.compact else 17)
            card.grid(row=0, column=column, sticky="nsew", padx=(0, 12) if column < 2 else 0)
            tk.Label(card, text=icon, bg=fill, fg=accent,
                     font=("Segoe UI Symbol", 18 if self.compact else 22, "bold")).pack(side="right", anchor="ne")
            tk.Label(card, text=str(count), bg=fill, fg=accent,
                     font=("Microsoft YaHei UI", 24 if self.compact else 31, "bold")).pack(anchor="w")
            tk.Label(card, text=label, bg=fill, fg=INK,
                     font=("Microsoft YaHei UI", 9 if self.compact else 10, "bold")).pack(anchor="w", pady=(2, 0))
        config = self.db.config()
        schedule = f"每日 {config['schedule_time']} · {config['timezone']}" if config["schedule_enabled"] else "自动运行尚未开启"
        frame = tk.Frame(self.content, bg=PANEL, highlightbackground=BORDER, highlightthickness=1,
                         padx=14 if self.compact else 20, pady=12 if self.compact else 17)
        frame.pack(fill="x", pady=12 if self.compact else 20)
        top = tk.Frame(frame, bg=PANEL)
        top.pack(fill="x")
        tk.Label(top, text="今日创作流程", bg=PANEL, fg=INK,
                 font=("Microsoft YaHei UI", 12 if self.compact else 14, "bold")).pack(side="left")
        tk.Label(top, text=schedule, bg="#EEF0FF", fg=PURPLE, padx=10, pady=4,
                 font=("Microsoft YaHei UI", 9, "bold")).pack(side="right")
        flow = tk.Frame(frame, bg=PANEL)
        flow.pack(fill="x", pady=(12, 8))
        steps = (("1", "同步文章"), ("2", "筛选热点"), ("3", "联网研究"),
                 ("4", "独立写作"), ("5", "人工审核"))
        for index, (number, label) in enumerate(steps):
            step = tk.Frame(flow, bg="#F7F7FC", padx=7 if self.compact else 10, pady=7)
            step.pack(side="left")
            tk.Label(step, text=number, bg=PURPLE if index < 3 else GREEN, fg="white",
                     width=2, font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")
            tk.Label(step, text=label, bg="#F7F7FC", fg=INK,
                     font=("Microsoft YaHei UI", 9 if self.compact else 10, "bold")).pack(side="left", padx=(6, 0))
            if index < len(steps) - 1:
                tk.Label(flow, text="›", bg=PANEL, fg="#B4B7C8",
                         font=("Microsoft YaHei UI", 16, "bold")).pack(side="left", padx=4)
        tk.Label(frame, text=f"采集最近 {config['lookback_hours']} 小时，选择阅读量最高的 {config.get('draft_count', 3)} 篇，各生成一篇不同主题的草稿。",
                 bg=PANEL, fg=MUTED, font=("Microsoft YaHei UI", 9 if self.compact else 10)).pack(anchor="w", pady=(0, 10))
        row = tk.Frame(frame, bg=PANEL)
        row.pack(fill="x")
        self.button(row, "开始今日创作", self.run_manual, True)
        self.button(row, "停止任务", self.engine.stop)
        self.button(row, "配置接口与定时", lambda: self.show("设置"))
        ttk.Label(self.content, text="最近任务", style="Section.TLabel").pack(anchor="w")
        tree = self.table(self.content, [("time", "开始时间", 140), ("status", "状态", 100), ("message", "执行结果", 520)], 5)
        for job in self.db.rows("SELECT * FROM jobs ORDER BY id DESC LIMIT 6"):
            tree.insert("", "end", iid=job["id"], values=(local_time(job["created_at"]), job["status"], job["message"]))
        self.stripe_rows(tree)
        ttk.Label(self.content, text="电脑需保持开机联网；关闭主窗口可留在托盘运行。首次使用请在设置中保存密钥。",
                  style="Sub.TLabel", wraplength=850).pack(side="bottom", fill="x", pady=(12, 0), before=tree.master)

    def accounts(self):
        self.heading("管理你的内容来源", "在手机微信读书加入公众号，然后完成下面两步。已同步的公众号会自动出现在列表中。")
        connect = tk.Frame(self.content, bg=PANEL, highlightbackground=BORDER, highlightthickness=1,
                           padx=14 if self.compact else 18, pady=12 if self.compact else 16)
        connect.pack(fill="x", pady=(0, 12))
        tk.Label(connect, text="连接微信读书", bg=PANEL, fg=INK,
                 font=("Microsoft YaHei UI", 12 if self.compact else 14, "bold")).pack(anchor="w")
        tk.Label(connect, text="扫码登录只需偶尔执行；以后点击同步即可更新公众号。",
                 bg=PANEL, fg=MUTED, font=("Microsoft YaHei UI", 9 if self.compact else 10)).pack(anchor="w", pady=(2, 10))
        login_row = tk.Frame(connect, bg=PANEL)
        login_row.pack(fill="x")
        self.button(login_row, "①  Edge 扫码登录", self.login_weread, True)
        self.button(login_row, "②  同步公众号", self.sync_weread)
        state = "已保存登录状态（有效性以同步结果为准）" if self.vault.has("weread") else "尚未登录"
        tk.Label(login_row, text="●  " + state, bg="#EAF8F4" if self.vault.has("weread") else "#FFF4E1",
                 fg=TEAL if self.vault.has("weread") else "#A46A00", padx=10, pady=6,
                 font=("Microsoft YaHei UI", 9, "bold")).pack(side="right")
        form = ttk.Frame(self.content)
        name, identifier = tk.StringVar(), tk.StringVar()
        ttk.Label(form, text="名称").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Entry(form, textvariable=name, width=20).grid(row=1, column=0, sticky="ew", padx=(0, 12))
        ttk.Label(form, text="微信读书标识 MP_WXS_…（推荐使用同步书架自动填写）").grid(row=0, column=1, sticky="w")
        ttk.Entry(form, textvariable=identifier).grid(row=1, column=1, sticky="ew")
        form.columnconfigure(1, weight=1)
        def add():
            self.require_idle()
            self.db.add_account(name.get(), identifier.get())
            self.show("公众号管理")
        ttk.Button(form, text="添加公众号", style="Primary.TButton", command=lambda: self.safe(add)).grid(row=1, column=2, padx=(12, 0))
        manual = ttk.Button(self.content, text="＋ 手动添加公众号（可选）")
        def toggle_form():
            if form.winfo_manager():
                form.pack_forget()
                manual.configure(text="＋ 手动添加公众号（可选）")
            else:
                form.pack(fill="x", pady=(0, 10), after=manual)
                manual.configure(text="－ 收起手动添加")
        manual.configure(command=toggle_form)
        manual.pack(anchor="w", pady=(0, 4))
        row = ttk.Frame(self.content)
        row.pack(fill="x", pady=(8, 0))
        def toggle():
            self.require_idle()
            self.db.execute("UPDATE accounts SET enabled=1-enabled WHERE id=?", (self.selection(tree),))
            self.show("公众号管理")
        def delete():
            self.require_idle()
            self.db.execute("DELETE FROM accounts WHERE id=?", (self.selection(tree),))
            self.show("公众号管理")
        def test():
            self.require_live()
            account = self.db.rows("SELECT * FROM accounts WHERE id=?", (self.selection(tree),))[0]
            def work():
                from .weread import WeReadSource
                source = WeReadSource(self.vault.get('weread'), self.db,
                    state_saver=lambda value: self.vault.set_many({'weread': value}),
                    profile_dir=self.data_dir / 'weread-edge-profile')
                try:
                    return source.test(account['identifier'])
                finally:
                    source.close()
            self.start_worker(work, "正在验证所选来源…")
        self.button(row, "启用 / 停用", toggle)
        self.button(row, "删除关注", delete)
        self.button(row, "测试所选公众号采集", test)
        tree = self.table(self.content, [("name", "公众号名称", 170), ("identifier", "公众号标识", 480), ("enabled", "状态", 100)])
        for a in self.db.accounts():
            tree.insert("", "end", iid=a["id"], values=(a["name"], a["identifier"], "已启用" if a["enabled"] else "已停用"))
        self.stripe_rows(tree)

    def sync_weread(self):
        self.require_live()
        from .weread import WeReadSource, sync_books
        def work():
            source = WeReadSource(self.vault.get('weread'), self.db,
                state_saver=lambda value: self.vault.set_many({'weread': value}),
                profile_dir=self.data_dir / 'weread-edge-profile')
            try:
                return sync_books(self.db, source.shelf())
            finally:
                source.close()
        self.start_worker(work, "正在读取微信读书书架…")

    def login_weread(self):
        self.require_live()
        self.require_idle()
        from .weread import browser_login
        cancel = threading.Event()
        def work():
            state, count = browser_login(self.data_dir / 'weread-edge-profile', cancel)
            self.vault.set_many({'weread': state})
            config = self.db.config()
            config['source_provider'] = 'weread'
            self.db.save_config(config)
            return f'微信读书登录及文章接口验证成功，发现 {count} 个书架公众号；请同步书架公众号'
        self.start_worker(work, "已打开 Edge，请在浏览器中扫码登录；验证成功后窗口会自动关闭…")

    def text_window(self, title, text, images=()):
        win = tk.Toplevel(self.root)
        win.title(title)
        width = min(850, self.root.winfo_screenwidth() - 60)
        height = min(650, self.root.winfo_screenheight() - 100)
        win.geometry(f"{width}x{height}")
        editor = ScrolledText(win, wrap="word", padx=20, pady=20, font=("Microsoft YaHei UI", 11), relief="flat")
        editor.pack(fill="both", expand=True)
        editor.insert("1.0", text)
        import re
        for n, match in enumerate(re.finditer(r'https?://[^\s<>"）]+', text)):
            url = match.group().rstrip('。，;')
            tag = 'source_url_' + str(n)
            editor.tag_add(tag, f'1.0+{match.start()}c', f'1.0+{match.start() + len(url)}c')
            editor.tag_config(tag, foreground='#1763ad', underline=True)
            editor.tag_bind(tag, '<Button-1>', lambda event, link=url: webbrowser.open(link))
        photos = []
        if images:
            from PIL import Image, ImageTk
            root = self.data_dir.resolve()
            for item in images:
                try:
                    path = (self.data_dir / item['local_path']).resolve()
                    if not path.is_relative_to(root) or not path.is_file():
                        continue
                    with Image.open(path) as original:
                        picture = original.convert('RGBA')
                    picture.thumbnail((max(360, width - 90), 520), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(picture)
                    photos.append(photo)
                    label = f"\n\n—— 原文图片 {item['position']} ——"
                    if item.get('alt_text'):
                        label += '\n' + item['alt_text']
                    editor.insert('end', label + '\n')
                    editor.image_create('end', image=photo)
                except (OSError, ValueError, tk.TclError):
                    continue
        editor._article_photos = photos
        editor.configure(state="disabled")
        return win

    def articles(self):
        self.heading("发现值得写的素材", "文章按采集到的阅读量排序。正文处理时会同时保存原文图片，可在详情中查看。")
        row = ttk.Frame(self.content)
        row.pack(fill="x")
        query = tk.StringVar()
        ttk.Entry(row, textvariable=query, width=33).pack(side="left", padx=(0, 8))
        def refresh():
            tree.delete(*tree.get_children())
            for a in self.db.rows("SELECT a.*,m.reads,m.likes,(SELECT COUNT(*) FROM article_images i WHERE i.article_id=a.id) image_count FROM articles a LEFT JOIN article_metrics m ON m.article_id=a.id WHERE a.cleared=0 AND (title LIKE ? OR account_name LIKE ?) ORDER BY m.reads DESC,published_at DESC",
                                  ("%" + query.get() + "%", "%" + query.get() + "%")):
                status = "待提取正文" if not a["content"] else f"正文 · {a['image_count']} 图"
                if a["error"]:
                    status = "处理异常"
                tree.insert("", "end", iid=a["id"], values=(a["title"], a["account_name"], a['reads'] if a['reads'] is not None else '未知', a['likes'] if a['likes'] is not None else '未知', local_time(a["published_at"]), status))
            self.stripe_rows(tree)
        def detail():
            article = self.db.rows("SELECT * FROM articles WHERE id=?", (self.selection(tree),))[0]
            text = f"{article['title']}\n公众号：{article['account_name']}\n原文：{article['url']}\n发布时间：{article['published_at']}\n"
            if article["error"]:
                text += "\n处理提示：" + article["error"] + "\n"
            images = self.db.article_images(article['id'])
            text += f"\n原文图片：已保存 {len(images)} 张\n"
            text += "\n—— 正文 ——\n\n" + (article["content"] or "尚未获取正文")
            for a in self.db.rows("SELECT result FROM analyses WHERE article_id=? ORDER BY created_at", (article["id"],)):
                text += "\n\n—— 摘要与证据 ——\n" + json.dumps(json.loads(a["result"]), ensure_ascii=False, indent=2)
            self.text_window(article["title"], text, images)
        def open_url():
            from .api import wechat_url
            url = self.db.rows("SELECT url FROM articles WHERE id=?", (self.selection(tree),))[0]["url"]
            webbrowser.open(wechat_url(url))
        self.button(row, "搜索", refresh)
        self.button(row, "查看正文与摘要", detail)
        self.button(row, "打开原文", open_url)
        def fetch_images():
            self.require_idle()
            self.require_live()
            ids = [int(value) for value in tree.selection()]
            if not ids:
                raise ValueError('请先选择要补采图片的文章，可按 Ctrl 或 Shift 多选')
            def work():
                from .weread import WeReadSource
                self.engine.cancel.clear()
                source = WeReadSource(self.vault.get('weread'), self.db, self.engine.cancel,
                    state_saver=lambda value: self.vault.set_many({'weread': value}),
                    profile_dir=self.data_dir / 'weread-edge-profile')
                saved = 0
                try:
                    for article_id in ids:
                        article = self.db.rows('SELECT * FROM articles WHERE id=? AND cleared=0',
                                               (article_id,))
                        if not article:
                            continue
                        body = source.content(article[0]['url'])
                        if not article[0]['content']:
                            from .api import digest
                            self.db.execute("UPDATE articles SET content=?,content_hash=?,error='' WHERE id=?",
                                            (body, digest(body), article_id))
                        saved += len(self.db.article_images(article_id))
                    return f'图片补采完成：{len(ids)} 篇文章，共保存 {saved} 张原文图片'
                finally:
                    source.close()
            self.start_worker(work, '正在补采原文图片…')
        self.button(row, '补采所选图片', fetch_images)
        tools = row
        def clear(all_records=False):
            self.require_idle()
            ids = None if all_records else list(tree.selection())
            if ids == []:
                raise ValueError('请先选择要清理的文章，可按 Ctrl 或 Shift 多选')
            total = self.db.rows('SELECT COUNT(*) n FROM articles WHERE cleared=0')[0]['n'] if all_records else len(ids)
            if not total:
                return
            scope = '整个文章资料库（包含搜索结果之外的文章）' if all_records else '所选文章'
            if not messagebox.askyesno('确认清理文章', f'将清理{scope}，共 {total} 篇正文、摘要与缓存。\n已生成稿件及其素材报告会保留。此操作无法在界面撤销。\n是否继续？', parent=self.root):
                return
            total = self.db.clear_records('articles', ids)
            refresh()
            self.status.set(f'已清理 {total} 篇文章；保留标识防止再次自动采集')
        self.button(tools, '全选列表', lambda: tree.selection_set(tree.get_children()))
        self.button(tools, '清理所选文章', clear)
        self.button(tools, '一键清空资料库', lambda: clear(True))
        ttk.Label(tools, text='Ctrl / Shift 多选', style='Sub.TLabel').pack(side='left', padx=8)
        tree = self.table(self.content, [("title", "文章标题", 300), ("account", "公众号", 120), ("reads", "阅读量", 80), ("likes", "点赞", 60), ("date", "发布时间（本机）", 130), ("status", "状态", 100)])
        tree.configure(selectmode='extended')
        tree.bind("<Double-1>", lambda e: self.safe(detail))
        refresh()

    def drafts(self):
        self.heading("把灵感打磨成好文章", "搜索并打开稿件，修改后保存版本。审核完成即可导出干净正文和参考网站。")
        toolbar = ttk.Frame(self.content)
        toolbar.pack(fill='x')
        query = tk.StringVar(value=getattr(self, 'draft_query', ''))
        search_entry = ttk.Entry(toolbar, textvariable=query, width=20 if self.compact else 27)
        search_entry.pack(side='left', padx=(0, 8))
        page_number = [0]
        page_label = tk.StringVar()
        mapping = {}
        tools = toolbar
        tree = self.table(self.content, [('id','编号',50), ('title','稿件标题',440), ('status','状态',80), ('time','更新时间',140)], height=3 if self.compact else 4)
        tree.configure(selectmode='extended')
        tree.master.pack_configure(expand=False)
        title = tk.StringVar()
        ttk.Entry(self.content, textvariable=title, font=("Microsoft YaHei UI", 13, "bold")).pack(fill="x", pady=(0, 10))
        editor = ScrolledText(self.content, wrap="word", undo=True, font=("Microsoft YaHei UI", 10 if self.compact else 11),
                              padx=10 if self.compact else 15, pady=10 if self.compact else 15,
                              relief="flat", height=3 if self.compact else 7)
        editor.pack(fill="both", expand=True)
        def modified(event=None):
            if editor.edit_modified():
                self.editor_dirty = True
                editor.edit_modified(False)
        editor.bind("<<Modified>>", modified)
        title.trace_add("write", lambda *args: setattr(self, "editor_dirty", True))
        state = tk.StringVar()
        detail_label = tk.StringVar()
        loading = [None]
        def discard():
            if self.editor_dirty and not messagebox.askyesno('未保存修改', '此操作将丢弃当前未保存的稿件修改，是否继续？', parent=self.root):
                return False
            self.editor_dirty = False
            return True
        def load(event=None):
            selected = tree.selection()
            if len(selected) != 1:
                return
            identifier = int(selected[0])
            if loading[0] == identifier:
                return
            if not discard():
                if loading[0] is not None and tree.exists(str(loading[0])):
                    tree.selection_set(str(loading[0]))
                return
            found = self.db.rows('SELECT * FROM drafts WHERE id=? AND cleared=0', (identifier,))
            if not found:
                return
            draft = found[0]
            self.draft_id = draft['id']
            title.set(draft['title'])
            editor.delete('1.0', 'end')
            editor.insert('1.0', draft['body'])
            editor.edit_modified(False)
            state.set(draft['status'])
            detail_label.set(f"当前编辑 #{draft['id']} · 版本 {draft['revision']} · 更新于 {local_time(draft['updated_at'])}")
            loading[0] = draft['id']
            self.editor_dirty = False
        def refresh(delta=0):
            if not discard():
                return
            self.draft_query = query.get().strip()
            pattern = '%' + self.draft_query.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'
            where = "cleared=0 AND (title LIKE ? ESCAPE '!' OR body LIKE ? ESCAPE '!')"
            total = self.db.rows('SELECT COUNT(*) n FROM drafts WHERE ' + where, (pattern,pattern))[0]['n']
            pages = max(1, (total + 49)//50)
            page_number[0] = max(0, min(page_number[0] + delta, pages-1))
            rows = self.db.rows('SELECT id,title,status,updated_at FROM drafts WHERE ' + where + ' ORDER BY id DESC LIMIT 50 OFFSET ?', (pattern,pattern,page_number[0]*50))
            tree.delete(*tree.get_children())
            mapping.clear()
            for draft in rows:
                mapping[draft['id']] = draft
                tree.insert('', 'end', iid=draft['id'], values=(draft['id'],draft['title'],draft['status'],local_time(draft['updated_at'])))
            self.stripe_rows(tree)
            loading[0] = None
            title.set('')
            editor.delete('1.0','end')
            editor.edit_modified(False)
            state.set('待审核')
            self.editor_dirty = False
            page_label.set(f"共 {total} 篇 · {page_number[0]+1}/{pages} 页")
            if rows:
                selected = self.draft_id if self.draft_id in mapping else rows[0]['id']
                tree.selection_set(str(selected))
                tree.see(str(selected))
                load()
            else:
                self.draft_id = None
                detail_label.set('未找到稿件；可更换标题或正文关键词检索')
        def search():
            if not discard():
                return
            page_number[0] = 0
            refresh()
        def clear(all_records=False):
            self.require_idle()
            ids = None if all_records else list(tree.selection())
            if ids == []:
                raise ValueError('请选择要清理的稿件，可按 Ctrl 或 Shift 多选')
            total = self.db.rows('SELECT COUNT(*) n FROM drafts WHERE cleared=0')[0]['n'] if all_records else len(ids)
            if not total:
                return
            scope = '全部稿件（含其他页及搜索结果之外的稿件）' if all_records else '所选稿件'
            if not messagebox.askyesno('确认清理稿件', f'将清理{scope}，共 {total} 篇，包括正文、素材报告和历史版本。\n文章资料库会保留。此操作无法在界面撤销。\n是否继续？', parent=self.root):
                return
            if not discard():
                return
            total = self.db.clear_records('drafts', ids)
            refresh()
            self.status.set(f'已清理 {total} 篇稿件及历史版本')
        def require_draft():
            if self.draft_id is None:
                raise ValueError('请先选择一篇稿件')
        self.button(toolbar, '搜索标题 / 正文', search, True)
        self.button(toolbar, '上一页', lambda: refresh(-1))
        self.button(toolbar, '下一页', lambda: refresh(1))
        ttk.Label(toolbar, textvariable=page_label, style='Sub.TLabel').pack(side='left', padx=8)
        self.button(tools, '全选本页', lambda: tree.selection_set(tree.get_children()))
        self.button(tools, '清理所选稿件', clear)
        self.button(tools, '一键清空稿件', lambda: clear(True))
        if not self.compact:
            ttk.Label(tools, text='单选打开稿件；Ctrl / Shift 多选清理', style='Sub.TLabel').pack(side='left', padx=8)
        search_entry.bind('<Return>', lambda e: self.safe(search))
        tree.bind('<<TreeviewSelect>>', lambda e: self.safe(load))
        row = ttk.Frame(self.content)
        ttk.Combobox(row, textvariable=state, values=["待审核", "已审核"], state="readonly", width=8).pack(side="left", padx=(0, 8))
        def save():
            require_draft()
            self.db.edit_draft(self.draft_id, title.get(), editor.get("1.0", "end-1c"), state.get())
            self.editor_dirty = False
            self.status.set("已保存稿件新版本")
            self.show("稿件工作台")
        def report():
            require_draft()
            d = self.db.rows("SELECT report FROM drafts WHERE id=?", (self.draft_id,))[0]
            self.text_window("素材报告与审核提示", d["report"])
        def export():
            require_draft()
            path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".md", initialfile=f"稿件-{self.draft_id}.md",
                                               filetypes=[("Markdown", "*.md"), ("纯文本", "*.txt")])
            if path:
                sources = json.loads(self.db.rows("SELECT sources FROM drafts WHERE id=?", (self.draft_id,))[0]["sources"])
                text = export_document(title.get(), editor.get('1.0', 'end-1c'), sources,
                                       markdown=Path(path).suffix.lower() != '.txt')
                Path(path).write_text(text, encoding="utf-8-sig")
                self.status.set("已导出无内部编号的正文，并附实际引用网站")
        def websites():
            require_draft()
            draft = self.db.rows("SELECT body,sources FROM drafts WHERE id=?", (self.draft_id,))[0]
            self.text_window("参考资料网站", source_website_text(draft['body'], json.loads(draft['sources'])))
        def versions():
            require_draft()
            versions = self.db.rows("SELECT * FROM draft_versions WHERE draft_id=? ORDER BY revision DESC", (self.draft_id,))
            self.text_window("稿件历史版本", "\n\n".join(f"版本 {v['revision']} · {local_time(v['created_at'])}\n{v['title']}\n\n{v['body']}" for v in versions))
        def regenerate():
            require_draft()
            self.require_idle()
            self.require_live()
            if self.editor_dirty:
                raise ValueError("请先保存当前修改，再重新生成")
            draft = self.db.rows("SELECT * FROM drafts WHERE id=?", (self.draft_id,))[0]
            selected = [s["article_id"] for s in json.loads(draft["sources"]) if 'article_id' in s]
            if any(not self.db.rows("SELECT id FROM articles WHERE id=? AND cleared=0", (i,)) for i in selected):
                raise ValueError("原素材已清理，无法重新生成；现有稿件仍可编辑和导出")
            key = "rewrite:" + uuid.uuid4().hex
            config = self.db.config()
            if len(selected) == 1:
                config.update(writing_mode='single_web', selection_mode='hot', min_sources=1, topic='')
            job = self.db.job(key, config)
            self.db.update_job(job["id"], selected=json.dumps(selected))
            self.start_worker(lambda: self.engine.run(key, config), "基于原素材重新生成…")
        self.button(row, "保存版本", save, True)
        self.button(row, "素材报告", report)
        self.button(row, "导出", export)
        self.button(row, "历史版本", versions)
        self.button(row, "重新生成（API 计费）", regenerate)
        self.button(row, "参考网站", websites)
        row.pack(fill="x", pady=(8, 6), before=editor)
        ttk.Label(self.content, textvariable=detail_label, style="Sub.TLabel").pack(
            anchor="w", pady=(0, 6), before=editor)
        refresh()

    def jobs(self):
        self.heading("查看每一次创作进度", "成功、警告和失败原因都保留在这里。失败任务可继续重试，已完成步骤不会重复执行。")
        row = ttk.Frame(self.content)
        row.pack(fill="x")
        def retry():
            self.require_live()
            job = self.db.rows("SELECT * FROM jobs WHERE id=?", (self.selection(tree),))[0]
            if job["status"] not in ("失败", "部分完成", "已中断", "已停止", "等待中"):
                raise ValueError("该任务无需重试；重新创作请到稿件工作台")
            self.start_worker(lambda: self.engine.run(job["run_key"], json.loads(job["config"])), "正在重试任务…")
        def detail():
            job = self.db.rows("SELECT * FROM jobs WHERE id=?", (self.selection(tree),))[0]
            hint = ERROR_HINTS.get(job.get('error_code') or '', '')
            self.text_window("任务详情", f"任务 #{job['id']}\n状态：{job['status']}\n阶段：{job['stage']}\n{job['message']}\n" +
                             (f"\n处理建议：{hint}\n" if hint else '') + "\n警告：\n" +
                             "\n".join(json.loads(job["warnings"])) + "\n\n配置快照（不含密钥）：\n" + json.dumps(json.loads(job["config"]), ensure_ascii=False, indent=2))
        self.button(row, "重试所选任务", retry, True)
        self.button(row, "查看详情", detail)
        self.button(row, "停止运行", self.engine.stop)
        self.button(row, "刷新", lambda: self.show("运行记录"))
        tree = self.table(self.content, [("id", "编号", 50), ("time", "开始时间", 120), ("status", "状态", 90), ("kind", "错误类型", 90), ("stage", "阶段", 100), ("message", "结果", 330)])
        for j in self.db.rows("SELECT * FROM jobs ORDER BY id DESC LIMIT 300"):
            kind = ERROR_LABELS.get(j.get('error_code') or '', '')
            tree.insert("", "end", iid=j["id"], values=(j["id"], local_time(j["created_at"]), j["status"], kind, j["stage"], j["message"]))
        self.stripe_rows(tree)
        tree.bind("<Double-1>", lambda e: self.safe(detail))

    def settings(self):
        self.heading("按你的方式创作", "模型、采集范围、自动运行和数据管理集中在四个标签中；修改后点击底部保存。")
        panel = ttk.Frame(self.content)
        panel.pack(side='bottom', fill='x', pady=(12, 0))
        notebook = ttk.Notebook(self.content)
        notebook.pack(fill='both', expand=True)
        canvases = {}
        def wheel(event):
            if self.page == '设置' and notebook.winfo_exists():
                canvas = canvases.get(notebook.select())
                if canvas is not None:
                    canvas.yview_scroll(int(-event.delta / 120), 'units')
        self.root.bind('<MouseWheel>', wheel)
        config = self.db.config()
        variables = {}
        keys = {}

        def section(title):
            tab = ttk.Frame(notebook, padding=(0, 12, 0, 0))
            notebook.add(tab, text={'接口与模型': '模型与联网', '采集与逐篇写作': '采集与写作',
                                    '每日定时': '自动运行', '数据备份与密钥管理': '数据管理'}.get(title, title))
            canvas = tk.Canvas(tab, bg=BG, highlightthickness=0)
            scroll = ttk.Scrollbar(tab, orient='vertical', command=canvas.yview)
            scroll.pack(side='right', fill='y')
            canvas.pack(fill='both', expand=True)
            canvas.configure(yscrollcommand=scroll.set)
            frame = ttk.Frame(canvas, padding=(16, 8, 20, 16))
            item = canvas.create_window((0, 0), window=frame, anchor='nw')
            frame.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
            canvas.bind('<Configure>', lambda e: canvas.itemconfigure(item, width=e.width))
            canvases[str(tab)] = canvas
            frame.columnconfigure(1, weight=1)
            return frame

        def field(frame, row, text, key, value, options=None, secret=False):
            ttk.Label(frame, text=text, width=22).grid(row=row, column=0, sticky="w", pady=5)
            var = tk.StringVar(value=str(value))
            if options:
                widget = ttk.Combobox(frame, textvariable=var, values=options, state="readonly")
            else:
                widget = ttk.Entry(frame, textvariable=var, show="●" if secret else "")
            widget.grid(row=row, column=1, sticky="ew", pady=5)
            variables[key] = var
            return var

        frame = section("接口与模型")
        for row, (provider, label) in enumerate((("everyinfra", "EveryInfra 评论"), ("openai", "OpenAI"), ("deepseek", "DeepSeek"))):
            saved = "已保存" if self.vault.has(provider) else "未配置"
            keys[provider] = field(frame, row, f"{label} Key · {saved}", provider + "_key", "", secret=True)
        for row, provider in ((3, "openai"), (5, "deepseek")):
            field(frame, row, provider + " 接口地址", provider + "_url", config["providers"][provider]["base_url"])
            field(frame, row + 1, provider + " 模型 ID", provider + "_model", config["providers"][provider]["model"])
        field(frame, 7, "逐篇摘要使用", "summary_provider", config["summary_provider"], ["openai", "deepseek"])
        field(frame, 8, "综合写作使用", "writer_provider", config["writer_provider"], ["openai", "deepseek"])
        ttk.Label(frame, text="Key 留空保留原密钥。OpenAI 使用 Responses API；DeepSeek 使用官方兼容接口。", style="Sub.TLabel").grid(row=9, column=0, columnspan=2, sticky="w", pady=7)
        ttk.Label(frame, text="文章采集来源：微信读书", style="Sub.TLabel").grid(row=10, column=0, columnspan=2, sticky="w", pady=5)
        ttk.Label(frame, text="weread：微信读书负责文章和阅读量；EveryInfra Key 用于评论。", style="Sub.TLabel").grid(row=11, column=0, columnspan=2, sticky="w", pady=5)
        field(frame, 12, 'OpenAI 联网研究接口', 'search_url', config['search_url'])
        ttk.Label(frame, text='联网研究使用 OpenAI web_search；EveryInfra 获取评论，由摘要模型分析。搜索、评论及模型调用可能计费。', wraplength=740, style='Sub.TLabel').grid(row=13, column=0, columnspan=2, sticky='w', pady=5)
        ttk.Label(frame, text='正文不足 100 字但含原图时，会均匀选择最多 8 张图片交给 OpenAI 识别；识别结果会缓存。', wraplength=740, style='Sub.TLabel').grid(row=14, column=0, columnspan=2, sticky='w', pady=5)

        field(frame, 14, 'OpenAI 联网模型 ID', 'search_model', config['search_model'])
        field(frame, 15, '每篇最多获取评论数', 'comment_limit', config['comment_limit'])
        comments_enabled = tk.BooleanVar(value=config['comments_enabled'])
        ttk.Checkbutton(frame, text='启用 EveryInfra 评论分析', variable=comments_enabled).grid(row=16, column=0, columnspan=2, sticky='w')

        frame = section("采集与逐篇写作")
        for key in ('article_limit', 'min_sources', 'topic'):
            variables[key] = tk.StringVar(value=str(config[key]))
        variables['selection_mode'] = tk.StringVar(value='hot')
        for row, (label, key) in enumerate((("采集最近多少小时", "lookback_hours"),
                ("每个公众号列表上限", "per_account_limit"), ("热点最低阅读量", "min_reads"),
                ("每篇目标字数", "target_words"), ("额外风格要求", "style"))):
            field(frame, row, label, key, config[key])
        field(frame, 5, '每次独立生成篇数', 'draft_count', config['draft_count'], ['2', '3'])
        ttk.Label(frame, text='按阅读量选前 2–3 篇未使用文章，每篇分别提取重点、分析风格、联网补充后写作。\n门槛 0 表示不设最低值，未知阅读量不参与排名。排名仅覆盖所选时间窗和列表范围。', wraplength=740, style='Sub.TLabel').grid(row=6, column=0, columnspan=2, sticky='w', pady=5)
        allow_recollect = tk.BooleanVar(value=config['allow_recollect_cleared'])
        ttk.Checkbutton(frame, text='允许重新采集已清理文章', variable=allow_recollect).grid(row=7, column=0, columnspan=2, sticky='w', pady=5)
        ttk.Label(frame, text='默认关闭。开启后，新任务可重新获取时间范围内已清理的文章，并再次用于生成；可能产生 API 费用。', wraplength=740, style='Sub.TLabel').grid(row=8, column=0, columnspan=2, sticky='w', pady=5)
        quality_review = tk.BooleanVar(value=config['quality_review_enabled'])
        ttk.Checkbutton(frame, text='生成后进行一次 AI 二审', variable=quality_review).grid(row=9, column=0, columnspan=2, sticky='w', pady=(10, 5))
        ttk.Label(frame, text='对照原始证据复核事实边界、标题、结构和表达；每篇会增加一次写作模型调用。二审失败时保留初稿并提示人工复核。', wraplength=740, style='Sub.TLabel').grid(row=10, column=0, columnspan=2, sticky='w', pady=5)
        skip_promotions = tk.BooleanVar(value=config.get('skip_promotions', True))
        ttk.Checkbutton(frame, text='跳过促销、直播带货类文章', variable=skip_promotions).grid(row=11, column=0, columnspan=2, sticky='w', pady=(10, 5))
        field(frame, 12, '促销关键词（用逗号分隔）', 'promotion_keywords', '，'.join(config.get('promotion_keywords', [])))
        image_vision = tk.BooleanVar(value=config.get('image_articles', 'skip') == 'vision')
        ttk.Checkbutton(frame, text='识别图片型文章（文字不足 100 字时调用 OpenAI 识图，额外计费）', variable=image_vision).grid(row=13, column=0, columnspan=2, sticky='w', pady=(10, 5))
        ttk.Label(frame, text='默认都按“跳过”处理：标题含关键词的文章不参与选文；图片为主的文章会由下一篇热门文章补位。', wraplength=740, style='Sub.TLabel').grid(row=14, column=0, columnspan=2, sticky='w', pady=5)
        frame = section("每日定时")
        schedule_enabled = tk.BooleanVar(value=config["schedule_enabled"])
        ttk.Checkbutton(frame, text="启用每日自动采集和生成", variable=schedule_enabled).grid(row=0, column=0, columnspan=2, sticky="w")
        field(frame, 1, "执行时间（24 小时制）", "schedule_time", config["schedule_time"])
        field(frame, 2, "调度时区", "timezone", config["timezone"], ["Asia/Shanghai", "Australia/Sydney", "UTC"])
        startup = tk.BooleanVar(value=startup_enabled())
        ttk.Checkbutton(frame, text="登录 Windows 时启动软件（托盘运行）", variable=startup).grid(row=3, column=0, columnspan=2, sticky="w", pady=6)
        ttk.Label(frame, text="关机或休眠时不执行；恢复后合并补执行最近一次遗漏任务。定时失败不会无限重试。", wraplength=740, style="Sub.TLabel").grid(row=4, column=0, columnspan=2, sticky="w", pady=5)

        def save():
            self.require_idle()
            self.require_live()
            latest = self.db.config()
            for key in ("article_limit", "lookback_hours", "per_account_limit", "min_sources", "target_words", "min_reads", "draft_count", "comment_limit"):
                try:
                    latest[key] = int(variables[key].get())
                except ValueError:
                    raise ValueError("篇数、时间范围和字数必须填写整数") from None
            for key in ("topic", "style", "summary_provider", "writer_provider", "schedule_time", "timezone", "selection_mode", "search_url", "search_model"):
                latest[key] = variables[key].get().strip()
            latest['allow_recollect_cleared'] = allow_recollect.get()
            latest['comments_enabled'] = comments_enabled.get()
            latest['quality_review_enabled'] = quality_review.get()
            latest['skip_promotions'] = skip_promotions.get()
            latest['promotion_keywords'] = [w.strip() for w in re.split(r'[,，、\s]+', variables['promotion_keywords'].get()) if w.strip()]
            latest['image_articles'] = 'vision' if image_vision.get() else 'skip'
            latest['source_provider'] = 'weread'
            latest['writing_mode'] = 'separate_web'
            for provider in ("openai", "deepseek"):
                latest["providers"][provider] = {"base_url": variables[provider + "_url"].get().strip().rstrip("/"),
                                                   "model": variables[provider + "_model"].get().strip()}
            if schedule_enabled.get() and not latest["schedule_enabled"]:
                latest["schedule_start"] = datetime.now(ZoneInfo(latest["timezone"])).date().isoformat()
            latest["schedule_enabled"] = schedule_enabled.get()
            validate(latest)
            self.vault.set_many({name: var.get() for name, var in keys.items()})
            if startup.get() != startup_enabled():
                set_startup(startup.get())
            self.db.save_config(latest)
            for var in keys.values():
                var.set("")
            self.status.set("配置已保存；新任务使用新配置。")
        def test_model(provider):
            self.require_live()
            save()
            settings = self.db.config()["providers"][provider]
            def work():
                Model(provider, settings, self.vault.get(provider), Transport()).complete(
                    '只输出 JSON 对象 {"ok":true}', "测试连接，输出 JSON", tokens=128)
                return provider + " 模型接口测试成功"
            self.start_worker(work, "正在测试模型…")
        row = ttk.Frame(panel)
        row.pack(fill="x", pady=(0, 15))
        self.button(row, "保存全部设置", save, True)
        self.button(row, "测试 OpenAI（API 计费）", lambda: test_model("openai"))
        self.button(row, "测试 DeepSeek（API 计费）", lambda: test_model("deepseek"))
        frame = section("数据备份与密钥管理")
        ttk.Label(frame, text=str(self.data_dir), wraplength=760, style="Sub.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        row = ttk.Frame(frame)
        row.grid(row=1, column=0, columnspan=2, sticky="w", pady=5)
        self.button(row, "备份数据库", self.backup)
        self.button(row, "恢复数据库", self.restore)
        self.button(row, "打开数据目录", lambda: os.startfile(self.data_dir))
        ttk.Label(frame, text="备份包含文章、稿件和配置，不包含 API Key。恢复前会自动保留当前数据库副本，并暂停定时。", wraplength=740, style="Sub.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=7)
        row = ttk.Frame(frame)
        row.grid(row=3, column=0, columnspan=2, sticky="w")
        def clear_key(provider):
            self.require_idle()
            self.vault.delete(provider)
            keys[provider].set("")
            self.status.set(provider + " 密钥已清除")
        for provider in ("everyinfra", "openai", "deepseek"):
            self.button(row, "清除 " + provider + " Key", lambda p=provider: clear_key(p))

    def backup(self):
        self.require_idle()
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".sqlite", initialfile="公众号工作台备份.sqlite", filetypes=[("SQLite 备份", "*.sqlite")])
        if path:
            self.db.backup(path)
            self.status.set("数据库已备份，不包含 API Key")

    def restore(self):
        self.require_idle()
        path = filedialog.askopenfilename(parent=self.root, filetypes=[("SQLite 备份", "*.sqlite")])
        if not path:
            return
        if not messagebox.askyesno("恢复数据库", "恢复将替换当前文章、稿件和配置，并暂停定时任务。当前数据会先自动备份。是否继续？", parent=self.root):
            return
        self.db.backup(self.data_dir / ("before-restore-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".sqlite"))
        self.db.restore(path)
        self.status.set("已恢复数据库，定时任务已暂停，请检查配置")
        self.show("设置")

    def run_manual(self):
        self.require_live()
        config = self.db.config()
        self.start_worker(lambda: self.engine.run("manual:" + uuid.uuid4().hex, config), "开始采集…")

    def report_callback_exception(self, kind, value, tb):
        logger.error("界面回调异常", exc_info=(kind, value, tb))
        try:
            self.status.set(f"界面出现异常（{kind.__name__}），已记录到日志，可继续使用")
        except Exception:
            pass

    def start_worker(self, action, status):
        self.require_idle()
        self.busy = True
        self.status.set(status)
        self.progress.start(12)
        def work():
            try:
                result = action()
                self.events.put(("done", str(result or "已完成")))
            except Exception as exc:
                safe = isinstance(exc, (ApiError, ValueError, RuntimeError))
                if not isinstance(exc, (ApiError, ValueError)):
                    logger.exception("后台任务异常：%s", status)
                detail = str(exc) if safe else f"内部错误（{type(exc).__name__}），详情已写入日志 logs\\wewriter.log"
                self.events.put(("done", "操作未完成：" + detail))
        threading.Thread(target=work, daemon=True, name="worker").start()

    def poll(self):
        # The poll loop must survive any single bad event; otherwise the
        # progress bar spins forever and the app believes a task is running.
        try:
            self._drain_events()
        except Exception:
            logger.exception("处理界面事件异常")
            self.busy = False
            try:
                self.progress.stop()
            except Exception:
                pass
        if self.closed:
            return
        try:
            if self.root.winfo_exists():
                self.root.after(150, self.poll)
        except tk.TclError:
            pass

    def _drain_events(self):
        try:
            while True:
                kind, text = self.events.get_nowait()
                if kind == "callback":
                    text()
                elif kind == "show":
                    self.root.deiconify()
                    self.root.lift()
                elif kind == "quit":
                    self.close(force=True)
                    if self.closed:
                        return
                elif kind == "done":
                    self.busy = False
                    self.progress.stop()
                    self.status.set(text)
                    if getattr(self, 'quit_pending', False):
                        self.close(force=True)
                        if self.closed:
                            return
                        continue
                    if self.page in ("概览", "运行记录", "文章资料库", "公众号管理"):
                        self.show(self.page)
                else:
                    self.status.set(text)
        except queue.Empty:
            pass

    def schedule_tick(self):
        if not self.demo and not self.busy:
            try:
                config = self.db.config()
                key = due_key(config)
                if key and not self.db.rows("SELECT id FROM jobs WHERE run_key=?", (key,)):
                    self.start_worker(lambda: self.engine.run(key, config), "执行每日定时任务…")
            except Exception:
                logger.exception("定时检查异常")
                self.status.set("定时配置异常，请检查设置")
        self.root.after(30000, self.schedule_tick)

    def minimize(self):
        if not self.tray:
            try:
                import pystray
                from PIL import Image
                icon = Image.open(asset_path("wewriter-cat.png")).convert("RGBA").resize(
                    (64, 64), Image.Resampling.LANCZOS)
                self.tray = pystray.Icon("WeWriter", icon, "公众号工作台", menu=pystray.Menu(
                    pystray.MenuItem("打开工作台", lambda: self.events.put(("show", "")), default=True),
                    pystray.MenuItem("退出软件", lambda: self.events.put(("quit", "")))))
                self.tray.run_detached()
            except Exception:
                self.tray = None
                self.root.iconify()
                self.status.set("托盘不可用，已最小化至任务栏")
                return
        self.root.withdraw()

    def close(self, force=False):
        if self.editor_dirty:
            if not messagebox.askyesno("未保存修改", "继续将丢弃尚未保存的稿件修改，是否继续？", parent=self.root):
                return
            self.editor_dirty = False
        if not force:
            answer = messagebox.askyesnocancel("关闭窗口", "是否继续在托盘运行？\n\n是：隐藏窗口，继续定时任务。\n否：退出软件，停止定时。", parent=self.root)
            if answer is None:
                return
            if answer:
                self.minimize()
                return
        if self.busy:
            self.engine.stop()
            self.quit_pending = True
            self.status.set("正在停止任务，当前请求结束后软件会自动退出（最长约 100 秒）")
            return
        if self.tray:
            self.tray.stop()
        self.closed = True
        self.root.destroy()
