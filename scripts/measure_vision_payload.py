"""Measure the prepared vision request images without printing image data or credentials."""
import base64
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import sys

from PIL import Image, ImageOps

sys.stdout.reconfigure(encoding='utf-8')


def prepared_size(path):
    with Image.open(path) as original:
        picture = ImageOps.exif_transpose(original).convert('RGB')
        picture.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
        dimensions = picture.size
        output = BytesIO()
        picture.save(output, format='JPEG', quality=82, optimize=True)
    raw_size = len(output.getvalue())
    return dimensions, raw_size, len(base64.b64encode(output.getvalue()))


database = Path(sys.argv[1]).resolve()
article_id = int(sys.argv[2])
connection = sqlite3.connect(database)
connection.row_factory = sqlite3.Row
rows = list(connection.execute(
    'SELECT position,local_path FROM article_images WHERE article_id=? ORDER BY position',
    (article_id,)))
if len(rows) > 12:
    indexes = sorted({round(i * (len(rows) - 1) / 11) for i in range(12)})
    rows = [rows[index] for index in indexes]
result = []
for row in rows:
    path = database.parent / row['local_path']
    dimensions, raw_size, base64_size = prepared_size(path)
    result.append({'position': row['position'], 'prepared_dimensions': dimensions,
                   'jpeg_bytes': raw_size, 'base64_bytes': base64_size})
print(json.dumps(result, ensure_ascii=False))
print(json.dumps({'images': len(result), 'jpeg_bytes': sum(x['jpeg_bytes'] for x in result),
                  'base64_bytes': sum(x['base64_bytes'] for x in result)}, ensure_ascii=False))
