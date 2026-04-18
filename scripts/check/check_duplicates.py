# -*- coding: utf-8 -*-
"""Проверка базы данных на дубликаты"""
import sqlite3
import os
import sys
import io

# Безопасная настройка encoding для stdout
try:
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
except (io.UnsupportedOperation, AttributeError, OSError):
    pass  # stdout не поддерживает fileno (IDE, pipe и т.д.)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app_data.db')

def check_duplicates():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    print("=" * 70)
    print("  ПРОВЕРКА БАЗЫ НА ДУБЛИКАТЫ")
    print("=" * 70)

    # 1. catalog_materials — по имени + user_id
    print("\n1. МАТЕРИАЛЫ (catalog_materials) — дубли по имени:")
    c.execute("""
        SELECT name, user_id, COUNT(*) as cnt,
               GROUP_CONCAT(id) as ids
        FROM catalog_materials
        GROUP BY name, user_id
        HAVING cnt > 1
        ORDER BY cnt DESC
    """)
    rows = c.fetchall()
    if rows:
        print(f"   Найдено {len(rows)} групп дублей:")
        for name, uid, cnt, ids in rows[:20]:
            print(f"   • '{name}' (user={uid}) — {cnt} шт, IDs: {ids}")
        if len(rows) > 20:
            print(f"   ... и ещё {len(rows)-20} групп")
    else:
        print("   ✅ Дублей нет")

    # 2. catalog_works — по имени + user_id
    print("\n2. РАБОТЫ (catalog_works) — дубли по имени:")
    c.execute("""
        SELECT name, user_id, COUNT(*) as cnt,
               GROUP_CONCAT(id) as ids
        FROM catalog_works
        GROUP BY name, user_id
        HAVING cnt > 1
        ORDER BY cnt DESC
    """)
    rows = c.fetchall()
    if rows:
        print(f"   Найдено {len(rows)} групп дублей:")
        for name, uid, cnt, ids in rows[:20]:
            print(f"   • '{name}' (user={uid}) — {cnt} шт, IDs: {ids}")
    else:
        print("   ✅ Дублей нет")

    # 3. clients — по имени + user_id
    print("\n3. КЛИЕНТЫ (clients) — дубли по имени:")
    c.execute("""
        SELECT name, user_id, COUNT(*) as cnt,
               GROUP_CONCAT(id) as ids
        FROM clients
        GROUP BY name, user_id
        HAVING cnt > 1
        ORDER BY cnt DESC
    """)
    rows = c.fetchall()
    if rows:
        print(f"   Найдено {len(rows)} групп дублей:")
        for name, uid, cnt, ids in rows[:20]:
            print(f"   • '{name}' (user={uid}) — {cnt} шт, IDs: {ids}")
    else:
        print("   ✅ Дублей нет")

    # 4. objects — по name + date_start + user_id
    print("\n4. ОБЪЕКТЫ (objects) — дубли по имени+дата:")
    c.execute("""
        SELECT name, date_start, user_id, COUNT(*) as cnt,
               GROUP_CONCAT(id) as ids
        FROM objects
        GROUP BY name, date_start, user_id
        HAVING cnt > 1
        ORDER BY cnt DESC
    """)
    rows = c.fetchall()
    if rows:
        print(f"   Найдено {len(rows)} групп дублей:")
        for name, ds, uid, cnt, ids in rows[:20]:
            print(f"   • '{name}' ({ds}) user={uid} — {cnt} шт, IDs: {ids}")
    else:
        print("   ✅ Дублей нет")

    # 5. estimates — по number + user_id
    print("\n5. СМЕТЫ (estimates) — дубли по номеру:")
    c.execute("""
        SELECT number, user_id, COUNT(*) as cnt,
               GROUP_CONCAT(id) as ids
        FROM estimates
        GROUP BY number, user_id
        HAVING cnt > 1
        ORDER BY cnt DESC
    """)
    rows = c.fetchall()
    if rows:
        print(f"   Найдено {len(rows)} групп дублей:")
        for num, uid, cnt, ids in rows[:20]:
            print(f"   • '{num}' (user={uid}) — {cnt} шт, IDs: {ids}")
    else:
        print("   ✅ Дублей нет")

    # 6. estimate_items — дубли в сметах
    print("\n6. ПОЗИЦИИ СМЕТ (estimate_items) — дубли name+estimate_id:")
    c.execute("""
        SELECT name, estimate_id, COUNT(*) as cnt,
               GROUP_CONCAT(id) as ids
        FROM estimate_items
        GROUP BY name, estimate_id
        HAVING cnt > 1
        ORDER BY cnt DESC
    """)
    rows = c.fetchall()
    if rows:
        print(f"   Найдено {len(rows)} групп дублей:")
        for name, eid, cnt, ids in rows[:20]:
            print(f"   • '{name}' (estimate={eid}) — {cnt} шт, IDs: {ids}")
    else:
        print("   ✅ Дублей нет")

    # Общая статистика
    print("\n" + "=" * 70)
    print("  ОБЩАЯ СТАТИСТИКА")
    print("=" * 70)
    tables = ['catalog_materials', 'catalog_works', 'clients', 'objects', 'estimates', 'estimate_items', 'users', 'categories']
    for t in tables:
        c.execute(f"SELECT COUNT(*) FROM {t}")
        cnt = c.fetchone()[0]
        print(f"   {t}: {cnt} записей")

    # Проверка orphan записей
    print("\n" + "=" * 70)
    print("  ПРОВЕРКА НА ОРФАННЫЕ ЗАПИСИ")
    print("=" * 70)

    # Сметы без объекта
    c.execute("SELECT COUNT(*) FROM estimates e LEFT JOIN objects o ON e.object_id=o.id WHERE e.object_id > 0 AND o.id IS NULL")
    cnt = c.fetchone()[0]
    print(f"   Сметы с несуществующим object_id: {cnt}")

    # Позиции без сметы
    c.execute("SELECT COUNT(*) FROM estimate_items ei LEFT JOIN estimates e ON ei.estimate_id=e.id WHERE e.id IS NULL")
    cnt = c.fetchone()[0]
    print(f"   Позиции без существующей сметы: {cnt}")

    conn.close()
    print("\n✅ Проверка завершена")

if __name__ == '__main__':
    check_duplicates()
