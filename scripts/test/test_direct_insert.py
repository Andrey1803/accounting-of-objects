import sys
sys.stdout.reconfigure(encoding='utf-8')
import sqlite3
from datetime import datetime

# Подключаемся к базе
conn = sqlite3.connect('app_data.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

# Находим первого пользователя с объектами и рабочими
c.execute("""
    SELECT u.id as user_id, u.username,
           (SELECT COUNT(*) FROM objects WHERE user_id = u.id) as obj_count,
           (SELECT COUNT(*) FROM workers WHERE user_id = u.id) as worker_count
    FROM users u
    WHERE obj_count > 0 AND worker_count > 0
    LIMIT 1
""")
user = c.fetchone()

if not user:
    print("No user with both objects and workers")
    conn.close()
    sys.exit(1)

user_id = user['user_id']
print(f"Testing for user: {user['username']} (id={user_id})")

# Получаем первого рабочего
c.execute("SELECT id, full_name, daily_rate FROM workers WHERE user_id = ? LIMIT 1", (user_id,))
worker = c.fetchone()
print(f"Worker: {worker['full_name']} (id={worker['id']}, rate={worker['daily_rate']})")

# Получаем первый объект
c.execute("SELECT id, name, date_start, salary FROM objects WHERE user_id = ? LIMIT 1", (user_id,))
obj = c.fetchone()
print(f"Object: {obj['name']} (id={obj['id']}, date={obj['date_start']}, salary={obj['salary']})")

# Добавляем рабочего к объекту
work_date = datetime.now().strftime('%Y-%m-%d')
days = 2
total_pay = worker['daily_rate'] * days

print(f"\nAdding worker {worker['id']} to object {obj['id']}...")
print(f"  Days: {days}, Pay: {total_pay}")

c.execute("""
    INSERT INTO worker_assignments (user_id, worker_id, object_id, work_date, days_worked, total_pay)
    VALUES (?, ?, ?, ?, ?, ?)
""", (user_id, worker['id'], obj['id'], work_date, days, total_pay))

assignment_id = c.lastrowid
print(f"  Assignment created: id={assignment_id}")

# Теперь вызываем recalc_object_salary логику
obj_date = obj['date_start'][:10]
print(f"\nRecalculating salary for objects starting on {obj_date}...")

c.execute("""
    SELECT COALESCE(SUM(wa.total_pay), 0) as total
    FROM worker_assignments wa
    JOIN objects o ON o.id = wa.object_id
    WHERE wa.user_id = ? AND o.date_start LIKE ?
""", (user_id, obj_date + '%'))

total_wages = c.fetchone()['total']
print(f"  Total wages: {total_wages}")

c.execute("""
    SELECT COUNT(*) as cnt
    FROM objects
    WHERE user_id = ? AND date_start LIKE ?
""", (user_id, obj_date + '%'))

obj_count = c.fetchone()['cnt']
print(f"  Objects count: {obj_count}")

if obj_count > 0 and total_wages > 0:
    worker_cost = total_wages / obj_count
else:
    worker_cost = 0

print(f"  Worker cost per object: {worker_cost}")

c.execute("UPDATE objects SET salary = ? WHERE id = ? AND user_id = ?",
          (round(worker_cost, 2), obj['id'], user_id))

conn.commit()

# Проверяем результат
c.execute("SELECT salary FROM objects WHERE id = ?", (obj['id'],))
new_salary = c.fetchone()['salary']
print(f"\nUpdated salary: {new_salary}")

# Показываем все назначения рабочего
c.execute("""
    SELECT wa.id, wa.work_date, wa.days_worked, wa.total_pay, w.full_name
    FROM worker_assignments wa
    JOIN workers w ON w.id = wa.worker_id
    WHERE wa.object_id = ?
""", (obj['id'],))

print(f"\nWorker assignments for object {obj['id']}:")
for row in c.fetchall():
    print(f"  {row['full_name']}: {row['days_worked']} days, {row['total_pay']} rub")

conn.close()
print("\nOK - Test completed successfully!")
