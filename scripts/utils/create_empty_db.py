import sqlite3
import os

# Извлекаем схему из текущей базы
source_db = 'app_data.db'
dest_db = 'dist/app_data_empty.db'

conn = sqlite3.connect(source_db)
cursor = conn.cursor()

# Получаем схему всех таблиц (исключая внутренние)
cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL AND name NOT LIKE 'sqlite_%'")
schemas = cursor.fetchall()
conn.close()

# Создаём пустую базу
if os.path.exists(dest_db):
    os.remove(dest_db)

empty_conn = sqlite3.connect(dest_db)
for schema in schemas:
    empty_conn.execute(schema[0])
empty_conn.commit()

# Проверяем
cursor = empty_conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
import sys
print(f"[OK] Empty database created with {len(tables)} tables", flush=True)
for t in tables:
    cursor.execute(f"SELECT COUNT(*) FROM {t[0]}")
    count = cursor.fetchone()[0]
    print(f"  - {t[0]}: {count} records", flush=True)

empty_conn.close()
print(f"\nFile: {dest_db}", flush=True)
