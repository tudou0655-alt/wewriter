"""Print recent local job and article errors without credentials or article bodies."""
import json
from pathlib import Path
import sqlite3
import sys

sys.stdout.reconfigure(encoding='utf-8')


connection = sqlite3.connect(sys.argv[1])
connection.row_factory = sqlite3.Row
data_dir = Path(sys.argv[1]).resolve().parent
print('JOBS')
for row in connection.execute('''SELECT id,created_at,status,stage,message,warnings,selected,config
                                  FROM jobs ORDER BY id DESC LIMIT 8'''):
    print(json.dumps(dict(row), ensure_ascii=False))
print('ARTICLES')
for row in connection.execute('''SELECT id,title,length(content) AS content_len,error,cleared
                                  FROM articles ORDER BY id DESC LIMIT 16'''):
    print(json.dumps(dict(row), ensure_ascii=False))
print('IMAGES')
for row in connection.execute('''SELECT i.article_id,a.title,i.position,i.local_path,i.width,i.height
                                  FROM article_images i JOIN articles a ON a.id=i.article_id
                                  ORDER BY i.article_id,i.position'''):
    item = dict(row)
    image_path = data_dir / item['local_path']
    item['size_bytes'] = image_path.stat().st_size if image_path.is_file() else None
    print(json.dumps(item, ensure_ascii=False))
connection.close()
