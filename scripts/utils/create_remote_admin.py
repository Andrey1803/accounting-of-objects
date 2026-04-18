# -*- coding: utf-8 -*-
"""
Создать пользователя в удалённой БД (PostgreSQL на Railway и т.п.).

Локальная регистрация пишет в SQLite; облако использует другой DATABASE_URL —
учётку нужно создать отдельно (этот скрипт) или временно открыть /register.

Пример (PowerShell), URL скопируйте из Variables сервиса Postgres на Railway:
  $env:DATABASE_URL = 'postgresql://...'
  python scripts/utils/create_remote_admin.py --username you --password 'YourPass8+'

Или одной строкой:
  python scripts/utils/create_remote_admin.py --database-url "postgresql://..." -u you -p YourPass8+
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

# Корень проекта до импорта database (там читается DATABASE_URL)
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main() -> int:
    p = argparse.ArgumentParser(description="Create user in remote DB (DATABASE_URL / PostgreSQL).")
    p.add_argument("-u", "--username", required=True)
    p.add_argument("-p", "--password", required=True)
    p.add_argument("--database-url", help="If omitted, uses env DATABASE_URL")
    p.add_argument("--admin", action="store_true", help="Force role admin (default: admin only if DB has no users)")
    p.add_argument("--reset-password", action="store_true", help="If username exists, set new password instead of failing")
    args = p.parse_args()

    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url.strip()

    if not os.environ.get("DATABASE_URL"):
        print("Need DATABASE_URL (Railway Postgres) or --database-url", file=sys.stderr)
        return 1

    if len(args.password) < 8:
        print("Password must be at least 8 characters (same as registration form).", file=sys.stderr)
        return 1

    # Импорт после выставления DATABASE_URL
    from auth import hash_pw  # noqa: E402
    from database import execute, fetch_one, init_db  # noqa: E402

    init_db()

    ph = hash_pw(args.password)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    row = fetch_one("SELECT id, role FROM users WHERE username = ?", (args.username.strip(),))
    if row:
        if args.reset_password:
            execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (ph, row["id"]),
            )
            print(f"OK: password updated for '{args.username}' (id={row['id']}).")
            return 0
        print(f"User '{args.username}' already exists. Use --reset-password or pick another name.", file=sys.stderr)
        return 1

    n = fetch_one("SELECT COUNT(*) AS c FROM users")["c"]
    role = "admin" if (n == 0 or args.admin) else "user"

    execute(
        "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
        (args.username.strip(), ph, role, now),
    )
    print(f"OK: created '{args.username}' role={role}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
