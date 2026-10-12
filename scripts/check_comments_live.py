import sys, sqlite3
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wewriter.secrets import Vault
from wewriter.api import EveryInfra, Transport, ApiError
base = Path.home() / 'AppData/Local/WeWriter'
vault = Vault(base / 'keys.dpapi.json')
with sqlite3.connect((base / 'workspace.sqlite').as_uri() + '?mode=ro', uri=True) as db:
    row = db.execute("SELECT url FROM articles ORDER BY published_at DESC LIMIT 1").fetchone()
if row:
    try:
        comments = EveryInfra(vault.get('everyinfra'), Transport()).comments(row[0], 13)
        print({'status':'ok', 'visible_comments':len(comments)})
    except (ApiError, ValueError) as exc:
        print({'status':'unavailable', 'reason':str(exc)})
else:
    print({'status':'no_article'})
