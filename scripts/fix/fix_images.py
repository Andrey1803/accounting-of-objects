"""
Очистить пустые файлы и обновить БД
"""
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding='utf-8')

# Удаляем пустые файлы
img_dir = 'static/images/catalog'
deleted = 0
kept = 0

for f in os.listdir(img_dir):
    path = os.path.join(img_dir, f)
    if os.path.getsize(path) == 0:
        os.remove(path)
        deleted += 1
    else:
        kept += 1

print(f'Удалено пустых файлов: {deleted}')
print(f'Осталось файлов с данными: {kept}')

# Обновляем БД — убираем image_path у товаров с пустыми файлами
conn = sqlite3.connect('app_data.db')

# Проверяем какие файлы реально существуют
materials = conn.execute("SELECT id, image_path FROM catalog_materials WHERE image_path IS NOT NULL AND image_path != ''").fetchall()

updated = 0
for mat_id, img_path in materials:
    full_path = os.path.join('static', img_path)
    if not os.path.exists(full_path) or os.path.getsize(full_path) == 0:
        conn.execute("UPDATE catalog_materials SET image_path = '' WHERE id = ?", (mat_id,))
        updated += 1

conn.commit()
conn.close()

print(f'Обновлено записей в БД (убран путь к несуществующему фото): {updated}')
