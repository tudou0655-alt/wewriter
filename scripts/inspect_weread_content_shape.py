"""Inspect response structure without printing article text or credentials."""
import json
from pathlib import Path
import sys
import urllib.parse

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wewriter.db import Database
from wewriter.secrets import Vault
from wewriter.weread import WeReadSource

sys.stdout.reconfigure(encoding='utf-8')
data_dir = Path(sys.argv[1]).resolve()
article_id = int(sys.argv[2])
database = Database(data_dir / 'workspace.sqlite')
rows = database.rows('SELECT url,title FROM articles WHERE id=?', (article_id,))
if not rows:
    raise SystemExit('article not found')
refs = database.rows('SELECT review_id FROM source_refs WHERE url=?', (rows[0]['url'],))
if not refs:
    raise SystemExit('source ref not found')
source = WeReadSource(Vault(data_dir / 'keys.dpapi.json').get('weread'), database,
                      profile_dir=data_dir / 'weread-edge-profile')
try:
    raw = source.browser.content(refs[0]['review_id'])
    result = {'article_id': article_id, 'title_length': len(rows[0]['title']),
              'url': rows[0]['url'], 'review_id': refs[0]['review_id'],
              'raw_length': len(raw), 'format': 'html'}
    try:
        value = json.loads(raw)
        result['format'] = 'json'
        result['json_type'] = type(value).__name__
        result['json_keys'] = sorted(value) if isinstance(value, dict) else []
        result['error_code'] = value.get('errCode', value.get('errcode')) if isinstance(value, dict) else None
        result['error_message'] = value.get('errMsg', value.get('errmsg')) if isinstance(value, dict) else None
    except ValueError:
        soup = BeautifulSoup(raw, 'html.parser')
        node = soup.select_one('#js_content')
        result['has_js_content'] = node is not None
        result['text_length'] = len(node.get_text('\n', strip=True)) if node else 0
        result['image_count'] = len(node.select('img')) if node else 0
        result['top_level_tags'] = [tag.name for tag in soup.find_all(recursive=False)][:10]
        result['page_title'] = soup.title.get_text(' ', strip=True)[:120] if soup.title else ''
        result['markers'] = [marker for marker in (
            '环境异常', '请验证', '访问过于频繁', '该内容已被发布者删除', '内容已被删除',
            '已停止访问', '违反相关法律法规', 'verify', 'captcha') if marker.lower() in raw.lower()]
        if node is None:
            page = source.browser.open().new_page()
            try:
                page.goto('https://weread.qq.com/web/mp/content?reviewId=' +
                          urllib.parse.quote(refs[0]['review_id'], safe=''),
                          wait_until='domcontentloaded', timeout=60000)
                page.wait_for_timeout(5000)
                result['navigation'] = page.evaluate("""() => {
                    const node = document.querySelector('#js_content');
                    return {title: document.title, hasContent: !!node,
                            textLength: node ? (node.innerText || '').trim().length : 0,
                            imageCount: node ? node.querySelectorAll('img').length : 0,
                            urlHost: location.host, urlPath: location.pathname};
                }""")
            finally:
                page.close()
    print(json.dumps(result, ensure_ascii=False))
finally:
    source.close()
