"""Read-only verification of browser-based WeRead credential refresh."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wewriter.db import Database
from wewriter.secrets import Vault
from wewriter.api import ApiError
from wewriter.weread import WeReadSource

sys.stdout.reconfigure(encoding='utf-8')

data_dir = Path(sys.argv[1]).resolve()
database = Database(data_dir / 'workspace.sqlite')
accounts = database.accounts(enabled=True)
source = WeReadSource(Vault(data_dir / 'keys.dpapi.json').get('weread'), database,
                      profile_dir=data_dir / 'weread-edge-profile')
try:
    books = source.shelf()
    result = {'shelf_accounts': len(books), 'article_probe': 'skipped'}
    if accounts:
        articles = source.articles(accounts[0]['identifier'], 1)
        result['article_probe'] = len(articles)
    print(result)
except ApiError as exc:
    cause = exc.__cause__
    print({'ok': False, 'error': str(exc), 'cause_type': type(cause).__name__ if cause else '',
           'cause': str(cause)[:500] if cause else ''})
    raise
finally:
    source.close()
