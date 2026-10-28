"""Offline browser checks for the five-page shell and workbench bridge controls."""
import unittest

from wewriter.webapp import WEB_DIR


BRIDGE = """
window.bridgeCalls = [];
const job = {
  id: 101, running: false, step: 4, waiting: 2, fix: 0, processing: 0,
  passed: 0, target: 2, range: '近 3 天', elapsed: 142,
  chip: 'wait', chip_text: '待你审核', message: '机器审查已完成',
  drafts: [
    {id: 1, title: '演示稿件 1：让信息更容易读懂', state: '待审核', hint: '1 处引用待人工核实'},
    {id: 2, title: '演示稿件 2：把复杂流程讲清楚', state: '待审核', hint: '二审已完成'}
  ]
};
window.demoJob = job;
window.pywebview = {api: new Proxy({}, {get: (_, name) => async (...args) => {
  window.bridgeCalls.push([name, ...args]);
  if (name === 'meta') return {ok: true, version: '0.10.4', demo: true, theme: 'light'};
  if (name === 'wb_state') return {ok: true, accounts: 3, available: 4, busy: false, queue: 0,
    restored: false, mode: 'today', hours: 24, count: 2, job};
  if (name === 'wb_history') return {ok: true, total: 1, per: 5, rows: [
    {id: 101, when: '10-26 19:33', chip: 'wait', status: '待你审核', made: 2, target: 2, ok: 0}
  ]};
  if (name === 'wb_detail') return {ok: true, title: '任务 · 10-26 19:33', sub: '待你审核',
    alert: '', has_funnel: false, rewrite: false, running: false, retryable: false};
  return {ok: true, queued: false, count: 0, rows: [], total: 0, accounts: []};
}})};
"""


class UiShellBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
            cls.pw = sync_playwright().start()
        except Exception as exc:  # pragma: no cover - environment dependent
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
            raise unittest.SkipTest('没有可用的浏览器')

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def page_for(self, name, width=1440, height=700):
        page = self.browser.new_page(viewport={'width': width, 'height': height})
        page.add_init_script(BRIDGE)
        page.goto((WEB_DIR / f'{name}.html').resolve().as_uri())
        page.locator('#theme [data-theme]').first.wait_for()
        return page

    def test_five_pages_share_sidebar_and_two_theme_choices(self):
        for name in ('workbench', 'accounts', 'articles', 'drafts', 'settings'):
            with self.subTest(page=name):
                page = self.page_for(name)
                try:
                    self.assertEqual(page.locator('.rail .nav button').count(), 5)
                    self.assertEqual(page.locator('#theme [data-theme]').count(), 2)
                    self.assertEqual(page.locator('.brand-mark').inner_text(), 'W')
                    self.assertEqual(page.locator('.rail .nav [aria-current="page"]').get_attribute('data-page'), name)
                    page.locator('#theme [data-theme="dark"]').click()
                    self.assertEqual(page.locator('html').get_attribute('data-theme'), 'dark')
                    self.assertEqual(page.locator('#theme [data-theme="dark"]').get_attribute('aria-checked'), 'true')
                finally:
                    page.close()

    def test_workbench_shows_real_state_and_keeps_existing_actions(self):
        page = self.page_for('workbench')
        try:
            self.assertIn('2 篇待你审核', page.locator('#hero-status').inner_text())
            self.assertEqual(page.locator('#cat-stack').get_attribute('data-phase'), 'awaiting')
            self.assertTrue(page.locator('.wb-time-card').is_visible())
            self.assertRegex(page.locator('#clock').inner_text(), r'^\d{2}:\d{2}$')
            self.assertEqual(page.locator('#drafts > li').count(), 2)
            self.assertIn('已生成 2/2', self._open_history(page))
            page.locator('#detail').click()
            self.assertTrue(page.locator('#drawer').is_visible())
            self.assertIn('wb_detail', [row[0] for row in page.evaluate('window.bridgeCalls')])
            page.locator('.dw-tab').click()
            page.locator('#collect-open').click()
            self.assertTrue(page.locator('#new-dialog').is_visible())
            self.assertEqual(page.locator('#range button:visible').count(), 3)
            self.assertEqual(page.locator('#count button:visible').count(), 0)
            page.locator('#range [data-v="72"]').click()
            self.assertEqual(page.locator('#range [aria-pressed="true"]').get_attribute('data-v'), '72')
            page.locator('#start').click()
            self.assertFalse(page.locator('#new-dialog').is_visible())
            self.assertIn(['wb_collect', '72'], page.evaluate('window.bridgeCalls'))
            page.locator('#write-open').click()
            self.assertEqual(page.locator('#range button:visible').count(), 0)
            self.assertEqual(page.locator('#count button:visible').count(), 5)
            page.locator('#count [data-v="3"]').click()
            self.assertEqual(page.locator('#count [aria-pressed="true"]').get_attribute('data-v'), '3')
            page.locator('#start').click()
            self.assertIn(['wb_write', 3], page.evaluate('window.bridgeCalls'))
            page.evaluate("window.demoJob.chip = 'fail'; window.demoJob.chip_text = '采集失败'; window.demoJob.running = false; window.demoJob.waiting = 0; refresh()")
            self.assertEqual(page.locator('#cat-stack').get_attribute('data-phase'), 'failed')
            self.assertTrue(page.locator('[data-cat="failed"]').is_visible())
        finally:
            page.close()

    @staticmethod
    def _open_history(page):
        page.locator('#histtoggle').click()
        return page.locator('#histbody').inner_text()

    def test_workbench_sidebar_stays_visible_and_narrow_layout_has_no_overflow(self):
        wide = self.page_for('workbench', height=520)
        try:
            wide.evaluate('window.scrollTo(0, 600)')
            self.assertEqual(round(wide.locator('.rail').bounding_box()['y']), 0)
        finally:
            wide.close()
        for width in (800, 600, 390):
            with self.subTest(width=width):
                narrow = self.page_for('workbench', width=width, height=750)
                try:
                    self.assertLessEqual(narrow.evaluate('document.documentElement.scrollWidth'), width)
                    self.assertTrue(narrow.locator('#collect-open').is_visible())
                    self.assertTrue(narrow.locator('#write-open').is_visible())
                    self.assertTrue(narrow.locator('#drafts').is_visible())
                    copy = narrow.locator('.wb-hero-copy').bounding_box()
                    bubble = narrow.locator('.wb-cat-bubble').bounding_box()
                    self.assertLess(copy['y'] + copy['height'], bubble['y'])
                finally:
                    narrow.close()


if __name__ == '__main__':
    unittest.main()
