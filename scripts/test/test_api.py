import sqlite3
import json

conn = sqlite3.connect('app_data.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()
c.execute('SELECT * FROM catalog_materials WHERE user_id=999 LIMIT 2')
rows = c.fetchall()
for r in rows:
    print(json.dumps(dict(r), ensure_ascii=False, indent=2))
    print('---')
conn.close()
