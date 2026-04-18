# -*- coding: utf-8 -*-
import sqlite3, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

conn = sqlite3.connect('app_data.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

c.execute("""SELECT e.id as est_id, o.id as obj_id, o.name as obj_name 
             FROM estimates e 
             JOIN objects o ON o.id = e.object_id 
             WHERE o.user_id = 1 AND o.name LIKE '%Варвара%' 
             ORDER BY e.id DESC LIMIT 5""")
est_rows = c.fetchall()
print(f'Найдено смет: {len(est_rows)}')

for er in est_rows:
    eid = er['est_id']
    obj_name = er['obj_name']
    print(f'\n=== Смета {eid} (Объект: {obj_name}) ===')
    c.execute('SELECT section, name, quantity, price, purchase_price, material_profit, total FROM estimate_items WHERE estimate_id=?', (eid,))
    rows = c.fetchall()
    total_mat_profit = 0
    for r in rows:
        mp = r['material_profit'] or 0
        total_mat_profit += mp
        print(f'  [{r["section"]}] {r["name"][:40]} qty={r["quantity"]} price={r["price"]} purch={r["purchase_price"]} profit={mp:.2f} total={r["total"]}')
    print(f'  ИТОГО material_profit: {total_mat_profit:.2f}')

conn.close()
