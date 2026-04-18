"""
Пересчёт затрат на рабочих для всех существующих объектов
"""
import sqlite3
import sys

sys.stdout.reconfigure(encoding='utf-8')

DB_PATH = 'app_data.db'

def recalc_all_worker_costs():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # Получаем все объекты
    c.execute("SELECT id, user_id, date_start FROM objects WHERE date_start IS NOT NULL AND date_start != '' ORDER BY user_id, date_start")
    objects = c.fetchall()
    
    print(f"Найдено объектов: {len(objects)}")
    
    updated = 0
    for obj in objects:
        obj_id = obj['id']
        user_id = obj['user_id']
        obj_date = obj['date_start'][:10]
        
        # Общая зарплата рабочих за этот день (по всем объектам)
        c.execute("""
            SELECT COALESCE(SUM(wa.total_pay), 0) as total
            FROM worker_assignments wa
            JOIN objects o ON o.id = wa.object_id
            WHERE wa.user_id = ? AND o.date_start LIKE ?
        """, (user_id, obj_date + '%'))
        total_wages = c.fetchone()['total']
        
        # Сколько объектов за этот день
        c.execute("""
            SELECT COUNT(*) as cnt FROM objects 
            WHERE user_id = ? AND date_start LIKE ?
        """, (user_id, obj_date + '%'))
        obj_count = c.fetchone()['cnt']
        
        # Распределяем
        if obj_count > 0 and total_wages > 0:
            worker_cost = total_wages / obj_count
        else:
            worker_cost = 0
        
        c.execute("UPDATE objects SET salary = ? WHERE id = ?", (round(worker_cost, 2), obj_id))
        updated += 1
    
    conn.commit()
    conn.close()
    print(f"Обновлено объектов: {updated}")

if __name__ == '__main__':
    print("=" * 50)
    print("ПЕРЕСЧЁТ ЗАТРАТ НА РАБОЧИХ")
    print("=" * 50)
    recalc_all_worker_costs()
    print("ГОТОВО!")
