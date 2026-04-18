# -*- coding: utf-8 -*-
"""Исправление кодировки в базе данных — cp1251 -> utf-8"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app_data.db')

def fix_text(s):
    """Перекодирует текст из cp1251 (сохранённого как latin-1) в UTF-8"""
    if not s or not isinstance(s, str):
        return s or ''
    try:
        # Пробуем: строка была записана как cp1251 но прочитана как latin-1
        # Сначала encode в latin-1 (получим оригинальные байты cp1251), затем decode как cp1251
        fixed = s.encode('latin-1').decode('cp1251')
        return fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s

def main():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Находим все импортированные записи — только существующие колонки
    c.execute("SELECT id, name, category, description, unit FROM catalog_materials WHERE user_id = 1")
    rows = c.fetchall()

    fixed_count = 0
    for row in rows:
        rid, name, category, description, unit = row

        new_name = fix_text(name)
        new_category = fix_text(category)
        new_description = fix_text(description)
        new_unit = fix_text(unit)

        c.execute("""UPDATE catalog_materials SET
            name=?, category=?, description=?, unit=?
            WHERE id=?""",
            (new_name, new_category, new_description, new_unit, rid))
        fixed_count += 1
    
    conn.commit()
    conn.close()
    print("Исправлено записей:", fixed_count)

if __name__ == '__main__':
    main()
