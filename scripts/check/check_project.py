"""
Полная проверка проекта ObjectAccounting
"""
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding='utf-8')

def check_file_exists(path, description):
    exists = os.path.exists(path)
    status = "✅" if exists else "❌"
    print(f"{status} {description}: {path}")
    return exists

def check_db():
    print("\n📊 Проверка базы данных:")
    conn = sqlite3.connect('app_data.db')
    c = conn.cursor()
    
    # Таблицы
    c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in c.fetchall()]
    print(f"  Таблиц: {len(tables)}")
    
    # Сметы
    c.execute("SELECT COUNT(*) FROM estimates")
    print(f"  Смет: {c.fetchone()[0]}")
    
    # Позиции
    c.execute("SELECT COUNT(*) FROM estimate_items")
    print(f"  Позиций в сметах: {c.fetchone()[0]}")
    
    # Материалы
    c.execute("SELECT COUNT(*) FROM catalog_materials WHERE user_id=999")
    print(f"  Материалов в каталоге: {c.fetchone()[0]}")
    
    # Категории
    c.execute("SELECT COUNT(*) FROM categories WHERE user_id=999")
    print(f"  Категорий: {c.fetchone()[0]}")
    
    # Пользователи
    c.execute("SELECT COUNT(*) FROM users")
    print(f"  Пользователей: {c.fetchone()[0]}")
    
    # Проверяем image_path
    c.execute("SELECT COUNT(*) FROM catalog_materials WHERE image_path IS NOT NULL AND image_path != ''")
    with_img = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM catalog_materials")
    total = c.fetchone()[0]
    print(f"  Материалов с фото: {with_img}/{total}")
    
    conn.close()

def check_static():
    print("\n📁 Проверка статических файлов:")
    check_file_exists('static/js/offline.js', 'OfflineDB (js/offline.js)')
    check_file_exists('static/images/catalog', 'Директория фото')
    
    # Проверяем фото
    if os.path.exists('static/images/catalog'):
        files = os.listdir('static/images/catalog')
        non_empty = sum(1 for f in files if os.path.getsize(os.path.join('static/images/catalog', f)) > 0)
        print(f"  Фото в каталоге: {non_empty}/{len(files)}")

def check_templates():
    print("\n📄 Проверка шаблонов:")
    check_file_exists('templates/estimate/editor.html', 'Редактор смет')
    check_file_exists('templates/estimate/catalog.html', 'Каталог')
    check_file_exists('templates/estimate/list.html', 'Список смет')
    
    # Проверяем что offline.js подключён
    with open('templates/estimate/editor.html', 'r', encoding='utf-8') as f:
        content = f.read()
        has_offline = 'js/offline.js' in content
        has_csrf = 'csrf-token' in content
        has_fetchcsrf = 'function fetchCsrf' in content
        has_saveEstimateToDB = 'function saveEstimateToDB' in content

        print(f"  {'✅' if has_offline else '❌'} js/offline.js подключён")
        print(f"  {'✅' if has_csrf else '❌'} CSRF токен в meta")
        print(f"  {'✅' if has_fetchcsrf else '❌'} функция fetchCsrf")
        print(f"  {'✅' if has_saveEstimateToDB else '❌'} функция saveEstimateToDB")

def check_backend():
    print("\n🔧 Проверка бэкенда:")
    check_file_exists('app_objects.py', 'Главный файл')
    check_file_exists('estimate_module.py', 'Модуль смет')
    check_file_exists('database.py', 'База данных')
    
    # Проверяем API endpoint дерева категорий
    with open('estimate_module.py', 'r', encoding='utf-8') as f:
        content = f.read()
        has_tree = '/api/catalog/categories/tree' in content
        has_no_cache = 'SEND_FILE_MAX_AGE_DEFAULT' in open('app_objects.py', encoding='utf-8').read()
        
        print(f"  {'✅' if has_tree else '❌'} API дерева категорий")
        print(f"  {'✅' if has_no_cache else '❌'} Отключение кэширования")

if __name__ == '__main__':
    print("=" * 60)
    print("ПОЛНАЯ ПРОВЕРКА ПРОЕКТА")
    print("=" * 60)
    
    check_db()
    check_static()
    check_templates()
    check_backend()
    
    print("\n" + "=" * 60)
    print("ПРОВЕРКА ЗАВЕРШЕНА")
    print("=" * 60)
