"""One-time local upgrade after the user's successful QR probe; no model calls."""
from pathlib import Path
from datetime import datetime
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wewriter.db import Database
from wewriter.secrets import Vault
from wewriter.weread import WeReadSession, WeReadSource, sync_shelf
from wewriter.api import digest


def main():
    data = Path(sys.argv[1])
    probe = Path(__file__).resolve().parents[1] / '.local-data/weread-probe/session.dpapi.json'
    state = Vault(probe).get('weread')
    session = WeReadSession(state)
    books = session.shelf()
    if not any(b.get('bookId') == 'MP_WXS_3931691072' for b in books):
        raise RuntimeError('Target not on shelf; no changes made')
    db = Database(data / 'workspace.sqlite')
    db.backup(data / ('before-weread-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.sqlite'))
    vault = Vault(data / 'keys.dpapi.json')
    vault.set_many({'weread': session.encrypted_state_payload()})
    message = sync_shelf(db, session)
    config = db.config()
    config['source_provider'] = 'weread'
    db.save_config(config)
    source = WeReadSource(vault.get('weread'), db)
    items = source.articles('MP_WXS_3931691072', 2)
    saved = []
    for item in items:
        body = source.content(item['url'])
        article = db.save_article({'source_key': digest(item['url']), 'account_name': '示例公众号E',
                                  'title': item['title'], 'url': item['url'], 'published_at': item['posted_at']})
        db.execute("UPDATE articles SET content=?,content_hash=?,error='' WHERE id=?", (body, digest(body), article))
        saved.append({'title': item['title'], 'characters': len(body), 'published_at': item['posted_at']})
    print(json.dumps({'sync': message, 'saved': saved, 'model_calls': 0}, ensure_ascii=True))


if __name__ == '__main__':
    main()
