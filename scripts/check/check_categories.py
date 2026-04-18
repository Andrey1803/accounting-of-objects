import sqlite3

conn = sqlite3.connect('app_data.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

print("=== КАТЕГОРИИ ===")
c.execute('SELECT id, name, parent_id, category_type FROM categories ORDER BY id')
rows = c.fetchall()
for r in rows:
    print(f"id={r['id']}, name='{r['name']}', parent_id={r['parent_id']}, type='{r['category_type']}'")

print("\n=== МАТЕРИАЛЫ (первые 5) ===")
c.execute('SELECT id, name, category FROM catalog_materials LIMIT 5')
rows = c.fetchall()
for r in rows:
    print(f"id={r['id']}, name='{r['name']}', category='{r['category']}'")

conn.close()
