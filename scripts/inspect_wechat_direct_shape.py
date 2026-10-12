"""Inspect a public WeChat article in Edge without printing its content."""
from pathlib import Path
import json
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wewriter.db import Database
from wewriter.weread import WeReadBrowser

sys.stdout.reconfigure(encoding='utf-8')
data_dir = Path(sys.argv[1]).resolve()
article_id = int(sys.argv[2])
database = Database(data_dir / 'workspace.sqlite')
rows = database.rows('SELECT url FROM articles WHERE id=?', (article_id,))
if not rows:
    raise SystemExit('article not found')
profile = data_dir / ('wechat-direct-probe-' + str(article_id))
browser = WeReadBrowser(profile)
try:
    context = browser.open()
    page = context.new_page()
    try:
        response = page.goto(rows[0]['url'], wait_until='domcontentloaded', timeout=60000)
        result = page.evaluate("""() => {
            const node = document.querySelector('#js_content');
            return {title: document.title, hasContent: !!node,
                    textLength: node ? (node.innerText || '').trim().length : 0,
                    imageCount: node ? node.querySelectorAll('img').length : 0,
                    bodyText: (document.body ? document.body.innerText : '').slice(0, 120)};
        }""")
        result['status'] = response.status if response else None
        markers = ('环境异常', '请验证', '访问过于频繁', '内容已被删除', '已停止访问')
        result['markers'] = [item for item in markers if item in result.pop('bodyText', '')]
        print(json.dumps(result, ensure_ascii=False))
    finally:
        page.close()
finally:
    browser.close()
    shutil.rmtree(profile, ignore_errors=True)
