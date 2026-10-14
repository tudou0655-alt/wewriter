"""Direct WeRead collection; credentials stay in the local encrypted vault."""
from io import BytesIO
import http.cookiejar
import html
import hashlib
import json
import os
from pathlib import Path
import socket
import re
import shutil
import subprocess
import threading
import tempfile
import time
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from PIL import Image, UnidentifiedImageError
import urllib.error
import urllib.parse
import urllib.request
from .api import ApiError, Cancelled, NoRedirect, wechat_url, parse_date
from .logs import logger

BASE = "https://weread.qq.com"
SKILL_GATEWAY = "https://i.weread.qq.com/api/agent/gateway"
SKILL_VERSION = "1.0.5"
IMAGE_LIMIT = 20
IMAGE_BYTES_LIMIT = 8 * 1024 * 1024


def kill_process_tree(pid):
    """Best-effort removal of an Edge launcher and every child it spawned."""
    if os.name != 'nt' or not isinstance(pid, int) or pid <= 0:
        return
    try:
        subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except Exception:
        pass


def remove_profile_dir(path, attempts=10, delay=0.5):
    """Delete a disposable Edge profile, waiting briefly for file locks to clear."""
    path = Path(path)
    for attempt in range(attempts):
        if not path.exists():
            return True
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return True
        if attempt + 1 < attempts:
            time.sleep(delay)
    return not path.exists()


def cleanup_stale_profiles(profile_root, older_than=3600):
    """Remove disposable profiles left behind by crashes or older versions.

    Only call this while no collection task is running (e.g. at app start; the
    single-instance lock guarantees no other WeWriter process owns them).
    """
    profile_root = Path(profile_root)
    parent = profile_root.parent
    if not parent.is_dir():
        return 0
    removed = 0
    now = time.time()
    for item in parent.glob(profile_root.name + '-*'):
        try:
            if not item.is_dir() or now - item.stat().st_mtime < older_than:
                continue
        except OSError:
            continue
        if remove_profile_dir(item, attempts=2, delay=0.2):
            removed += 1
    return removed


def article_image_candidates(node):
    """Return unique, trusted image URLs from a WeChat article body."""
    results, seen = [], set()
    for image in node.select('img'):
        raw = next((image.get(name) for name in
                    ('data-src', 'data-original', 'data-backsrc', 'src')
                    if image.get(name)), '')
        value = html.unescape(str(raw or '')).strip()
        if value.startswith('//'):
            value = 'https:' + value
        elif value.startswith('/'):
            value = BASE + value
        elif value.startswith('http://'):
            value = 'https://' + value[7:]
        parts = urlsplit(value)
        host = (parts.hostname or '').lower()
        trusted = (host.endswith('.qpic.cn') or host.endswith('.qlogo.cn')
                   or host == 'res.weread.qq.com')
        if parts.scheme != 'https' or not trusted or value in seen:
            continue
        seen.add(value)
        caption = str(image.get('alt') or image.get('data-caption') or '').strip()
        if not caption:
            figure = image.find_parent('figure')
            label = figure.select_one('figcaption') if figure else None
            caption = label.get_text(' ', strip=True) if label else ''
        results.append({'url': value, 'alt_text': caption[:300]})
        if len(results) >= IMAGE_LIMIT:
            break
    return results


def encoded_id(value):
    """Match the public WeRead web reader route encoding."""
    text = str(value)
    digest = hashlib.md5(text.encode('utf-8')).hexdigest()
    if text.isdigit():
        chunks = [format(int(text[index:index + 9]), 'x') for index in range(0, len(text), 9)]
        type_flag = '3'
    else:
        chunks = [''.join(format(value, 'x') for value in text.encode('utf-8'))]
        type_flag = '4'
    result = digest[:3] + type_flag + '2' + digest[-2:]
    result += 'g'.join(format(len(chunk), '02x') + chunk for chunk in chunks)
    if len(result) < 20:
        result += digest[:20 - len(result)]
    return result + hashlib.md5(result.encode('utf-8')).hexdigest()[:3]


def browser_cookie_state(cookies, ticket='', wrpa=''):
    rows = []
    for cookie in cookies:
        if str(cookie.get('domain', '')).lstrip('.') != 'weread.qq.com':
            continue
        expires = cookie.get('expires')
        rows.append({'name': cookie['name'], 'value': cookie['value'], 'domain': cookie['domain'],
                     'path': cookie.get('path') or '/', 'secure': bool(cookie.get('secure', True)),
                     'expires': int(expires) if isinstance(expires, (int, float)) and expires > 0 else None})
    return json.dumps({'version': 2, 'cookies': rows,
                       'ticket': str(ticket or ''), 'wrpa': str(wrpa or '')}, ensure_ascii=False)


class WeReadBrowser:
    """Use WeRead's own browser JavaScript for the per-request WPA validation."""
    def __init__(self, profile_dir, cancel=None, headless=True, state=None):
        self.profile_dir = Path(profile_dir)
        self.cancel = cancel or threading.Event()
        self.headless = headless
        self.state = state
        self.playwright = self.browser = self.context = self.process = None
        self.worker = None
        self.ticket = ''
        self.wrpa = ''
        if state:
            try:
                saved = json.loads(state)
                if isinstance(saved, dict):
                    self.ticket = str(saved.get('ticket') or '')
                    self.wrpa = str(saved.get('wrpa') or '')
            except (TypeError, ValueError):
                # The normal state loader reports malformed credentials later;
                # browser startup should stay focused on creating the session.
                pass

    @staticmethod
    def edge_path():
        candidates = [
            Path(os.environ.get('PROGRAMFILES(X86)', '')) / 'Microsoft/Edge/Application/msedge.exe',
            Path(os.environ.get('PROGRAMFILES', '')) / 'Microsoft/Edge/Application/msedge.exe',
            Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft/Edge/Application/msedge.exe',
        ]
        return next((path for path in candidates if path.is_file()), None)

    def open(self):
        if self.context:
            return self.context
        edge = self.edge_path()
        if not edge:
            raise ApiError('未检测到 Microsoft Edge，请安装或修复 Edge 后重试')
        last_error = None
        # Slower customer PCs can need more than ten seconds to create the CDP
        # endpoint, especially immediately after an application update. Try a
        # second isolated profile before failing the article task.
        for attempt in range(2):
            try:
                from playwright.sync_api import sync_playwright
                runtime_profile = (self.profile_dir if attempt == 0 else
                                   self.profile_dir / ('retry-' + str(attempt)))
                runtime_profile.mkdir(parents=True, exist_ok=True)
                port_file = runtime_profile / 'DevToolsActivePort'
                if port_file.exists():
                    port_file.unlink()
                flags = [str(edge), '--remote-debugging-port=0', '--no-first-run',
                         '--no-default-browser-check', '--disable-background-mode',
                         '--user-data-dir=' + str(runtime_profile)]
                if self.headless:
                    flags.append('--headless=new')
                else:
                    # Give Edge the destination at process startup.  Navigating only
                    # after CDP attaches can leave Edge's MSN/new-tab page in front on
                    # some installations, which makes the login button look broken.
                    flags.extend(['--start-maximized', '--new-window', BASE + '/'])
                self.process = subprocess.Popen(
                    flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                # Do not fail as soon as the launcher process exits: Edge can hand
                # startup to a child process which creates this file shortly after.
                for _ in range(300):
                    if port_file.exists():
                        break
                    if self.cancel.wait(0.1):
                        raise Cancelled('任务已停止')
                if not port_file.exists():
                    code = self.process.poll()
                    suffix = '' if code is None else f'（退出码 {code}）'
                    raise RuntimeError('Edge 在 30 秒内未完成启动' + suffix)
                port = port_file.read_text(encoding='utf-8').splitlines()[0].strip()
                self.playwright = sync_playwright().start()
                self.browser = self.playwright.chromium.connect_over_cdp(
                    'http://127.0.0.1:' + port, timeout=15000)
                self.context = self.browser.contexts[0]
                existing = self.context.cookies([BASE])
                existing_names = {row.get('name') for row in existing if row.get('value')}
                if self.state and not {'wr_vid', 'wr_skey'} <= existing_names:
                    saved = json.loads(self.state)
                    rows = saved if isinstance(saved, list) else saved.get('cookies', [])
                    cookies = []
                    for row in rows:
                        if str(row.get('domain', '')).lstrip('.') != 'weread.qq.com':
                            continue
                        cookie = {'name': row['name'], 'value': row['value'],
                                  'domain': row['domain'], 'path': row.get('path') or '/',
                                  'secure': bool(row.get('secure', True))}
                        if isinstance(row.get('expires'), (int, float)) and row['expires'] > 0:
                            cookie['expires'] = int(row['expires'])
                        cookies.append(cookie)
                    if cookies:
                        self.context.add_cookies(cookies)
                return self.context
            except Cancelled:
                self.close()
                raise
            except Exception as exc:
                last_error = exc
                self.close()
                if attempt == 0:
                    time.sleep(0.5)
        detail = str(last_error).strip() or type(last_error).__name__
        raise ApiError(
            'Microsoft Edge 采集窗口连续两次启动失败：' + detail +
            '。请关闭所有 Edge 窗口后重试；若仍失败，请检查安全软件是否拦截 Edge') from last_error

    def close(self):
        # Playwright's Browser.close() only *disconnects* a connect_over_cdp
        # session; Edge itself keeps running and keeps the profile locked.
        # Ask Edge to exit through CDP first, then make sure the whole process
        # tree is gone so the disposable profile directory can be removed.
        if self.browser:
            try:
                self.browser.new_browser_cdp_session().send('Browser.close')
            except Exception:
                pass
            try:
                self.browser.close()
            except Exception:
                pass
        if self.playwright:
            try:
                self.playwright.stop()
            except Exception:
                pass
        process = self.process
        if process is not None:
            try:
                process.wait(timeout=5)
            except Exception:
                pass
            if process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass
            kill_process_tree(getattr(process, 'pid', None))
        self.context = self.browser = self.playwright = self.process = None
        self.worker = None

    def worker_page(self):
        """One long-lived WeRead tab per session for same-origin fetch() calls.

        Loading the WeRead homepage for every article was slow and made
        verification pages more likely; the page is reloaded only when it was
        closed, crashed or navigated away."""
        context = self.open()
        page = self.worker
        try:
            usable = page is not None and not page.is_closed() and str(page.url).startswith(BASE)
        except Exception:
            usable = False
        if not usable:
            page = context.new_page()
            page.goto(BASE + '/', wait_until='domcontentloaded', timeout=60000)
            self.worker = page
        return page

    def reload_worker(self):
        """Reload WeRead so its own JavaScript issues fresh request credentials."""
        page = self.worker_page()
        page.goto(BASE + '/', wait_until='domcontentloaded', timeout=60000)

    def cookie_state(self):
        return browser_cookie_state(self.open().cookies([BASE]), self.ticket, self.wrpa)

    def skill_enabled(self, page=None):
        """Return whether the signed-in account has created its official Skill key.

        The value is never returned to Python or logged; this is only a capability
        check used to turn an otherwise opaque -2041 into a useful recovery step.
        """
        page = page or self.worker_page()
        cookies = {item.get('name'): item.get('value')
                   for item in self.open().cookies([BASE]) if item.get('value')}
        result = page.evaluate("""async ({vid, skey}) => {
            const response = await fetch('/api/skills/apikeyGet?only_show=1', {
                credentials: 'include',
                headers: {'Accept': 'application/json, text/plain, */*',
                          'X-Vid': vid || '', 'X-Skey': skey || ''}
            });
            let data = {};
            try { data = await response.json(); } catch (_) {}
            return {status: response.status, enabled: Boolean(data.apikey)};
        }""", {'vid': cookies.get('wr_vid', ''), 'skey': cookies.get('wr_skey', '')})
        return bool(isinstance(result, dict) and result.get('status') == 200
                    and result.get('enabled'))

    def shelf(self):
        result = self.worker_page().evaluate("""async () => {
            const response = await fetch('/web/shelf/sync?userVid=&synckey=0&lectureSynckey=0',
                {credentials: 'include'});
            return await response.json();
        }""")
        WeReadSession.check(result)
        books = result.get('books')
        if not isinstance(books, list):
            raise ApiError('Edge 登录验证未通过：书架接口没有返回 books 字段')
        return [book for book in books if isinstance(book, dict)
                and str(book.get('bookId', '')).startswith('MP_WXS_')]

    def content(self, review_id):
        result = self.worker_page().evaluate("""async (reviewId) => {
            const response = await fetch('/web/mp/content?' + new URLSearchParams({reviewId}),
                {credentials: 'include'});
            return {status: response.status, body: await response.text()};
        }""", review_id)
        if not isinstance(result, dict) or result.get('status') != 200:
            # Usually expired per-request credentials; _browser_call reloads
            # WeRead once before treating it as a login failure.
            raise ApiError('Edge 读取公众号正文失败，请重新扫码登录', True, kind='weread_ticket')
        return str(result.get('body') or '')

    def public_content(self, url):
        """Open a trusted public WeChat URL when WeRead returns a verification page."""
        url = wechat_url(url)
        context = self.open()
        page = context.new_page()
        try:
            page.goto(url, wait_until='domcontentloaded', timeout=60000)
            if self.cancel.wait(1):
                raise Cancelled('任务已停止')
            return page.content()
        finally:
            page.close()

    def articles(self, book_id, limit):
        context = self.open()
        page = context.new_page()
        results = []
        captured = {'ticket': self.ticket, 'wrpa': self.wrpa}

        def request_sent(request):
            if '/web/mp/articles' not in request.url or 'bookId=' + book_id not in request.url:
                return
            try:
                headers = {str(key).lower(): value for key, value in request.headers.items()}
            except Exception:
                return
            if headers.get('x-wr-ticket'):
                captured['ticket'] = headers['x-wr-ticket']
            if headers.get('x-wrpa-0'):
                captured['wrpa'] = headers['x-wrpa-0']

        def response_received(response):
            if '/web/mp/articles' not in response.url or 'bookId=' + book_id not in response.url:
                return
            try:
                data = response.json()
            except Exception:
                return
            if isinstance(data, dict):
                results.append(data)

        page.on('request', request_sent)
        page.on('response', response_received)
        try:
            page.goto(BASE + '/web/mp/reader/' + encoded_id(book_id),
                      wait_until='domcontentloaded', timeout=60000)
            unchanged = 0
            previous = 0
            for _ in range(12):
                if self.cancel.wait(1.5):
                    raise Cancelled('任务已停止')
                count = sum(len(x.get('reviews', [])) for x in results)
                if count >= limit:
                    break
                unchanged = unchanged + 1 if count == previous else 0
                previous = count
                if unchanged >= 3 and results:
                    break
                page.mouse.wheel(0, 5000)
            self.ticket, self.wrpa = captured['ticket'], captured['wrpa']
            successful = [item for item in results
                          if isinstance(item.get('reviews'), list)
                          and str(item.get('errCode', item.get('errcode', 0)) or 0) == '0']
            if successful:
                return successful
            if not results:
                raise ApiError('Edge 未收到公众号文章列表；请重新登录微信读书',
                               True, kind='weread_auth')
            WeReadSession.check(results[-1])
            raise ApiError('微信读书未返回公众号文章列表；请重新扫码登录',
                           True, kind='weread_auth')
        finally:
            page.close()


def browser_login(profile_dir, cancel=None):
    """Interactive first-party login; return encrypted-vault-ready cookie state."""
    profile_root = Path(profile_dir)
    profile_root.parent.mkdir(parents=True, exist_ok=True)
    # A terminated or crashed Edge process can keep its profile locked. Each
    # login therefore gets an isolated disposable profile instead of reusing
    # the previous directory.
    if profile_root.exists():
        remove_profile_dir(profile_root, attempts=3)
    login_dir = Path(tempfile.mkdtemp(prefix=profile_root.name + '-', dir=profile_root.parent))
    browser = WeReadBrowser(login_dir, cancel, headless=False)
    context = browser.open()
    try:
        initial_pages = list(context.pages)
        page = next((item for item in initial_pages
                     if str(getattr(item, 'url', '')).startswith(BASE)), None)
        if page is None:
            page = context.new_page()
        page.goto(BASE + '/', wait_until='domcontentloaded', timeout=60000)
        page.bring_to_front()
        for other in initial_pages:
            if other is not page:
                try:
                    other.close()
                except Exception:
                    pass
        if not str(page.url).startswith(BASE):
            raise ApiError('Edge 已打开，但未能进入微信读书，请关闭窗口后重试',
                           True, kind='weread_auth')
        deadline = time.monotonic() + 300
        validation_failures = 0
        while time.monotonic() < deadline:
            if cancel and cancel.wait(1):
                raise Cancelled('已取消登录')
            cookies = context.cookies([BASE])
            names = {c.get('name') for c in cookies if c.get('value')}
            if {'wr_vid', 'wr_skey'} <= names:
                try:
                    shelf = page.evaluate("""async () => {
                        const response = await fetch('/web/shelf/sync?userVid=&synckey=0&lectureSynckey=0',
                            {credentials: 'include'});
                        return await response.json();
                    }""")
                    WeReadSession.check(shelf)
                    books = shelf.get('books')
                    if not isinstance(books, list):
                        raise ApiError('Edge 登录后未读取到微信读书书架')
                except ApiError as exc:
                    validation_failures += 1
                    if validation_failures >= 3:
                        raise ApiError(
                            '扫码后微信读书仍未识别当前用户。请确认使用的是手机微信读书当前登录的同一微信；'
                            '若仍失败，请先在手机微信读书退出并重新登录，再回软件扫码',
                            True, kind='weread_auth') from exc
                    time.sleep(1)
                    continue
                mp = [book for book in books if str(book.get('bookId', '')).startswith('MP_WXS_')]
                return browser.cookie_state(), len(mp)
        raise ApiError('登录等待超过 5 分钟，请重新点击 Edge 登录', True, kind='weread_auth')
    finally:
        browser.close()
        remove_profile_dir(login_dir)


class WeReadSession:
    def __init__(self, state=None):
        self.cookies = http.cookiejar.CookieJar()
        self.ticket = ''
        self.wrpa = ''
        self.opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPCookieProcessor(self.cookies))
        if state:
            saved = json.loads(state)
            # v1 stored only a cookie list. v2 also keeps the browser verification
            # headers now required by the public-account article-list endpoint.
            if isinstance(saved, list):
                cookie_rows = saved
            elif isinstance(saved, dict):
                cookie_rows = saved.get('cookies', [])
                self.ticket = str(saved.get('ticket') or '')
                self.wrpa = str(saved.get('wrpa') or '')
            else:
                raise ValueError('微信读书登录状态格式无效，请重新扫码登录')
            for c in cookie_rows:
                if c["domain"].lstrip(".") != "weread.qq.com":
                    continue
                self.cookies.set_cookie(http.cookiejar.Cookie(0, c["name"], c["value"], None, False,
                    c["domain"], True, c["domain"].startswith("."), c["path"], True,
                    c["secure"], c["expires"], False, None, None, {}, False))

    def request(self, path, params=None, timeout=25, json_result=True, payload=None, return_headers=False):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("Invalid WeRead path")
        url = BASE + path
        if params is not None:
            url += "?" + urllib.parse.urlencode(params)
        headers = {
            "Accept": "application/json, text/plain, */*", "Referer": BASE + "/",
            "Origin": BASE, "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
        }
        if path.startswith('/web/mp/'):
            if self.ticket:
                headers['x-wr-ticket'] = self.ticket
            if self.wrpa:
                headers['x-wrpa-0'] = self.wrpa
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            headers['Content-Type'] = 'application/json;charset=UTF-8'
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with self.opener.open(req, timeout=timeout) as response:
                body = response.read(4 * 1024 * 1024 + 1)
                response_headers = response.headers
                if len(body) > 4 * 1024 * 1024:
                    raise ApiError("微信读书响应过大")
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            raise ApiError(f"微信读书返回 HTTP {code}；暂停采集，未自动重试", code in (401, 403, 429, 499)) from None
        except (TimeoutError, socket.timeout):
            raise ApiError("微信读书请求超时，请稍后重试") from None
        except urllib.error.URLError:
            raise ApiError("无法连接微信读书，请检查本机网络") from None
        if not json_result:
            result = body.decode("utf-8", errors="replace")
            return (result, response_headers) if return_headers else result
        try:
            result = json.loads(body)
        except ValueError:
            raise ApiError("微信读书返回非 JSON 内容，需检查网页是否要求验证") from None
        if not isinstance(result, dict):
            raise ApiError("微信读书响应格式不匹配")
        return (result, response_headers) if return_headers else result

    @staticmethod
    def check(result):
        code = result.get("errCode", result.get("errcode", 0))
        if str(code or 0) != "0":
            messages = {
                '-2010': '当前登录凭据未识别到微信读书用户；请在公众号管理中使用 Edge 重新扫码登录',
                '-2012': '登录会话已过期或冲突，请在公众号管理中重新扫码登录',
                '-2041': '公众号文章接口临时凭据已失效；请重启软件后重试，仍失败时请在公众号管理中使用 Edge 重新扫码登录',
            }
            kind = ('weread_ticket' if str(code) == '-2041' else
                    'weread_auth' if str(code) in messages else 'weread')
            raise ApiError(f"微信读书业务码 {code}：{messages.get(str(code), '访问受限或接口暂不可用，已停止采集')}",
                           True, kind=kind)
        return result

    def _set_login_cookie(self, name, value):
        if not value:
            return
        for cookie in list(self.cookies):
            if cookie.name == name and cookie.domain.lstrip('.') == 'weread.qq.com':
                self.cookies.clear(cookie.domain, cookie.path, cookie.name)
        self.cookies.set_cookie(http.cookiejar.Cookie(0, name, str(value), None, False,
            '.weread.qq.com', True, True, '/', True, True, None, False, None, None, {}, False))

    def login_uid(self):
        result = self.check(self.request("/api/auth/getLoginUid"))
        uid = result.get("uid") or result.get("data", {}).get("uid")
        if not isinstance(uid, str) or not uid:
            raise ApiError("微信读书没有返回登录二维码标识")
        return uid

    def poll(self, uid):
        result = self.request("/api/auth/getLoginInfo", {"uid": uid, "otp": ""}, timeout=25)
        inner = result.get("data") if isinstance(result.get("data"), dict) else {}
        if result.get("succeed") is True or inner.get("succeed") is True:
            login = {**result, **inner}
            self._set_login_cookie('wr_vid', login.get('webLoginVid'))
            self._set_login_cookie('wr_skey', login.get('accessToken'))
            self._set_login_cookie('wr_ql', '0')
            refresh = login.get('refreshToken')
            if refresh:
                self._set_login_cookie('wr_rt', urllib.parse.quote(str(refresh), safe=''))
            if not any(c.name == 'wr_skey' and c.value for c in self.cookies):
                raise ApiError('扫码已确认，但微信读书未返回完整登录凭据，请重新扫码', True, kind='weread_auth')
            return "confirmed"
        code = result.get("logicCode") or inner.get("logicCode")
        if code == "NEED_OTP":
            raise ApiError("微信读书要求额外验证，请使用官方网页完成登录；本验证工具不自动处理验证码")
        return "waiting"

    def renew(self):
        result, headers = self.request('/web/login/renewal', payload={
            'rq': '%2Fweb%2Fbook%2Fread', 'ql': False}, return_headers=True)
        if not isinstance(result, dict) or result.get('succ') not in (1, True):
            self.check(result if isinstance(result, dict) else {})
            raise ApiError('微信读书登录续期失败，请重新扫码登录', True, kind='weread_auth')
        ticket = headers.get('x-wr-ticket')
        wrpa = headers.get('x-wrpa-0')
        if ticket:
            self.ticket = ticket
        if wrpa:
            self.wrpa = wrpa
        return result

    def shelf(self):
        self.renew()
        result = self.check(self.request("/web/shelf/sync", {"userVid": "", "synckey": 0, "lectureSynckey": 0}))
        if not isinstance(result.get("books"), list):
            raise ApiError("登录验证未通过：书架接口没有返回 books 字段")
        return [book for book in result["books"] if isinstance(book, dict) and str(book.get("bookId", "")).startswith("MP_WXS_")]

    def encrypted_state_payload(self):
        return json.dumps({'version': 2, 'cookies': [
            {"name": c.name, "value": c.value, "domain": c.domain,
             "path": c.path, "secure": c.secure, "expires": c.expires}
            for c in self.cookies if c.domain.lstrip(".") == "weread.qq.com"],
            'ticket': self.ticket, 'wrpa': self.wrpa}, ensure_ascii=False)


class WeReadSkill:
    """Small adapter for the official WeRead Skill gateway.

    The official gateway provides stable authentication and current metadata for
    one public-account article.  It does not currently expose the historical MP
    article list, so WeReadSource uses it as an explicit fallback for -2041.
    """
    def __init__(self, key, cancel=None):
        self.key = str(key or '').strip()
        self.cancel = cancel or threading.Event()

    def request(self, api_name, **params):
        if api_name not in ('/shelf/sync', '/review/single'):
            raise ValueError('不支持的微信读书 Skill 接口')
        if self.cancel.is_set():
            raise Cancelled('任务已停止')
        payload = {'api_name': api_name, 'skill_version': SKILL_VERSION, **params}
        request = urllib.request.Request(SKILL_GATEWAY,
            data=json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8'),
            headers={'Authorization': 'Bearer ' + self.key,
                     'Content-Type': 'application/json;charset=UTF-8',
                     'Accept': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read(4 * 1024 * 1024 + 1)
                if len(body) > 4 * 1024 * 1024:
                    raise ApiError('微信读书 Skill 响应过大')
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            messages = {401: 'API Key 无效', 403: '当前 Key 没有接口权限',
                        429: '请求过于频繁，请稍后重试'}
            raise ApiError(f"微信读书 Skill HTTP {code}：{messages.get(code, '接口请求失败')}",
                           code in (401, 403), kind='weread_skill') from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
            raise ApiError('无法连接微信读书 Skill，请检查本机网络', kind='network') from None
        try:
            result = json.loads(body)
        except (ValueError, UnicodeError):
            raise ApiError('微信读书 Skill 返回格式无效', kind='weread_skill') from None
        if not isinstance(result, dict):
            raise ApiError('微信读书 Skill 响应格式不匹配', kind='weread_skill')
        code = result.get('errcode', result.get('errCode', 0))
        if str(code or 0) != '0':
            raise ApiError(f'微信读书 Skill 业务码 {code}：接口暂不可用',
                           str(code) in ('-2010', '-2012'), kind='weread_skill')
        return result

    def shelf(self):
        result = self.request('/shelf/sync')
        books = result.get('books')
        if not isinstance(books, list):
            raise ApiError('微信读书 Skill 未返回书架数据', kind='weread_skill')
        return [book for book in books if isinstance(book, dict)
                and str(book.get('bookId', '')).startswith('MP_WXS_')]

    def review(self, review_id):
        result = self.request('/review/single', reviewId=review_id,
                              commentsCount=0, likesCount=0)
        review = result.get('review')
        if not isinstance(review, dict):
            raise ApiError('微信读书 Skill 未返回文章详情', kind='weread_skill')
        return review


class WeReadSource:
    def __init__(self, state, db, cancel=None, session=None, state_saver=None, profile_dir=None,
                 skill_key=None, skill=None):
        if not state and session is None:
            raise ApiError("尚未登录微信读书，请在公众号管理中扫码登录", True)
        self.session = session or WeReadSession(state)
        self.db = db
        self.cancel = cancel or threading.Event()
        self.started = False
        self.books = None
        self.state_saver = state_saver
        self.skill = skill or (WeReadSkill(skill_key, self.cancel) if skill_key else None)
        self.browser_profile_dir = None
        self.browser = None
        if profile_dir:
            # A parent collection task can remain open while its child writing
            # tasks read article bodies. Edge locks its user-data directory, so
            # every source must own a disposable directory. Refreshed cookies
            # are persisted through state_saver instead of through the profile.
            profile_root = Path(profile_dir)
            profile_root.parent.mkdir(parents=True, exist_ok=True)
            self.browser_profile_dir = Path(tempfile.mkdtemp(
                prefix=profile_root.name + '-runtime-', dir=profile_root.parent))
            self.browser = WeReadBrowser(self.browser_profile_dir, self.cancel, state=state)

    def _save_browser_state(self):
        if not self.browser:
            return
        state = self.browser.cookie_state()
        self.browser.state = state
        self.session = WeReadSession(state)
        if self.state_saver:
            self.state_saver(state)

    def _browser_call(self, action, *args):
        """Run one browser action and rebuild a broken Edge session once."""
        last_error = None
        for attempt in range(2):
            try:
                return getattr(self.browser, action)(*args)
            except Cancelled:
                raise
            except ApiError as exc:
                # -2041 / non-200 body: the page's temporary request
                # credentials expired. Reload WeRead once and retry; a second
                # failure is a real login problem and is raised as such.
                if exc.kind != 'weread_ticket' or attempt:
                    raise
                logger.info("微信读书临时凭据失效，重新加载页面后重试：%s", action)
                try:
                    self.browser.reload_worker()
                except Exception:
                    self.browser.close()
                continue
            except Exception as exc:
                last_error = exc
                self.browser.close()
                if attempt == 0:
                    continue
        detail = str(last_error).strip() or type(last_error).__name__
        labels = {'shelf': '同步书架', 'articles': '读取文章列表',
                  'content': '读取正文', 'public_content': '读取公众号原文'}
        raise ApiError(f"Edge {labels.get(action, '采集')}会话连续两次中断：{detail}") from last_error

    def shelf(self):
        if self.skill:
            return self.skill.shelf()
        if self.browser:
            books = self._browser_call('shelf')
            self._save_browser_state()
            return books
        books = self.session.shelf()
        if self.state_saver:
            self.state_saver(self.session.encrypted_state_payload())
        return books

    def check_cancel(self):
        if self.cancel.is_set():
            raise Cancelled("任务已停止")

    def request(self, *args, **kwargs):
        self.check_cancel()
        if self.started and self.cancel.wait(2):
            raise Cancelled("任务已停止")
        self.started = True
        result = self.session.request(*args, **kwargs)
        self.check_cancel()
        if not isinstance(result, dict):
            return result
        try:
            return self.session.check(result)
        except ApiError as exc:
            if exc.kind != 'weread_ticket':
                raise
            # Refresh x-wr-ticket / x-wrpa-0 once, then retry the same request.
            self.session.renew()
            if self.state_saver:
                self.state_saver(self.session.encrypted_state_payload())
            result = self.session.request(*args, **kwargs)
            self.check_cancel()
            return self.session.check(result) if isinstance(result, dict) else result

    def _skill_latest_article(self, account):
        if not self.skill:
            raise ApiError(
                '公众号历史列表凭据已失效，且尚未配置微信读书 Skill Key；'
                '请在手机微信读书“我 → 设置 → 微信读书 Skill”获取 Key，并在软件设置中保存',
                True, kind='weread_auth')
        cover = self.request('/api/mp/cover', {'bookId': account})
        review_id = str(cover.get('reviewId') or '')
        if not re.fullmatch(re.escape(account) + r'_[A-Za-z0-9_~\-]+', review_id):
            raise ApiError('微信读书没有返回该公众号的最新文章标识', kind='weread_skill')
        review = self.skill.review(review_id)
        info = review.get('mpInfo') if isinstance(review.get('mpInfo'), dict) else {}
        title = str(info.get('title') or cover.get('title') or '').strip()
        try:
            published = parse_date(info.get('time') or review.get('createTime')).isoformat()
        except (ValueError, TypeError, OverflowError, OSError):
            raise ApiError('微信读书 Skill 返回的最新文章缺少发布时间', kind='weread_skill') from None
        candidate_url = str(info.get('doc_url') or '').strip()
        if not candidate_url:
            token = review_id[len(account) + 1:].replace('~', '_')
            candidate_url = 'https://mp.weixin.qq.com/s/' + token
        try:
            url = wechat_url(candidate_url)
        except ValueError:
            raise ApiError('微信读书 Skill 返回的原文链接无效', kind='weread_skill') from None
        if not title:
            raise ApiError('微信读书 Skill 返回的最新文章缺少标题', kind='weread_skill')
        self.db.execute('INSERT OR REPLACE INTO source_refs(url,review_id) VALUES(?,?)',
                        (url, review_id))
        return {'url': url, 'title': title, 'posted_at': published,
                'read_count': info.get('readNum'), 'like_count': info.get('likeNum'),
                'collection_warning': '公众号历史列表临时不可用，已通过官方 Skill 获取该公众号最新文章'}

    def _legacy_articles(self, account, limit):
        if not re.fullmatch(r"MP_WXS_\d+", account):
            raise ApiError("请先在微信读书关注该公众号，再点击“同步书架公众号”更新标识")
        items, seen, offset, skipped = [], set(), 0, 0
        browser_pages = self._browser_call('articles', account, limit) if self.browser else None
        if self.browser:
            self._save_browser_state()
        while len(items) < limit:
            result = (browser_pages.pop(0) if browser_pages else
                      self.request('/web/mp/articles', {'bookId': account, 'offset': offset}))
            groups = result.get('reviews')
            if not isinstance(groups, list):
                raise ApiError("微信读书未返回文章列表；不会用旧封面文章代替")
            if not groups:
                break
            before = len(seen)
            for group in groups:
                subs = group.get('subReviews') if isinstance(group, dict) else None
                for sub in subs if isinstance(subs, list) else []:
                    # One malformed entry must not fail the whole account:
                    # skip it, and only fail if nothing usable was returned.
                    if not isinstance(sub, dict):
                        skipped += 1
                        continue
                    review = sub.get('review') if isinstance(sub.get('review'), dict) else {}
                    info = review.get('mpInfo') if isinstance(review.get('mpInfo'), dict) else {}
                    rid = str(review.get('reviewId') or sub.get('reviewId') or '')
                    if not re.fullmatch(re.escape(account) + r'_[A-Za-z0-9_~\-]+', rid):
                        skipped += 1
                        continue
                    if rid in seen:
                        continue
                    seen.add(rid)
                    token = rid[len(account) + 1:].replace('~', '_')
                    try:
                        url = wechat_url('https://mp.weixin.qq.com/s/' + token)
                        # Without a real publish time an article cannot be
                        # judged "new"; skip it rather than guess.
                        published = parse_date(info.get('time')).isoformat()
                    except (ValueError, TypeError, OverflowError, OSError):
                        skipped += 1
                        continue
                    self.db.execute('INSERT OR REPLACE INTO source_refs(url,review_id) VALUES(?,?)', (url, rid))
                    items.append({'url': url, 'title': info.get('title'), 'posted_at': published,
                                  'read_count': info.get('readNum'), 'like_count': info.get('likeNum')})
                    if len(items) >= limit:
                        break
                if len(items) >= limit:
                    break
            offset += len(groups)
            if len(seen) == before:
                break
            if browser_pages is not None and not browser_pages:
                break
        if skipped and not items:
            raise ApiError(f"微信读书返回的 {skipped} 条文章数据均缺少有效标识或发布时间，接口格式可能已变化")
        return items

    def articles(self, account, limit):
        if not re.fullmatch(r"MP_WXS_\d+", account):
            raise ApiError("请先在微信读书关注该公众号，再点击“同步书架公众号”更新标识")
        if self.books is None:
            self.books = {b['bookId'] for b in self.shelf()}
        if account not in self.books:
            raise ApiError("微信读书书架未找到该公众号，请用同一微信账号在手机加入书架后同步")
        try:
            return self._legacy_articles(account, limit)
        except ApiError as exc:
            if exc.kind != 'weread_ticket':
                raise
            logger.warning('公众号历史列表凭据失效，使用官方 Skill 获取最新文章：%s', account)
            return [self._skill_latest_article(account)]

    def close(self):
        if self.browser:
            self.browser.close()
        if self.browser_profile_dir:
            remove_profile_dir(self.browser_profile_dir)
            self.browser_profile_dir = None

    def content(self, url):
        url = wechat_url(url)
        refs = self.db.rows('SELECT review_id FROM source_refs WHERE url=?', (url,))
        if not refs:
            raise ApiError("这篇旧素材没有微信读书标识，请重新采集；不要重试旧 EveryInfra 任务")
        if self.browser:
            try:
                raw = self._browser_call('content', refs[0]['review_id'])
            except ApiError as exc:
                if exc.kind != 'weread_ticket':
                    raise
                logger.warning('正文接口凭据失效，直接读取公众号原文：%s', url)
                raw = self._browser_call('public_content', url)
        else:
            raw = self.request('/web/mp/content', {'reviewId': refs[0]['review_id']},
                               json_result=False)
        if self.browser:
            self._save_browser_state()
        soup = BeautifulSoup(raw, 'html.parser')
        node = soup.select_one('#js_content')
        if node is None and self.browser:
            # A per-article WeRead verification page does not necessarily mean
            # the public article is unavailable. Try its trusted original URL
            # once before moving to the next ranked candidate.
            direct = self._browser_call('public_content', url)
            direct_soup = BeautifulSoup(direct, 'html.parser')
            direct_node = direct_soup.select_one('#js_content')
            if direct_node is not None:
                soup, node = direct_soup, direct_node
            else:
                direct_text = direct_soup.get_text(' ', strip=True)
                if '参数错误' in direct_text:
                    raise ApiError('微信读书对该篇返回验证页，公众号原文链接同时提示“参数错误”，该素材当前不可用')
        if node is None:
            title = soup.title.get_text(' ', strip=True) if soup.title else ''
            if '验证' in title or 'verify' in raw.lower():
                raise ApiError("微信读书对该篇返回验证页，公众号原文也未取得正文，该素材当前不可用")
            raise ApiError("微信读书和公众号原文均未返回正文，文章可能已删除或限制访问")
        for tag in node.select('script,style'):
            tag.decompose()
        text = node.get_text('\n', strip=True)
        if len(text) > 240000:
            raise ApiError("正文超过 24 万字符，已跳过")
        articles = self.db.rows('SELECT id FROM articles WHERE url=?', (url,))
        image_count = 0
        if articles:
            image_count = self._save_images(articles[0]['id'], node, url)
        if len(text) < 100 and image_count == 0:
            raise ApiError("正文不足 100 字且未取得原文图片，无法可靠总结")
        return text

    def _fetch_image(self, url, referer):
        self.check_cancel()
        request = urllib.request.Request(url, headers={
            'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
            'Referer': referer,
            'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                           'AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36'),
        })
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                content_type = str(response.headers.get('Content-Type') or '').lower()
                if content_type and not content_type.startswith('image/'):
                    raise ValueError('响应不是图片')
                data = response.read(IMAGE_BYTES_LIMIT + 1)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            raise ValueError('图片下载失败') from exc
        if not data or len(data) > IMAGE_BYTES_LIMIT:
            raise ValueError('图片为空或过大')
        try:
            with Image.open(BytesIO(data)) as picture:
                width, height = picture.size
                image_format = str(picture.format or '').upper()
                picture.verify()
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise ValueError('图片格式无效') from exc
        if width < 32 or height < 32:
            raise ValueError('忽略装饰或跟踪小图')
        suffixes = {'JPEG': '.jpg', 'PNG': '.png', 'GIF': '.gif', 'WEBP': '.webp',
                    'BMP': '.bmp', 'TIFF': '.tif'}
        suffix = suffixes.get(image_format)
        if not suffix:
            raise ValueError('不支持的图片格式')
        return data, suffix, width, height

    def _save_images(self, article_id, node, referer):
        old = self.db.article_images(article_id)
        directory = self.db.path.parent / 'article-images' / str(article_id)
        candidates = article_image_candidates(node)
        if not candidates:
            return len(old)
        staged = []
        deadline = time.monotonic() + 60
        for candidate in candidates:
            if time.monotonic() >= deadline:
                break
            try:
                data, suffix, width, height = self._fetch_image(candidate['url'], referer)
                directory.mkdir(parents=True, exist_ok=True)
                position = len(staged) + 1
                path = directory / (f'{position:03d}' + suffix)
                temporary = directory / (f'.new-{position:03d}' + suffix)
                temporary.write_bytes(data)
                staged.append((temporary, path, {'position': position,
                    'source_url': candidate['url'],
                    'local_path': path.relative_to(self.db.path.parent).as_posix(),
                    'alt_text': candidate['alt_text'], 'width': width, 'height': height}))
            except (OSError, ValueError):
                continue
        if not staged:
            return len(old)
        self.db.remove_image_files([item['local_path'] for item in old])
        saved = []
        for temporary, path, metadata in staged:
            temporary.replace(path)
            saved.append(metadata)
        self.db.replace_article_images(article_id, saved)
        return len(saved)

    def test(self, account):
        articles = self.articles(account, 1)
        if not articles:
            return "书架已连接，但该公众号暂无文章"
        body = self.content(articles[0]['url'])
        return f"微信读书采集成功：{articles[0]['title']}；正文 {len(body)} 字"


def sync_shelf(db, session):
    return sync_books(db, session.shelf())


def sync_books(db, books):
    added = updated = 0
    for book in books:
        identifier, name = book.get('bookId', ''), str(book.get('title') or '').strip()
        if not re.fullmatch(r'MP_WXS_\d+', identifier) or not name:
            continue
        if db.rows('SELECT id FROM accounts WHERE identifier=?', (identifier,)):
            continue
        matches = db.rows('SELECT id FROM accounts WHERE name=?', (name,))
        if len(matches) == 1:
            db.execute('UPDATE accounts SET identifier=? WHERE id=?', (identifier, matches[0]['id']))
            updated += 1
        else:
            db.add_account(name, identifier)
            added += 1
    return f"书架同步完成：找到 {len(books)} 个公众号，新增 {added} 个，更新标识 {updated} 个"
