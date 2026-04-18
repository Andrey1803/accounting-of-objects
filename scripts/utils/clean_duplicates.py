# -*- coding: utf-8 -*-
"""Очистка базы от дубликатов"""
import sqlite3
import os
import sys
import io

# Безопасная настройка encoding для stdout
try:
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
except (io.UnsupportedOperation, AttributeError, OSError):
    pass

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app_data.db')

def clean_duplicates():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    print("=" * 70)
    print("  ОЧИСТКА БАЗЫ ОТ ДУБЛИКАТОВ")
    print("=" * 70)

    # 1. Удаляем дубли материалов — оставляем запись с наименьшим ID
    print("\n1. Очистка МАТЕРИАЛОВ (catalog_materials)...")
    c.execute("""
        SELECT id FROM catalog_materials
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM catalog_materials
            GROUP BY name, user_id
        )
    """)
    mat_ids = [r[0] for r in c.fetchall()]
    print(f"   Найдено {len(mat_ids)} дублей для удаления")

    if mat_ids:
        # Удаляем чанками по 500
        batch_size = 500
        for i in range(0, len(mat_ids), batch_size):
            batch = mat_ids[i:i+batch_size]
            placeholders = ','.join('?' for _ in batch)
            c.execute(f"DELETE FROM catalog_materials WHERE id IN ({placeholders})", batch)
        conn.commit()
        print(f"   ✅ Удалено {len(mat_ids)} дублей материалов")

    # 2. Очистка дублей клиентов
    print("\n2. Очистка КЛИЕНТОВ (clients)...")
    c.execute("""
        SELECT id FROM clients
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM clients
            GROUP BY name, user_id
        )
    """)
    client_ids = [r[0] for r in c.fetchall()]
    print(f"   Найдено {len(client_ids)} дублей для удаления")

    if client_ids:
        for cid in client_ids:
            c.execute("DELETE FROM clients WHERE id = ?", (cid,))
        conn.commit()
        print(f"   ✅ Удалено {len(client_ids)} дублей клиентов")

    # 3. Сжимаем AUTOINCREMENT (опционально — пересоздаём таблицы без gaps)
    print("\n3. Проверка целостности...")

    # Материалы
    c.execute("SELECT COUNT(*) FROM catalog_materials")
    print(f"   catalog_materials: {c.fetchone()[0]} записей")

    # Клиенты
    c.execute("SELECT COUNT(*) FROM clients")
    print(f"   clients: {c.fetchone()[0]} записей")

    # Проверка UNIQUE constraint для catalog_materials
    c.execute("SELECT name, user_id, COUNT(*) FROM catalog_materials GROUP BY name, user_id HAVING COUNT(*) > 1")
    remaining = c.fetchall()
    if remaining:
        print(f"\n   ⚠️ Осталось {len(remaining)} дублей (нельзя удалить):")
        for name, uid, cnt in remaining[:5]:
            print(f"      • '{name}' (user={uid}) — {cnt} шт")
    else:
        print("\n   ✅ Дублей не осталось")

    conn.close()
    print("\n✅ Очистка завершена!")

if __name__ == '__main__':
    clean_duplicates()
