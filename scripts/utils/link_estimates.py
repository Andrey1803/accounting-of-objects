# -*- coding: utf-8 -*-
"""Привязка смет к объектам по совпадению имени и клиента"""
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

def link_estimates():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    print("=" * 70)
    print("  ПРИВЯЗКА СМЕТ К ОБЪЕКТАМ")
    print("=" * 70)

    # Получаем все объекты
    c.execute("SELECT id, name, client, user_id FROM objects")
    objects = c.fetchall()
    print(f"\nНайдено объектов: {len(objects)}")

    # Получаем все сметы без object_id
    c.execute("SELECT id, object_name, client, user_id FROM estimates WHERE object_id = 0 OR object_id IS NULL")
    estimates = c.fetchall()
    print(f"Найдено непривязанных смет: {len(estimates)}")

    if not estimates:
        print("\n✅ Все сметы уже привязаны!")
        conn.close()
        return

    linked = 0
    unmatched = 0

    for est in estimates:
        est_name = (est['object_name'] or '').strip().lower()
        est_client = (est['client'] or '').strip().lower()
        est_uid = est['user_id']

        best_match = None
        best_score = 0

        for obj in objects:
            if obj['user_id'] != est_uid:
                continue

            obj_name = (obj['name'] or '').strip().lower()
            obj_client = (obj['client'] or '').strip().lower()

            # Проверяем совпадение
            score = 0
            if est_name == obj_name:
                score += 2
            elif est_name and obj_name and (est_name in obj_name or obj_name in est_name):
                score += 1

            if est_client == obj_client and est_client:
                score += 1
            elif est_client and obj_client and (est_client in obj_client or obj_client in est_client):
                score += 0.5

            if score > best_score:
                best_score = score
                best_match = obj

        if best_match and best_score >= 2:
            c.execute("UPDATE estimates SET object_id = ? WHERE id = ?", (best_match['id'], est['id']))
            print(f"  ✓ Смета #{est['id']} '{est['object_name']}' → Объект #{best_match['id']} '{best_match['name']}' (score: {best_score})")
            linked += 1
        else:
            print(f"  ✗ Смета #{est['id']} '{est['object_name']}' — не найдено совпадение")
            unmatched += 1

    conn.commit()
    conn.close()

    print(f"\n{'=' * 70}")
    print(f"  Привязано: {linked}")
    print(f"  Не найдено совпадений: {unmatched}")
    print("=" * 70)

if __name__ == '__main__':
    link_estimates()
