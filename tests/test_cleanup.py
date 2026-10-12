import unittest
import test_core


class CleanupTest(unittest.TestCase):
    def setUp(self):
        self.f = test_core.CoreTest(); self.f.setUp()
        self.f.engine.run('cleanup:fixture', self.f.config)
    def tearDown(self):
        self.f.tearDown()

    def test_article_cleanup_purges_content_but_preserves_draft(self):
        db = self.f.db
        draft = db.rows('SELECT * FROM drafts')[0]
        article = db.rows('SELECT * FROM articles')[0]
        image = db.path.parent / 'article-images' / str(article['id']) / '001.png'
        image.parent.mkdir(parents=True)
        image.write_bytes(b'image')
        db.replace_article_images(article['id'], [{'position': 1, 'source_url': 'https://mmbiz.qpic.cn/a',
            'local_path': image.relative_to(db.path.parent).as_posix(), 'alt_text': '',
            'width': 100, 'height': 100}])
        self.assertEqual(db.clear_records('articles', [article['id']]), 1)
        a = db.rows('SELECT * FROM articles WHERE id=?', (article['id'],))[0]
        self.assertEqual(a['content'], '')
        self.assertEqual(a['cleared'], 1)
        self.assertFalse(db.rows('SELECT * FROM analyses WHERE article_id=?', (article['id'],)))
        self.assertFalse(db.article_images(article['id']))
        self.assertFalse(image.exists())
        self.assertEqual(db.rows('SELECT * FROM drafts')[0], draft)
        self.assertEqual(self.f.engine.run('cleanup:next', self.f.config), '无新文章')

    def test_draft_cleanup_removes_versions_and_does_not_regenerate(self):
        db = self.f.db
        draft = db.rows('SELECT * FROM drafts')[0]
        db.edit_draft(draft['id'], '新标题', '新正文', '已审核')
        self.assertEqual(db.clear_records('drafts', [draft['id']]), 1)
        self.assertFalse(db.rows('SELECT * FROM draft_versions'))
        d = db.rows('SELECT * FROM drafts')[0]
        self.assertEqual((d['body'],d['report'],d['cleared']), ('','',1))
        self.assertTrue(db.rows('SELECT * FROM articles WHERE content<>? AND cleared=0', ('',)))
        self.assertEqual(self.f.engine.run('cleanup:fixture', self.f.config), '已清理')
        self.assertEqual(self.f.engine.run('cleanup:next', self.f.config), '无新文章')

    def test_empty_selection_never_means_clear_all(self):
        self.assertEqual(self.f.db.clear_records('drafts', []), 0)
        self.assertEqual(len(self.f.db.rows('SELECT * FROM drafts WHERE cleared=0')), 1)
        self.assertEqual(self.f.db.clear_records('articles'), 2)
        self.assertEqual(self.f.db.clear_records('articles'), 0)

    def test_running_job_blocks_cleanup_atomically(self):
        db = self.f.db
        job = db.job('running', self.f.config)
        db.update_job(job['id'], status='运行中')
        with self.assertRaises(ValueError):
            db.clear_records('articles')
        self.assertEqual(len(db.rows('SELECT * FROM articles WHERE cleared=0')), 2)

    def test_opt_in_recollects_cleared_used_articles_once(self):
        db = self.f.db
        old_draft = db.rows('SELECT * FROM drafts')[0]
        db.clear_records('articles')
        self.f.config['allow_recollect_cleared'] = True
        self.assertEqual(self.f.engine.run('recollect:new', self.f.config), '已完成')
        self.assertEqual(len(db.rows('SELECT * FROM drafts')), 2)
        self.assertEqual(db.rows('SELECT * FROM drafts WHERE id=?', (old_draft['id'],))[0], old_draft)
        self.assertEqual(len(db.rows("SELECT * FROM articles WHERE cleared=0 AND content<>''")), 2)
        self.assertEqual(self.f.engine.run('recollect:again', self.f.config), '无新文章')

    def test_recollect_does_not_reset_finished_jobs(self):
        self.f.db.clear_records('articles')
        self.f.config['allow_recollect_cleared'] = True
        self.assertEqual(self.f.engine.run('cleanup:fixture', self.f.config), '已完成')
        self.assertEqual(len(self.f.db.rows('SELECT * FROM articles WHERE cleared=1')), 2)
