"""Inspect one failed article without printing credentials or changing the database."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import urllib.request
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wewriter.api import Transport, wechat_url, clean_text
from wewriter.secrets import Vault

base = Path(os.environ["LOCALAPPDATA"]) / "WeWriter"
db = sqlite3.connect((base / "workspace.sqlite").as_uri() + "?mode=ro", uri=True)
try:
    row = db.execute("SELECT id,url,error FROM articles WHERE error<>'' ORDER BY id DESC LIMIT 1").fetchone()
finally:
    db.close()
if not row:
    raise SystemExit("No failed article to diagnose")
key = Vault(base / "keys.dpapi.json").get("everyinfra")
url = wechat_url(row[1])
request = urllib.request.Request("https://api.everyinfra.com/api/v1/search",
    json.dumps({"tool": "read", "params": {"url": url}}).encode(),
    {"Authorization": "Bearer " + key, "Content-Type": "application/json"})
try:
    response = Transport().opener.open(request, timeout=100)
except urllib.error.HTTPError as error:
    response = error
with response:
    status = response.code
    raw = response.read(1024 * 1024).decode("utf-8", errors="replace")
try:
    result = json.loads(raw)
except ValueError:
    result = {"non_json_body": raw[:1500]}
if not isinstance(result, dict):
    result = {"unexpected_response": result}

def sanitize(value):
    if isinstance(value, dict):
        return {k: ("[redacted]" if any(x in k.lower() for x in ("key", "token", "authorization", "cookie")) else sanitize(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(x) for x in value[:5]]
    if isinstance(value, str):
        return value.replace(key, "[redacted]")[:1500]
    return value

report = {"http_status": status, "article_id": row[0], "previous_error": row[2], "response": sanitize(result), "items": []}
for item in result.get("results", []):
    if not isinstance(item, dict):
        continue
    try:
        matches = wechat_url(item.get("url", url)) == url
    except ValueError:
        matches = False
    report["items"].append({"fields": list(item), "url_matches": matches,
                             "content_characters": len(clean_text(item.get("content")))})
output = Path(__file__).resolve().parents[1] / ".local-data" / "article-diagnostic.json"
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=True))
