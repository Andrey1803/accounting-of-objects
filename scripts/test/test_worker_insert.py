import logging, os

log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'server_errors.log')

# Перезаписываем лог
logging.basicConfig(
    filename=log_path,
    level=logging.ERROR,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filemode='w'
)

print(f"Logging to: {log_path}")

# Тестируем функцию execute
from database import fetch_one, execute

# Имитируем запрос добавления рабочего
worker_id = 1
user_id = 1
obj_id = 20
days = 1
total_pay = 150
work_date = '2026-04-09'

try:
    worker = fetch_one("SELECT id, daily_rate, full_name FROM workers WHERE id = ? AND user_id = ?", (worker_id, user_id))
    print(f"Worker found: {worker}")
    
    if worker:
        print(f"Inserting assignment...")
        aid = execute("""INSERT INTO worker_assignments
            (user_id, worker_id, object_id, work_date, days_worked, total_pay)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, worker_id, obj_id, work_date, days, total_pay), return_id=True)
        print(f"Inserted id={aid}")
        
        print("Recalculating salary...")
        from app_objects import recalc_object_salary
        recalc_object_salary(obj_id)
        print("Done!")
except Exception as e:
    import traceback
    tb = traceback.format_exc()
    print(f"ERROR: {e}")
    print(tb)
    logging.error(f"{e}\n{tb}")
