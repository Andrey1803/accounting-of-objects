# -*- coding: utf-8 -*-
"""Проверка корректности данных объектов Варвара Сосновщина"""
import sqlite3, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

conn = sqlite3.connect('app_data.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

for obj_id, obj_name in [(21, 'Варвара Сосновщина 2'), (20, 'Варвара Сосновщина')]:
    print(f"\n{'='*60}")
    print(f"  Объект: {obj_name} (ID={obj_id})")
    print(f"{'='*60}")
    
    # Данные объекта
    c.execute('SELECT id, name, date_start, date_end, sum_work, expenses, status, advance, salary FROM objects WHERE id=?', (obj_id,))
    obj = dict(c.fetchone())
    print(f"\n📋 Объект:")
    print(f"  sum_work={obj['sum_work']}  expenses={obj['expenses']}  status='{obj['status']}'  advance={obj['advance']}  salary={obj['salary']}")
    
    # Сметы
    c.execute('SELECT id, number FROM estimates WHERE object_id=?', (obj_id,))
    estimates = c.fetchall()
    print(f"\n📊 Сметы ({len(estimates)}):")
    for est in estimates:
        print(f"  Смета #{est['id']} ({est['number']})")
        c.execute('''
            SELECT section, COUNT(*) as cnt, 
                   COALESCE(SUM(total),0) as total_sum,
                   COALESCE(SUM(CASE WHEN section='material' THEN material_profit ELSE 0 END),0) as mat_profit
            FROM estimate_items WHERE estimate_id=? GROUP BY section
        ''', (est['id'],))
        for row in c.fetchall():
            print(f"    [{row['section']}] позиций={row['cnt']} сумма={row['total_sum']:.2f} прибыль_мат={row['mat_profit']:.2f}")
    
    # Агрегаты смет
    c.execute('''
        SELECT 
            COALESCE(SUM(CASE WHEN section='work' THEN total ELSE 0 END), 0) as estimate_works,
            COALESCE(SUM(CASE WHEN section='material' THEN total ELSE 0 END), 0) as estimate_materials,
            COALESCE(SUM(CASE WHEN section='material' THEN material_profit ELSE 0 END), 0) as estimate_material_profit
        FROM estimate_items ei
        JOIN estimates e ON e.id = ei.estimate_id
        WHERE e.object_id=?
    ''', (obj_id,))
    agg = dict(c.fetchone())
    
    # Расчёты
    ew = agg['estimate_works']
    em = agg['estimate_materials']
    emp = agg['estimate_material_profit']
    
    total_revenue = obj['sum_work'] + ew + em
    total_expenses = obj['expenses'] + em
    total_profit = total_revenue - total_expenses - obj['salary'] + emp
    margin = (total_profit / total_revenue * 100) if total_revenue > 0 else 0
    balance = total_revenue - obj['advance']
    
    print(f"\n📈 Расчёты:")
    print(f"  Выручка = {obj['sum_work']} + {ew:.2f} + {em:.2f} = {total_revenue:.2f}")
    print(f"  Затраты = {obj['expenses']} + {em:.2f} = {total_expenses:.2f}")
    print(f"  Прибыль = {total_revenue:.2f} - {total_expenses:.2f} - {obj['salary']} + {emp:.2f} = {total_profit:.2f}")
    print(f"  Маржа = {margin:.1f}%")
    print(f"  Остаток = {total_revenue:.2f} - {obj['advance']} = {balance:.2f}")
    
    # Проверки
    print(f"\n✅ Проверки:")
    if balance > 0:
        print(f"  ⚠️  Остаток +{balance:.2f} BYN — клиент ЕЩЁ должен!")
    elif balance < 0:
        print(f"  ℹ️  Остаток {balance:.2f} BYN — клиент ПЕРЕплатил на {-balance:.2f} BYN")
    else:
        print(f"  ✅ Остаток 0 — всё оплачено")
    
    if obj['status'] in ('Закрыт', 'Оплачен') and balance > 0:
        print(f"  ❌ Несоответствие: статус «закрыт/оплачен» но клиент ещё должен!")
    
    # Рабочие
    c.execute('SELECT COUNT(*) as cnt, COALESCE(SUM(total_pay),0) as total FROM worker_assignments WHERE object_id=?', (obj_id,))
    wa = dict(c.fetchone())
    print(f"\n👷 Рабочие: {wa['cnt']} назначений, сумма={wa['total']:.2f}")

conn.close()
