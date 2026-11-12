"""Paired live writing test. Reads only provider settings and lets the app vault authenticate."""
import json
import os
import re
import sqlite3
import tempfile
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wewriter.api import Model, Transport
from wewriter.config import migrate, defaults
from wewriter.costs import CostMeter, UtilityFunnel, utility_summary
from wewriter.db import Database
from wewriter.secrets import Vault
from wewriter.wiki import add_note, retrieve, save_preference, preferences_for

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs' / 'wiki-ab'
SOURCE = 'https://www.mofcom.gov.cn/zwgk/zcfb/art/2025/art_a656628b5cc0477680226df555503474.html'
WIKI = 'https://app.www.gov.cn/govdata/gov/202501/09/523473/article.html'

def main():
    data = Path(os.environ['LOCALAPPDATA']) / 'WeWriter'
    with sqlite3.connect((data/'workspace.sqlite').as_uri()+'?mode=ro', uri=True) as prod:
        row = prod.execute("SELECT value FROM settings WHERE key='config'").fetchone()
    local = migrate(json.loads(row[0]) if row else {})
    name = local['writer_provider']
    settings = local['providers'][name]
    source_facts = ['2025年，个人消费者购买12类符合条件的家电可享受以旧换新补贴。',
                    '二级能效或水效产品按最终售价的15%补贴，一级产品的比例为20%。']
    primary = {'source_id':'S1','article_id':1,'title':'2025年家电换新补贴范围和比例',
               'account_name':'','url':SOURCE,'published_at':'2025-01',
               'excerpt':'\n'.join(source_facts),'analysis':[{'summary':'2025年家电补贴面向个人消费者。',
               'facts':[{'claim':f,'quote':f} for f in source_facts], 'uncertainties':[]}]}
    passages = ['2025年家电补贴按人、按品类计算；通常每类可补贴1件，空调例外，每人最多可补贴3件。每件产品的补贴上限为2000元。',
                '即使个人消费者在2024年享受过某一类家电的补贴，2025年购买该类家电仍可再次享受补贴。']
    config = {**defaults(), 'writing_mode':'multi','target_words':500,'topic':'回顾2025年家电以旧换新：谁能享受，购买前怎样算清补贴？',
              'style':'面向家庭消费者，简洁说明购买前该核对什么。', 'draft_first_mode':True}
    OUT.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        db=Database(Path(folder)/'test.sqlite')
        job=db.job('synthetic-edit',config)
        did=db.save_draft(job['id'],'编辑示例','政策背景在前，读者对象在后。',[], '')
        rev=db.edit_draft(did,'编辑示例','先说明哪些家庭适用，再解释政策。','待审核')
        save_preference(db,0,did,rev,'家电','开头先写适用对象。')
        config['remembered_styles']=preferences_for(db,config['topic'],[primary])
        assert config['remembered_styles']==['开头先写适用对象。']
        for n, passage in enumerate(passages,1):
            add_note(db,'家电补贴',f'发改环资〔2025〕13号，第八条·摘记{n}',passage)
        add_note(db,'冷链运输','无关笔记','运输水果时应记录车厢温度。')
        transport=Transport()
        transport.retry_attempts=1
        transport.cost_meter=CostMeter(UtilityFunnel(db))
        model=Model(name,settings,Vault(data/'keys.dpapi.json').get(name),transport)
        for arm in ('A','B'):
            sources = [primary]
            if arm=='B':
                sources += retrieve(db, config['topic'], sources)
            output=model.write(sources, config)
            text='# '+output['title']+'\n\n'+output['body']+'\n'
            (OUT/f'{arm}.md').write_text(text,encoding='utf-8')
            print(arm,settings['model'],len(text),[s['source_id'] for s in sources],utility_summary(db),flush=True)
        (OUT/'inputs.json').write_text(json.dumps({'model':settings['model'],'topic':config['topic'],'style':config['style'],
                'preferences':config['remembered_styles'],'target':500,'primary':source_facts,'source_url':SOURCE,'wiki':passages,'wiki_url':WIKI,
                'retrieved':[{'id':s['source_id'],'excerpt':s['excerpt']} for s in sources[1:]],
                'cost':utility_summary(db)},ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':
    main()
