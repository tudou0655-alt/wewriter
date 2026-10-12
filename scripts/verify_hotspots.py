"""Explicit local acceptance run: rewrite existing latest materials in hot mode."""
import json
import sys
import uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wewriter.db import Database, stamp
from wewriter.secrets import Vault
from wewriter.weread import WeReadSource
from wewriter.hotspots import count, rank
from wewriter.api import digest, parse_date
from wewriter.engine import Engine

p = Path(sys.argv[1])
db = Database(p / 'workspace.sqlite')
vault = Vault(p / 'keys.dpapi.json')
source = WeReadSource(vault.get('weread'), db)
config = db.config()
config.update(selection_mode='hot', min_reads=0)
key = 'rewrite:hot-acceptance:' + uuid.uuid4().hex
job = db.job(key, config)
candidates = {}
for account in db.accounts(enabled=True):
    for item in source.articles(account['identifier'], 1):
        aid = db.save_article({'source_key': digest(item['url']), 'account_name': account['name'],
                              'title': item['title'], 'url': item['url'], 'published_at': item['posted_at']})
        reads, likes, now = count(item['read_count']), count(item['like_count']), stamp()
        db.execute('INSERT OR REPLACE INTO article_metrics VALUES(?,?,?,?)', (aid, reads, likes, now))
        db.execute('INSERT OR REPLACE INTO job_metrics VALUES(?,?,?,?,?)', (job['id'], aid, reads, likes, now))
        candidates[aid] = {'reads': reads, 'likes': likes, 'published': parse_date(item['posted_at'])}
        print(json.dumps({'account': account['name'], 'reads': reads, 'likes': likes}, ensure_ascii=True), flush=True)
selected = rank(candidates, 'hot')[:config['article_limit']]
if len(selected) < config['min_sources']:
    raise RuntimeError('Not enough measured sources for acceptance run')
db.update_job(job['id'], selected=json.dumps(selected))
engine = Engine(db, vault, lambda m: print(json.dumps({'progress': m}, ensure_ascii=True), flush=True))
print(json.dumps({'result': engine.run(key, config)}, ensure_ascii=True), flush=True)
