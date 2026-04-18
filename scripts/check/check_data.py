import sqlite3
conn = sqlite3.connect('app_data.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()
c.execute('SELECT name, category, purchase_price, retail_price FROM catalog_materials WHERE user_id=999 LIMIT 3')
rows = c.fetchall()
for r in rows:
    print(dict(r))
conn.close()
