"""Read-only live verification; never print cookies or general bookshelf contents."""
import json
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bs4 import BeautifulSoup
from wewriter.weread import WeReadSession
from wewriter.secrets import Vault

root = Path(__file__).resolve().parents[1] / ".local-data" / "weread-probe"
text = (root / "sample.html").read_text(encoding="utf-8")
soup = BeautifulSoup(text, "html.parser")
body = soup.select_one("#js_content")
print(json.dumps({"body_characters": len(body.get_text(strip=True)) if body else 0,
    "title": soup.select_one("#activity-name").get_text(strip=True) if soup.select_one("#activity-name") else None,
    "date": re.findall(r'var ct\s*=\s*[\"\x27](\d+)', text)}, ensure_ascii=True))
session = WeReadSession(Vault(root / "session.dpapi.json").get("weread"))
result = session.request("/web/mp/articles", {"bookId": "MP_WXS_3931691072", "offset": 0})
(root / "article-list.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"fields": list(result), "error": result.get("errCode", result.get("errcode")),
    "reviews": len(result.get("reviews", [])), "hasMore": result.get("hasMore")}, ensure_ascii=True))
for group in result.get("reviews", [])[:3]:
    for item in group.get("subReviews", [])[:2]:
        review = item.get("review", {})
        print(json.dumps({"review_fields": list(review), "title": review.get("mpInfo", {}).get("title"),
                          "createTime": review.get("createTime")}, ensure_ascii=True))
