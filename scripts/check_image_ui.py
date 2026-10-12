"""Local-only smoke check for article image rendering."""
import tempfile
import tkinter as tk
from pathlib import Path
import sys

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wewriter.db import Database
from wewriter.secrets import Vault
from wewriter.ui import App


base = Path(__file__).resolve().parents[1] / '.local-data'
base.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(dir=base) as temporary:
    data = Path(temporary)
    db = Database(data / 'workspace.sqlite')
    article = db.save_article({'source_key': 'ui-image', 'account_name': '演示公众号',
        'title': '图片预览检查', 'url': 'https://mp.weixin.qq.com/s/demo',
        'published_at': '2026-10-12T00:00:00+00:00'})
    path = data / 'article-images' / str(article) / '001.png'
    path.parent.mkdir(parents=True)
    Image.new('RGB', (640, 360), '#ff6b4a').save(path)
    db.replace_article_images(article, [{'position': 1,
        'source_url': 'https://mmbiz.qpic.cn/demo',
        'local_path': path.relative_to(data).as_posix(), 'alt_text': '图片预览测试',
        'width': 640, 'height': 360}])
    root = tk.Tk()
    root.withdraw()
    app = App(root, db, Vault(data / 'keys.json'), data, demo=True)
    window = app.text_window('图片预览检查', '正文内容', db.article_images(article))
    root.update_idletasks()
    root.update()
    editors = [child for child in window.winfo_children() if child.winfo_class() == 'Frame']
    text = next(child for frame in editors for child in frame.winfo_children()
                if child.winfo_class() == 'Text')
    assert len(text.image_names()) == 1
    window.destroy()
    root.destroy()
print('IMAGE_UI_OK')
