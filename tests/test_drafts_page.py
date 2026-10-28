"""补跑二审 on the real drafts page in a browser: immediate feedback, and no silent failure (offline).

The page talks to the real Api through a stand-in for the pywebview bridge; models are fakes.
Skipped when no Playwright browser (Edge or Chromium) can be started on this machine."""
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import test_core
import test_rerun_review
from test_rerun_review import Model
from wewriter.webapp import WEB_DIR, Api, Backend

BRIDGE = b"""<script>
window.pywebview = { api: new Proxy({}, { get: (_, name) => async (...args) => {
  const r = await fetch('/api/' + name, { method: 'POST', body: JSON.stringify(args) });
  return r.json(); } }) };
setTimeout(() => window.dispatchEvent(new Event('pywebviewready')), 0);
</script>"""


def serve(api, delays):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = WEB_DIR / self.path.lstrip('/').split('?')[0]
            if not path.is_file():
                self.send_error(404)
                return
            body = path.read_bytes()
            if path.suffix == '.html':
                body = body.replace(b'<script src="common.js">', BRIDGE + b'<script src="common.js">')
            self.send_response(200)
            self.send_header('Content-Type', {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
                                              '.css': 'text/css; charset=utf-8', '.png': 'image/png',
                                              '.svg': 'image/svg+xml'}.get(path.suffix, 'application/octet-stream'))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            name = self.path.rsplit('/', 1)[1]
            args = json.loads(self.rfile.read(int(self.headers['Content-Length'])) or b'[]')
            time.sleep(delays.get(name, 0))
            body = json.dumps(getattr(api, name)(*args), ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class DraftsPageBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
            cls.pw = sync_playwright().start()
        except Exception as exc:          # pragma: no cover - environment dependent
            raise unittest.SkipTest(f'Playwright 不可用：{exc}')
        cls.browser = None
        for options in ({'channel': 'msedge'}, {}):
            try:
                cls.browser = cls.pw.chromium.launch(headless=True, **options)
                break
            except Exception:
                continue
        if cls.browser is None:
            cls.pw.stop()
            raise unittest.SkipTest('没有可用的浏览器（Edge 或 Playwright Chromium）')

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        test_rerun_review.RerunReviewTest.setUp(self)
        self.backend = Backend(self.f.db, test_core.FakeVault(), Path(self.f.temp.name))
        self.backend.engine = self.engine
        self.delays = {}
        self.server = serve(Api(self.backend), self.delays)
        self.page = self.browser.new_page()

    def tearDown(self):
        self.page.close()
        self.server.shutdown()
        self.server.server_close()
        Model.review_mode = 'fail'
        test_rerun_review.RerunReviewTest.tearDown(self)

    def open_draft(self):
        self.page.goto(f'http://127.0.0.1:{self.server.server_port}/drafts.html')
        self.page.click('[data-open="%d"]' % self.draft['id'])
        self.page.wait_for_selector('#rerun')

    def test_click_gives_feedback_at_once_and_then_the_result(self):
        Model.review_mode = 'block'
        Model.started, Model.release = threading.Event(), threading.Event()
        self.delays['dr_rereview'] = 0.8                  # a slow bridge must not look like nothing happened
        self.open_draft()
        self.page.click('#rerun')
        self.assertEqual(self.page.text_content('#rerun'), '正在启动…')
        self.page.wait_for_function("document.getElementById('rerun').textContent === '补跑中…'")
        self.assertIn('正在补跑 AI 二审', self.page.text_content('.reviewline'))
        Model.release.set()
        self.page.wait_for_selector('#review-note', timeout=15000)
        self.assertIn('二审已完成，已保存为第 2 版', self.page.text_content('#review-note'))

    def test_a_refusal_stays_visible(self):
        self.open_draft()
        self.backend.busy = True
        self.backend.engine.cancel.set()
        try:
            self.page.click('#rerun')
            self.page.wait_for_selector('#review-note')
            self.assertIn('上一个任务正在停止', self.page.text_content('#review-note'))
            self.assertEqual(self.page.text_content('#rerun'), '补跑二审')
            self.assertFalse(self.page.is_disabled('#rerun'))
        finally:
            self.backend.busy = False
            self.backend.engine.cancel.clear()

    def test_a_failed_review_is_reported_on_the_page(self):
        self.open_draft()
        self.page.click('#rerun')
        self.page.wait_for_selector('#review-note', timeout=15000)
        self.assertIn('补跑二审未完成，稿件未改动', self.page.text_content('#review-note'))


if __name__ == '__main__':
    unittest.main()
