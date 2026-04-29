"""
Универсальный модуль для работы с базой данных (SQLite / PostgreSQL).

v2: Пул соединений через thread-local storage.
- Одно соединение на поток (переиспользуется между запросами)
- Автоматическое закрытие при завершении потока
- Транзакции в рамках одного соединения
"""
import os
import sqlite3
import logging
import threading

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DATABASE_URL = os.environ.get('DATABASE_URL')
IS_POSTGRES = bool(DATABASE_URL)

if IS_POSTGRES:
    import psycopg2
    from psycopg2.extras import RealDictCursor

# ---------------------------------------------------------------------------
# Thread-local storage для соединений
# ---------------------------------------------------------------------------
_local = threading.local()


def _normalize_postgres_url(url: str) -> str:
    """
    Параметры для облачного PostgreSQL (Railway и др.): SSL и таймаут.
    Без connect_timeout зависание на недоступном хосте может тянуться минутами — healthcheck не проходит.
    Локально без SSL: задайте PGSSLMODE=disable или prefer в переменных окружения.
    """
    u = (url or "").strip().replace("postgres://", "postgresql://", 1)
    parts = []
    if "sslmode=" not in u:
        mode = os.environ.get("PGSSLMODE", "").strip()
        if mode:
            parts.append(f"sslmode={mode}")
        elif os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"):
            parts.append("sslmode=require")
    if "connect_timeout=" not in u:
        parts.append(f"connect_timeout={os.environ.get('PG_CONNECT_TIMEOUT', '15')}")
    if parts:
        u += ("&" if "?" in u else "?") + "&".join(parts)
    return u


def get_connection():
    """
    Получить соединение для текущего потока.
    Если соединения нет — создаёт и сохраняет в thread-local.
    """
    conn = getattr(_local, 'db_conn', None)
    if conn is not None:
        # Проверяем, живо ли соединение
        try:
            if IS_POSTGRES:
                conn.cursor().execute('SELECT 1')
            else:
                conn.execute('SELECT 1')
            return conn
        except Exception:
            # Соединение мертво — закрываем и создаём новое
            try:
                conn.close()
            except Exception:
                pass
            _local.db_conn = None

    # Создаём новое соединение
    if IS_POSTGRES:
        url = _normalize_postgres_url(DATABASE_URL)
        conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
    else:
        # Путь к БД можно переопределить через DB_FILE
        db_path = os.environ.get('DB_FILE')
        if not db_path:
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app_data.db')
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        # WAL режим для лучшей конкурентности
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
        conn.execute('PRAGMA cache_size=-64000')  # 64MB кэш
        conn.execute('PRAGMA foreign_keys=ON')

    _local.db_conn = conn
    return conn


def close_connection():
    """Закрыть соединение текущего потока."""
    conn = getattr(_local, 'db_conn', None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _local.db_conn = None


def close_all_connections():
    """Закрыть все соединения (для shutdown)."""
    close_connection()


def fetch_all(query, args=()):
    """Выполнить SELECT и вернуть все строки."""
    conn = get_connection()
    cur = None
    try:
        cur = conn.cursor()
        q = query.replace('?', '%s') if IS_POSTGRES else query
        cur.execute(q, args)
        rows = cur.fetchall()
        if not IS_POSTGRES:
            rows = [dict(row) for row in rows]
        return rows
    except Exception as e:
        logging.error(f"DB Error (fetch_all): {e}\nQuery: {query}\nArgs: {args}")
        raise
    finally:
        if cur:
            cur.close()


def fetch_one(query, args=()):
    """Выполнить SELECT и вернуть одну строку (или None)."""
    res = fetch_all(query, args)
    return res[0] if res else None


def execute(query, args=(), return_id=False):
    """Выполнить INSERT/UPDATE/DELETE. Вернуть lastrowid или True."""
    conn = get_connection()
    cur = None
    try:
        cur = conn.cursor()
        q = query.replace('?', '%s') if IS_POSTGRES else query
        cur.execute(q, args)
        conn.commit()
        if return_id:
            if IS_POSTGRES:
                cur.execute("SELECT lastval() AS _lid")
                row = cur.fetchone()
                return row['_lid'] if row else None
            else:
                return cur.lastrowid
        return True
    except Exception as e:
        conn.rollback()
        logging.error(f"DB Error (execute): {e}\nQuery: {query}\nArgs: {args}")
        raise
    finally:
        if cur:
            cur.close()


def execute_rowcount(query, args=()):
    """UPDATE/DELETE: число затронутых строк (0 если ничего не изменилось)."""
    conn = get_connection()
    cur = None
    try:
        cur = conn.cursor()
        q = query.replace('?', '%s') if IS_POSTGRES else query
        cur.execute(q, args)
        rc = cur.rowcount
        conn.commit()
        return 0 if rc is None or rc < 0 else rc
    except Exception as e:
        conn.rollback()
        logging.error(f"DB Error (execute_rowcount): {e}\nQuery: {query}\nArgs: {args}")
        raise
    finally:
        if cur:
            cur.close()


def execute_many(queries_with_args):
    """
    Выполняет несколько запросов в одной транзакции.
    queries_with_args: список кортежей (query, args)
    Возвращает True.
    """
    conn = get_connection()
    cur = None
    try:
        cur = conn.cursor()
        for query, args in queries_with_args:
            q = query.replace('?', '%s') if IS_POSTGRES else query
            cur.execute(q, args)
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logging.error(f"DB Error (execute_many): {e}")
        raise
    finally:
        if cur:
            cur.close()


def delete_user_data(user_id):
    """
    Удалить все данные пользователя и саму запись users.
    Нужно для SQLite (нет CASCADE); в PostgreSQL можно вызывать тем же порядком.
    """
    estimates = fetch_all("SELECT id FROM estimates WHERE user_id = ?", (user_id,))
    queries = []
    for est in estimates:
        queries.append(("DELETE FROM estimate_items WHERE estimate_id = ?", (est['id'],)))
    queries.extend([
        ("DELETE FROM estimates WHERE user_id = ?", (user_id,)),
        ("DELETE FROM worker_account_entries WHERE user_id = ?", (user_id,)),
        ("DELETE FROM worker_assignments WHERE user_id = ?", (user_id,)),
        ("DELETE FROM workers WHERE user_id = ?", (user_id,)),
        ("DELETE FROM objects WHERE user_id = ?", (user_id,)),
        ("DELETE FROM clients WHERE user_id = ?", (user_id,)),
        ("DELETE FROM categories WHERE user_id = ?", (user_id,)),
        ("DELETE FROM catalog_materials WHERE user_id = ?", (user_id,)),
        ("DELETE FROM catalog_works WHERE user_id = ?", (user_id,)),
        ("DELETE FROM users WHERE id = ?", (user_id,)),
    ])
    execute_many(queries)


def _ensure_indexes():
    """Индексы под выборки по user_id и сметам."""
    conn = get_connection()
    cur = conn.cursor()
    stmts = [
        "CREATE INDEX IF NOT EXISTS idx_objects_user_date ON objects(user_id, date_start)",
        "CREATE INDEX IF NOT EXISTS idx_estimates_user ON estimates(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_estimates_user_object ON estimates(user_id, object_id)",
        "CREATE INDEX IF NOT EXISTS idx_estimate_items_estimate ON estimate_items(estimate_id)",
        "CREATE INDEX IF NOT EXISTS idx_workers_user ON workers(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_worker_account_user_worker ON worker_account_entries(user_id, worker_id)",
    ]
    try:
        for sql in stmts:
            q = sql.replace('?', '%s') if IS_POSTGRES else sql
            cur.execute(q)
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.warning(f"Indexes: {e}")
    finally:
        cur.close()


def _dedupe_sqlite_catalog_exact_names():
    """
    Удалить дубли каталога с одинаковыми (user_id, name), оставляя одну строку:
    максимальный use_count, при равенстве — меньший id.
    Нужно для SQLite до создания UNIQUE(user_id, name). На PostgreSQL не вызывается.
    """
    if IS_POSTGRES:
        return
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            DELETE FROM catalog_materials
            WHERE id IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY user_id, name
                               ORDER BY use_count DESC, id ASC
                           ) AS rn
                    FROM catalog_materials
                ) x WHERE rn > 1
            )
            """
        )
        mat_n = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
        cur.execute(
            """
            DELETE FROM catalog_works
            WHERE id IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY user_id, name
                               ORDER BY use_count DESC, id ASC
                           ) AS rn
                    FROM catalog_works
                ) x WHERE rn > 1
            )
            """
        )
        work_n = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
        conn.commit()
        if mat_n or work_n:
            logging.info(
                "SQLite: дедупликация каталога — удалено строк: materials=%s, works=%s",
                mat_n,
                work_n,
            )
    except Exception as e:
        conn.rollback()
        logging.error("SQLite: дедупликация каталога не выполнена: %s", e)
    finally:
        cur.close()


def _ensure_sqlite_catalog_unique_indexes():
    """
    В PostgreSQL уже UNIQUE(user_id, name) в DDL.
    Для существующих SQLite-баз добавляем уникальные индексы (как у PG).
    Если в таблице уже есть дубли имён у одного user_id — индекс не создастся (см. лог).
    """
    if IS_POSTGRES:
        return
    conn = get_connection()
    cur = conn.cursor()
    for sql, label in (
        (
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_catalog_materials_user_name ON catalog_materials(user_id, name)",
            "catalog_materials(user_id, name)",
        ),
        (
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_catalog_works_user_name ON catalog_works(user_id, name)",
            "catalog_works(user_id, name)",
        ),
    ):
        try:
            cur.execute(sql)
            conn.commit()
            logging.info("SQLite: уникальный индекс %s создан или уже есть", label)
        except Exception as e:
            conn.rollback()
            logging.warning(
                "SQLite: не создан уникальный индекс %s (часто из‑за дублей имён в каталоге). "
                "Очистите дубли или оставьте как есть. Ошибка: %s",
                label,
                e,
            )
    cur.close()


def _pg_exec_ddl(cur, conn, statements):
    """psycopg2: одна SQL-команда на execute; в конце commit."""
    for sql in statements:
        cur.execute(sql)
    conn.commit()


def _migrate_object_status_labels(conn):
    """Переименование статусов объектов в существующих строках (этапы работ vs оплата)."""
    mapping = (
        ('Запланирован', 'Ожидает старта'),
        ('Завершён', 'Выполнен'),
        ('Оплачен', 'Закрыт'),
    )
    mc = conn.cursor()
    try:
        for old, new in mapping:
            if IS_POSTGRES:
                mc.execute("UPDATE objects SET status = %s WHERE status = %s", (new, old))
            else:
                mc.execute("UPDATE objects SET status = ? WHERE status = ?", (new, old))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        mc.close()


def init_db():
    """Создать таблицы если не существуют. Не удаляет файл БД и не DROP TABLE — только CREATE/ALTER при необходимости."""
    conn = get_connection()
    cur = conn.cursor()

    if IS_POSTGRES:
        _pg_exec_ddl(cur, conn, [
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL, role TEXT DEFAULT 'user',
                email TEXT DEFAULT '', created_at TIMESTAMP DEFAULT NOW())
            """,
            """
            CREATE TABLE IF NOT EXISTS categories (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                parent_id INTEGER,
                category_type TEXT DEFAULT 'material',
                name TEXT NOT NULL,
                UNIQUE(user_id, name))
            """,
            """
            CREATE TABLE IF NOT EXISTS objects (
                id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                date_start TEXT, date_end TEXT, name TEXT NOT NULL, client TEXT, client_id INTEGER,
                sum_work REAL DEFAULT 0, expenses REAL DEFAULT 0, status TEXT DEFAULT 'Ожидает старта',
                advance REAL DEFAULT 0, salary REAL DEFAULT 0, notes TEXT,
                integration_source TEXT,
                created_at TIMESTAMP DEFAULT NOW(), updated_at TIMESTAMP DEFAULT NOW())
            """,
            """
            CREATE TABLE IF NOT EXISTS clients (
                id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL, phone TEXT DEFAULT '', email TEXT DEFAULT '', address TEXT DEFAULT '')
            """,
            """
            CREATE TABLE IF NOT EXISTS estimates (
                id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                number TEXT, date TEXT, object_id INTEGER, object_name TEXT, client TEXT,
                status TEXT DEFAULT 'Черновик', vat_percent REAL DEFAULT 0,
                markup_percent REAL DEFAULT 0, discount_percent REAL DEFAULT 0,
                notes TEXT, created_at TIMESTAMP DEFAULT NOW(), updated_at TIMESTAMP DEFAULT NOW())
            """,
            """
            CREATE TABLE IF NOT EXISTS estimate_items (
                id SERIAL PRIMARY KEY, estimate_id INTEGER REFERENCES estimates(id) ON DELETE CASCADE,
                section TEXT DEFAULT 'material', name TEXT, unit TEXT,
                quantity REAL, price_type TEXT, price REAL, purchase_price REAL, wholesale_price REAL DEFAULT 0,
                total REAL, material_profit REAL, sort_order INTEGER)
            """,
            """
            CREATE TABLE IF NOT EXISTS catalog_materials (
                id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL, unit TEXT DEFAULT 'шт', category TEXT DEFAULT '',
                article TEXT DEFAULT '', brand TEXT DEFAULT '', item_type TEXT DEFAULT '',
                purchase_price REAL DEFAULT 0, retail_price REAL DEFAULT 0, wholesale_price REAL DEFAULT 0,
                min_wholesale_qty REAL DEFAULT 10, description TEXT DEFAULT '', use_count INTEGER DEFAULT 1,
                image_path TEXT DEFAULT '',
                UNIQUE(user_id, name))
            """,
            """
            CREATE TABLE IF NOT EXISTS catalog_works (
                id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL, unit TEXT DEFAULT 'шт',
                price REAL DEFAULT 0, description TEXT DEFAULT '', use_count INTEGER DEFAULT 1,
                UNIQUE(user_id, name))
            """,
            """
            CREATE TABLE IF NOT EXISTS workers (
                id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                full_name TEXT NOT NULL, phone TEXT DEFAULT '', daily_rate REAL DEFAULT 150,
                hire_date TEXT DEFAULT '', notes TEXT DEFAULT '', is_active INTEGER DEFAULT 1)
            """,
            """
            CREATE TABLE IF NOT EXISTS worker_assignments (
                id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                worker_id INTEGER REFERENCES workers(id) ON DELETE CASCADE,
                object_id INTEGER, work_date TEXT,
                days_worked REAL DEFAULT 1, total_pay REAL DEFAULT 0)
            """,
            """
            CREATE TABLE IF NOT EXISTS worker_account_entries (
                id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                worker_id INTEGER REFERENCES workers(id) ON DELETE CASCADE,
                object_id INTEGER,
                entry_kind TEXT NOT NULL,
                expense_category TEXT DEFAULT '',
                amount REAL NOT NULL,
                entry_date TEXT NOT NULL,
                note TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT NOW())
            """,
            """
            CREATE TABLE IF NOT EXISTS invitations (
                id SERIAL PRIMARY KEY,
                token TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                expires_at TIMESTAMP NULL,
                max_uses INTEGER DEFAULT 1,
                used_count INTEGER DEFAULT 0,
                note TEXT DEFAULT '')
            """,
        ])

        for alter in (
            "ALTER TABLE categories ADD COLUMN IF NOT EXISTS category_type TEXT DEFAULT 'material'",
            "ALTER TABLE catalog_materials ADD COLUMN IF NOT EXISTS image_path TEXT DEFAULT ''",
            "ALTER TABLE catalog_materials ADD COLUMN IF NOT EXISTS article TEXT DEFAULT ''",
            "ALTER TABLE catalog_materials ADD COLUMN IF NOT EXISTS brand TEXT DEFAULT ''",
            "ALTER TABLE catalog_materials ADD COLUMN IF NOT EXISTS item_type TEXT DEFAULT ''",
            "ALTER TABLE estimate_items ADD COLUMN IF NOT EXISTS wholesale_price REAL DEFAULT 0",
            "ALTER TABLE objects ADD COLUMN IF NOT EXISTS client_id INTEGER",
            "ALTER TABLE objects ADD COLUMN IF NOT EXISTS integration_source TEXT",
        ):
            try:
                cur.execute(alter)
                conn.commit()
            except Exception:
                conn.rollback()
    else:
        cur.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL, role TEXT DEFAULT 'user',
                email TEXT DEFAULT '', created_at TEXT DEFAULT '');

            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                parent_id INTEGER,
                category_type TEXT DEFAULT 'material',
                name TEXT NOT NULL,
                UNIQUE(user_id, name));

            CREATE TABLE IF NOT EXISTS objects (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                date_start TEXT, date_end TEXT, name TEXT NOT NULL, client TEXT, client_id INTEGER,
                sum_work REAL DEFAULT 0, expenses REAL DEFAULT 0, status TEXT DEFAULT 'Ожидает старта',
                advance REAL DEFAULT 0, salary REAL DEFAULT 0, notes TEXT,
                integration_source TEXT,
                created_at TEXT DEFAULT '', updated_at TEXT DEFAULT '');

            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                name TEXT NOT NULL, phone TEXT DEFAULT '', email TEXT DEFAULT '', address TEXT DEFAULT '');

            CREATE TABLE IF NOT EXISTS estimates (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                number TEXT, date TEXT, object_id INTEGER, object_name TEXT, client TEXT,
                status TEXT DEFAULT 'Черновик', vat_percent REAL DEFAULT 0,
                markup_percent REAL DEFAULT 0, discount_percent REAL DEFAULT 0,
                notes TEXT, created_at TEXT DEFAULT '', updated_at TEXT DEFAULT '');

            CREATE TABLE IF NOT EXISTS estimate_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT, estimate_id INTEGER,
                section TEXT DEFAULT 'material', name TEXT, unit TEXT,
                quantity REAL, price_type TEXT, price REAL, purchase_price REAL, wholesale_price REAL DEFAULT 0,
                total REAL, material_profit REAL, sort_order INTEGER);

            CREATE TABLE IF NOT EXISTS catalog_materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                name TEXT NOT NULL, unit TEXT DEFAULT 'шт', category TEXT DEFAULT '',
                article TEXT DEFAULT '', brand TEXT DEFAULT '', item_type TEXT DEFAULT '',
                purchase_price REAL DEFAULT 0, retail_price REAL DEFAULT 0, wholesale_price REAL DEFAULT 0,
                min_wholesale_qty REAL DEFAULT 10, description TEXT DEFAULT '', use_count INTEGER DEFAULT 1,
                image_path TEXT DEFAULT '',
                UNIQUE(user_id, name));

            CREATE TABLE IF NOT EXISTS catalog_works (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                name TEXT NOT NULL, unit TEXT DEFAULT 'шт',
                price REAL DEFAULT 0, description TEXT DEFAULT '', use_count INTEGER DEFAULT 1,
                UNIQUE(user_id, name));

            CREATE TABLE IF NOT EXISTS workers (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                full_name TEXT NOT NULL, phone TEXT DEFAULT '', daily_rate REAL DEFAULT 150,
                hire_date TEXT DEFAULT '', notes TEXT DEFAULT '', is_active INTEGER DEFAULT 1);

            CREATE TABLE IF NOT EXISTS worker_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                worker_id INTEGER, object_id INTEGER, work_date TEXT,
                days_worked REAL DEFAULT 1, total_pay REAL DEFAULT 0);

            CREATE TABLE IF NOT EXISTS worker_account_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                worker_id INTEGER NOT NULL, object_id INTEGER,
                entry_kind TEXT NOT NULL, expense_category TEXT DEFAULT '',
                amount REAL NOT NULL, entry_date TEXT NOT NULL,
                note TEXT DEFAULT '', created_at TEXT DEFAULT '');

            CREATE TABLE IF NOT EXISTS invitations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT UNIQUE NOT NULL,
                created_at TEXT DEFAULT '',
                created_by INTEGER,
                expires_at TEXT DEFAULT '',
                max_uses INTEGER DEFAULT 1,
                used_count INTEGER DEFAULT 0,
                note TEXT DEFAULT '');
        """)

    # Миграция для старых баз (добавляем parent_id если нет)
    try:
        if not IS_POSTGRES:
            conn.execute("ALTER TABLE categories ADD COLUMN parent_id INTEGER")
            conn.commit()
    except Exception:
        pass

    # Миграция: добавляем category_type
    try:
        if not IS_POSTGRES:
            conn.execute("ALTER TABLE categories ADD COLUMN category_type TEXT DEFAULT 'material'")
            conn.commit()
    except Exception:
        pass

    # Миграция: image_path в каталоге материалов
    try:
        if not IS_POSTGRES:
            conn.execute("ALTER TABLE catalog_materials ADD COLUMN image_path TEXT DEFAULT ''")
            conn.commit()
    except Exception:
        pass

    for col_sql in (
        "ALTER TABLE catalog_materials ADD COLUMN article TEXT DEFAULT ''",
        "ALTER TABLE catalog_materials ADD COLUMN brand TEXT DEFAULT ''",
        "ALTER TABLE catalog_materials ADD COLUMN item_type TEXT DEFAULT ''",
    ):
        try:
            if not IS_POSTGRES:
                conn.execute(col_sql)
                conn.commit()
        except Exception:
            pass

    try:
        if not IS_POSTGRES:
            conn.execute("ALTER TABLE estimate_items ADD COLUMN wholesale_price REAL DEFAULT 0")
            conn.commit()
    except Exception:
        pass

    # Миграция: добавляем таблицу workers
    try:
        if not IS_POSTGRES:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS workers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                    full_name TEXT NOT NULL, phone TEXT DEFAULT '', daily_rate REAL DEFAULT 150,
                    hire_date TEXT DEFAULT '', notes TEXT DEFAULT '', is_active INTEGER DEFAULT 1)
            """)
            conn.commit()
    except Exception:
        pass

    # Миграция: добавляем таблицу worker_assignments
    try:
        if not IS_POSTGRES:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS worker_assignments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                    worker_id INTEGER, object_id INTEGER, work_date TEXT,
                    days_worked REAL DEFAULT 1, total_pay REAL DEFAULT 0)
            """)
            conn.commit()
    except Exception:
        pass

    try:
        if not IS_POSTGRES:
            conn.execute("ALTER TABLE objects ADD COLUMN client_id INTEGER")
            conn.commit()
    except Exception:
        pass

    try:
        if not IS_POSTGRES:
            conn.execute("ALTER TABLE objects ADD COLUMN integration_source TEXT")
            conn.commit()
    except Exception:
        pass

    # Миграция: подотчёт / выручка у рабочих
    try:
        if not IS_POSTGRES:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS worker_account_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                    worker_id INTEGER NOT NULL, object_id INTEGER,
                    entry_kind TEXT NOT NULL, expense_category TEXT DEFAULT '',
                    amount REAL NOT NULL, entry_date TEXT NOT NULL,
                    note TEXT DEFAULT '', created_at TEXT DEFAULT '')
            """)
            conn.commit()
    except Exception:
        pass

    try:
        _migrate_object_status_labels(conn)
    except Exception as e:
        logging.warning("Миграция статусов objects: %s", e)

    cur.close()
    _ensure_indexes()
    _dedupe_sqlite_catalog_exact_names()
    _ensure_sqlite_catalog_unique_indexes()
    print("[OK] База данных инициализирована")
