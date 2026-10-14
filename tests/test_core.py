import json
from io import BytesIO
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from PIL import Image
from wewriter.api import ApiError, EveryInfra, Model, Transport, digest, parse_date, wechat_url
from wewriter.config import defaults, due_key, validate
from wewriter.db import Database
from wewriter.engine import Engine
from wewriter.secrets import Vault


class FakeVault:
    def get(self, name):
        return "test-only-never-a-real-key"


class FakeSource:
    calls = 0
    failing = False

    def __init__(self, key, transport):
        pass

    def articles(self, account, limit):
        if self.failing:
            raise ApiError("simulated outage")
        when = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        return [{"id": str(i), "title": f"文章{i}", "url": f"https://mp.weixin.qq.com/s/article{i}", "posted_at": when} for i in range(2)]

    def content(self, url):
        type(self).calls += 1
        return "文中明确指出资料应当保留来源。" * 20 + url


class FakeModel:
    summaries = 0
    writings = 0
    reviews = 0
    failing_write = False

    def __init__(self, *args):
        pass

    def summary(self, article, content):
        type(self).summaries += 1
        return {"summary": "资料应当保留来源。", "facts": [{"claim": "保留来源", "quote": "文中明确指出资料应当保留来源。"}], "uncertainties": []}

    def write(self, sources, config):
        type(self).writings += 1
        if self.failing_write:
            raise ApiError("simulated model outage")
        return {"title": "测试稿件", "outline": ["保留来源"], "body": "内容应保留来源。" + "".join(f"[{s['source_id']}]" for s in sources), "review_notes": []}

    def review(self, draft, sources, config):
        type(self).reviews += 1
        return {**draft, "quality_notes": ["已核对事实边界和表达"]}


class CoreTest(unittest.TestCase):
    def setUp(self):
        workspace_temp = Path(__file__).resolve().parents[1] / ".local-data" / "tests"
        workspace_temp.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=workspace_temp)
        self.path = Path(self.temp.name)
        self.db = Database(self.path / "test.sqlite")
        self.db.add_account("测试公众号", "test")
        self.config = defaults()
        self.config["comments_enabled"] = False
        self.config['selection_mode'] = 'recent'
        self.config['writing_mode'] = 'combined'
        FakeSource.calls, FakeSource.failing = 0, False
        FakeModel.summaries, FakeModel.writings, FakeModel.reviews, FakeModel.failing_write = 0, 0, 0, False
        self.engine = Engine(self.db, FakeVault(), source_factory=FakeSource, model_factory=FakeModel)

    def tearDown(self):
        self.temp.cleanup()

    def test_pipeline_persists_sources_and_prevents_repeat(self):
        self.assertEqual(self.engine.run("manual:1", self.config), "已完成")
        draft = self.db.rows("SELECT * FROM drafts")[0]
        self.assertEqual(len(json.loads(draft["sources"])), 2)
        self.assertIn("原文证据", draft["report"])
        self.assertEqual(self.engine.run("manual:1", self.config), "已完成")
        self.assertEqual(FakeModel.writings, 1)
        self.assertEqual(FakeModel.reviews, 1)
        self.assertEqual(self.engine.run("manual:2", self.config), "无新文章")
        self.assertEqual(FakeSource.calls, 2)
        self.assertEqual(len(self.db.rows("SELECT * FROM articles")), 2)

    def test_image_only_article_uses_cached_openai_vision_text(self):
        database = self.db
        class ImageSource:
            def __init__(self, *args):
                pass
            def articles(self, account, limit):
                when = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
                return [{'title': '图片文章', 'url': 'https://mp.weixin.qq.com/s/image-only',
                         'posted_at': when}]
            def content(self, url):
                article = database.rows('SELECT id FROM articles WHERE url=?', (url,))[0]
                path = database.path.parent / 'article-images' / str(article['id']) / '001.png'
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.new('RGB', (100, 100), 'white').save(path)
                database.replace_article_images(article['id'], [{'position': 1,
                    'source_url': 'https://mmbiz.qpic.cn/example',
                    'local_path': path.relative_to(database.path.parent).as_posix(),
                    'alt_text': '', 'width': 100, 'height': 100}])
                return '短文字'
        class ImageModel(FakeModel):
            image_calls = 0
            def analyze_article_images(self, article, paths, existing_text=''):
                type(self).image_calls += 1
                return {'content': '【图片识别资料（AI 转写，可能有误，需人工核对）】\n' + '图片中的有效资料。' * 20,
                        'image_count': 1, 'article_summary': '图片资料'}
        config = dict(self.config)
        config.update(article_limit=1, min_sources=1, image_articles='vision')
        engine = Engine(database, FakeVault(), source_factory=ImageSource, model_factory=ImageModel)
        self.assertEqual(engine.run('image-only', config), '已完成')
        article = database.rows("SELECT * FROM articles WHERE title='图片文章'")[0]
        self.assertIn('图片识别资料', article['content'])
        self.assertEqual(ImageModel.image_calls, 1)
        self.assertIn('已使用 OpenAI 识别图片内容', database.rows('SELECT report FROM drafts WHERE job_id=(SELECT id FROM jobs WHERE run_key=?)', ('image-only',))[0]['report'])

    def test_retry_reuses_completed_summaries_and_content(self):
        FakeModel.failing_write = True
        self.assertEqual(self.engine.run("retry", self.config), "失败")
        self.assertEqual(FakeModel.summaries, 2)
        FakeModel.failing_write = False
        self.assertEqual(self.engine.run("retry", self.config), "已完成")
        self.assertEqual(FakeModel.summaries, 2)
        self.assertEqual(FakeSource.calls, 2)
        self.assertEqual(len(self.db.rows("SELECT * FROM drafts")), 1)

    def test_retry_replaces_old_warnings_instead_of_accumulating_them(self):
        class RetrySource(FakeSource):
            failing_content = True
            def content(self, url):
                if type(self).failing_content:
                    raise ApiError('第一次正文读取失败')
                return super().content(url)
        engine = Engine(self.db, FakeVault(), source_factory=RetrySource,
                        model_factory=FakeModel)
        self.assertEqual(engine.run('warning-retry', self.config), '失败')
        job = self.db.rows("SELECT * FROM jobs WHERE run_key='warning-retry'")[0]
        self.assertIn('第一次正文读取失败', job['warnings'])
        RetrySource.failing_content = False
        self.assertEqual(engine.run('warning-retry', self.config), '已完成')
        job = self.db.rows("SELECT * FROM jobs WHERE run_key='warning-retry'")[0]
        self.assertEqual(json.loads(job['warnings']), [])

    def test_collection_outage_is_not_no_new_articles(self):
        FakeSource.failing = True
        self.assertEqual(self.engine.run("outage", self.config), "失败")
        self.assertEqual(len(self.db.rows("SELECT * FROM drafts")), 0)

    def test_missing_keys_creates_failed_job(self):
        engine = Engine(self.db, Vault(self.path / "keys"))
        self.assertEqual(engine.run("no-key", self.config), "失败")
        self.assertIn("扫码登录", self.db.rows("SELECT message FROM jobs")[0]["message"])

    def test_insufficient_material_never_fabricates(self):
        self.config["min_sources"] = 3
        self.assertEqual(self.engine.run("few", self.config), "失败")
        self.assertEqual(FakeModel.writings, 0)

    def test_deleting_account_retains_articles(self):
        self.engine.run("keep", self.config)
        self.db.execute("DELETE FROM accounts")
        self.assertEqual(len(self.db.rows("SELECT * FROM articles")), 2)

    def test_recovery_marks_interrupted(self):
        job = self.db.job("crash", self.config)
        self.db.update_job(job["id"], status="运行中")
        self.db.recover()
        self.assertEqual(self.db.rows("SELECT status FROM jobs")[0]["status"], "已中断")

    def test_draft_versions_and_backup_restore(self):
        self.engine.run("save", self.config)
        draft = self.db.rows("SELECT * FROM drafts")[0]
        self.db.edit_draft(draft["id"], "编辑标题", "修改正文", "已审核")
        self.assertEqual(len(self.db.rows("SELECT * FROM draft_versions")), 2)
        self.config["schedule_enabled"] = True
        self.db.save_config(self.config)
        backup = self.path / "backup.sqlite"
        self.db.backup(backup)
        self.db.add_account("后来增加", "later")
        self.db.restore(backup)
        self.assertEqual(len(self.db.accounts()), 1)
        self.assertEqual(self.db.rows("SELECT title FROM drafts")[0]["title"], "编辑标题")
        self.assertFalse(self.db.config()["schedule_enabled"])

    def test_clear_all_preserves_settings_and_backup_can_restore(self):
        self.engine.run("clear-all", self.config)
        self.config["schedule_enabled"] = True
        self.db.save_config(self.config)
        backup = self.path / "before-clear.sqlite"
        self.db.backup(backup)
        removed = self.db.clear_all()
        self.assertGreater(removed["articles"], 0)
        for table in ("accounts", "articles", "drafts", "jobs", "analyses", "draft_versions"):
            self.assertFalse(self.db.rows("SELECT * FROM " + table))
        self.assertFalse(self.db.config()["schedule_enabled"])
        self.db.restore(backup)
        self.assertTrue(self.db.accounts())
        self.assertTrue(self.db.rows("SELECT * FROM drafts"))

    def test_restore_rejects_unrelated_database_without_damage(self):
        import sqlite3
        backup = self.path / "invalid.sqlite"
        sqlite3.connect(backup).close()
        with self.assertRaises(ValueError):
            self.db.restore(backup)
        self.assertEqual(len(self.db.accounts()), 1)

    def test_dpapi_roundtrip_does_not_store_plaintext(self):
        vault = Vault(self.path / "vault.json")
        secret = "test-secret-中文-123"
        vault.set_many({"qwen": secret})
        self.assertEqual(vault.get("qwen"), secret)
        self.assertNotIn(secret, vault.path.read_text())
        vault.set_many({"qwen": ""})
        self.assertEqual(vault.get("qwen"), secret)
        vault.delete("qwen")
        self.assertFalse(vault.has("qwen"))


class ScheduleTest(unittest.TestCase):
    def config(self):
        c = defaults()
        c.update(schedule_enabled=True, schedule_start="2026-10-10")
        return c

    def test_no_run_before_first_enabled_day(self):
        self.assertIsNone(due_key(self.config(), datetime.fromisoformat("2026-10-10T08:00:00+08:00")))

    def test_due_at_exact_time(self):
        self.assertEqual(due_key(self.config(), datetime.fromisoformat("2026-10-10T09:00:00+08:00")), "daily:2026-10-10")

    def test_missed_days_coalesced_and_before_today_schedule(self):
        self.assertEqual(due_key(self.config(), datetime.fromisoformat("2026-10-15T08:00:00+08:00")), "daily:2026-10-14")

    def test_disabled(self):
        self.assertIsNone(due_key(defaults()))

    def test_different_timezones(self):
        c = self.config()
        c["timezone"] = "Australia/Sydney"
        self.assertEqual(due_key(c, datetime.fromisoformat("2026-10-10T00:00:00+00:00")), "daily:2026-10-10")

    def test_validate_rejects_bad_ranges_and_key_in_url(self):
        for invalid in (0, 101):
            c = defaults()
            c["article_limit"] = invalid
            with self.assertRaises(ValueError):
                validate(c)
        c = defaults()
        c["providers"]["openai"]["base_url"] = "https://example.com/?key=secret"
        with self.assertRaises(ValueError):
            validate(c)


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.payloads = []
        self.timeouts = []

    def request(self, url, key, payload=None, *, timeout=100):
        self.payloads.append((url, payload))
        self.timeouts.append(timeout)
        return self.response


class AdapterTest(unittest.TestCase):
    def test_openai_vision_uses_responses_image_input(self):
        result = {'article_summary': '促销信息', 'images': [{'number': 1,
            'visible_text': '活动日期 10 月 16 日', 'visual_content': '商品信息表', 'uncertainty': ''}]}
        response = {'status': 'completed', 'output': [{'type': 'message', 'content': [
            {'type': 'output_text', 'text': json.dumps(result, ensure_ascii=False)}]}]}
        transport = FakeTransport(response)
        workspace_temp = Path(__file__).resolve().parents[1] / '.local-data' / 'tests'
        workspace_temp.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=workspace_temp) as temporary:
            path = Path(temporary) / 'input.png'
            Image.new('RGB', (100, 80), 'white').save(path)
            model = Model('openai', defaults()['providers']['openai'], 'x', transport)
            recognized = model.analyze_article_images({'title': '图片文章'}, [path], '')
        content = transport.payloads[0][1]['input'][0]['content']
        self.assertEqual([part['type'] for part in content], ['input_text', 'input_image'])
        self.assertIn('JSON', content[0]['text'])
        self.assertTrue(content[1]['image_url'].startswith('data:image/jpeg;base64,'))
        self.assertEqual(content[1]['detail'], 'auto')
        self.assertEqual(transport.timeouts[0], 200)
        self.assertIn('活动日期 10 月 16 日', recognized['content'])

    def test_openai_vision_limits_one_request_to_eight_images(self):
        result = {'article_summary': '概述', 'images': [
            {'number': index, 'visible_text': f'图{index}', 'visual_content': '', 'uncertainty': ''}
            for index in range(1, 9)]}
        response = {'status': 'completed', 'output': [{'type': 'message', 'content': [
            {'type': 'output_text', 'text': json.dumps(result, ensure_ascii=False)}]}]}
        transport = FakeTransport(response)
        workspace_temp = Path(__file__).resolve().parents[1] / '.local-data' / 'tests'
        workspace_temp.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=workspace_temp) as temporary:
            paths = []
            for index in range(12):
                path = Path(temporary) / f'{index}.png'
                Image.new('RGB', (20, 20), 'white').save(path)
                paths.append(path)
            model = Model('openai', defaults()['providers']['openai'], 'x', transport)
            self.assertEqual(model.analyze_article_images({'title': '多图文章'}, paths)['image_count'], 8)
        content = transport.payloads[0][1]['input'][0]['content']
        self.assertEqual(sum(part['type'] == 'input_image' for part in content), 8)

    def test_deepseek_disables_default_thinking_for_json_tasks(self):
        t = FakeTransport({"choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]})
        model = Model("deepseek", defaults()["providers"]["deepseek"], "x", t)
        self.assertEqual(model.complete("Only JSON", "test"), {"ok": True})
        self.assertEqual(t.payloads[0][1]["thinking"], {"type": "disabled"})
        self.assertNotIn("enable_thinking", t.payloads[0][1])

    def test_everyinfra_uses_verified_contract(self):
        transport = FakeTransport({"results": []})
        self.assertEqual(EveryInfra("x", transport).articles("gh_123", 24), [])
        url, body = transport.payloads[0]
        self.assertEqual(body, {"platform": "wechat_oa", "action": "account_articles", "params": {"account": "gh_123", "limit": 24}})

    def test_summary_is_not_accepted_as_fulltext(self):
        transport = FakeTransport({"results": [{"snippet": "摘要" * 100}]})
        with self.assertRaises(ApiError):
            EveryInfra("x", transport).content("https://mp.weixin.qq.com/s/abc")

    def test_body_accepts_documented_content(self):
        text = "正文资料" * 100
        transport = FakeTransport({"results": [{"content": text, "url": "https://mp.weixin.qq.com/s/abc"}]})
        self.assertEqual(EveryInfra("x", transport).content("https://mp.weixin.qq.com/s/abc"), text)

    def test_wrong_page_body_rejected(self):
        transport = FakeTransport({"results": [{"content": "正文资料" * 100, "url": "https://example.com"}]})
        with self.assertRaises(ApiError):
            EveryInfra("x", transport).content("https://mp.weixin.qq.com/s/abc")

    def test_tracking_url_deduplication(self):
        a = "https://mp.weixin.qq.com/s?__biz=abc&mid=12&idx=1&sn=xxx&scene=99"
        b = "http://mp.weixin.qq.com/s?sn=xxx&mid=12&idx=1&__biz=abc&from=share#read"
        self.assertEqual(wechat_url(a), wechat_url(b))

    def test_untrusted_url_rejected(self):
        for url in ("file:///secret", "http://localhost", "https://mp.weixin.qq.com.evil.com/s/1"):
            with self.assertRaises(ValueError):
                wechat_url(url)

    def test_timestamp_units_and_naive_china_time(self):
        expected = parse_date("2026-10-10T00:00:00Z")
        self.assertEqual(parse_date("2026-10-10 08:00:00"), expected)
        self.assertEqual(parse_date(expected.timestamp() * 1000), expected)

    def model(self, result, finish="stop"):
        t = FakeTransport({"choices": [{"finish_reason": finish, "message": {"content": json.dumps(result, ensure_ascii=False)}}]})
        return Model("deepseek", defaults()["providers"]["deepseek"], "x", t)

    def test_fabricated_quote_uses_extract_from_original(self):
        model = self.model({"summary": "总结", "facts": [{"claim": "事实", "quote": "并不存在"}], "uncertainties": []})
        result = model.summary({"title": "测试"}, "这是一段长度足够的原文证据，用于验证安全兜底逻辑。")
        self.assertIn(result["facts"][0]["quote"], "这是一段长度足够的原文证据，用于验证安全兜底逻辑。")
        self.assertIn("原文连续片段", result["uncertainties"][0])

    def test_valid_quote_and_json(self):
        model = self.model({"summary": "总结", "facts": [{"claim": "事实", "quote": "原文"}], "uncertainties": []})
        self.assertEqual(model.summary({"title": "测试"}, "原文证据")["summary"], "总结")

    def test_invalid_citation_rejected(self):
        model = self.model({"title": "标题", "body": "正文[S999]", "outline": [], "review_notes": []})
        with self.assertRaises(ApiError):
            model.write([{"source_id": "S1"}], defaults())

    def test_chat_assistant_follow_up_is_removed_from_article(self):
        model = self.model({'title': '标题', 'body':
            '这是可发布正文。[S1]\n\n现有资料已经说明主要结论。如果你愿意，我可以逐条打开商品页核验，请把链接发来。',
            'outline': [], 'review_notes': []})
        result = model.write([{"source_id": "S1"}], defaults())
        self.assertEqual(result['body'], '这是可发布正文。[S1]')
        self.assertNotIn('如果你愿意', result['body'])

    def test_truncated_model_output_rejected(self):
        with self.assertRaises(ApiError):
            self.model({"ok": True}, finish="length").complete("system", "user")

    def test_auth_error_not_retried_or_leaked(self):
        transport = Transport()
        error = HTTPError("https://example.com", 401, "secret-response", {}, None)
        with patch.object(transport.opener, "open", side_effect=error) as call:
            with self.assertRaises(ApiError) as caught:
                transport.request("https://example.com", "secret-key", {})
            self.assertEqual(call.call_count, 1)
            self.assertTrue(caught.exception.fatal)
            self.assertNotIn("secret", str(caught.exception))

    def test_bad_request_keeps_redacted_provider_reason_and_request_id(self):
        transport = Transport()
        body = json.dumps({'error': {'message':
            'Invalid image at data:image/jpeg;base64,' + ('A' * 200) + ' using sk-sensitive123'}}).encode()
        error = HTTPError('https://example.com', 400, 'bad request',
                          {'x-request-id': 'req_test_123'}, BytesIO(body))
        with patch.object(transport.opener, 'open', side_effect=error):
            with self.assertRaises(ApiError) as caught:
                transport.request('https://example.com', 'secret-key', {})
        message = str(caught.exception)
        self.assertIn('图片的格式或尺寸', message)
        self.assertIn('req_test_123', message)
        self.assertNotIn('AAAA', message)
        self.assertNotIn('sk-sensitive123', message)
        self.assertEqual(caught.exception.kind, 'bad_request')

    def test_limited_retry_for_429(self):
        transport = Transport()
        with patch.object(transport.opener, "open", side_effect=lambda *a, **k: (_ for _ in ()).throw(HTTPError("https://example.com", 429, "limit", {"Retry-After": "0"}, None))) as call:
            with self.assertRaises(ApiError):
                transport.request("https://example.com", "x", {})
            self.assertEqual(call.call_count, 3)


if __name__ == "__main__":
    unittest.main()
