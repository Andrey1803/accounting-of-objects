# -*- coding: utf-8 -*-
"""
Сбросить ошибочную привязку заказчика у объектов (client_id + текст client).

Пример:
  python scripts/fix/clear_object_client_link.py --user-id 1 --object-ids 12,34,56
  python scripts/fix/clear_object_client_link.py --user-id 1 --client-id 13 --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding='utf-8')

from database import init_db, fetch_all, execute


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--user-id', type=int, required=True)
    ap.add_argument('--object-ids', type=str, default='', help='через запятую')
    ap.add_argument('--client-id', type=int, default=0, help='сбросить у всех объектов с этим client_id')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    init_db()
    ids = []
    if args.object_ids.strip():
        ids = [int(x.strip()) for x in args.object_ids.split(',') if x.strip()]
    elif args.client_id:
        rows = fetch_all(
            'SELECT id, name, client FROM objects WHERE user_id = ? AND client_id = ?',
            (args.user_id, args.client_id),
        )
        print(f'Объектов с client_id={args.client_id}: {len(rows)}')
        for r in rows:
            print(f"  id={r['id']} name={r.get('name')} client_text={r.get('client')}")
        ids = [r['id'] for r in rows]
    else:
        print('Укажите --object-ids или --client-id')
        return 1

    if not ids:
        print('Нечего сбрасывать')
        return 0

    if args.dry_run:
        print('dry-run: сбросили бы client_id у', ids)
        return 0

    for oid in ids:
        execute(
            "UPDATE objects SET client_id = NULL, client = '' WHERE id = ? AND user_id = ?",
            (oid, args.user_id),
        )
    print('Сброшено объектов:', len(ids))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
