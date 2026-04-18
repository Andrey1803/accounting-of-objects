"""
Связать все материалы с изображениями по артикулу
"""
import sqlite3
import json
import sys

sys.stdout.reconfigure(encoding='utf-8')

DB_PATH = 'app_data.db'
MAPPING_PATH = 'catalog_images_mapping.json'

# Загружаем маппинг
with open(MAPPING_PATH, 'r', encoding='utf-8') as f:
    mapping = json.load(f)

image_map = mapping.get('images', {})
print(f'Загружено изображений: {len(image_map)}')

conn = sqlite3.connect(DB_PATH)

# Получаем все материалы
materials = conn.execute(
    "SELECT id, article FROM catalog_materials WHERE user_id = 999"
).fetchall()

print(f'Всего материалов: {len(materials)}')

linked = 0
not_found = 0

for mat_id, article in materials:
    if article and article in image_map:
        img_path = image_map[article]
        conn.execute(
            "UPDATE catalog_materials SET image_path = ? WHERE id = ?",
            (img_path, mat_id)
        )
        linked += 1
    else:
        not_found += 1

conn.commit()
conn.close()

print(f'\nРезультат:')
print(f'  Связано: {linked}')
print(f'  Не найдено фото: {not_found}')
