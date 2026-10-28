"""T-010: five-page UI support — config, review states, task paging, shelf import, export."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from wewriter.config import defaults, migrate, validate
from wewriter.db import Database
from wewriter.engine import window_start
from wewriter.ui import compose_style, draft_hint, job_status_label, phase_index, write_export, write_report
from wewriter.weread import identifier_from_link, shelf_accounts, sync_books


class ConfigTest(unittest.TestCase):
    def test_draft_count_accepts_one_to_five_integers_only(self):
        for count in (1, 2, 3, 4, 5):
            config = defaults()
            config['draft_count'] = count
            validate(config)
        for bad in (0, 6, True, '3', 2.0, None):
            config = defaults()
            config['draft_count'] = bad
            with self.assertRaises(ValueError, msg=repr(bad)):
                validate(config)

    def test_target_words_is_free_within_bounds(self):
        for words in (200, 1234, 20000):
            config = defaults()
            config['target_words'] = words
            validate(config)
        for bad in (199, 20001, True, '1500'):
            config = defaults()
            config['target_words'] = bad
            with self.assertRaises(ValueError):
                validate(config)

    def test_window_modes_and_export_choices_are_validated(self):
        config = defaults()
        for mode in ('today', 'rolling'):
            validate(dict(config, lookback_mode=mode))
        for fmt in ('docx', 'md', 'txt'):
            validate(dict(config, export_format=fmt))
        with self.assertRaises(ValueError):
            validate(dict(config, lookback_mode='week'))
        with self.assertRaises(ValueError):
            validate(dict(config, export_format='pdf'))

    def test_old_config_keeps_values_and_drops_comment_analysis(self):
        old = {'comments_enabled': True, 'draft_count': 2, 'schedule_time': '07:30', 'min_reads': 50}
        config = migrate(old)
        self.assertFalse(config['comments_enabled'])
        self.assertEqual((config['draft_count'], config['schedule_time'], config['min_reads']), (2, '07:30', 50))
        self.assertEqual(config['lookback_mode'], 'rolling')
        validate(config)

    def test_today_window_is_china_calendar_day(self):
        # 2026-10-24 17:30 UTC is 2026-10-25 01:30 in China.
        now = datetime(2026, 10, 24, 17, 30, tzinfo=timezone.utc)
        start = window_start({'lookback_mode': 'today', 'lookback_hours': 24}, now)
        self.assertEqual(start.isoformat(), '2026-10-25T00:00:00+08:00')
        rolling = window_start({'lookback_mode': 'rolling', 'lookback_hours': 72}, now)
        self.assertEqual((now - rolling).total_seconds(), 72 * 3600)

    def test_style_preset_and_extra_compose_one_prompt_field(self):
        self.assertIn('专业简洁', compose_style('专业简洁', ''))
        self.assertTrue(compose_style('通俗自然', '多用案例').endswith('补充要求：多用案例'))
        self.assertEqual(compose_style('我的自定义风格', ''), '我的自定义风格')


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / 'w.sqlite')

    def tearDown(self):
        self.temp.cleanup()

    def draft(self, key='k'):
        job = self.db.job(key, defaults())
        return job, self.db.save_draft(job['id'], '标题', '正文', [], '# 机器报告\n二审提示：需复核')

    def test_review_keeps_machine_report_and_edit_returns_to_review(self):
        job, draft_id = self.draft()
        self.db.set_draft_status(draft_id, '需修改', '数据口径不清')
        self.db.set_draft_status(draft_id, '已通过')
        draft = self.db.rows('SELECT * FROM drafts WHERE id=?', (draft_id,))[0]
        self.assertEqual(draft['status'], '已通过')
        self.assertEqual(draft['revision'], 1)
        self.assertTrue(draft['report'].startswith('# 机器报告\n二审提示：需复核'))
        self.assertIn('需修改 · 数据口径不清', draft['report'])
        self.assertEqual(self.db.qualified_drafts(job['id']), 1)
        self.db.edit_draft(draft_id, '标题', '正文（已改）', '已通过')
        draft = self.db.rows('SELECT * FROM drafts WHERE id=?', (draft_id,))[0]
        self.assertEqual((draft['status'], draft['revision']), ('待审核', 2))
        self.assertEqual(self.db.qualified_drafts(job['id']), 0)
        with self.assertRaises(ValueError):
            self.db.set_draft_status(draft_id, '已发布')

    def test_unchanged_save_keeps_approval_and_old_name_reads_as_passed(self):
        job, draft_id = self.draft()
        self.db.execute("UPDATE drafts SET status='已审核' WHERE id=?", (draft_id,))
        self.assertEqual(Database.review_state('已审核'), '已通过')
        self.assertEqual(self.db.qualified_drafts(job['id']), 1)
        self.db.edit_draft(draft_id, '标题', '正文', '已审核')
        self.assertEqual(self.db.rows('SELECT status FROM drafts')[0]['status'], '已审核')

    def test_batch_children_are_folded_into_parent_task(self):
        parent = self.db.job('parent', defaults())
        child = self.db.job('child', defaults())
        self.db.execute('INSERT INTO batch_items(parent_job_id,article_id,child_job_id) VALUES(?,?,?)',
                        (parent['id'], 1, child['id']))
        draft_id = self.db.save_draft(child['id'], '子稿', '正文', [], '报告')
        total, rows = self.db.top_jobs(10)
        self.assertEqual((total, [r['run_key'] for r in rows]), (1, ['parent']))
        self.assertEqual([d['id'] for d in self.db.task_drafts(parent['id'])], [draft_id])
        for n in range(12):
            self.db.job(f'more-{n}', defaults())
        total, page = self.db.top_jobs(10, 10)
        self.assertEqual((total, len(page)), (13, 3))

    def test_queued_job_has_its_own_label(self):
        job = self.db.job('queued', defaults())
        self.db.update_job(job['id'], stage='排队中')
        self.assertEqual(job_status_label(self.db.rows('SELECT * FROM jobs')[0]), '排队中')


class ShelfTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / 'w.sqlite')
        self.books = [{'bookId': 'MP_WXS_1', 'title': '甲'}, {'bookId': 'MP_WXS_2', 'title': '乙'},
                      {'bookId': 'MP_WXS_1', 'title': '甲'}, {'bookId': '3300012345', 'title': '一本书'},
                      {'bookId': 'MP_WXS_3', 'title': ''}]

    def tearDown(self):
        self.temp.cleanup()

    def test_only_unique_public_accounts_are_offered(self):
        self.assertEqual(shelf_accounts(self.books), [{'identifier': 'MP_WXS_1', 'name': '甲'},
                                                      {'identifier': 'MP_WXS_2', 'name': '乙'}])
        self.assertEqual(shelf_accounts([]), [])

    def test_selected_import_keeps_existing_state_and_does_not_match_by_name(self):
        existing = self.db.add_account('乙', 'MP_WXS_9')
        self.db.execute('UPDATE accounts SET enabled=0 WHERE id=?', (existing,))
        sync_books(self.db, self.books, selected={'MP_WXS_1'})
        names = {a['identifier']: a for a in self.db.accounts()}
        self.assertEqual(set(names), {'MP_WXS_1', 'MP_WXS_9'})
        self.assertEqual(names['MP_WXS_9']['enabled'], 0)
        sync_books(self.db, self.books)
        self.assertEqual({a['identifier'] for a in self.db.accounts()}, {'MP_WXS_1', 'MP_WXS_2', 'MP_WXS_9'})
        sync_books(self.db, self.books)
        self.assertEqual(len(self.db.accounts()), 3)

    def test_manual_add_reads_biz_from_article_link(self):
        link = 'https://mp.weixin.qq.com/s?__biz=MzUzMzUwOTEyNQ==&mid=1&idx=1&sn=a&chksm=b'
        self.assertEqual(identifier_from_link(link), 'MP_WXS_3533509125')
        self.assertEqual(identifier_from_link(' MP_WXS_42 '), 'MP_WXS_42')
        for bad in ('思博产业研究', 'https://example.com/s?__biz=MzA=', 'https://mp.weixin.qq.com/s/abc',
                    'https://mp.weixin.qq.com/s?__biz=!!!'):
            with self.assertRaises(ValueError, msg=bad):
                identifier_from_link(bad)


class ExportFileTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.sources = [{'source_id': 'S1', 'title': '原文', 'url': 'https://mp.weixin.qq.com/s/x'}]
        self.body = '## 小标题\n\n正文事实。[S1]\n\n- 要点一 [W3]\n\n> 引用'

    def tearDown(self):
        self.temp.cleanup()

    def test_docx_md_txt_open_and_contain_no_internal_marks(self):
        from docx import Document
        path = write_export(self.dir / 'a.docx', 'docx', '标题', self.body, self.sources)
        text = '\n'.join(p.text for p in Document(str(path)).paragraphs)
        self.assertIn('标题', text)
        self.assertIn('正文事实。', text)
        self.assertNotRegex(text, r'\[[SW]\d+\]')
        self.assertNotIn('mp.weixin.qq.com', text)            # iron rule: no WeChat post in exports
        for fmt in ('md', 'txt'):
            content = write_export(self.dir / ('a.' + fmt), fmt, '标题', self.body, self.sources).read_text(
                encoding='utf-8-sig')
            self.assertNotRegex(content, r'\[[SW]\d+\]')
            self.assertEqual(content.startswith('# 标题'), fmt == 'md')

    def test_report_export_keeps_source_mapping(self):
        report = '[S1] 原文\n【人工审核记录】2026 · 已通过'
        content = write_report(self.dir / 'r.md', 'md', '标题', report).read_text(encoding='utf-8-sig')
        self.assertIn('[S1] 原文', content)
        self.assertIn('人工审核记录', content)


class PhaseTest(unittest.TestCase):
    def test_stage_text_maps_to_four_phases(self):
        self.assertEqual([phase_index(s) for s in ('采集', '正文与摘要', '逐篇写作 1/3', '二审', '完成')],
                         [0, 1, 2, 3, 4])


class DraftHintTest(unittest.TestCase):
    def test_research_hint_only_from_engine_status_section(self):
        ok = '## 审核提示\n- 应删除或降级为“待核实”的内容：国家14类\n\n## 联网补充资料\n[W1] 来源'
        self.assertEqual(draft_hint(ok), '')
        failed = ok + '\n## 联网研究状态\n联网搜索连接失败或超过 300 秒，本稿已降级为仅基于公众号原文生成，未使用联网补充资料。\n'
        self.assertEqual(draft_hint(failed), '联网资料降级，仅基于原文')


class LongOutputTimeoutTest(unittest.TestCase):
    def _model(self, reply):
        from wewriter.api import Model
        seen = []

        class FakeTransport:
            def request(self, url, key, payload, timeout=100):
                seen.append(timeout)
                return {'choices': [{'message': {'content': json.dumps(reply, ensure_ascii=False)}, 'finish_reason': 'stop'}]}
        return Model('deepseek', {'model': 'deepseek-v4-pro', 'base_url': 'https://api.deepseek.com'}, 'k', FakeTransport()), seen

    def test_writing_uses_long_timeout(self):
        from wewriter.api import LONG_OUTPUT_TIMEOUT
        model, seen = self._model({'title': '标题', 'outline': ['a'], 'body': '正文。[S1]', 'review_notes': []})
        model.write([{'source_id': 'S1', 'title': '原文'}], {'topic': '', 'style': '', 'target_words': 2500})
        self.assertEqual(seen, [LONG_OUTPUT_TIMEOUT])

    def test_rejected_review_revision_says_first_draft_kept(self):
        from wewriter.api import ApiError
        revision = {'title': '标题', 'outline': ['a'], 'body': '只剩原文。[S1]', 'review_notes': [], 'quality_notes': ['改了'],
                    'claim_checks': [{'sentence': '标题：标题', 'evidence': 'S1：原文', 'action': '保留', 'note': ''}]}
        model, _ = self._model(revision)
        sources = [{'source_id': 'S1', 'title': '原文'}, {'source_id': 'W1', 'title': '网页'}]
        with self.assertRaises(ApiError) as caught:
            model.review({'title': '标题', 'body': '原文。[S1] 网页。[W1]', 'review_notes': []}, sources,
                         {'writing_mode': 'single_web', 'style': '', 'target_words': 2500})
        self.assertIn('二审修改稿两次未通过校验', str(caught.exception))
        self.assertIn('缺少联网来源 [W]', str(caught.exception))
        self.assertIn('已弃用修改稿', str(caught.exception))
        self.assertNotIn('未保存', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
