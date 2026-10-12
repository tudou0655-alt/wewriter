"""Acceptance: explicitly rewrite the existing top three measured samples independently."""
from pathlib import Path
import json
import sys
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wewriter.db import Database
from wewriter.engine import Engine
from wewriter.secrets import Vault

p = Path(sys.argv[1])
db = Database(p / 'workspace.sqlite')
tracking = Path(__file__).resolve().parents[1] / '.local-data/independent-job.json'
if '--retry' in sys.argv:
    key = json.loads(tracking.read_text(encoding='utf-8'))['key']
    job = db.rows('SELECT * FROM jobs WHERE run_key=?', (key,))[0]
    config = json.loads(job['config'])
else:
    if db.rows("SELECT id FROM jobs WHERE status='运行中'"):
        raise RuntimeError('Another job is running')
    db.backup(p / ('before-independent-' + uuid.uuid4().hex[:8] + '.sqlite'))
    config = db.config()
    config.update(writing_mode='separate_web', selection_mode='hot', draft_count=3)
    key = 'rewrite:independent-acceptance:' + uuid.uuid4().hex
    job = db.job(key, config)
    selected = db.rows('SELECT article_id,reads,likes,observed_at FROM article_metrics WHERE reads IS NOT NULL ORDER BY reads DESC,likes DESC LIMIT 3')
    db.update_job(job['id'], selected=json.dumps([s['article_id'] for s in selected]))
    for s in selected:
        db.execute('INSERT INTO job_metrics VALUES(?,?,?,?,?)', (job['id'],s['article_id'],s['reads'],s['likes'],s['observed_at']))
    tracking.write_text(json.dumps({'key':key}), encoding='utf-8')
e = Engine(db,Vault(p/'keys.dpapi.json'), lambda m:print(json.dumps({'progress':m},ensure_ascii=True),flush=True))
print(json.dumps({'result':e.run(key,config)},ensure_ascii=True),flush=True)
for d in db.rows('SELECT d.title,d.sources,length(d.body) characters FROM drafts d JOIN batch_items b ON b.child_job_id=d.job_id WHERE b.parent_job_id=?',(job['id'],)):
    sources=json.loads(d.pop('sources'))
    d['web_sources']=len([s for s in sources if s.get('kind')=='web'])
    print(json.dumps(d,ensure_ascii=True),flush=True)
