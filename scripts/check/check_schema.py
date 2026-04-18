import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')

conn = sqlite3.connect('app_data.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

print("=== workers таблица ===")
c.execute("SELECT sql FROM sqlite_master WHERE name='workers'")
r = c.fetchone()
if r: print(r[0])
else: print("Нет таблицы workers!")

print("\n=== worker_assignments таблица ===")
c.execute("SELECT sql FROM sqlite_master WHERE name='worker_assignments'")
r = c.fetchone()
if r: print(r[0])
else: print("Нет таблицы worker_assignments!")

print("\n=== objects столбцы ===")
c.execute("PRAGMA table_info(objects)")
for row in c.fetchall():
    print(f"  {row['name']} ({row['type']})")

conn.close()
