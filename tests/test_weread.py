import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from wewriter.api import ApiError, Cancelled
from wewriter.db import Database
from wewriter.weread import (WeReadBrowser, WeReadSession, WeReadSkill, WeReadSource,
                             browser_cookie_state, browser_login, encoded_id, sync_shelf)


BOOK = 'MP_WXS_123'


def group(token, timestamp=1791534053):
    return {'subReviews': [{'review': {'reviewId': BOOK + '_' + token,
            'mpInfo': {'title': token, 'time': timestamp}}}]}


class WeReadTest(unittest.TestCase):
    def setUp(self):
        base = Path('.local-data/tests')
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.db = Database(Path(self.temp.name) / 'db.sqlite')
        self.session = Mock()
        self.session.shelf.return_value = [{'bookId': BOOK, 'title': '测试号'}]
        self.session.check.side_effect = lambda result: result
        self.cancel = Mock()
        self.cancel.is_set.return_value = False
        self.cancel.wait.return_value = False
        self.source = WeReadSource(None, self.db, self.cancel, self.session)

    def tearDown(self):
        self.temp.cleanup()

    def test_pagination_and_persisted_content_ref(self):
        self.session.request.side_effect = [{'reviews': [group('a~b')]}, {'reviews': [group('c')]}]
        articles = self.source.articles(BOOK, 2)
        self.assertEqual(articles[0]['url'], 'https://mp.weixin.qq.com/s/a_b')
        self.assertEqual(self.session.request.call_args_list[1].args[1]['offset'], 1)
        restarted = WeReadSource(None, self.db, self.cancel, self.session)
        self.session.request.side_effect = None
        self.session.request.return_value = '<div id="js_content"><script>secret</script><p>' + '有效正文内容。' * 30 + '</p></div>'
        body = restarted.content(articles[0]['url'])
        self.assertNotIn('secret', body)
        self.assertEqual(self.session.request.call_args.args[1]['reviewId'], BOOK + '_a~b')

    def test_no_cover_fallback_on_error(self):
        self.session.check.side_effect = ApiError('业务码 -2041', True)
        self.session.request.return_value = {'errCode': -2041}
        with self.assertRaises(ApiError):
            self.source.articles(BOOK, 3)
        self.assertEqual(self.session.request.call_count, 1)

    def test_expired_history_list_falls_back_to_official_skill_latest_article(self):
        skill = Mock()
        skill.shelf.return_value = [{'bookId': BOOK, 'title': '测试号'}]
        skill.review.return_value = {'reviewId': BOOK + '_official', 'createTime': 1791910256,
            'mpInfo': {'title': '官方最新文章', 'time': 1791910256,
                       'doc_url': 'https://mp.weixin.qq.com/s/official',
                       'readNum': 3210, 'likeNum': 98}}
        source = WeReadSource('{"version":2,"cookies":[]}', self.db, self.cancel,
                              self.session, profile_dir=Path(self.temp.name) / 'profile',
                              skill=skill)
        source.browser = Mock()
        source.browser.articles.side_effect = ApiError('-2041', True, kind='weread_ticket')
        source.browser.reload_worker.return_value = None
        self.session.request.return_value = {'reviewId': BOOK + '_official',
                                             'title': '封面标题'}
        items = source.articles(BOOK, 10)
        self.assertEqual((items[0]['title'], items[0]['read_count'], items[0]['like_count']),
                         ('官方最新文章', 3210, 98))
        self.assertIn('官方 Skill', items[0]['collection_warning'])
        self.assertEqual(self.db.rows('SELECT review_id FROM source_refs')[0]['review_id'],
                         BOOK + '_official')
        skill.review.assert_called_once_with(BOOK + '_official')
        source.close()

    def test_expired_history_list_without_skill_key_has_actionable_message(self):
        source = WeReadSource('{"version":2,"cookies":[]}', self.db, self.cancel,
                              self.session, profile_dir=Path(self.temp.name) / 'profile')
        source.browser = Mock()
        source.browser.shelf.return_value = [{'bookId': BOOK, 'title': '测试号'}]
        source.browser.cookie_state.return_value = '{"version":2,"cookies":[]}'
        source.browser.articles.side_effect = ApiError('-2041', True, kind='weread_ticket')
        with self.assertRaisesRegex(ApiError, '采集服务配置.*极致了 Key'):
            source.articles(BOOK, 10)
        source.close()

    def test_2041_is_reported_as_server_refusal_not_login_expiry(self):
        # Verified 2026-10-23: the official web reader got the same -2041 right
        # after a fresh QR login, and rescanning in the app did not clear it.
        with self.assertRaises(ApiError) as raised:
            WeReadSession.check({'errCode': -2041})
        message = str(raised.exception)
        self.assertEqual(raised.exception.kind, 'weread_ticket')
        self.assertIn('登录本身仍有效', message)
        self.assertIn('稍后再采集', message)
        self.assertNotIn('请在公众号管理中使用 Edge 重新扫码登录', message)
        self.assertNotIn('登录设备', message)

    def test_unknown_user_credential_requires_edge_relogin(self):
        with self.assertRaises(ApiError) as raised:
            WeReadSession.check({'errCode': -2010, 'errMsg': '用户不存在'})
        self.assertTrue(raised.exception.fatal)
        self.assertEqual(raised.exception.kind, 'weread_auth')
        self.assertIn('Edge 重新扫码登录', str(raised.exception))

    def test_no_fabricated_publication_date(self):
        self.session.request.return_value = {'reviews': [group('a', None)]}
        with self.assertRaisesRegex(ApiError, '发布时间'):
            self.source.articles(BOOK, 1)

    def test_sync_updates_existing_without_reenabling(self):
        account = self.db.add_account('测试号', 'old-everyinfra-id')
        self.db.execute('UPDATE accounts SET enabled=0 WHERE id=?', (account,))
        sync_shelf(self.db, self.session)
        sync_shelf(self.db, self.session)
        self.assertEqual(len(self.db.accounts()), 1)
        self.assertEqual(self.db.accounts()[0]['identifier'], BOOK)
        self.assertEqual(self.db.accounts()[0]['enabled'], 0)

    def test_unfollowed_account_no_list_request(self):
        self.session.shelf.return_value = []
        with self.assertRaisesRegex(ApiError, '书架'):
            self.source.articles(BOOK, 1)
        self.session.request.assert_not_called()

    def test_browser_source_refreshes_credentials_for_shelf_articles_and_content(self):
        saver = Mock()
        source = WeReadSource('{"version":2,"cookies":[]}', self.db, self.cancel,
                              self.session, saver, Path(self.temp.name) / 'profile')
        source.browser = Mock()
        source.browser.cookie_state.return_value = '{"version":2,"cookies":[]}'
        source.browser.shelf.return_value = [{'bookId': BOOK, 'title': '测试号'}]
        source.browser.articles.return_value = [{'reviews': [group('browser-token')]}]
        source.browser.content.return_value = (
            "<div id='js_content'><p>" + '浏览器刷新凭证后读取正文。' * 20 + '</p></div>')
        articles = source.articles(BOOK, 1)
        body = source.content(articles[0]['url'])
        self.assertIn('浏览器刷新凭证', body)
        source.browser.shelf.assert_called_once()
        source.browser.articles.assert_called_once_with(BOOK, 1)
        source.browser.content.assert_called_once_with(BOOK + '_browser-token')
        self.session.shelf.assert_not_called()
        self.session.request.assert_not_called()
        self.assertGreaterEqual(saver.call_count, 3)

    def test_each_browser_source_uses_and_cleans_an_independent_profile(self):
        root = Path(self.temp.name) / 'weread-edge-profile'
        first = WeReadSource('{"version":2,"cookies":[]}', self.db, self.cancel,
                             self.session, profile_dir=root)
        second = WeReadSource('{"version":2,"cookies":[]}', self.db, self.cancel,
                              self.session, profile_dir=root)
        first_dir = first.browser_profile_dir
        second_dir = second.browser_profile_dir
        self.assertNotEqual(first_dir, second_dir)
        self.assertTrue(first_dir.is_dir())
        self.assertTrue(second_dir.is_dir())
        first.browser = Mock()
        second.browser = Mock()
        first.close()
        second.close()
        self.assertFalse(first_dir.exists())
        self.assertFalse(second_dir.exists())

    def test_verification_page_not_saved_as_body(self):
        self.db.execute('INSERT INTO source_refs VALUES(?,?)', ('https://mp.weixin.qq.com/s/a', BOOK + '_a'))
        self.session.request.return_value = '<html>环境异常，请验证</html>'
        with self.assertRaisesRegex(ApiError, '均未返回正文'):
            self.source.content('https://mp.weixin.qq.com/s/a')

    def test_browser_verification_page_falls_back_to_public_article(self):
        url = 'https://mp.weixin.qq.com/s/direct-fallback'
        self.db.save_article({'source_key': 'direct-fallback', 'account_name': '测试号',
            'title': '可直接读取文章', 'url': url, 'published_at': '2026-10-12T00:00:00+00:00'})
        self.db.execute('INSERT INTO source_refs VALUES(?,?)', (url, BOOK + '_direct'))
        source = WeReadSource('{"version":2,"cookies":[]}', self.db, self.cancel,
                              self.session, profile_dir=Path(self.temp.name) / 'profile')
        source.browser = Mock()
        source.browser.content.return_value = '<html><title>验证</title></html>'
        source.browser.public_content.return_value = (
            "<div id='js_content'><p>" + '公众号原文降级读取成功。' * 20 + '</p></div>')
        source.browser.cookie_state.return_value = '{"version":2,"cookies":[]}'
        self.assertIn('公众号原文降级读取成功', source.content(url))
        source.browser.public_content.assert_called_once_with(url)
        source.close()

    def test_expired_content_ticket_falls_back_directly_to_public_article(self):
        url = 'https://mp.weixin.qq.com/s/ticket-fallback'
        self.db.execute('INSERT INTO source_refs VALUES(?,?)', (url, BOOK + '_direct'))
        source = WeReadSource('{"version":2,"cookies":[]}', self.db, self.cancel,
                              self.session, profile_dir=Path(self.temp.name) / 'profile')
        source.browser = Mock()
        source.browser.content.side_effect = ApiError('-2041', True, kind='weread_ticket')
        source.browser.public_content.return_value = (
            "<div id='js_content'><p>" + '直接读取公众号原文成功。' * 20 + '</p></div>')
        source.browser.cookie_state.return_value = '{"version":2,"cookies":[]}'
        self.assertIn('直接读取公众号原文成功', source.content(url))
        self.assertEqual(source.browser.content.call_count, 2)
        source.browser.public_content.assert_called_once_with(url)
        source.close()

    def test_browser_and_invalid_public_link_report_precise_reason(self):
        url = 'https://mp.weixin.qq.com/s/invalid-direct'
        self.db.execute('INSERT INTO source_refs VALUES(?,?)', (url, BOOK + '_invalid'))
        source = WeReadSource('{"version":2,"cookies":[]}', self.db, self.cancel,
                              self.session, profile_dir=Path(self.temp.name) / 'profile')
        source.browser = Mock()
        source.browser.content.return_value = '<html><title>验证</title></html>'
        source.browser.public_content.return_value = '<html><body>参数错误</body></html>'
        source.browser.cookie_state.return_value = '{"version":2,"cookies":[]}'
        with self.assertRaisesRegex(ApiError, '原文链接同时提示“参数错误”'):
            source.content(url)
        source.close()

    def test_content_saves_trusted_original_images_locally(self):
        url = 'https://mp.weixin.qq.com/s/image-article'
        article_id = self.db.save_article({'source_key': 'image-key', 'account_name': '测试号',
            'title': '有图文章', 'url': url, 'published_at': '2026-10-12T00:00:00+00:00'})
        self.db.execute('INSERT INTO source_refs VALUES(?,?)', (url, BOOK + '_image'))
        self.session.request.return_value = ("<div id='js_content'>"
            "<img data-src='//mmbiz.qpic.cn/mmbiz_png/example/640' alt='数据图'>"
            "<img src='https://evil.example/tracker.png'>"
            "<p>" + '有效正文内容。' * 30 + "</p></div>")
        self.source._fetch_image = Mock(return_value=(b'local-image', '.png', 640, 480))
        self.source.content(url)
        images = self.db.article_images(article_id)
        self.assertEqual(len(images), 1)
        self.assertEqual((images[0]['alt_text'], images[0]['width'], images[0]['height']),
                         ('数据图', 640, 480))
        self.assertEqual((self.db.path.parent / images[0]['local_path']).read_bytes(), b'local-image')
        self.source._fetch_image.assert_called_once()
        self.source._fetch_image = Mock(side_effect=ValueError('offline'))
        self.source.content(url)
        preserved = self.db.article_images(article_id)
        self.assertEqual(len(preserved), 1)
        self.assertEqual((self.db.path.parent / preserved[0]['local_path']).read_bytes(), b'local-image')

    def test_short_image_article_is_returned_for_vision_processing(self):
        url = 'https://mp.weixin.qq.com/s/short-image'
        self.db.save_article({'source_key': 'short-image-key', 'account_name': '测试号',
            'title': '图片型文章', 'url': url, 'published_at': '2026-10-12T00:00:00+00:00'})
        self.db.execute('INSERT INTO source_refs VALUES(?,?)', (url, BOOK + '_short'))
        self.session.request.return_value = ("<div id='js_content'><p>短文</p>"
            "<img data-src='https://mmbiz.qpic.cn/example/640'></div>")
        self.source._fetch_image = Mock(return_value=(b'image', '.jpg', 640, 480))
        self.assertEqual(self.source.content(url), '短文')

    def test_cancel_prevents_requests(self):
        self.cancel.is_set.return_value = True
        with self.assertRaises(Cancelled):
            self.source.request('/web/mp/articles')
        self.session.request.assert_not_called()

    def test_qr_result_creates_required_web_cookies(self):
        session = WeReadSession()
        session.request = Mock(return_value={'succeed': True, 'webLoginVid': '123',
            'accessToken': 'access', 'refreshToken': 'refresh token'})
        self.assertEqual(session.poll('uid'), 'confirmed')
        cookies = {c.name: c.value for c in session.cookies}
        self.assertEqual(cookies['wr_vid'], '123')
        self.assertEqual(cookies['wr_skey'], 'access')
        self.assertEqual(cookies['wr_rt'], 'refresh%20token')

    def test_renewal_saves_mp_verification_headers(self):
        session = WeReadSession()
        session.request = Mock(return_value=({'succ': 1}, {'x-wr-ticket': 'ticket', 'x-wrpa-0': 'wrpa'}))
        session.renew()
        restored = WeReadSession(session.encrypted_state_payload())
        self.assertEqual((restored.ticket, restored.wrpa), ('ticket', 'wrpa'))

    def test_browser_state_preserves_mp_verification_headers(self):
        state = browser_cookie_state([
            {'name': 'wr_skey', 'value': 'saved', 'domain': '.weread.qq.com',
             'path': '/', 'secure': True, 'expires': None}], 'ticket', 'wrpa')
        restored = json.loads(state)
        self.assertEqual((restored['ticket'], restored['wrpa']), ('ticket', 'wrpa'))

    def test_browser_detects_missing_official_skill_key_without_returning_it(self):
        browser = WeReadBrowser(Path(self.temp.name) / 'profile', cancel=threading.Event())
        context = Mock()
        context.cookies.return_value = [
            {'name': 'wr_vid', 'value': 'vid'}, {'name': 'wr_skey', 'value': 'skey'}]
        browser.context = context
        page = Mock()
        page.evaluate.return_value = {'status': 200, 'enabled': False}
        self.assertFalse(browser.skill_enabled(page))
        payload = page.evaluate.call_args.args[1]
        self.assertEqual((payload['vid'], payload['skey']), ('vid', 'skey'))

    def test_browser_2041_remains_ticket_error_for_source_fallback(self):
        cancel = Mock(is_set=Mock(return_value=False), wait=Mock(return_value=False))
        browser = WeReadBrowser(Path(self.temp.name) / 'profile', cancel=cancel)
        context, page = Mock(), Mock()
        context.new_page.return_value = page
        browser.open = Mock(return_value=context)
        handlers = {}
        page.on.side_effect = lambda name, callback: handlers.__setitem__(name, callback)
        request = Mock(url=f'https://weread.qq.com/web/mp/articles?bookId={BOOK}&offset=0')
        request.headers = {'x-wrpa-0': 'present'}
        response = Mock(url=request.url, status=200)
        response.json.return_value = {'errCode': -2041}
        page.goto.side_effect = lambda *args, **kwargs: (
            handlers['request'](request), handlers['response'](response))
        with self.assertRaises(ApiError) as caught:
            browser.articles(BOOK, 1)
        self.assertEqual(caught.exception.kind, 'weread_ticket')
        page.close.assert_called_once()

    def test_browser_ignores_transient_2041_when_a_successful_retry_arrives(self):
        cancel = Mock(is_set=Mock(return_value=False), wait=Mock(return_value=False))
        browser = WeReadBrowser(Path(self.temp.name) / 'profile', cancel=cancel)
        context, page = Mock(), Mock()
        context.new_page.return_value = page
        browser.open = Mock(return_value=context)
        handlers = {}
        page.on.side_effect = lambda name, callback: handlers.__setitem__(name, callback)
        request = Mock(url=f'https://weread.qq.com/web/mp/articles?bookId={BOOK}&offset=0')
        request.headers = {'x-wr-ticket': 'fresh', 'x-wrpa-0': 'present'}
        failed = Mock(url=request.url, status=200)
        failed.json.return_value = {'errCode': -2041}
        recovered = Mock(url=request.url, status=200)
        recovered.json.return_value = {'reviews': [group('recovered')]}
        page.goto.side_effect = lambda *args, **kwargs: (
            handlers['request'](request), handlers['response'](failed),
            handlers['response'](recovered))
        pages = browser.articles(BOOK, 1)
        self.assertEqual(pages, [{'reviews': [group('recovered')]}])
        self.assertEqual((browser.ticket, browser.wrpa), ('fresh', 'present'))
        page.close.assert_called_once()

    def test_browser_pages_history_list_by_offset_until_limit_or_exhausted(self):
        cancel = Mock(is_set=Mock(return_value=False), wait=Mock(return_value=False))
        browser = WeReadBrowser(Path(self.temp.name) / 'profile', cancel=cancel)
        context, page = Mock(), Mock()
        context.new_page.return_value = page
        browser.open = Mock(return_value=context)
        handlers = {}
        page.on.side_effect = lambda name, callback: handlers.__setitem__(name, callback)
        first = Mock(url=f'https://weread.qq.com/web/mp/articles?bookId={BOOK}&offset=0', status=200)
        first.json.return_value = {'reviews': [group('a'), group('b')]}
        page.goto.side_effect = lambda *args, **kwargs: handlers['response'](first)
        later = {2: {'reviews': [group('c'), group('d')]}, 4: {'reviews': []}}
        page.evaluate.side_effect = lambda script, args: later[args['offset']]
        pages = browser.articles(BOOK, 10)
        self.assertEqual([len(p['reviews']) for p in pages], [2, 2, 0])
        self.assertEqual([c.args[1]['offset'] for c in page.evaluate.call_args_list], [2, 4])
        page.evaluate.reset_mock()
        self.assertEqual(len(browser.articles(BOOK, 3)), 2)
        self.assertEqual(page.evaluate.call_count, 1)
        page.evaluate.side_effect = lambda script, args: {'errCode': -2041}
        self.assertEqual(len(browser.articles(BOOK, 10)), 1)

    def test_browser_offset_pages_feed_history_list_coverage(self):
        source = WeReadSource('[]', Mock(), session=Mock())
        source.books = {BOOK}
        source.browser = Mock()
        source._save_browser_state = Mock()
        source.browser.articles.return_value = [
            {'reviews': [group('a'), group('b')]}, {'reviews': [group('c')]}, {'reviews': []}]
        items = source.articles(BOOK, 10)
        self.assertEqual(len(items), 3)
        self.assertEqual(source.last_coverage, {'mode': 'history_list', 'end': 'exhausted'})

    def test_browser_stops_paging_once_time_window_is_covered(self):
        cancel = Mock(is_set=Mock(return_value=False), wait=Mock(return_value=False))
        browser = WeReadBrowser(Path(self.temp.name) / 'profile', cancel=cancel)
        context, page = Mock(), Mock()
        context.new_page.return_value = page
        browser.open = Mock(return_value=context)
        handlers = {}
        page.on.side_effect = lambda name, callback: handlers.__setitem__(name, callback)
        first = Mock(url=f'https://weread.qq.com/web/mp/articles?bookId={BOOK}&offset=0', status=200)
        first.json.return_value = {'reviews': [dict(group('a'), createTime=2000),
                                               dict(group('b'), createTime=1500)]}
        page.goto.side_effect = lambda *args, **kwargs: handlers['response'](first)
        page.evaluate.return_value = {'reviews': [dict(group('c'), createTime=900)]}
        self.assertEqual(len(browser.articles(BOOK, 50, 1800)), 1)
        page.evaluate.assert_not_called()
        self.assertEqual(len(browser.articles(BOOK, 50, 1000)), 2)
        self.assertEqual(page.evaluate.call_count, 1)

    def test_list_refusal_is_not_retried_and_skips_later_accounts(self):
        from datetime import datetime, timezone
        other = 'MP_WXS_456'
        source = WeReadSource('[]', Mock(), session=Mock(),
                              skill=Mock(review=Mock(return_value={
                                  'mpInfo': {'title': '最新', 'time': 1791534053}})))
        source.books = {BOOK, other}
        source.browser = Mock()
        source._save_browser_state = Mock()
        source.request = Mock(side_effect=lambda path, params: {'reviewId': params['bookId'] + '_tok'})
        source.browser.articles.side_effect = ApiError('-2041', True, kind='weread_ticket')
        source.list_since = datetime.fromtimestamp(1791000000, timezone.utc)
        self.assertEqual(source.articles(BOOK, 10)[0]['metric_source'], 'weread_skill')
        source.browser.articles.assert_called_once_with(BOOK, 10, 1791000000.0)
        source.browser.reload_worker.assert_not_called()
        self.assertEqual(source.articles(other, 10)[0]['metric_source'], 'weread_skill')
        self.assertEqual(source.browser.articles.call_count, 1)
        self.assertEqual(source.last_coverage['mode'], 'latest_only')

    def test_provider_lists_by_biz_and_ranks_only_window_articles(self):
        from datetime import datetime, timezone
        account = 'MP_WXS_3533509125'
        biz = 'MzUzMzUwOTEyNQ=='
        def row(mid, ts, owner=biz):
            return {'url': f'http://mp.weixin.qq.com/s?__biz={owner}&mid={mid}&idx=1&sn=x{mid}',
                    'title': 't' + str(mid), 'posted_at': ts}
        provider = Mock()
        provider.articles.return_value = [row(1, 1791000300), row(2, 1791000200),
                                          row(3, 1790000000), row(4, 1791000250, 'Other==')]
        def metrics(url):
            if 'mid=2' in url:
                raise ApiError('HTTP 503', kind='network')
            return {'read_count': 1706, 'like_count': 10}
        provider.metrics.side_effect = metrics
        source = WeReadSource('[]', Mock(), session=Mock(), provider=provider)
        source.books = {account}
        source.book_titles = {account: '思博产业研究'}
        source.list_since = datetime.fromtimestamp(1791000000, timezone.utc)
        items = source.articles(account, 20)
        provider.articles.assert_called_once_with('思博产业研究', 20)
        self.assertEqual([i['title'] for i in items], ['t1', 't2', 't3'])
        self.assertEqual([i['read_count'] for i in items], [1706, None, None])
        self.assertEqual(provider.metrics.call_count, 2)
        self.assertIn('不按 0 阅读处理', items[0]['collection_warning'])
        self.assertEqual(items[0]['metric_source'], 'everyinfra_metrics')
        self.assertEqual(source.last_coverage,
                         {'mode': 'history_list', 'end': 'window', 'provider': 'everyinfra'})
        source.list_since = datetime.fromtimestamp(1789000000, timezone.utc)
        source.articles(account, 20)
        self.assertEqual(source.last_coverage['end'], 'provider_page')

    def test_dajiala_list_carries_reads_and_pages_until_window_start(self):
        from datetime import datetime, timezone
        account, biz = 'MP_WXS_3533509125', 'MzUzMzUwOTEyNQ=='
        def post(mid, ts, read):
            return {'url': f'http://mp.weixin.qq.com/s?__biz={biz}&mid={mid}&idx=1&sn=s{mid}&chksm=c',
                    'title': 't' + str(mid), 'post_time': ts, 'read': read, 'zan': 1}
        primary, backup = Mock(), Mock()
        pages = {'': {'data': [post(1, 1791000300, 1761), post(2, 1791000200, 0)], 'offset': 'p2', 'is_end': 0},
                 'p2': {'data': [post(3, 1790000000, 9)], 'offset': 'p3', 'is_end': 0}}
        primary.history.side_effect = lambda name, offset: pages[offset]
        source = WeReadSource('[]', Mock(), session=Mock(), provider=backup, primary=primary)
        source.books, source.book_titles = {account}, {account: '思博产业研究'}
        source.list_since = datetime.fromtimestamp(1791000000, timezone.utc)
        items = source.articles(account, 20)
        self.assertEqual([c.args for c in primary.history.call_args_list],
                         [('思博产业研究', ''), ('思博产业研究', 'p2')])
        self.assertEqual([(i['title'], i['read_count']) for i in items],
                         [('t1', 1761), ('t2', 0), ('t3', 9)])
        self.assertEqual(items[0]['metric_source'], 'dajiala_list')
        self.assertEqual(source.last_coverage['provider'], 'dajiala')
        self.assertEqual(source.last_coverage['end'], 'window')
        backup.articles.assert_not_called()
        backup.metrics.assert_not_called()

    def test_dajiala_digit_string_fields_are_numbers_and_stop_paging(self):
        from datetime import datetime, timezone
        account, biz = 'MP_WXS_3931691072', 'MzkzMTY5MTA3Mg=='
        primary = Mock()
        primary.history.return_value = {'data': [
            {'url': f'https://mp.weixin.qq.com/s?__biz={biz}&mid=1&idx=1&sn=a', 'title': 'x',
             'post_time': '1791906180', 'read': '1235', 'zan': '9'}], 'offset': 'next', 'is_end': 0}
        source = WeReadSource('[]', Mock(), session=Mock(), primary=primary)
        source.books, source.book_titles = {account}, {account: '示例公众号E'}
        source.list_since = datetime.fromtimestamp(1792000000, timezone.utc)
        items = source.articles(account, 24)
        self.assertEqual((items[0]['read_count'], items[0]['like_count']), (1235, 9))
        self.assertEqual(primary.history.call_count, 1)
        self.assertEqual(source.last_coverage['end'], 'window')

    def test_dajiala_failure_falls_back_to_everyinfra_with_warning(self):
        account, biz = 'MP_WXS_3533509125', 'MzUzMzUwOTEyNQ=='
        primary, backup = Mock(), Mock()
        primary.history.side_effect = ApiError('极致了返回错误码 -1：限流', kind='dajiala')
        backup.articles.return_value = [{'url': f'https://mp.weixin.qq.com/s?__biz={biz}&mid=1&idx=1&sn=a',
                                         'title': 'x', 'posted_at': 1791000300}]
        backup.metrics.return_value = {'read_count': 5}
        source = WeReadSource('[]', Mock(), session=Mock(), provider=backup, primary=primary)
        source.books, source.book_titles = {account}, {account: '思博产业研究'}
        items = source.articles(account, 5)
        self.assertEqual(items[0]['read_count'], 5)
        self.assertIn('已改用备用数据服务', items[0]['collection_warning'])
        self.assertEqual(source.last_coverage['provider'], 'everyinfra')
        backup.articles.side_effect = ApiError('HTTP 503：服务暂不可用')
        with self.assertRaisesRegex(ApiError, '极致了.*HTTP 503'):
            source.articles(account, 5)

    def test_provider_capacity_refusal_is_not_repeated_for_later_accounts(self):
        provider = Mock()
        provider.articles.side_effect = ApiError('HTTP 503：服务暂不可用，请稍后重试')
        source = WeReadSource('[]', Mock(), session=Mock(), provider=provider)
        source.books = {'MP_WXS_1', 'MP_WXS_2'}
        source.book_titles = {'MP_WXS_1': 'a', 'MP_WXS_2': 'b'}
        with self.assertRaises(ApiError):
            source.articles('MP_WXS_1', 5)
        with self.assertRaisesRegex(ApiError, '容量不足，未再请求'):
            source.articles('MP_WXS_2', 5)
        self.assertEqual(provider.articles.call_count, 1)

    def test_provider_rejects_list_of_another_account(self):
        provider = Mock()
        provider.articles.return_value = [{'url': 'https://mp.weixin.qq.com/s?__biz=Other==&mid=1&idx=1&sn=a',
                                           'title': 'x', 'posted_at': 1791000300}]
        source = WeReadSource('[]', Mock(), session=Mock(), provider=provider)
        source.books = {BOOK}
        source.book_titles = {BOOK: '同名号'}
        with self.assertRaisesRegex(ApiError, '不属于该公众号'):
            source.articles(BOOK, 5)
        provider.metrics.assert_not_called()

    def test_provider_article_body_is_read_from_public_original(self):
        db = Database(Path(self.temp.name) / 'p.sqlite')
        source = WeReadSource('[]', db, session=Mock(), provider=Mock())
        source.browser = Mock()
        source._save_browser_state = Mock()
        body = '<div id="js_content">' + '正文' * 80 + '</div>'
        original = 'https://mp.weixin.qq.com/s?__biz=MA==&mid=1&idx=1&sn=a&chksm=c1'
        provider = source.provider
        provider.articles.return_value = [{'url': original.replace('https', 'http'),
                                           'title': 't', 'posted_at': 1791000300}]
        provider.metrics.return_value = {'read_count': 1}
        source.books, source.book_titles = {'MP_WXS_0'}, {'MP_WXS_0': 'n'}
        url = source.articles('MP_WXS_0', 5)[0]['url']
        source._public_page = Mock(return_value=body)
        self.assertEqual(source.content(url), '正文' * 80)
        source._public_page.assert_called_once_with(original)
        source.browser.content.assert_not_called()
        source.browser.public_content.assert_not_called()
        # A verification page from the plain request falls back to Edge.
        source._public_page.return_value = '<html>环境异常</html>'
        source.browser.public_content.return_value = body
        self.assertEqual(source.content(url), '正文' * 80)
        source.browser.public_content.assert_called_once_with(original)

    def test_old_cookie_only_login_state_still_loads(self):
        old = '[{"name":"wr_skey","value":"x","domain":".weread.qq.com","path":"/","secure":true,"expires":null}]'
        session = WeReadSession(old)
        self.assertEqual({c.name: c.value for c in session.cookies}['wr_skey'], 'x')

    def test_public_account_reader_id_encoding(self):
        value = encoded_id('MP_WXS_3286016687')
        self.assertGreaterEqual(len(value), 20)
        self.assertEqual(value, encoded_id('MP_WXS_3286016687'))
        self.assertNotIn('MP_WXS_', value)

    @patch('wewriter.weread.WeReadBrowser')
    def test_browser_login_uses_disposable_profile(self, browser_type):
        profile = Path(self.temp.name) / 'weread-edge-profile'
        profile.mkdir()
        (profile / 'stale-lock').write_text('old', encoding='utf-8')
        browser = browser_type.return_value
        page = Mock()
        page.url = 'https://weread.qq.com/'
        context = Mock()
        context.pages = [page]
        context.cookies.return_value = [
            {'name': 'wr_vid', 'value': '1'}, {'name': 'wr_skey', 'value': 'key'}]
        page.evaluate.return_value = {'books': [{'bookId': BOOK, 'title': '测试号'}]}
        browser.open.return_value = context
        browser.cookie_state.return_value = 'saved-state'
        state, count = browser_login(profile)
        used_profile = Path(browser_type.call_args.args[0])
        self.assertEqual((state, count), ('saved-state', 1))
        self.assertNotEqual(used_profile, profile)
        self.assertFalse(used_profile.exists())
        browser.close.assert_called_once()
        page.bring_to_front.assert_called_once()

    def test_visible_edge_starts_directly_on_weread(self):
        state = ('{"version":2,"cookies":[{"name":"wr_skey","value":"saved",'
                 '"domain":".weread.qq.com","path":"/","secure":true,"expires":null}]}')
        browser = WeReadBrowser(Path(self.temp.name) / 'profile', headless=False, state=state)
        edge = Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
        with patch.object(browser, 'edge_path', return_value=edge), \
             patch('wewriter.weread.subprocess.Popen') as popen, \
             patch.object(Path, 'exists', return_value=True), \
             patch.object(Path, 'unlink'), \
             patch.object(Path, 'read_text', return_value='12345\n'):
            process = popen.return_value
            process.poll.return_value = None
            with patch('playwright.sync_api.sync_playwright') as sync:
                chromium = sync.return_value.start.return_value.chromium
                context = Mock()
                context.cookies.return_value = []
                chromium.connect_over_cdp.return_value.contexts = [context]
                browser.open()
        flags = popen.call_args.args[0]
        self.assertIn('--new-window', flags)
        self.assertIn('https://weread.qq.com/', flags)
        browser.context.add_cookies.assert_called_once()

    def test_edge_startup_timeout_retries_with_isolated_profile(self):
        cancel = Mock()
        cancel.wait.return_value = False
        browser = WeReadBrowser(Path(self.temp.name) / 'profile', cancel=cancel)
        edge = Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
        first_process, second_process = Mock(), Mock()
        first_process.poll.return_value = None
        second_process.poll.return_value = None
        # First attempt: stale-port check, all 300 waits and final check are false.
        # Second attempt: stale-port check is false, first wait succeeds.
        port_states = [False] * 303 + [True, True]
        with patch.object(browser, 'edge_path', return_value=edge), \
             patch('wewriter.weread.subprocess.Popen',
                   side_effect=[first_process, second_process]) as popen, \
             patch.object(Path, 'exists', side_effect=port_states), \
             patch.object(Path, 'read_text', return_value='12345\n'):
            with patch('playwright.sync_api.sync_playwright') as sync:
                context = Mock()
                context.cookies.return_value = []
                sync.return_value.start.return_value.chromium.connect_over_cdp.return_value.contexts = [context]
                self.assertIs(browser.open(), context)
        self.assertEqual(popen.call_count, 2)
        self.assertIn('retry-1', ' '.join(popen.call_args_list[1].args[0]))
        first_process.terminate.assert_called_once()


if __name__ == '__main__':
    unittest.main()
