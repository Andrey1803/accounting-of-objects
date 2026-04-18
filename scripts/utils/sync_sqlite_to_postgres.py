# -*- coding: utf-8 -*-
"""
Перенос данных одного пользователя из локальной SQLite в PostgreSQL (Railway).

Логин один и тот же, но в БД разные id и разные файлы БД — «синхронизация» = копирование
всех строк, привязанных к локальному user_id, на аккаунт с тем же именем в Postgres
(строка users на сервере не трогается: пароль и роль остаются как после регистрации).

ВНИМАНИЕ: для выбранного пользователя на сервере сначала удаляются его объекты, сметы,
каталоги и т.д. (всё кроме записи users). Если на сервере уже что-то вводили — оно пропадёт.

Пример (PowerShell):
  $env:DATABASE_URL = 'postgresql://...'   # из Railway → Postgres → Variables
  python scripts/utils/sync_sqlite_to_postgres.py --username andrey

Логин сопоставляется без учёта регистра (Е/е и т.п.) и с обрезкой пробелов.

Разные подписи локально и на сервере (если всё же нужно):
  python scripts/utils/sync_sqlite_to_postgres.py --local-username old --remote-username new

Проверка без записи:
  python scripts/utils/sync_sqlite_to_postgres.py -u andrey --dry-run
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _print_usage_example() -> None:
    print(
        "\nПример:\n"
        "  python scripts/utils/sync_sqlite_to_postgres.py -u ВАШ_ЛОГИН "
        '--database-url "postgresql://..."\n'
        "Проверка без записи в Postgres:\n"
        "  ... -u ВАШ_ЛОГИН --database-url \"...\" --dry-run\n"
        "В PowerShell можно задать URL один раз:\n"
        "  $env:DATABASE_URL = 'postgresql://...'\n"
        "  python scripts/utils/sync_sqlite_to_postgres.py -u ВАШ_ЛОГИН\n"
        "Либо добавьте в корне проекта строку DATABASE_URL=... в файл .env.railway (он в .gitignore).\n"
        "Если консоль портит кириллицу в -u, используйте:\n"
        "  --local-user-id N --remote-user-id M\n",
        file=sys.stderr,
    )


def _hydrate_database_url_from_env_files() -> None:
    """Подставить DATABASE_URL из .env.railway (или DATABASE_PUBLIC_URL для доступа с ПК)."""
    for name in (".env.railway", ".env.railway.local"):
        path = _ROOT / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        direct: Optional[str] = None
        public: Optional[str] = None
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            up = s.upper()
            if up.startswith("DATABASE_URL="):
                v = s.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    direct = v
            elif up.startswith("DATABASE_PUBLIC_URL="):
                v = s.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    public = v
        chosen = direct or public
        if chosen:
            os.environ.setdefault("DATABASE_URL", chosen)
            break


def _pg_connect(url: str):
    import psycopg2
    from psycopg2.extras import RealDictCursor

    u = url.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(u, cursor_factory=RealDictCursor)


def _sqlite_connect(path: Path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn, sql: str, params: Tuple = ()) -> List[sqlite3.Row]:
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur.fetchall()


def _norm_login(s: str) -> str:
    return (s or "").strip().casefold()


def _resolve_user_sqlite(conn, username: str) -> Tuple[int, str]:
    needle = _norm_login(username)
    if not needle:
        raise SystemExit("SQLite: пустой логин.")
    cur = conn.cursor()
    cur.execute("SELECT id, username FROM users")
    matches: List[Tuple[int, str]] = []
    for row in cur.fetchall():
        un = row[1]
        if un is None:
            continue
        if _norm_login(un) == needle:
            matches.append((int(row[0]), str(un)))
    if not matches:
        raise SystemExit(
            f"SQLite: пользователь «{username.strip()}» не найден "
            "(точное совпадение и без учёта регистра)."
        )
    if len(matches) > 1:
        desc = ", ".join(f"id={m[0]} «{m[1]}»" for m in matches)
        raise SystemExit(
            f"SQLite: несколько учёток под этот логин (без учёта регистра): {desc}. "
            "Оставьте одну или укажите разные --local-username / --remote-username."
        )
    return matches[0]


def _user_by_id_sqlite(conn, uid: int) -> Tuple[int, str]:
    cur = conn.cursor()
    cur.execute("SELECT id, username FROM users WHERE id = ?", (uid,))
    row = cur.fetchone()
    if not row:
        raise SystemExit(f"SQLite: пользователь с id={uid} не найден.")
    return int(row[0]), str(row[1])


def _user_by_id_pg(pgc, uid: int) -> Tuple[int, str]:
    with pgc.cursor() as cur:
        cur.execute("SELECT id, username FROM users WHERE id = %s", (uid,))
        row = cur.fetchone()
        if not row:
            raise SystemExit(f"PostgreSQL: пользователь с id={uid} не найден.")
        return int(row["id"]), str(row["username"])


def _resolve_user_pg(pgc, username: str) -> Tuple[int, str]:
    needle = _norm_login(username)
    if not needle:
        raise SystemExit("PostgreSQL: пустой логин.")
    with pgc.cursor() as cur:
        cur.execute("SELECT id, username FROM users")
        rows = cur.fetchall()
    matches: List[Tuple[int, str]] = []
    for row in rows:
        un = row["username"]
        if un is None:
            continue
        if _norm_login(str(un)) == needle:
            matches.append((int(row["id"]), str(un)))
    if not matches:
        raise SystemExit(
            f"PostgreSQL: пользователь «{username.strip()}» не найден "
            "(точное совпадение и без учёта регистра)."
        )
    if len(matches) > 1:
        desc = ", ".join(f"id={m[0]} «{m[1]}»" for m in matches)
        raise SystemExit(
            f"PostgreSQL: несколько учёток под этот логин: {desc}. "
            "Удалите дубликаты вручную или уточните логин."
        )
    return matches[0]


def _insert_returning(cur, table: str, columns: List[str], values: List[Any]) -> int:
    placeholders = ", ".join(["%s"] * len(columns))
    cols = ", ".join(columns)
    cur.execute(
        f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) RETURNING id",
        values,
    )
    row = cur.fetchone()
    return int(row["id"])


def _copy_categories(
    slc, pgc, local_uid: int, remote_uid: int, dry: bool
) -> Dict[int, int]:
    rows = _rows(slc, "SELECT * FROM categories WHERE user_id = ? ORDER BY id", (local_uid,))
    by_id = {int(r["id"]): dict(r) for r in rows}
    id_map: Dict[int, int] = {}
    remaining = set(by_id.keys())
    if dry:
        return {k: k for k in remaining}  # фиктивно, только для подсчётов
    cur = pgc.cursor()
    while remaining:
        progressed = False
        for old_id in list(remaining):
            r = by_id[old_id]
            pid = r["parent_id"]
            if pid is not None:
                pid = int(pid)
            if pid is not None and pid not in id_map:
                continue
            new_parent = id_map[pid] if pid is not None else None
            new_id = _insert_returning(
                cur,
                "categories",
                ["user_id", "parent_id", "category_type", "name"],
                [
                    remote_uid,
                    new_parent,
                    r["category_type"] or "material",
                    r["name"],
                ],
            )
            id_map[old_id] = new_id
            remaining.remove(old_id)
            progressed = True
        if not progressed:
            raise RuntimeError(
                "Категории: цикл или битая ссылка parent_id — проверьте данные в SQLite."
            )
    return id_map


def _copy_simple(
    slc,
    pgc,
    table: str,
    local_uid: int,
    remote_uid: int,
    data_columns: List[str],
    dry: bool,
) -> Dict[int, int]:
    rows = _rows(slc, f"SELECT * FROM {table} WHERE user_id = ?", (local_uid,))
    if dry:
        return {int(r["id"]): int(r["id"]) for r in rows}
    id_map: Dict[int, int] = {}
    cur = pgc.cursor()
    insert_cols = ["user_id"] + data_columns
    for r in rows:
        vals = [remote_uid] + [r[c] for c in data_columns]
        new_id = _insert_returning(cur, table, insert_cols, vals)
        id_map[int(r["id"])] = new_id
    return id_map


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Перенос данных пользователя SQLite → PostgreSQL (Railway)."
    )
    ap.add_argument(
        "-u",
        "--username",
        help="Одинаковый логин в SQLite и в Postgres",
    )
    ap.add_argument("--local-username", help="Логин в локальной БД")
    ap.add_argument("--remote-username", help="Логин на сервере (Postgres)")
    ap.add_argument(
        "--local-user-id",
        type=int,
        default=None,
        help="Локальный users.id (если консоль ломает кириллицу в -u)",
    )
    ap.add_argument(
        "--remote-user-id",
        type=int,
        default=None,
        help="Серверный users.id (часто 1 для первого зарегистрированного)",
    )
    ap.add_argument(
        "--sqlite",
        type=Path,
        default=_ROOT / "app_data.db",
        help="Путь к app_data.db",
    )
    ap.add_argument("--database-url", help="Или задайте переменную окружения DATABASE_URL")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать объёмы данных, без удаления и вставки в Postgres",
    )
    args = ap.parse_args()
    _hydrate_database_url_from_env_files()

    by_id = args.local_user_id is not None and args.remote_user_id is not None
    one_id = (args.local_user_id is not None) ^ (args.remote_user_id is not None)
    if one_id:
        print("Задайте оба: --local-user-id и --remote-user-id.", file=sys.stderr)
        return 2

    lu = args.local_username or args.username
    ru = args.remote_username or args.username
    if not by_id and (not lu or not ru):
        if sys.stdin.isatty():
            print(
                "Логин не передан в аргументах. "
                "Укажите один и тот же логин в SQLite и на сервере (или Ctrl+Z / Ctrl+D для выхода)."
            )
            u = input("Логин: ").strip()
            if u:
                lu = ru = u
        if not by_id and (not lu or not ru):
            print("Ошибка: нужен логин.", file=sys.stderr)
            _print_usage_example()
            return 2

    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url.strip()
    db_url = os.environ.get("DATABASE_URL", "").strip() or None
    if not db_url and sys.stdin.isatty():
        print("Вставьте DATABASE_URL (Railway → ваш сервис Postgres → Variables).")
        db_url = input("DATABASE_URL: ").strip() or None
        if db_url:
            os.environ["DATABASE_URL"] = db_url
    if not db_url:
        print("Ошибка: нужен DATABASE_URL.", file=sys.stderr)
        _print_usage_example()
        return 1

    if not args.sqlite.is_file():
        print(f"Файл не найден: {args.sqlite}", file=sys.stderr)
        return 1

    slc = _sqlite_connect(args.sqlite)
    try:
        if by_id:
            local_uid, local_label = _user_by_id_sqlite(slc, args.local_user_id)
        else:
            local_uid, local_label = _resolve_user_sqlite(slc, lu)
    except SystemExit:
        slc.close()
        raise

    pgc = _pg_connect(db_url)
    try:
        if by_id:
            remote_uid, remote_label = _user_by_id_pg(pgc, args.remote_user_id)
        else:
            remote_uid, remote_label = _resolve_user_pg(pgc, ru)
    except SystemExit:
        slc.close()
        pgc.close()
        raise

    def count(tbl: str) -> int:
        cur = slc.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {tbl} WHERE user_id = ?", (local_uid,))
        return int(cur.fetchone()[0])

    print(
        f"Локально: «{local_label}» id={local_uid}; "
        f"сервер: «{remote_label}» id={remote_uid}."
    )
    print(
        "Объёмы в SQLite:",
        f"categories={count('categories')} clients={count('clients')} "
        f"objects={count('objects')} estimates={count('estimates')} "
        f"materials={count('catalog_materials')} works={count('catalog_works')} "
        f"workers={count('workers')} assignments={count('worker_assignments')}",
    )

    if args.dry_run:
        est_ids = [int(r[0]) for r in _rows(slc, "SELECT id FROM estimates WHERE user_id = ?", (local_uid,))]
        n_items = 0
        if est_ids:
            cur = slc.cursor()
            q = "SELECT COUNT(*) FROM estimate_items WHERE estimate_id IN ({})".format(
                ",".join("?" * len(est_ids))
            )
            cur.execute(q, est_ids)
            n_items = int(cur.fetchone()[0])
        print(f"Позиций в сметах (estimate_items): {n_items}")
        print("--dry-run: запись в PostgreSQL не выполнялась.")
        slc.close()
        pgc.close()
        return 0

    with pgc:
        cur = pgc.cursor()
        cur.execute("DELETE FROM estimates WHERE user_id = %s", (remote_uid,))
        cur.execute("DELETE FROM workers WHERE user_id = %s", (remote_uid,))
        cur.execute("DELETE FROM objects WHERE user_id = %s", (remote_uid,))
        cur.execute("DELETE FROM clients WHERE user_id = %s", (remote_uid,))
        cur.execute("DELETE FROM categories WHERE user_id = %s", (remote_uid,))
        cur.execute("DELETE FROM catalog_materials WHERE user_id = %s", (remote_uid,))
        cur.execute("DELETE FROM catalog_works WHERE user_id = %s", (remote_uid,))

        _ = _copy_categories(slc, pgc, local_uid, remote_uid, False)

        client_cols = ["name", "phone", "email", "address"]
        client_map = _copy_simple(
            slc, pgc, "clients", local_uid, remote_uid, client_cols, False
        )

        mat_cols = [
            "name",
            "unit",
            "category",
            "article",
            "brand",
            "item_type",
            "purchase_price",
            "retail_price",
            "wholesale_price",
            "min_wholesale_qty",
            "description",
            "use_count",
            "image_path",
        ]
        _copy_simple(slc, pgc, "catalog_materials", local_uid, remote_uid, mat_cols, False)

        work_cols = ["name", "unit", "price", "description", "use_count"]
        _copy_simple(slc, pgc, "catalog_works", local_uid, remote_uid, work_cols, False)

        worker_cols = [
            "full_name",
            "phone",
            "daily_rate",
            "hire_date",
            "notes",
            "is_active",
        ]
        worker_map = _copy_simple(
            slc, pgc, "workers", local_uid, remote_uid, worker_cols, False
        )

        obj_rows = _rows(slc, "SELECT * FROM objects WHERE user_id = ?", (local_uid,))
        object_map: Dict[int, int] = {}
        cur = pgc.cursor()
        for r in obj_rows:
            old_cid = r["client_id"]
            new_cid: Optional[int] = None
            if old_cid is not None:
                new_cid = client_map.get(int(old_cid))
            new_id = _insert_returning(
                cur,
                "objects",
                [
                    "user_id",
                    "date_start",
                    "date_end",
                    "name",
                    "client",
                    "client_id",
                    "sum_work",
                    "expenses",
                    "status",
                    "advance",
                    "salary",
                    "notes",
                    "created_at",
                    "updated_at",
                ],
                [
                    remote_uid,
                    r["date_start"],
                    r["date_end"],
                    r["name"],
                    r["client"],
                    new_cid,
                    r["sum_work"] or 0,
                    r["expenses"] or 0,
                    r["status"],
                    r["advance"] or 0,
                    r["salary"] or 0,
                    r["notes"],
                    r["created_at"],
                    r["updated_at"],
                ],
            )
            object_map[int(r["id"])] = new_id

        est_rows = _rows(slc, "SELECT * FROM estimates WHERE user_id = ?", (local_uid,))
        estimate_map: Dict[int, int] = {}
        for r in est_rows:
            oid = r["object_id"]
            new_oid: Optional[int] = None
            if oid is not None:
                new_oid = object_map.get(int(oid))
            new_eid = _insert_returning(
                cur,
                "estimates",
                [
                    "user_id",
                    "number",
                    "date",
                    "object_id",
                    "object_name",
                    "client",
                    "status",
                    "vat_percent",
                    "markup_percent",
                    "discount_percent",
                    "notes",
                    "created_at",
                    "updated_at",
                ],
                [
                    remote_uid,
                    r["number"],
                    r["date"],
                    new_oid,
                    r["object_name"],
                    r["client"],
                    r["status"],
                    r["vat_percent"] or 0,
                    r["markup_percent"] or 0,
                    r["discount_percent"] or 0,
                    r["notes"],
                    r["created_at"],
                    r["updated_at"],
                ],
            )
            estimate_map[int(r["id"])] = new_eid

        for old_eid, new_eid in estimate_map.items():
            items = _rows(
                slc,
                "SELECT * FROM estimate_items WHERE estimate_id = ?",
                (old_eid,),
            )
            for it in items:
                cur.execute(
                    """
                    INSERT INTO estimate_items (
                        estimate_id, section, name, unit, quantity, price_type,
                        price, purchase_price, wholesale_price, total,
                        material_profit, sort_order
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        new_eid,
                        it["section"],
                        it["name"],
                        it["unit"],
                        it["quantity"],
                        it["price_type"],
                        it["price"],
                        it["purchase_price"],
                        it["wholesale_price"] or 0,
                        it["total"],
                        it["material_profit"],
                        it["sort_order"],
                    ),
                )

        wa_rows = _rows(
            slc, "SELECT * FROM worker_assignments WHERE user_id = ?", (local_uid,)
        )
        for r in wa_rows:
            wid = r["worker_id"]
            oid = r["object_id"]
            new_w = worker_map.get(int(wid)) if wid is not None else None
            new_o = object_map.get(int(oid)) if oid is not None else None
            if new_w is None:
                continue
            cur.execute(
                """
                INSERT INTO worker_assignments (
                    user_id, worker_id, object_id, work_date, days_worked, total_pay
                ) VALUES (%s,%s,%s,%s,%s,%s)
                """,
                (
                    remote_uid,
                    new_w,
                    new_o,
                    r["work_date"],
                    r["days_worked"] or 1,
                    r["total_pay"] or 0,
                ),
            )

    print("Готово: данные скопированы на сервер для «%s»." % remote_label)
    slc.close()
    pgc.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        pass
