"""Obtain a user-confirmed WeRead login and verify one public article locally."""
from datetime import datetime, timezone
from pathlib import Path
import json
import sys
import time
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wewriter.weread import WeReadSession, BASE
from wewriter.secrets import Vault
from wewriter.api import ApiError
import qrcode

root = Path(__file__).resolve().parents[1] / ".local-data" / "weread-probe"
root.mkdir(parents=True, exist_ok=True)


def status(state, message, **extra):
    data = {"state": state, "message": message, "time": datetime.now(timezone.utc).isoformat(), **extra}
    (root / "status.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data, ensure_ascii=True), flush=True)


def run():
    session = WeReadSession()
    uid = session.login_uid()
    qr = root / "login.png"
    qrcode.make(BASE + "/web/confirm?uid=" + quote(uid, safe="")).save(qr)
    status("waiting", "请用微信扫码，并在手机确认登录微信读书；二维码仅用于本机验证", qr_path=str(qr))
    start = time.monotonic()
    while time.monotonic() - start < 300:
        try:
            result = session.poll(uid)
        except TimeoutError:
            continue
        if result == "confirmed":
            status("checking", "扫码已确认，正在检查登录状态")
            books = session.shelf()
            Vault(root / "session.dpapi.json").set_many({"weread": session.encrypted_state_payload()})
            status("logged_in", "登录通过，本地加密保存会话，未发送给第三方服务", mp_on_shelf=len(books))
            # This candidate book ID is inferred from the user's previously failed article's __biz.
            # A successful response is required before treating it as a working data source.
            candidate = "MP_WXS_3931691072"
            cover = session.check(session.request("/api/mp/cover", {"bookId": candidate}))
            review_id = cover.get("reviewId")
            if not isinstance(review_id, str) or not review_id.startswith(candidate + "_"):
                raise ApiError("已登录，但目标公众号没有返回可验证的文章标识，不能确认支持该公众号")
            time.sleep(2)
            body = session.request("/web/mp/content", {"reviewId": review_id}, json_result=False)
            has_body = 'id="js_content"' in body or "id='js_content'" in body
            (root / "sample.html").write_text(body, encoding="utf-8")
            status("article_received" if has_body else "body_unverified",
                   "已收到目标文章页面，仍需核对正文完整性" if has_body else "目标响应未发现文章正文，不能作为采集成功",
                   title=cover.get("title", ""), response_characters=len(body), article_body_found=has_body)
            return
        time.sleep(2)
    status("expired", "二维码已过期，需要重新生成")


if __name__ == "__main__":
    try:
        run()
    except (ApiError, TimeoutError) as exc:
        status("failed", str(exc))
        raise SystemExit(1)
