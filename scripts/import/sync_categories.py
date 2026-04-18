# -*- coding: utf-8 -*-
"""Синхронизация таблицы categories с catalog_materials"""
import sqlite3
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

DB = 'app_data.db'

def main():
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    # Категории из catalog_materials
    c.execute("SELECT DISTINCT category FROM catalog_materials WHERE user_id=1 AND category != ''")
    mat_cats = set(r[0] for r in c.fetchall())

    # Категории из таблицы categories
    c.execute("SELECT id, name FROM categories WHERE user_id=1 AND category_type='material'")
    cat_rows = c.fetchall()
    existing_cats = {r[1]: r[0] for r in cat_rows}

    # 1. Удаляем пустые категории (нет материалов)
    deleted = 0
    for name, cat_id in existing_cats.items():
        if name not in mat_cats:
            c.execute("DELETE FROM categories WHERE id=?", (cat_id,))
            deleted += 1

    # 2. Добавляем недостающие категории
    added = 0
    for cat_name in mat_cats:
        if cat_name not in existing_cats:
            c.execute("INSERT INTO categories (user_id, name, category_type) VALUES (1, ?, 'material')", (cat_name,))
            added += 1

    conn.commit()
    conn.close()
    print(f"Удалено пустых: {deleted}")
    print(f"Добавлено недостающих: {added}")
    print(f"Всего категорий material: {len(mat_cats)}")

if __name__ == '__main__':
    main()
