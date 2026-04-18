import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')

conn = sqlite3.connect('app_data.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

print("=== WORKERS ===")
c.execute("SELECT * FROM workers")
rows = c.fetchall()
for r in rows:
    print(f"  id={r['id']}, user={r['user_id']}, name={r['full_name']}, rate={r['daily_rate']}, active={r['is_active']}")

print("\n=== WORKER_ASSIGNMENTS ===")
c.execute("SELECT * FROM worker_assignments")
rows = c.fetchall()
for r in rows:
    print(f"  id={r['id']}, worker={r['worker_id']}, obj={r['object_id']}, days={r['days_worked']}, pay={r['total_pay']}")

print("\n=== OBJECT 20 ===")
c.execute("SELECT id, user_id, name, date_start, salary FROM objects WHERE id=20")
r = c.fetchone()
if r:
    print(f"  id={r['id']}, user={r['user_id']}, name={r['name']}, date={r['date_start']}, salary={r['salary']}")
else:
    print("  Не найден!")

conn.close()
