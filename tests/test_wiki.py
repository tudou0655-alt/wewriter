"""Offline Wiki tests. All policy names, dates and quotations here are fictional."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from wewriter.api import public_source
from wewriter.db import Database, stamp
from wewriter.pipeline import composition
from wewriter.style import fact_sheet
from wewriter.wiki import add_note, backfill, compile_page, read_document, retrieve


class WikiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / 'test.sqlite')
        self.primary = self.article('本次设备更新', '本次设备更新只公布申报窗口。')
        self.sources = [{'source_id': 'S1', 'article_id': self.primary, 'title': '本次设备更新',
                         'account_name': '甲', 'url': 'test:本次设备更新', 'published_at': '',
                         'analysis': [{'summary': '本次设备更新开放申报', 'facts': [], 'uncertainties': []}]}]

    def article(self, title, content):
        article_id = self.db.save_article(dict(title=title, source_key=title, account_name='甲',
                                          url='test:' + title, published_at='2024-01-01'))
        self.db.execute('UPDATE articles SET content=? WHERE id=?', (content, article_id))
        data = {'wiki_topic': '设备更新', 'summary': title,
                'facts': [{'claim': content, 'quote': content}, {'quote': '原文根本没有这句话。'}]}
        self.db.execute('INSERT INTO analyses VALUES(?,?,?,?)', (article_id,'a'*64,json.dumps(data),stamp()))
        return article_id

    def test_old_summaries_are_grounded_and_user_documents_survive_article_clear(self):
        historical = self.article('往期材料的公众号长标题', '往期设备更新试点仅面向甲县。')
        self.assertEqual(backfill(self.db), 2)
        self.assertEqual(backfill(self.db), 0)
        add_note(self.db, '设备更新', '虚构实施细则', '前言\n设备更新需分别记录采购与验收环节。')
        add_note(self.db, '烘焙食谱', '笔记乙', '先揉面团再加入黄油。')
        hits = retrieve(self.db, '', self.sources)
        self.assertEqual({h['wiki_origin'] for h in hits}, {'自有资料', '公众号'})
        old = next(h for h in hits if h['wiki_origin'] == '公众号')
        self.assertEqual(old['excerpt'], '往期设备更新试点仅面向甲县。')
        self.assertNotIn('长标题', str(public_source(old)))
        self.assertIn('2024', fact_sheet([old])[0]['scope'])
        self.db.clear_records('articles', [historical])
        self.assertEqual([h['title'] for h in retrieve(self.db, '', self.sources)], ['虚构实施细则'])
        self.db.clear_all()
        self.assertFalse(retrieve(self.db, '', self.sources))

    def test_persistent_synthesis_checks_quotes_and_invalidation(self):
        topic = '设备更新'
        add_note(self.db, topic, '虚构细则', '只有设备更新验收完成后才能申请款项。')
        key = self.db.rows('SELECT id FROM wiki_entries')[0]['id']
        model = Mock()
        model.complete.return_value = {'notes': [{'id': key, 'point': '先验收，再申请。', 'quote': '设备更新验收完成后才能申请款项'},
                                                {'id': key, 'point': '虚构', 'quote': '所有企业都能申报'}]}
        compile_page(self.db, topic, model)
        compile_page(self.db, topic, model)
        model.complete.assert_called_once()
        hits = retrieve(Database(self.db.path), '', self.sources)
        self.assertEqual(hits[0]['analysis'][0]['facts'][0]['claim'], '先验收，再申请。')
        add_note(self.db, topic, '虚构细则', '修订后设备更新项目需要重新验收。')
        self.assertFalse(self.db.rows('SELECT * FROM wiki_pages'))
        self.assertEqual(len(self.db.rows('SELECT * FROM wiki_entries')), 1)
        with self.assertRaises(ValueError):
            compile_page(self.db, topic, model)
        self.assertFalse(self.db.rows('SELECT * FROM wiki_pages'))

    def test_writer_receives_selected_passages_and_report_records_actual_citation(self):
        for i in range(5):
            add_note(self.db, '设备更新', f'虚构材料{i}', ('甲'*400 + '\n') + f'设备更新需要核对申报资格{i}。')
        for enabled in (False, True):
            config = {**self.db.config(), 'wiki_enabled': enabled, 'min_sources': 1, 'writing_mode': 'multi'}
            ctx = SimpleNamespace(config=config, job=self.db.job(str(enabled), config), writer=None,
                                  warnings=[], review_state='skipped', warn=lambda _: None,
                                  funnel=SimpleNamespace(data={}, save=lambda: None))
            def write(ctx, sources):
                self.assertEqual(len(sources), 4 if enabled else 1)
                self.assertTrue(all(len(s['excerpt']) <= 320 for s in sources[1:]))
                return {'title':'资格怎么核对', 'body':'先核对申报资格。[S1]' + ('[S2]' if enabled else ''),
                        'outline':[], 'review_notes':[]}, []
            engine = SimpleNamespace(db=self.db, check=lambda:None, notify=lambda _:None,
                                     _materials=lambda *_:self.sources.copy(), _compose=write, _report=composition.report)
            composition.write_one(engine, ctx, [self.primary])
            draft = self.db.rows('SELECT report,sources FROM drafts WHERE job_id=?', (ctx.job['id'],))[0]
            self.assertEqual('正文已标注' in draft['report'], enabled)
            self.assertEqual('提供给模型参考' in draft['report'], enabled)
            self.assertEqual(len(json.loads(draft['sources'])), 4 if enabled else 1)

    def test_import_keeps_word_tables_and_markdown_text(self):
        import docx
        path = Path(self.temp.name) / '试点.docx'
        document = docx.Document()
        document.add_table(1,1).cell(0,0).text = '设备更新验收条件'
        document.save(path)
        self.assertIn('设备更新验收条件',read_document(path))
        path = path.with_suffix('.md')
        path.write_text('# 笔记\n正文', encoding='utf-8-sig')
        self.assertEqual(read_document(path), '# 笔记\n正文')
