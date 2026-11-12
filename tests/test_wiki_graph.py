import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from wewriter.api import writing_style
from wewriter.db import Database
from wewriter.wiki import (add_note, compile_page, preferences_for, retrieve, revision_change, save_card,
                          save_preference)


class WikiGraphTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Database(Path(tmp.name)/'graph.sqlite')

    def test_confirmed_revision_scoped_to_matching_draft(self):
        db = self.db
        job = db.job('sample',{})
        did = db.save_draft(job['id'],'试稿','先写背景。',[],'')
        rev = db.edit_draft(did,'试稿','先给申报对象。','待审核')
        self.assertEqual(revision_change(db,did,rev)['body']['after'],'先给申报对象。')
        self.assertEqual(preferences_for(db,'设备更新',[]),[])
        save_preference(db,0,did,rev,'设备更新','先写适用对象。')
        self.assertEqual(preferences_for(db,'城市供热',[]),[])
        hits=preferences_for(db,'设备更新',[])
        self.assertEqual(hits,['先写适用对象。'])
        self.assertIn('本篇要求（优先）：写给工程师',writing_style({'remembered_styles':hits,'style':'写给工程师'}))
        self.assertEqual(writing_style({'style':'原风格'}),'原风格')
        db.clear_all()
        self.assertFalse(preferences_for(db,'设备更新',[]))

    def test_manual_card_provenance_edit_removal_and_recompile(self):
        db=self.db
        quote='设备更新只对甲地企业适用'
        add_note(db,'设备更新','来源 A',quote)
        eid=db.rows('SELECT id FROM wiki_entries')[0]['id']
        model=SimpleNamespace(complete=lambda *a,**k:{'notes':[{'id':eid,'quote':quote,'point':'机归要点'}]})
        compile_page(db,'设备更新',model)
        card=db.rows('SELECT id FROM wiki_cards')[0]['id']
        with self.assertRaises(ValueError):
            save_card(db,eid,card,'错误','正文里没有的引文')
        save_card(db,eid,card,'仅甲地适用',quote)
        add_note(db,'设备更新','来源 B','另有资料提到设备更新。')
        compile_page(db,'设备更新',model)
        self.assertEqual(db.rows('SELECT point FROM wiki_cards'),[{'point':'仅甲地适用'}])
        db.execute("UPDATE wiki_cards SET state='hidden' WHERE id=?",(card,))
        add_note(db,'设备更新','来源 B','设备更新资料再次修订。')
        compile_page(db,'设备更新',model)
        self.assertFalse(db.rows("SELECT * FROM wiki_cards WHERE state<>'hidden'"))
        self.assertFalse(any(f['analysis'][0]['facts'][0]['claim']=='仅甲地适用' for f in retrieve(db,'设备更新',[])))
        add_note(db,'设备更新','来源 A','设备更新采用另一份依据。')
        self.assertFalse(db.rows('SELECT * FROM wiki_cards WHERE entry_id=?',(eid,)))
