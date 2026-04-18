# -*- coding: utf-8 -*-
import sqlite3, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

conn = sqlite3.connect('app_data.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()
c.execute('SELECT category, item_type, brand, COUNT(*) as cnt FROM catalog_materials WHERE user_id=1 GROUP BY category, item_type, brand ORDER BY category, item_type, brand')
rows = c.fetchall()

current_cat = ''
for r in rows:
    cat = r['category']
    itype = r['item_type']
    brand = r['brand']
    cnt = r['cnt']
    if cat != current_cat:
        print(f'\n📁 {cat}')
        current_cat = cat
    print(f'  📂 {itype}  →  🏷️ {brand} ({cnt})')

conn.close()
