"""Browser -> real backend -> isolated SQLite; this test never contacts a model."""
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from playwright.sync_api import sync_playwright
from wewriter.db import Database
from wewriter.webapp import Api, Backend, WEB_DIR
from wewriter.wiki import preferences_for


class GraphBrowserTest(unittest.TestCase):
    def test_create_review_edit_forget_through_desktop_pages(self):
        with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
            db=Database(Path(tmp)/'test.sqlite')
            job=db.job('ui',{})
            did=db.save_draft(job['id'],'虚构示例','初版先讲背景。',[], '')
            api=Api(Backend(db,SimpleNamespace(has=lambda _:False),tmp))
            try:
                browser=pw.chromium.launch(channel='msedge',headless=True)
            except Exception:
                self.skipTest('没有可用的 Edge')
            page=browser.new_page()
            page.set_default_timeout(6000)
            errors=[]
            page.on('pageerror',lambda exc:errors.append(str(exc)))
            page.expose_function('bridge',lambda name,args:getattr(api,name)(*args))
            page.add_init_script("window.pywebview={api:new Proxy({}, {get:(_,n)=>(...a)=>window.bridge(n,a)})}")
            page.goto((WEB_DIR/'wiki.html').as_uri())
            page.locator('#sources-panel summary').click()
            page.locator('#topic').fill('设备更新')
            page.locator('#origin').fill('甲地指南')
            page.locator('#body').fill('本次设备更新适用于甲地试点企业。')
            page.locator('#add').click()
            page.get_by_role('button',name='甲地指南',exact=True).click()
            page.locator('#point').fill('甲地试点')
            page.locator('#quote').fill('适用于甲地试点企业')
            page.locator('#store').click()
            page.get_by_role('button',name='甲地试点',exact=True).wait_for()
            self.assertEqual(page.locator('#graph path').count(),2)
            page.locator('#point').fill('仅限甲地试点')
            page.locator('#store').click()
            page.get_by_role('button',name='仅限甲地试点',exact=True).wait_for()
            page.locator('#remove').click()
            page.locator('.cbox [data-v="1"]').click()
            page.get_by_role('button',name='仅限甲地试点',exact=True).wait_for(state='detached')
            self.assertEqual(len(db.rows('select * from wiki_entries')),1)
            page.evaluate("id=>sessionStorage.setItem('openDraft',id)",str(did))
            page.locator('[data-page="drafts"]').click()
            page.locator('#edit').click()
            page.locator('#e-body').fill('开头先说明政策适用于谁。')
            page.locator('#teach').click()
            page.locator('#teach-rule').fill('开头先写适用对象。')
            page.locator('#teach-scope').fill('设备更新')
            self.assertFalse(preferences_for(db,'设备更新',[]))
            page.locator('#confirm-rule').click()
            page.locator('#confirm-rule').wait_for(state='detached')
            self.assertEqual(preferences_for(db,'设备更新',[]),['开头先写适用对象。'])
            page.locator('[data-page="wiki"]').click()
            page.locator('#graph [data-key="root"]').wait_for()
            page.locator('#habits').click()
            page.get_by_role('button',name='开头先写适用对象。').click()
            page.locator('#remove').click()
            page.locator('.cbox [data-v="1"]').click()
            page.get_by_role('button',name='开头先写适用对象。').wait_for(state='detached')
            self.assertFalse(preferences_for(db,'设备更新',[]))
            self.assertFalse(errors,errors)
            browser.close()
