"""
Главный файл приложения (Web-интерфейс и API)
Автоматически адаптируется под SQLite (локально) или PostgreSQL (Railway).
"""
import atexit
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def _load_local_dotenv() -> None:
    """Подхватить `ObjectAccounting/.env` до импорта database (там читается DATABASE_URL)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    p = Path(__file__).resolve().parent / ".env"
    if p.is_file():
        load_dotenv(p)


_load_local_dotenv()

import logging
import secrets
import threading
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_file
from flask_login import LoginManager, login_required, current_user, logout_user

# Импорт ядра базы данных и модулей
from database import init_db, fetch_all, fetch_one, execute, execute_rowcount, execute_many, IS_POSTGRES, close_all_connections
from auth import auth_bp, User, hash_pw, check_pw
from estimate_module import estimate_bp
from extensions import limiter
from io import BytesIO

from well_passport import (
    build_passport_context,
    generate_passport_files,
    survey_row_for_passport,
)

app = Flask(__name__)
# Секретный ключ для сессий — генерируется при запуске или берётся из окружения
_secret_key = os.environ.get('SECRET_KEY')
if not _secret_key:
    # Пытаемся загрузить/создать файл .secret_key
    _secret_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.secret_key')
    if os.path.exists(_secret_path):
        with open(_secret_path, 'r') as f:
            _secret_key = f.read().strip()
    else:
        _secret_key = secrets.token_hex(32)
        try:
            with open(_secret_path, 'w') as f:
                f.write(_secret_key)
        except OSError:
            logging.warning(
                "Не удалось записать .secret_key (read-only?). Задайте SECRET_KEY в окружении — "
                "иначе сессии сбросятся при перезапуске."
            )
app.secret_key = _secret_key

if os.environ.get('SESSION_COOKIE_SECURE', '').lower() in ('1', 'true', 'yes'):
    app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

limiter.init_app(app)

# За reverse-proxy (Railway и т.п.): корректные X-Forwarded-* для редиректов и сессий
if os.environ.get('FLASK_DEBUG', '0') != '1':
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=0)


@app.context_processor
def inject_register_enabled():
    off = os.environ.get('DISABLE_REGISTER', '').lower() in ('1', 'true', 'yes')
    return {'register_enabled': not off}


@app.context_processor
def inject_service_worker_flag():
    """DISABLE_SERVICE_WORKER=1 — без регистрации SW (диагностика «503 (index)»)."""
    return {
        'service_worker_disabled': os.environ.get('DISABLE_SERVICE_WORKER', '').lower()
        in ('1', 'true', 'yes')
    }


def _normalize_public_app_url(raw: str) -> str:
    """Базовый URL внешнего веб-приложения: допускаем ввод без схемы (подставляем https)."""
    v = (raw or '').strip().rstrip('/')
    if not v:
        return ''
    if not re.match(r'^[a-zA-Z][a-zA-Z\d+\-.]*://', v):
        v = 'https://' + v
    return v


@app.context_processor
def inject_dispatcher_tasks_url():
    """Ссылка в шапке на веб «Диспетчер задач» (переключение между программами)."""
    raw = (
        os.environ.get('DISPATCHER_TASKS_URL')
        or os.environ.get('DISPATCHER_APP_URL')
        or ''
    )
    return {'dispatcher_tasks_url': _normalize_public_app_url(raw)}

# Отключаем кэширование статических файлов Flask
try:
    app.send_file_max_age_default = 0
except AttributeError:
    # Flask < 2.3
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# Настройка LoginManager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'

# Регистрация модулей (Blueprints)
app.register_blueprint(auth_bp)
app.register_blueprint(estimate_bp)

# Загрузка пользователя из сессии
@login_manager.user_loader
def load_user(user_id):
    user = fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if user:
        return User(user['id'], user['username'], user['role'], user['created_at'])
    return None

# Отключаем кэширование браузером (для всех маршрутов включая статику)
@app.after_request
def disable_cache(response):
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

# Monkey-patch для статических файлов — отключаем кэширование
def _patched_send_static_file(self, filename):
    from flask import send_from_directory
    response = send_from_directory(self.static_folder, filename)
    response.cache_control.max_age = 0
    response.cache_control.no_cache = True
    response.cache_control.no_store = True
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

Flask.send_static_file = _patched_send_static_file

# БД: не вызывать init_db() при импорте (иначе сбой psycopg2 убивает воркер — Railway healthcheck = «unavailable»).
# Первый запрос (кроме /health и статики) вызывает ensure_db_initialized(). Опционально в gunicorn: EAGER_DB_INIT=1.
_db_init_state = {"ready": False}
_db_init_lock = threading.Lock()


def ensure_db_initialized():
    """
    Идемпотентно поднять схему БД.
    Вызывается с первого «боевого» запроса (before_request) или из gunicorn post_worker_init
    до приёма соединений — иначе первый GET / на холодном воркере часто упирался в таймаут прокси (503).
    """
    with _db_init_lock:
        if _db_init_state["ready"]:
            return
        init_db()
        _backfill_object_client_links_once()
        _db_init_state["ready"] = True
        _start_startup_recalc_if_enabled()
        _log_integration_env_once()


@app.before_request
def _ensure_db_before_request():
    if _db_init_state["ready"]:
        return
    p = request.path or ""
    if p.rstrip("/") == "/health" or p == "/favicon.ico" or p.startswith("/static/"):
        return
    ensure_db_initialized()


atexit.register(close_all_connections)

# CSRF: генерация токена в сессии
def _csrf_token():
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(32)
    return session['_csrf_token']

@app.context_processor
def inject_csrf():
    return {'csrf_token': _csrf_token()}

@app.route('/api/csrf-token', methods=['GET'])
@login_required
def csrf_token_route():
    return jsonify({'csrf_token': _csrf_token()})

def require_csrf(f):
    """Декоратор для проверки CSRF-токена в заголовке X-CSRF-Token"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token')
        if not token or token != session.get('_csrf_token'):
            return jsonify({'error': 'CSRF token missing or invalid'}), 403
        return f(*args, **kwargs)
    return decorated_function


def _integration_api_key_matches() -> bool:
    """Сервис-сервис: Authorization: Bearer … или X-Integration-Key; значение из INTEGRATION_API_KEY."""
    expected = (os.environ.get('INTEGRATION_API_KEY') or '').strip()
    if not expected:
        return False
    auth = request.headers.get('Authorization', '') or ''
    if auth.lower().startswith('bearer '):
        got = auth[7:].strip()
    else:
        got = (request.headers.get('X-Integration-Key') or '').strip()
    if not got:
        return False
    return secrets.compare_digest(got, expected)


def _integration_should_reuse_client_card(data: dict | None) -> bool:
    """
    Искать существующую карточку clients по телефону/имени или всегда создавать новую для новой задачи.

    По умолчанию переиспользование включено (как раньше). Чтобы две разные задачи с одним телефоном
    не делили одну карточку клиента: INTEGRATION_REUSE_CLIENT_BY_PHONE=0 или в теле POST
    передать \"dedupe_client\": false (только при первом создании объекта; повторные вызовы
    с тем же task_id по-прежнему обновляют уже привязанного клиента).
    """
    data = data or {}
    if 'dedupe_client' in data:
        v = data.get('dedupe_client')
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        if s in ('0', 'false', 'no', 'off', ''):
            return False
        if s in ('1', 'true', 'yes', 'on'):
            return True
    raw = (os.environ.get('INTEGRATION_REUSE_CLIENT_BY_PHONE') or '1').strip().lower()
    return raw not in ('0', 'false', 'no', 'off')


def _log_integration_env_once():
    """
    Подсказка в логах Railway без «скриншотов»: частая причина 503 — не задан INTEGRATION_USER_ID.
    Вызывается один раз после инициализации БД.
    """
    if getattr(app, "_integration_env_logged", False):
        return
    app._integration_env_logged = True
    key_set = bool((os.environ.get("INTEGRATION_API_KEY") or "").strip())
    uid_raw = (os.environ.get("INTEGRATION_USER_ID") or "").strip()
    if not key_set:
        logging.warning("integration: INTEGRATION_API_KEY is not set — POST /api/integration/from-taskmgr/object will return 401")
        return
    if not uid_raw:
        logging.warning(
            "integration: INTEGRATION_USER_ID is not set — POST /api/integration/from-taskmgr/object will return 503 "
            "(укажите числовой id пользователя из таблицы users этой БД, владельца создаваемых объектов)"
        )
        return
    try:
        uid = int(uid_raw)
    except ValueError:
        logging.warning("integration: INTEGRATION_USER_ID must be an integer — got %r", uid_raw)
        return
    row = fetch_one("SELECT id FROM users WHERE id = ?", (uid,))
    if not row:
        logging.warning("integration: user id=%s from INTEGRATION_USER_ID not found in users table", uid)


# Статусы объекта: этап работ (не путать «работы сданы» с «оплачено / закрыто»).
OBJECT_STATUS_WAITING = 'Ожидает старта'
OBJECT_STATUS_ACTIVE = 'В работе'
OBJECT_STATUS_DONE = 'Выполнен'
OBJECT_STATUS_CLOSED = 'Закрыт'
OBJECT_STATUSES_SALARY = (OBJECT_STATUS_ACTIVE, OBJECT_STATUS_DONE, OBJECT_STATUS_CLOSED, 'Завершён', 'Оплачен')
OBJECT_STATUSES_FINISHED = (OBJECT_STATUS_DONE, OBJECT_STATUS_CLOSED, 'Завершён', 'Оплачен')
# До миграции БД или в копии дампа без перезапуска могли остаться старые подписи
OBJECT_STATUSES_NOT_DEBT = (OBJECT_STATUS_WAITING, 'Запланирован')

# Распределение «затраты на рабочих» по объекту (поле objects.salary_allocation_mode)
SALARY_ALLOCATION_ALL_WORKERS = 'all_workers'
SALARY_ALLOCATION_ASSIGNED_WORKERS = 'assigned_workers'
SALARY_ALLOCATION_MANUAL = 'manual'


def _norm_salary_allocation_mode(raw, fallback=SALARY_ALLOCATION_ALL_WORKERS):
    if raw is None or (isinstance(raw, str) and not str(raw).strip()):
        return fallback
    s = str(raw).strip().lower()
    if s in (SALARY_ALLOCATION_ALL_WORKERS, SALARY_ALLOCATION_ASSIGNED_WORKERS, SALARY_ALLOCATION_MANUAL):
        return s
    return fallback


def _parse_iso_date_ymd(s):
    if not s:
        return None
    s = str(s).strip()[:10]
    if len(s) < 10:
        return None
    try:
        datetime.strptime(s, '%Y-%m-%d')
        return s
    except ValueError:
        return None


def _expand_date_range_inclusive(date_start, date_end):
    """Список YYYY-MM-DD от date_start до date_end включительно (если одна дата — один день)."""
    a = _parse_iso_date_ymd(date_start)
    b = _parse_iso_date_ymd(date_end) or a
    if not a:
        return []
    if not b or b < a:
        b = a
    out = []
    cur = datetime.strptime(a, '%Y-%m-%d')
    end = datetime.strptime(b, '%Y-%m-%d')
    while cur <= end:
        out.append(cur.strftime('%Y-%m-%d'))
        cur += timedelta(days=1)
    return out


def object_touched_calendar_day(row, date_ymd):
    """Есть ли у объекта рабочий день date_ymd (YYYY-MM-DD)."""
    if not date_ymd:
        return False
    return date_ymd in object_work_days_from_row(row)


def sum_profit_for_calendar_day(objects, date_ymd):
    """Сумма total_profit по объектам, у которых в этот день есть work_dates."""
    total = 0.0
    for o in objects or []:
        if object_touched_calendar_day(o, date_ymd):
            total += float(o.get('total_profit') or 0)
    return round(total, 2)


def object_work_days_from_row(row):
    """Календарные дни работ на объекте (YYYY-MM-DD), отсортированы и уникальны."""
    if not row:
        return []
    raw = row.get('work_dates')
    if raw is not None and str(raw).strip() not in ('', '[]', 'null'):
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(parsed, list):
                days = []
                for x in parsed:
                    d = _parse_iso_date_ymd(x)
                    if d:
                        days.append(d)
                if days:
                    return sorted(set(days))
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return _expand_date_range_inclusive(row.get('date_start'), row.get('date_end'))


def _serialize_work_dates(days):
    return json.dumps(sorted(set(days)), ensure_ascii=False)


def _resolve_work_dates_bounds_for_save(data, existing=None):
    """
    Из тела запроса data и (для PUT) existing: список дней, JSON, date_start, date_end (min/max).
    Если передан work_dates — он главный; иначе разворачиваем date_start/date_end.
    """
    existing = existing or {}
    if 'work_dates' in data and data['work_dates'] is not None:
        wd = data['work_dates']
        if isinstance(wd, str):
            try:
                wd = json.loads(wd.strip() or '[]')
            except json.JSONDecodeError:
                wd = []
        if not isinstance(wd, list):
            wd = []
        days = []
        for x in wd:
            d = _parse_iso_date_ymd(x)
            if d:
                days.append(d)
        days = sorted(set(days))
        if days:
            return days, _serialize_work_dates(days), days[0], days[-1]
    ds = data.get('date_start') if 'date_start' in data else existing.get('date_start')
    de = data.get('date_end') if 'date_end' in data else existing.get('date_end')
    days = _expand_date_range_inclusive(ds, de)
    if days:
        return days, _serialize_work_dates(days), days[0], days[-1]
    return [], '[]', None, None


def _build_salary_calendar_counts(uid):
    """Для каждой календарной даты — сколько объектов (со статусами из OBJECT_STATUSES_SALARY) в эту день."""
    from collections import defaultdict

    rows = fetch_all(
        "SELECT id, work_dates, date_start, date_end, status FROM objects WHERE user_id = ?",
        (uid,),
    )
    cnt = defaultdict(int)
    for r in rows:
        if r.get('status') not in OBJECT_STATUSES_SALARY:
            continue
        for d in object_work_days_from_row(r):
            cnt[d] += 1
    return cnt


# Подотчёт рабочих: выручка у клиента, расходы, сдача
CASHBOOK_KINDS = frozenset({'client_payment', 'expense', 'handover'})
CASHBOOK_EXPENSE_CATS = frozenset({'lunch', 'fuel', 'repair', 'other'})

# Дополнительные расходы по объекту (журнал)
OBJECT_EXPENSE_CATEGORIES = frozenset({'fuel', 'subcontract', 'equipment', 'materials', 'other'})
OBJECT_EXPENSE_CATEGORY_LABELS = {
    'fuel': 'Топливо',
    'subcontract': 'Субподряд',
    'equipment': 'Аренда техники',
    'materials': 'Материалы вне сметы',
    'other': 'Прочее',
}

SETTLEMENT_TYPES = frozenset({'cash', 'cashless'})
TAX_REGIMES = frozenset({'none', 'ip', 'chpu'})
TAX_RATE_BY_REGIME = {'ip': 0.25, 'chpu': 0.17}
TAX_REGIME_LABELS = {'none': 'Без резерва', 'ip': 'ИП (25%)', 'chpu': 'ЧТУП (17%)'}


def _sql_fragment_order_objects_by_status(prefix: str) -> str:
    """Фрагмент ORDER BY: статус (этап работ), затем дата начала (новее выше), затем название."""
    p = f"{prefix}." if prefix else ""
    return (
        f"CASE {p}status "
        f"WHEN '{OBJECT_STATUS_WAITING}' THEN 0 "
        f"WHEN 'Запланирован' THEN 0 "
        f"WHEN '{OBJECT_STATUS_ACTIVE}' THEN 1 "
        f"WHEN 'Приостановлен' THEN 2 "
        f"WHEN '{OBJECT_STATUS_DONE}' THEN 3 "
        f"WHEN 'Завершён' THEN 3 "
        f"WHEN '{OBJECT_STATUS_CLOSED}' THEN 4 "
        f"WHEN 'Оплачен' THEN 4 "
        f"ELSE 99 END, {p}date_start DESC, {p}name ASC"
    )


_SQL_OBJECTS_ORDER = _sql_fragment_order_objects_by_status("o")


def _sql_objects_estimate_aggregates(as_work, as_mat, as_profit, as_mat_cost='estimate_material_cost'):
    """Общий фрагмент SELECT + JOIN для объектов с агрегатами по сметам."""
    return (
        f"SELECT o.*, "
        f"COALESCE(SUM(CASE WHEN ei.section = 'work' THEN ei.total ELSE 0 END), 0) AS {as_work}, "
        f"COALESCE(SUM(CASE WHEN ei.section = 'material' THEN ei.total ELSE 0 END), 0) AS {as_mat}, "
        f"COALESCE(SUM(CASE WHEN ei.section = 'material' THEN ei.material_profit ELSE 0 END), 0) AS {as_profit}, "
        f"COALESCE(SUM(CASE WHEN ei.section = 'material' THEN "
        f"CASE WHEN COALESCE(ei.purchase_price, 0) > 0 AND COALESCE(ei.quantity, 0) > 0 "
        f"THEN ei.purchase_price * ei.quantity "
        f"ELSE CASE WHEN COALESCE(ei.total, 0) - COALESCE(ei.material_profit, 0) > 0 "
        f"THEN COALESCE(ei.total, 0) - COALESCE(ei.material_profit, 0) ELSE 0 END "
        f"END ELSE 0 END), 0) AS {as_mat_cost} "
        "FROM objects o "
        "LEFT JOIN estimates e ON e.object_id = o.id AND e.user_id = o.user_id "
        "AND ("
        "  e.status = 'Утверждена'"
        "  OR ("
        "    e.status = 'Отправлена' AND NOT EXISTS ("
        "      SELECT 1 FROM estimates e2 WHERE e2.object_id = o.id AND e2.user_id = o.user_id AND e2.status = 'Утверждена'"
        "    )"
        "  )"
        "  OR ("
        "    e.status = 'Черновик' AND NOT EXISTS ("
        "      SELECT 1 FROM estimates e3 WHERE e3.object_id = o.id AND e3.user_id = o.user_id AND e3.status IN ('Утверждена','Отправлена')"
        "    )"
        "  )"
        ") "
        "LEFT JOIN estimate_items ei ON ei.estimate_id = e.id "
    )


def _work_revenue_once(sum_work, estimate_works):
    """Выручка по работам: sum_work подтягивается из сметы, не суммировать с estimate_works."""
    try:
        sw = float(sum_work or 0)
        ew = float(estimate_works or 0)
    except (TypeError, ValueError):
        sw, ew = 0.0, 0.0
    return max(sw, ew)


def _norm_settlement_type(value):
    s = (str(value or 'cash')).strip().lower()
    return s if s in SETTLEMENT_TYPES else 'cash'


def _norm_tax_regime(value):
    s = (str(value or 'none')).strip().lower()
    if s in ('chp', 'chtup', 'чтуп', 'чп'):
        return 'chpu'
    if s in ('ip', 'ип'):
        return 'ip'
    return s if s in TAX_REGIMES else 'none'


def _sum_extra_expenses_for_object(object_id, user_id):
    row = fetch_one(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM object_expense_entries WHERE object_id = ? AND user_id = ?",
        (object_id, user_id),
    )
    return float(row['s'] or 0) if row else 0.0


def _compute_tax_on_profit(profit_before_tax, settlement_type, tax_regime):
    st = _norm_settlement_type(settlement_type)
    tr = _norm_tax_regime(tax_regime)
    if st != 'cashless' or tr not in TAX_RATE_BY_REGIME:
        return 0.0, 0.0, round(float(profit_before_tax or 0), 2)
    rate = TAX_RATE_BY_REGIME[tr]
    pbt = float(profit_before_tax or 0)
    if pbt <= 0:
        return rate, 0.0, round(pbt, 2)
    tax_amt = round(pbt * rate, 2)
    return rate, tax_amt, round(pbt - tax_amt, 2)


def _compute_object_financials(
    sum_work,
    estimate_works,
    estimate_materials,
    estimate_material_profit,
    expenses,
    salary,
    estimate_material_cost=None,
    extra_expenses=0.0,
):
    """
    Выручка, затраты и прибыль объекта без двойного учёта материалов по смете.

    Поле objects.expenses часто равно сумме позиций материалов в смете (розница) — тому же, что
    агрегат estimate_materials (em). Старая формула делала total_expenses = expenses + em и ещё
    вычитала зарплату, а material_profit прибавляла отдельно; при expenses ≈ em это эквивалентно
    двойному вычитанию розницы материалов и занижало прибыль.

    Сейчас:
    - Выручка = max(sum_work, работы_по_смете) + розница_материалов_по_смете (em)
    - Закуп материалов: сумма по смете (purchase_price*qty), иначе em - material_profit
    - Прочие затраты из objects.expenses: сначала снимаем дубль розницы сметы (если expenses >= em),
      иначе при известном закупе — дубль закупа (если expenses >= emc); иначе expenses целиком
    - Прибыль = Выручка - закуп_мат - прочие - зарплата
    """
    try:
        ew = float(estimate_works or 0)
        em = float(estimate_materials or 0)
        emp = float(estimate_material_profit or 0)
        emc = float(estimate_material_cost or 0)
        raw_exp = float(expenses or 0)
        sal = float(salary or 0)
        extra = float(extra_expenses or 0)
    except (TypeError, ValueError):
        ew = em = emp = emc = raw_exp = sal = extra = 0.0

    work_rev = _work_revenue_once(sum_work, ew)
    total_revenue = work_rev + em
    # Для старых смет material_profit часто пустой; тогда считаем себестоимость из purchase_price*qty.
    mat_cogs = max(0.0, emc) if emc > 0 else max(0.0, em - emp)
    # Поле objects.expenses часто дублирует смету: либо розницу (em), либо закуп (emc), либо «прочее».
    if em > 0 and raw_exp + 1e-9 >= em:
        other_exp = max(0.0, raw_exp - em)
    elif emc > 0 and raw_exp + 1e-9 >= emc:
        # raw_exp < em, но покрывает закуп: иначе mat_cogs уже включает emc, а в other_exp оставался бы полный raw → двойной учёт.
        other_exp = max(0.0, raw_exp - emc)
    else:
        other_exp = raw_exp

    total_expenses = mat_cogs + other_exp + max(0.0, extra)
    total_profit = total_revenue - total_expenses - sal
    return total_revenue, total_expenses, total_profit


def _float_object_field(obj, *keys):
    """Первое числовое поле из obj по списку ключей (в т.ч. legacy est_* из SQL)."""
    for key in keys:
        if key not in obj:
            continue
        val = obj[key]
        if val is None or val == '':
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    return 0.0


def _estimate_fields_from_object(obj):
    """Агрегаты сметы с учётом legacy-имён колонок (est_works и т.д.)."""
    return (
        _float_object_field(obj, 'estimate_works', 'est_works'),
        _float_object_field(obj, 'estimate_materials', 'est_materials'),
        _float_object_field(obj, 'estimate_material_profit', 'est_mat_profit'),
        _float_object_field(obj, 'estimate_material_cost', 'est_mat_cost'),
    )


def _fetch_objects_with_financials(user_id, where_sql='', extra_params=()):
    """Объекты пользователя с агрегатами смет и полями total_revenue / total_profit / balance."""
    sql = (
        _sql_objects_estimate_aggregates(
            'estimate_works', 'estimate_materials', 'estimate_material_profit', 'estimate_material_cost',
        )
        + "WHERE o.user_id = ? " + where_sql + " GROUP BY o.id ORDER BY " + _SQL_OBJECTS_ORDER
    )
    params = (user_id,) + tuple(extra_params)
    rows = fetch_all(sql, params)
    for row in rows:
        _apply_object_financial_enrichment(row, user_id)
    return rows


def _portfolio_financial_totals(objects):
    """Сводные суммы по уже обогащённым объектам — одна логика для всех API."""
    total_revenue = 0.0
    total_expenses = 0.0
    total_profit = 0.0
    total_profit_before_tax = 0.0
    total_tax = 0.0
    total_advance = 0.0
    total_debt = 0.0
    total_mat_profit = 0.0
    total_salary = 0.0
    debt_objects = 0
    for obj in objects:
        total_revenue += float(obj.get('total_revenue') or 0)
        total_expenses += float(obj.get('total_expenses') or 0)
        total_profit += float(obj.get('total_profit') or 0)
        total_profit_before_tax += float(obj.get('profit_before_tax') or 0)
        total_tax += float(obj.get('tax_amount') or 0)
        total_advance += float(obj.get('advance') or 0)
        total_mat_profit += _float_object_field(
            obj, 'estimate_material_profit', 'est_mat_profit',
        )
        total_salary += float(obj.get('salary') or 0)
        bal = float(obj.get('balance') or 0)
        if bal > 0 and obj.get('status') not in OBJECT_STATUSES_NOT_DEBT:
            total_debt += bal
            debt_objects += 1
    return {
        'total_revenue': round(total_revenue, 2),
        'total_expenses': round(total_expenses, 2),
        'total_profit': round(total_profit, 2),
        'total_profit_before_tax': round(total_profit_before_tax, 2),
        'total_tax': round(total_tax, 2),
        'total_advance': round(total_advance, 2),
        'total_debt': round(total_debt, 2),
        'total_material_profit': round(total_mat_profit, 2),
        'total_salary': round(total_salary, 2),
        'debt_objects': debt_objects,
        'objects_total': len(objects),
    }


def _clients_map_for_user(user_id):
    """id → каноническое имя из справочника clients."""
    rows = fetch_all(
        'SELECT id, name FROM clients WHERE user_id = ?',
        (user_id,),
    )
    out = {}
    for row in rows:
        try:
            cid = int(row['id'])
        except (TypeError, ValueError):
            continue
        out[cid] = (row.get('name') or '').strip()
    return out


def _client_group_for_object(obj, clients_map):
    """
    Группировка заказчика: сначала client_id (справочник), иначе текст objects.client.
    Возвращает (key, display_name); key — уникальный ключ для агрегатов.
    """
    raw_id = obj.get('client_id')
    cid = None
    try:
        if raw_id not in (None, '', 0, '0'):
            cid = int(raw_id)
    except (TypeError, ValueError):
        cid = None
    if cid is not None:
        label = clients_map.get(cid) or (obj.get('client') or '').strip() or f'Клиент #{cid}'
        return ('id', cid), label
    name = (obj.get('client') or '').strip()
    if name:
        return ('name', name.casefold()), name
    return ('name', ''), 'Без клиента'


def _apply_object_financial_enrichment(obj, user_id):
    """Добавляет в dict объекта выручку, затраты, доп. расходы, налог (безнал) и прибыль после налога."""
    oid = obj.get('id')
    extra = _sum_extra_expenses_for_object(oid, user_id) if oid else 0.0
    ew, em, emp, emc = _estimate_fields_from_object(obj)
    tr, te, tp = _compute_object_financials(
        obj.get('sum_work'),
        ew,
        em,
        emp,
        obj.get('expenses'),
        obj.get('salary'),
        emc,
        extra_expenses=extra,
    )
    obj['extra_expenses'] = round(extra, 2)
    obj['total_revenue'] = tr
    obj['total_expenses'] = te
    obj['profit_before_tax'] = round(tp, 2)
    rate, tax_amt, profit_after = _compute_tax_on_profit(
        tp, obj.get('settlement_type'), obj.get('tax_regime'),
    )
    obj['tax_rate'] = rate
    obj['tax_amount'] = tax_amt
    obj['total_profit'] = profit_after
    obj['balance'] = round(tr - float(obj.get('advance', 0) or 0), 2)
    st = _norm_settlement_type(obj.get('settlement_type'))
    trg = _norm_tax_regime(obj.get('tax_regime'))
    obj['settlement_type'] = st
    obj['tax_regime'] = trg


def _object_client_fields_from_payload(user_id, data, existing=None):
    """Имя клиента и client_id для сохранения объекта (различение одноимённых клиентов)."""
    existing = existing or {}
    ex_cli = existing.get('client_id')
    ex_name = existing.get('client') or ''

    if 'client_id' in data:
        raw = data.get('client_id')
        if raw is None or raw == '':
            # client_id: null без поля client — частичное обновление (статус и т.п.), не сбрасывать заказчика
            if 'client' not in data:
                return (str(ex_name).strip(), ex_cli)
            name = str(data.get('client') or '').strip()
            if not name:
                return ('', None)
            return (name, None)
        try:
            cid = int(raw)
        except (TypeError, ValueError):
            cid = None
        if cid:
            row = fetch_one("SELECT name FROM clients WHERE id = ? AND user_id = ?", (cid, user_id))
            if row:
                return (str(row['name'] or '').strip(), cid)
            if 'client' not in data:
                return (str(ex_name).strip(), ex_cli)
        name = data.get('client') if 'client' in data else ex_name
        return (str(name or '').strip(), None)

    if 'client' in data:
        name = str(data.get('client') or '').strip()
        return (name, ex_cli)

    return (str(ex_name).strip(), ex_cli)


def _backfill_object_client_links_once():
    """
    Дозаполняет старые objects.client_id и выравнивает objects.client по связанному clients.name.
    Привязка выполняется только при однозначном совпадении имени клиента.
    """
    if getattr(app, "_object_client_backfill_done", False):
        return
    app._object_client_backfill_done = True
    try:
        users = fetch_all("SELECT id FROM users")
        linked = 0
        normalized = 0
        ambiguous = 0
        for u in users:
            uid = u.get('id')
            if uid is None:
                continue
            cards = fetch_all("SELECT id, name FROM clients WHERE user_id = ?", (uid,))
            by_name = {}
            for c in cards:
                key = str(c.get('name') or '').strip().lower()
                if not key:
                    continue
                by_name.setdefault(key, []).append(c)

            # Объекты без client_id: привязка только если имя однозначно и ровно ОДИН объект
            # с таким текстом «Заказчик» (иначе массово вешаем одного Жигалко/и т.п. на чужие объекты).
            wo_link = fetch_all(
                "SELECT id, client FROM objects WHERE user_id = ? AND (client_id IS NULL OR client_id = 0) AND COALESCE(client, '') <> ''",
                (uid,),
            )
            text_obj_count = {}
            for o in wo_link:
                key = str(o.get('client') or '').strip().lower()
                if key:
                    text_obj_count[key] = text_obj_count.get(key, 0) + 1
            for o in wo_link:
                key = str(o.get('client') or '').strip().lower()
                if not key:
                    continue
                if text_obj_count.get(key, 0) != 1:
                    continue
                variants = by_name.get(key) or []
                if len(variants) == 1:
                    c = variants[0]
                    execute(
                        "UPDATE objects SET client_id = ?, client = ? WHERE id = ? AND user_id = ?",
                        (c.get('id'), c.get('name'), o.get('id'), uid),
                    )
                    linked += 1
                elif len(variants) > 1:
                    ambiguous += 1

            # Объекты со связью: текстовое поле client держим синхронно с карточкой клиента.
            mismatched = fetch_all(
                """
                SELECT o.id AS object_id, c.name AS client_name
                FROM objects o
                JOIN clients c ON c.id = o.client_id AND c.user_id = o.user_id
                WHERE o.user_id = ? AND COALESCE(o.client, '') <> COALESCE(c.name, '')
                """,
                (uid,),
            )
            for row in mismatched:
                execute(
                    "UPDATE objects SET client = ? WHERE id = ? AND user_id = ?",
                    (row.get('client_name'), row.get('object_id'), uid),
                )
                normalized += 1

        logging.info(
            "object-client backfill: linked=%s normalized=%s ambiguous=%s",
            linked,
            normalized,
            ambiguous,
        )
    except Exception:
        logging.exception("object-client backfill failed")


def _normalize_phone_digits(phone):
    if not phone:
        return ''
    return ''.join(c for c in str(phone) if c.isdigit())


def _integration_parse_money(value):
    """Мягкий разбор суммы из строки/числа: '1 234,56' -> 1234.56."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    s = str(value).strip()
    if not s:
        return None
    s = s.replace(' ', '').replace(',', '.')
    m = re.search(r'-?\d+(?:\.\d+)?', s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except (TypeError, ValueError):
        return None


def _integration_client_card_name(contact: str, company: str) -> str:
    """
    Имя карточки клиента для интеграции.
    Приоритет: контакт (ФИО) -> компания -> заглушка.
    Не склеиваем "ФИО — Компания", чтобы поле `clients.name` оставалось именно именем контакта.
    """
    c = (contact or '').strip()
    co = (company or '').strip()
    if c:
        return c
    if co:
        return co
    return '—'


def _integration_find_or_create_client(
    target_user_id, card_name: str, phone: str, address: str, *, reuse_existing: bool = True
):
    """
    Карточка в таблице clients: создать или найти по телефону (7+ цифр) / по точному имени карточки.
    При совпадении дополняем пустые телефон, адрес и имя (если было «—» или пусто).

    Если reuse_existing=False — сразу новая строка clients (отдельная карточка на каждую задачу
    диспетчера при том же заказчике).
    """
    phone = str(phone or '').strip()
    address = str(address or '').strip()
    if not reuse_existing:
        cid = execute(
            "INSERT INTO clients (user_id, name, phone, email, address) VALUES (?, ?, ?, '', ?)",
            (target_user_id, card_name, phone, address),
            return_id=True,
        )
        return cid, card_name
    norm = _normalize_phone_digits(phone)
    rows = fetch_all('SELECT * FROM clients WHERE user_id = ?', (target_user_id,))
    if norm and len(norm) >= 7:
        for r in rows:
            if _normalize_phone_digits(r.get('phone')) == norm:
                cid = r['id']
                ex_name = (r.get('name') or '').strip()
                ex_phone = (r.get('phone') or '').strip()
                ex_addr = (r.get('address') or '').strip()
                # Миграция "на лету": раньше имя могло записываться как "ФИО — Компания".
                # Если пришёл нормальный контакт и видим старую склейку, заменяем на контакт.
                # Не перезаписываем имя карточки при совпадении только телефона — иначе чужие
                # объекты с тем же client_id визуально «становятся» другим заказчиком.
                should_replace_name = (
                    not ex_name
                    or ex_name == '—'
                    or ('—' in ex_name and card_name and card_name != '—')
                )
                new_name = card_name if should_replace_name else ex_name
                new_phone = phone or ex_phone
                new_addr = address or ex_addr
                execute(
                    'UPDATE clients SET name=?, phone=?, address=? WHERE id=? AND user_id=?',
                    (new_name, new_phone, new_addr, cid, target_user_id),
                )
                return cid, new_name
    if card_name and card_name != '—':
        for r in rows:
            if (r.get('name') or '').strip() == card_name:
                cid = r['id']
                ex_phone = (r.get('phone') or '').strip()
                ex_addr = (r.get('address') or '').strip()
                new_phone = phone or ex_phone
                new_addr = address or ex_addr
                if (phone and phone != ex_phone) or (address and address != ex_addr):
                    execute(
                        'UPDATE clients SET phone=?, address=? WHERE id=? AND user_id=?',
                        (new_phone, new_addr, cid, target_user_id),
                    )
                return cid, card_name
    cid = execute(
        "INSERT INTO clients (user_id, name, phone, email, address) VALUES (?, ?, ?, '', ?)",
        (target_user_id, card_name, phone, address),
        return_id=True,
    )
    return cid, card_name


def _dispatcher_api_base_url() -> str:
    v = (os.environ.get('DISPATCHER_API_BASE_URL') or os.environ.get('DISPATCHER_API_URL') or '').strip().rstrip('/')
    return v


def _dispatcher_profile_by_phone(client_id: str, phone: str):
    """
    Прочитать профиль клиента из Диспетчера по телефону:
    GET /v1/integration/object-accounting/clients/:clientId/customer-profiles/by-phone?phone=...
    """
    base_url = _dispatcher_api_base_url()
    key = (os.environ.get('INTEGRATION_API_KEY') or '').strip()
    digits = _normalize_phone_digits(phone)
    if not base_url or not key or not client_id or not digits:
        return None
    url = (
        f"{base_url}/v1/integration/object-accounting/clients/"
        f"{urllib.parse.quote(str(client_id).strip(), safe='')}/customer-profiles/by-phone"
        f"?phone={urllib.parse.quote(str(phone), safe='')}"
    )
    req = urllib.request.Request(
        url=url,
        headers={
            'Authorization': f'Bearer {key}',
            'Accept': 'application/json',
        },
        method='GET',
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode('utf-8', errors='replace')
            data = json.loads(body) if body else {}
            profile = data.get('profile') if isinstance(data, dict) else None
            return profile if isinstance(profile, dict) else None
    except Exception:
        logging.exception("integration: dispatcher profile GET failed")
        return None


def _dispatcher_upsert_profile(client_id: str, payload: dict):
    """
    Обновить профиль клиента в Диспетчере:
    PUT /v1/integration/object-accounting/clients/:clientId/customer-profiles/by-phone
    """
    base_url = _dispatcher_api_base_url()
    key = (os.environ.get('INTEGRATION_API_KEY') or '').strip()
    if not base_url or not key or not client_id:
        return
    customer_phone = str(payload.get('customerPhone') or '').strip()
    if not _normalize_phone_digits(customer_phone):
        return
    url = (
        f"{base_url}/v1/integration/object-accounting/clients/"
        f"{urllib.parse.quote(str(client_id).strip(), safe='')}/customer-profiles/by-phone"
    )
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        url=url,
        data=body,
        headers={
            'Authorization': f'Bearer {key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
        method='PUT',
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            return
    except Exception:
        logging.exception("integration: dispatcher profile PUT failed")
        return


# Глобальный обработчик ошибок для API
@app.errorhandler(500)
def handle_500(e):
    import logging
    logging.error(f"Internal server error: {e}")
    # Если запрос к API — возвращаем JSON
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Internal server error'}), 500
    # Иначе — стандартная страница ошибки
    return str(e), 500

@app.route('/favicon.ico')
def favicon():
    return '', 204

@app.route('/offline')
def offline_page():
    return render_template('offline.html')

@app.route('/service-worker.js')
def service_worker():
    return send_file('static/service-worker.js', mimetype='application/javascript')

@app.route('/static/manifest.json')
def manifest():
    return send_file('static/manifest.json', mimetype='application/json')

@app.route('/health')
def health_check():
    try:
        from well_passport import libreoffice_pdf_available
        pdf_ok = libreoffice_pdf_available()
    except Exception:
        pdf_ok = False
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "passport_pdf": pdf_ok,
    }), 200


@app.route('/api/backup-database')
@login_required
def backup_database():
    """Скачать файл SQLite (только локальный режим без PostgreSQL)."""
    if IS_POSTGRES:
        return jsonify({"error": "Резервная копия файла доступна только в режиме SQLite"}), 400
    db_path = os.environ.get('DB_FILE')
    if not db_path:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app_data.db')
    if not os.path.isfile(db_path):
        return jsonify({"error": "Файл базы не найден"}), 404
    return send_file(db_path, as_attachment=True, download_name='app_data_backup.db', mimetype='application/octet-stream')

# ============================
# СТРАНИЦЫ ИНТЕРФЕЙСА
# ============================
@app.route('/')
@login_required
def index():
    return render_template('objects/index.html')

@app.route('/clients')
@login_required
def clients_page():
    return render_template('clients/index.html')

@app.route('/stats')
@login_required
def stats_page():
    return render_template('stats/index.html')

@app.route('/debts')
@login_required
def debts_page():
    return render_template('debts/index.html')

@app.route('/profit')
@login_required
def profit_page():
    return render_template('profit/index.html')

@app.route('/report')
@login_required
def report_page():
    return render_template('report/index.html')

@app.route('/workers')
@login_required
def workers_page():
    return render_template('workers/index.html')

# ============================
# API: УПРАВЛЕНИЕ АККАУНТОМ
# ============================

@app.route('/api/user/change-password', methods=['POST'])
@login_required
@require_csrf
def change_password():
    data = request.json
    if not data:
        return jsonify({"error": "Нет данных"}), 400
    user = fetch_one("SELECT * FROM users WHERE id = ?", (current_user.id,))

    if not user or not check_pw(data.get('old_password'), user['password_hash']):
        return jsonify({"error": "Неверный текущий пароль"}), 400
        
    execute("UPDATE users SET password_hash = ? WHERE id = ?", 
            (hash_pw(data.get('new_password')), current_user.id))
    return jsonify({"ok": True})

@app.route('/api/user/delete', methods=['POST'])
@login_required
@require_csrf
def delete_account():
    data = request.json
    if not data:
        return jsonify({"error": "Нет данных"}), 400
    user = fetch_one("SELECT * FROM users WHERE id = ?", (current_user.id,))
    if not user or not check_pw(data.get('password'), user['password_hash']):
        return jsonify({"error": "Неверный пароль"}), 400
    # Удаляем пользователя и его данные
    execute("DELETE FROM users WHERE id = ?", (current_user.id,))
    logout_user()
    return jsonify({"ok": True})

# ============================
# API: ОБЪЕКТЫ
# ============================

@app.route('/api/objects-with-estimates', methods=['GET'])
@login_required
def get_objects_with_estimates():
    # ОДИН запрос с LEFT JOIN вместо N+1
    objects = _fetch_objects_with_financials(current_user.id)

    # Подсчёт по статусам
    in_progress = sum(1 for o in objects if o.get('status') == OBJECT_STATUS_ACTIVE)
    completed = sum(1 for o in objects if o.get('status') in OBJECT_STATUSES_FINISHED)
    today_str = datetime.now().strftime('%Y-%m-%d')
    today_profit = sum_profit_for_calendar_day(objects, today_str)
    today_objects = sum(1 for o in objects if object_touched_calendar_day(o, today_str))

    return jsonify({
        'objects': objects,
        'total': len(objects),
        'in_progress': in_progress,
        'completed': completed,
        'today_profit': today_profit,
        'today_objects': today_objects,
        'today_date': today_str,
        'page': 1,
        'per_page': 50,
        'total_pages': 1
    })

@app.route('/api/objects', methods=['GET'])
@login_required
def get_objects():
    order = _sql_fragment_order_objects_by_status("")
    objects = fetch_all(f"SELECT * FROM objects WHERE user_id = ? ORDER BY {order}", (current_user.id,))
    return jsonify(objects)

@app.route('/api/objects', methods=['POST'])
@login_required
@require_csrf
def add_object():
    data = request.json or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({"error": "Укажите название объекта"}), 400
    client_name, client_id = _object_client_fields_from_payload(current_user.id, data, None)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    mode_ins = _norm_salary_allocation_mode(data.get('salary_allocation_mode'))
    is_regular_to = 1 if data.get('is_regular_to') else 0
    next_to_date = (data.get('next_to_date') or '').strip() or None
    next_to_note = (data.get('next_to_note') or '').strip() or None
    _days, work_dates_json, ds_b, de_b = _resolve_work_dates_bounds_for_save(data, None)
    settlement_ins = _norm_settlement_type(data.get('settlement_type'))
    tax_ins = _norm_tax_regime(data.get('tax_regime'))
    oid = execute("""INSERT INTO objects 
        (user_id, date_start, date_end, work_dates, name, client, client_id, sum_work, expenses, status, advance, salary, notes, is_regular_to, next_to_date, next_to_note, created_at, updated_at, salary_allocation_mode, settlement_type, tax_regime) 
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", 
        (current_user.id, ds_b, de_b, work_dates_json, name,
         client_name, client_id, data.get('sum_work', 0), data.get('expenses', 0), data.get('status'),
         data.get('advance', 0), data.get('salary', 0), data.get('notes'),
         is_regular_to, next_to_date, next_to_note, now, now, mode_ins, settlement_ins, tax_ins), return_id=True)
    _recalc_salaries_for_user_id(current_user.id)
    obj = fetch_one("SELECT * FROM objects WHERE id = ? AND user_id = ?", (oid, current_user.id))
    if obj:
        _apply_object_financial_enrichment(obj, current_user.id)
    return jsonify(obj), 201


@app.route('/api/integration/from-taskmgr/object', methods=['POST'])
def integration_create_object_from_taskmgr():
    """
    Создать объект по событию из диспетчера задач (без браузерной сессии).
    Окружение: INTEGRATION_API_KEY, INTEGRATION_USER_ID — id пользователя в этой БД (владелец данных).
    Повтор с тем же task_id не создаёт дубликат (поле integration_source).

    Разные задачи — разные объекты учёта (ключ task_id). Один заказчик может иметь общую карточку
    clients или отдельные: см. INTEGRATION_REUSE_CLIENT_BY_PHONE и поле dedupe_client в JSON.

    Для уже существующего объекта: поле advance_delta (число, BYN) прибавляется к advance
    (поступления/сдача из диспетчера «выручка на руках» с привязкой к задаче).
    """
    try:
        if not _integration_api_key_matches():
            return jsonify({'error': 'Unauthorized'}), 401
        uid_raw = (os.environ.get('INTEGRATION_USER_ID') or '').strip()
        if not uid_raw:
            logging.warning("integration: rejected request — INTEGRATION_USER_ID is not configured")
            return jsonify({'error': 'INTEGRATION_USER_ID is not configured'}), 503
        try:
            target_user_id = int(uid_raw)
        except ValueError:
            logging.warning("integration: rejected request — INTEGRATION_USER_ID must be an integer (got %r)", uid_raw)
            return jsonify({'error': 'INTEGRATION_USER_ID must be an integer'}), 503
        user_row = fetch_one('SELECT id FROM users WHERE id = ?', (target_user_id,))
        if not user_row:
            logging.warning(
                "integration: rejected request — user id=%s from INTEGRATION_USER_ID not found", target_user_id
            )
            return jsonify({'error': 'User for INTEGRATION_USER_ID not found'}), 503

        data = request.json or {}
        task_id = (data.get('task_id') or '').strip()
        if not task_id:
            return jsonify({'error': 'task_id is required'}), 400
        business_client_id = str(data.get('business_client_id') or '').strip()
        reuse_client_card = _integration_should_reuse_client_card(data)
        advance_payload = _integration_parse_money(data.get('advance'))
        advance_delta_payload = _integration_parse_money(data.get('advance_delta'))
        source_key = f'taskmgr:{task_id}'
        existing = fetch_one(
            'SELECT * FROM objects WHERE user_id = ? AND integration_source = ?',
            (target_user_id, source_key),
        )
        if existing:
            # Повторный вызов: дополняем клиента и привязку, если в диспетчере уже появились контакты
            # (первый синк часто без body — объект создался только с названием).
            contact_in = (data.get('contact_name') or data.get('client') or '').strip()
            if contact_in in ('—', '-', '–'):
                contact_in = ''
            company_in = (data.get('company_name') or '').strip()
            date_start_in = (data.get('date_start') or '').strip()
            phone = str(data.get('customer_phone') or '').strip()
            address = str(data.get('object_address') or '').strip()
            if phone and business_client_id:
                remote = _dispatcher_profile_by_phone(business_client_id, phone) or {}
                if not contact_in:
                    contact_in = str(remote.get('contactName') or '').strip()
                if not address:
                    address = str(remote.get('objectAddress') or '').strip()
            has_payload = bool(
                contact_in
                or company_in
                or phone
                or address
                or date_start_in
                or (data.get('date_end') or '').strip()
                or ('work_dates' in data and data.get('work_dates') is not None)
                or advance_payload is not None
                or advance_delta_payload is not None
            )
            contacts_changed = bool(contact_in or company_in or phone or address)
            if contacts_changed:
                card_name = _integration_client_card_name(contact_in, company_in)
                client_id, client_name = _integration_find_or_create_client(
                    target_user_id, card_name, phone, address, reuse_existing=reuse_client_card
                )
            else:
                client_id = existing.get('client_id')
                client_name = (existing.get('client') or '').strip()
                if client_id:
                    row_c = fetch_one(
                        'SELECT name FROM clients WHERE id = ? AND user_id = ?',
                        (client_id, target_user_id),
                    )
                    if row_c and row_c.get('name'):
                        client_name = str(row_c['name']).strip()
            ex_client = (existing.get('client') or '').strip()
            ex_client_id = existing.get('client_id')
            mismatch = contacts_changed and (
                (ex_client_id != client_id) or (ex_client != (client_name or '').strip())
            )
            if not has_payload and not mismatch:
                return jsonify({'ok': True, 'idempotent': True, 'object': existing}), 200
            name_new = (data.get('name') or '').strip() or (existing.get('name') or '')
            integ_dates = {}
            if 'work_dates' in data and data.get('work_dates') is not None:
                integ_dates['work_dates'] = data['work_dates']
            if date_start_in:
                integ_dates['date_start'] = date_start_in
            elif existing.get('date_start'):
                integ_dates['date_start'] = existing.get('date_start')
            de_in = (data.get('date_end') or '').strip()
            if de_in:
                integ_dates['date_end'] = de_in
            elif existing.get('date_end'):
                integ_dates['date_end'] = existing.get('date_end')
            _wdays, work_dates_json, ds_b, de_b = _resolve_work_dates_bounds_for_save(integ_dates, existing)
            if advance_delta_payload is not None:
                try:
                    base_adv = float(existing.get('advance') or 0)
                except (TypeError, ValueError):
                    base_adv = 0.0
                advance_new = base_adv + float(advance_delta_payload)
            elif advance_payload is not None:
                advance_new = advance_payload
            else:
                try:
                    advance_new = float(existing.get('advance') or 0)
                except (TypeError, ValueError):
                    advance_new = 0.0
            execute(
                'UPDATE objects SET name=?, date_start=?, date_end=?, work_dates=?, client=?, client_id=?, advance=? WHERE id=? AND user_id=?',
                (name_new, ds_b, de_b, work_dates_json, client_name, client_id, advance_new, existing['id'], target_user_id),
            )
            if business_client_id and phone:
                _dispatcher_upsert_profile(
                    business_client_id,
                    {
                        'customerPhone': phone,
                        'contactName': client_name,
                        'objectAddress': address,
                        'startAt': ds_b or None,
                        'dueAt': de_b or None,
                    },
                )
            try:
                _recalc_salaries_for_user_id(target_user_id)
            except Exception:
                pass
            obj = fetch_one('SELECT * FROM objects WHERE id=? AND user_id=?', (existing['id'], target_user_id))
            return jsonify({'ok': True, 'idempotent': True, 'object': obj, 'synced': True}), 200

        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({'error': 'name is required'}), 400

        contact_in = (data.get('contact_name') or data.get('client') or '').strip()
        if contact_in in ('—', '-', '–'):
            contact_in = ''
        company_in = (data.get('company_name') or '').strip()
        card_name = _integration_client_card_name(contact_in, company_in)
        phone = str(data.get('customer_phone') or '').strip()
        address = str(data.get('object_address') or '').strip()
        if phone and business_client_id:
            remote = _dispatcher_profile_by_phone(business_client_id, phone) or {}
            if not contact_in:
                contact_in = str(remote.get('contactName') or '').strip()
            if not address:
                address = str(remote.get('objectAddress') or '').strip()
            card_name = _integration_client_card_name(contact_in, company_in)
        client_id, client_name = _integration_find_or_create_client(
            target_user_id, card_name, phone, address, reuse_existing=reuse_client_card
        )
        status_val = data.get('status')
        if status_val is not None and str(status_val).strip():
            obj_status = str(status_val).strip()
        else:
            obj_status = OBJECT_STATUS_WAITING
        if advance_delta_payload is not None:
            advance_new = float(advance_delta_payload)
        elif advance_payload is not None:
            advance_new = advance_payload
        else:
            advance_new = 0

        extra_notes = (data.get('notes') or '').strip()
        line = f'Задача диспетчера: {task_id}'
        notes = f'{extra_notes}\n{line}'.strip() if extra_notes else line

        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        _wdays_i, work_dates_json_i, ds_i, de_i = _resolve_work_dates_bounds_for_save(data, None)
        oid = execute(
            """INSERT INTO objects
            (user_id, date_start, date_end, work_dates, name, client, client_id, sum_work, expenses, status, advance, salary, notes, created_at, updated_at, integration_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                target_user_id,
                ds_i,
                de_i,
                work_dates_json_i,
                name,
                client_name,
                client_id,
                data.get('sum_work', 0),
                data.get('expenses', 0),
                obj_status,
                advance_new,
                data.get('salary', 0),
                notes,
                now,
                now,
                source_key,
            ),
            return_id=True,
        )
        try:
            _recalc_salaries_for_user_id(target_user_id)
        except Exception:
            pass
        obj = fetch_one('SELECT * FROM objects WHERE id = ? AND user_id = ?', (oid, target_user_id))
        if business_client_id and phone:
            _dispatcher_upsert_profile(
                business_client_id,
                {
                    'customerPhone': phone,
                    'contactName': client_name,
                    'objectAddress': address,
                    'startAt': ds_i,
                    'dueAt': de_i,
                },
            )
        return jsonify({'ok': True, 'idempotent': False, 'object': obj}), 201
    except Exception as exc:
        logging.exception("integration: unhandled error in /api/integration/from-taskmgr/object")
        return jsonify({'error': 'Internal error', 'detail': str(exc)}), 500


@app.route('/api/objects/<int:obj_id>', methods=['PUT'])
@login_required
@require_csrf
def update_object(obj_id):
    data = request.json or {}
    ex = fetch_one("SELECT * FROM objects WHERE id = ? AND user_id = ?", (obj_id, current_user.id))
    if not ex:
        return jsonify({"error": "Not found"}), 404

    def pick(key):
        return data[key] if key in data else ex.get(key)

    def pickf(key, default=0.0):
        if key not in data:
            v = ex.get(key)
            try:
                return float(v) if v is not None else default
            except (TypeError, ValueError):
                return default
        v = data[key]
        try:
            return float(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    name = pick('name')
    if name is not None:
        name = str(name).strip()
    if not name:
        return jsonify({"error": "Укажите название объекта"}), 400

    client_name, client_id = _object_client_fields_from_payload(current_user.id, data, ex)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    mode_out = _norm_salary_allocation_mode(
        data['salary_allocation_mode'] if 'salary_allocation_mode' in data else ex.get('salary_allocation_mode'),
    )
    def pick_bool01(key):
        if key not in data:
            return 1 if ex.get(key) else 0
        return 1 if data.get(key) else 0

    date_payload = {k: data[k] for k in ('work_dates', 'date_start', 'date_end') if k in data}
    _days, work_dates_json, ds_b, de_b = _resolve_work_dates_bounds_for_save(date_payload, ex)

    settlement_out = _norm_settlement_type(
        data['settlement_type'] if 'settlement_type' in data else ex.get('settlement_type'),
    )
    tax_out = _norm_tax_regime(
        data['tax_regime'] if 'tax_regime' in data else ex.get('tax_regime'),
    )

    execute("""UPDATE objects SET date_start=?, date_end=?, work_dates=?, name=?, client=?, client_id=?, sum_work=?,
           expenses=?, status=?, advance=?, salary=?, notes=?, is_regular_to=?, next_to_date=?, next_to_note=?, salary_allocation_mode=?, settlement_type=?, tax_regime=?, updated_at=? WHERE id=? AND user_id=?""",
        (ds_b, de_b, work_dates_json, name, client_name, client_id,
         pickf('sum_work'), pickf('expenses'), pick('status'), pickf('advance'),
         pickf('salary'), pick('notes'),
         pick_bool01('is_regular_to'),
         pick('next_to_date'),
         pick('next_to_note'),
         mode_out, settlement_out, tax_out, now, obj_id, current_user.id))

    if any(k in data for k in ('status', 'date_start', 'date_end', 'work_dates', 'salary_allocation_mode')):
        _recalc_salaries_for_user_id(current_user.id)

    row = fetch_one("SELECT * FROM objects WHERE id = ? AND user_id = ?", (obj_id, current_user.id))
    if row:
        obj_out = dict(row)
        _apply_object_financial_enrichment(obj_out, current_user.id)
        return jsonify(obj_out)
    return jsonify({"ok": True})

@app.route('/api/objects/<int:obj_id>', methods=['DELETE'])
@login_required
@require_csrf
def delete_object(obj_id):
    # Получаем все сметы объекта
    estimates = fetch_all("SELECT id FROM estimates WHERE object_id = ? AND user_id = ?", (obj_id, current_user.id))

    # Формируем запросы для единой транзакции
    queries = []
    for est in estimates:
        queries.append(("DELETE FROM estimate_items WHERE estimate_id = ?", (est['id'],)))
    queries.append(("DELETE FROM estimates WHERE object_id = ? AND user_id = ?", (obj_id, current_user.id)))
    queries.append(("UPDATE worker_account_entries SET object_id = NULL WHERE object_id = ? AND user_id = ?", (obj_id, current_user.id)))
    queries.append(("DELETE FROM object_expense_entries WHERE object_id = ? AND user_id = ?", (obj_id, current_user.id)))
    queries.append(("DELETE FROM objects WHERE id = ? AND user_id = ?", (obj_id, current_user.id)))

    # Выполняем всё в одной транзакции
    execute_many(queries)
    _recalc_salaries_for_user_id(current_user.id)
    return jsonify({"ok": True})

# ============================
# API: РАБОЧИЕ
# ============================

@app.route('/api/workers', methods=['GET'])
@login_required
def get_workers():
    return jsonify(fetch_all("SELECT * FROM workers WHERE user_id = ? ORDER BY full_name", (current_user.id,)))

@app.route('/api/workers', methods=['POST'])
@login_required
@require_csrf
def add_worker():
    data = request.json
    wid = execute("""INSERT INTO workers 
        (user_id, full_name, phone, daily_rate, hire_date, notes, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (current_user.id, data.get('full_name', ''), data.get('phone', ''),
         data.get('daily_rate', 300), data.get('hire_date', ''), data.get('notes', ''), 1), return_id=True)
    _recalc_salaries_for_user_id(current_user.id)
    return jsonify(fetch_one("SELECT * FROM workers WHERE id = ? AND user_id = ?", (wid, current_user.id))), 201

@app.route('/api/workers/<int:worker_id>', methods=['PUT'])
@login_required
@require_csrf
def update_worker(worker_id):
    data = request.json
    execute("""UPDATE workers SET full_name=?, phone=?, daily_rate=?, hire_date=?, notes=?, is_active=?
               WHERE id=? AND user_id=?""",
        (data.get('full_name'), data.get('phone'), data.get('daily_rate', 300),
         data.get('hire_date', ''), data.get('notes', ''), data.get('is_active', 1),
         worker_id, current_user.id))
    w = fetch_one("SELECT * FROM workers WHERE id = ? AND user_id = ?", (worker_id, current_user.id))
    if not w:
        return jsonify({"error": "Не найдено"}), 404
    _recalc_salaries_for_user_id(current_user.id)
    return jsonify(w)

@app.route('/api/workers/<int:worker_id>', methods=['DELETE'])
@login_required
@require_csrf
def delete_worker(worker_id):
    # Сначала назначения (при включённых FK в PostgreSQL иначе нарушение ссылки)
    execute("DELETE FROM worker_account_entries WHERE worker_id = ? AND user_id = ?", (worker_id, current_user.id))
    execute("DELETE FROM worker_assignments WHERE worker_id = ? AND user_id = ?", (worker_id, current_user.id))
    execute("DELETE FROM workers WHERE id = ? AND user_id = ?", (worker_id, current_user.id))
    _recalc_salaries_for_user_id(current_user.id)
    return jsonify({"ok": True})


@app.route('/api/workers/cashbook-balances', methods=['GET'])
@login_required
def get_workers_cashbook_balances():
    """Сводный остаток по каждому рабочему (получено − расходы − сдача)."""
    rows = fetch_all(
        """
        SELECT worker_id,
               SUM(CASE WHEN entry_kind = 'client_payment' THEN amount ELSE -amount END) AS balance
        FROM worker_account_entries
        WHERE user_id = ?
        GROUP BY worker_id
        """,
        (current_user.id,),
    )
    out = []
    for r in rows:
        out.append(
            {
                "worker_id": r["worker_id"],
                "balance": round(float(r["balance"] or 0), 2),
            }
        )
    return jsonify(out)


@app.route('/api/workers/<int:worker_id>/cashbook', methods=['GET'])
@login_required
def get_worker_cashbook(worker_id):
    """Журнал подотчёта: получено от клиентов, расходы, сдача. Остаток = «на руках / к сдаче»."""
    w = fetch_one(
        "SELECT id, full_name FROM workers WHERE id = ? AND user_id = ?",
        (worker_id, current_user.id),
    )
    if not w:
        return jsonify({"error": "Не найдено"}), 404
    rows = fetch_all(
        """
        SELECT e.id, e.worker_id, e.object_id, e.entry_kind, e.expense_category, e.amount, e.entry_date, e.note, e.created_at,
               o.name AS object_name
        FROM worker_account_entries e
        LEFT JOIN objects o ON o.id = e.object_id AND o.user_id = e.user_id
        WHERE e.worker_id = ? AND e.user_id = ?
        ORDER BY e.entry_date ASC, e.id ASC
        """,
        (worker_id, current_user.id),
    )
    balance = 0.0
    out = []
    for r in rows:
        rd = dict(r)
        amt = float(rd["amount"] or 0)
        if rd.get("entry_kind") == "client_payment":
            balance += amt
        else:
            balance -= amt
        rd["balance_after"] = round(balance, 2)
        out.append(rd)
    return jsonify({"worker": dict(w), "balance": round(balance, 2), "entries": out})


@app.route('/api/workers/<int:worker_id>/cashbook', methods=['POST'])
@login_required
@require_csrf
def add_worker_cashbook_entry(worker_id):
    w = fetch_one("SELECT id FROM workers WHERE id = ? AND user_id = ?", (worker_id, current_user.id))
    if not w:
        return jsonify({"error": "Рабочий не найден"}), 404
    data = request.json or {}
    kind = (data.get("entry_kind") or "").strip()
    if kind not in CASHBOOK_KINDS:
        return jsonify({"error": "Неверный тип операции"}), 400
    try:
        amount = float(data.get("amount"))
    except (TypeError, ValueError):
        return jsonify({"error": "Укажите сумму"}), 400
    if amount <= 0:
        return jsonify({"error": "Сумма должна быть больше 0"}), 400
    exp_cat = (data.get("expense_category") or "other").strip()
    if kind == "expense":
        if exp_cat not in CASHBOOK_EXPENSE_CATS:
            exp_cat = "other"
    else:
        exp_cat = ""
    entry_date = (data.get("entry_date") or "").strip() or datetime.now().strftime("%Y-%m-%d")
    note = (data.get("note") or "").strip()[:2000]
    object_id = data.get("object_id")
    if object_id is not None and object_id != "":
        try:
            object_id = int(object_id)
        except (TypeError, ValueError):
            return jsonify({"error": "Неверный объект"}), 400
        obj = fetch_one("SELECT id FROM objects WHERE id = ? AND user_id = ?", (object_id, current_user.id))
        if not obj:
            return jsonify({"error": "Объект не найден"}), 404
    else:
        object_id = None
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    eid = execute(
        """INSERT INTO worker_account_entries
        (user_id, worker_id, object_id, entry_kind, expense_category, amount, entry_date, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (current_user.id, worker_id, object_id, kind, exp_cat, amount, entry_date, note, now),
        return_id=True,
    )
    row = fetch_one(
        """
        SELECT e.id, e.worker_id, e.object_id, e.entry_kind, e.expense_category, e.amount, e.entry_date, e.note, e.created_at,
               o.name AS object_name
        FROM worker_account_entries e
        LEFT JOIN objects o ON o.id = e.object_id AND o.user_id = e.user_id
        WHERE e.id = ? AND e.user_id = ?
        """,
        (eid, current_user.id),
    )
    return jsonify(dict(row)), 201


@app.route('/api/workers/<int:worker_id>/cashbook/<int:entry_id>', methods=['DELETE'])
@login_required
@require_csrf
def delete_worker_cashbook_entry(worker_id, entry_id):
    row = fetch_one(
        "SELECT id FROM worker_account_entries WHERE id = ? AND worker_id = ? AND user_id = ?",
        (entry_id, worker_id, current_user.id),
    )
    if not row:
        return jsonify({"error": "Запись не найдена"}), 404
    execute("DELETE FROM worker_account_entries WHERE id = ? AND user_id = ?", (entry_id, current_user.id))
    return jsonify({"ok": True})


def _object_owned_or_404(obj_id, user_id):
    row = fetch_one("SELECT * FROM objects WHERE id = ? AND user_id = ?", (obj_id, user_id))
    if not row:
        return None, (jsonify({"error": "Объект не найден"}), 404)
    return row, None


@app.route('/api/objects/<int:obj_id>/expense-entries', methods=['GET'])
@login_required
def list_object_expense_entries(obj_id):
    _, err = _object_owned_or_404(obj_id, current_user.id)
    if err:
        return err
    rows = fetch_all(
        """SELECT id, object_id, entry_date, amount, category, title, note, source, created_at
           FROM object_expense_entries WHERE object_id = ? AND user_id = ?
           ORDER BY entry_date DESC, id DESC""",
        (obj_id, current_user.id),
    )
    return jsonify([dict(r) for r in rows])


@app.route('/api/objects/<int:obj_id>/expense-entries', methods=['POST'])
@login_required
@require_csrf
def add_object_expense_entry(obj_id):
    _, err = _object_owned_or_404(obj_id, current_user.id)
    if err:
        return err
    data = request.json or {}
    try:
        amount = float(data.get('amount'))
    except (TypeError, ValueError):
        return jsonify({"error": "Укажите сумму"}), 400
    if amount <= 0:
        return jsonify({"error": "Сумма должна быть больше 0"}), 400
    category = (data.get('category') or 'other').strip()
    if category not in OBJECT_EXPENSE_CATEGORIES:
        category = 'other'
    entry_date = (data.get('entry_date') or '').strip() or datetime.now().strftime('%Y-%m-%d')
    title = (data.get('title') or '').strip()[:500]
    note = (data.get('note') or '').strip()[:2000]
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    eid = execute(
        """INSERT INTO object_expense_entries
        (user_id, object_id, entry_date, amount, category, title, note, source, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'manual', ?)""",
        (current_user.id, obj_id, entry_date, amount, category, title, note, now),
        return_id=True,
    )
    row = fetch_one(
        "SELECT id, object_id, entry_date, amount, category, title, note, source, created_at FROM object_expense_entries WHERE id = ? AND user_id = ?",
        (eid, current_user.id),
    )
    return jsonify(dict(row)), 201


@app.route('/api/objects/<int:obj_id>/expense-entries/<int:entry_id>', methods=['DELETE'])
@login_required
@require_csrf
def delete_object_expense_entry(obj_id, entry_id):
    _, err = _object_owned_or_404(obj_id, current_user.id)
    if err:
        return err
    row = fetch_one(
        "SELECT id FROM object_expense_entries WHERE id = ? AND object_id = ? AND user_id = ?",
        (entry_id, obj_id, current_user.id),
    )
    if not row:
        return jsonify({"error": "Запись не найдена"}), 404
    execute("DELETE FROM object_expense_entries WHERE id = ? AND user_id = ?", (entry_id, current_user.id))
    return jsonify({"ok": True})


# ============================
# API: ПРИВЯЗКА РАБОЧИХ К ОБЪЕКТАМ
# ============================

@app.route('/api/objects/<int:obj_id>/workers', methods=['GET'])
@login_required
def get_object_workers(obj_id):
    try:
        assignments = fetch_all("""
            SELECT wa.*, w.full_name, w.daily_rate 
            FROM worker_assignments wa
            JOIN workers w ON w.id = wa.worker_id
            WHERE wa.object_id = ? AND wa.user_id = ?
            ORDER BY wa.work_date, w.full_name
        """, (obj_id, current_user.id))
        
        total_salary = sum(a['total_pay'] for a in assignments) if assignments else 0
        return jsonify({'assignments': assignments, 'total_salary': total_salary})
    except Exception as e:
        import logging
        logging.error(f"get_object_workers error: {e}")
        return jsonify({'assignments': [], 'total_salary': 0, 'error': str(e)})

@app.route('/api/objects/<int:obj_id>/workers', methods=['POST'])
@login_required
@require_csrf
def add_object_worker(obj_id):
    """Добавить рабочий к объекту (CSRF через JS заголовок или form data)"""
    try:
        data = request.json
        if not data:
            return jsonify({"error": "Нет данных"}), 400
        
        worker_id = data.get('worker_id')
        if not worker_id:
            return jsonify({"error": "Не выбран рабочий"}), 400
        
        days = float(data.get('days_worked', 1))
        if days <= 0:
            return jsonify({"error": "Дней должно быть > 0"}), 400
        
        worker = fetch_one("SELECT id, daily_rate, full_name FROM workers WHERE id = ? AND user_id = ?", (int(worker_id), current_user.id))
        if not worker:
            return jsonify({"error": "Рабочий не найден. Сначала создайте рабочих на странице /workers"}), 404

        total_pay = worker['daily_rate'] * days
        work_date = data.get('work_date', '')
        
        aid = execute("""INSERT INTO worker_assignments
            (user_id, worker_id, object_id, work_date, days_worked, total_pay)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (current_user.id, int(worker_id), obj_id, work_date, days, total_pay), return_id=True)

        # Пересчитать зарплату объекта (учитывает статус)
        recalc_object_salary(obj_id)

        return jsonify({"id": aid, "total_pay": total_pay}), 201
    except Exception as e:
        import logging, traceback
        tb = traceback.format_exc()
        logging.error(f"add_object_worker error: {e}\n{tb}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/objects/<int:obj_id>/workers/<int:assignment_id>', methods=['DELETE'])
@login_required
@require_csrf
def remove_object_worker(obj_id, assignment_id):
    execute("DELETE FROM worker_assignments WHERE id = ? AND object_id = ? AND user_id = ?",
            (assignment_id, obj_id, current_user.id))
    # Пересчитать зарплату объекта (учитывает статус)
    recalc_object_salary(obj_id)
    return jsonify({"ok": True})

def _recalc_salaries_for_user_id(uid):
    """Пересчитать objects.salary у всех объектов пользователя с одним calendar_counts.

    Делитель по дням общий для всех объектов; при изменении дат/статуса одного объекта
    нужно пересчитать всех, иначе у соседей остаётся устаревшая доля.

    Режим manual не трогаем (recalc_object_salary выходит без UPDATE); предварительный
    UPDATE salary=0 по всем объектам не выполняем — он затирал ручные суммы.
    """
    if not uid:
        return 0
    all_objs = fetch_all("SELECT id FROM objects WHERE user_id = ?", (uid,))
    if not all_objs:
        return 0
    counts = _build_salary_calendar_counts(uid)
    for obj in all_objs:
        recalc_object_salary(obj['id'], uid, calendar_counts=counts)
    return len(all_objs)

def _json_obj(v, default=None):
    d = {} if default is None else default
    if v is None:
        return dict(d)
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return dict(d)
    return dict(d)


def _well_survey_public(row):
    if not row:
        return None
    out = dict(row)
    out['inputs'] = _json_obj(out.get('inputs_json'), {})
    out['computed'] = _json_obj(out.get('computed_json'), {})
    out.pop('inputs_json', None)
    out.pop('computed_json', None)
    return out


def _integration_resolve_object_for_survey(target_user_id, data):
    """
    object_id в теле — явная привязка; иначе объект с integration_source = taskmgr:{task_id}.
    """
    data = data or {}
    oid_raw = data.get('object_id')
    if oid_raw is not None and str(oid_raw).strip() != '':
        try:
            oid = int(oid_raw)
        except (TypeError, ValueError):
            return None, 'object_id must be an integer'
        row = fetch_one('SELECT id FROM objects WHERE id = ? AND user_id = ?', (oid, target_user_id))
        if not row:
            return None, 'object not found'
        return oid, None
    task_id = (data.get('task_id') or '').strip()
    if not task_id:
        return None, 'task_id or object_id is required'
    src = f'taskmgr:{task_id}'
    row = fetch_one(
        'SELECT id FROM objects WHERE user_id = ? AND integration_source = ?',
        (target_user_id, src),
    )
    if not row:
        return (
            None,
            'object for this task_id not found; create object via /api/integration/from-taskmgr/object first',
        )
    return row['id'], None


@app.route('/api/objects/<int:obj_id>/well-surveys', methods=['GET'])
@login_required
def list_object_well_surveys(obj_id):
    obj = fetch_one('SELECT id FROM objects WHERE id = ? AND user_id = ?', (obj_id, current_user.id))
    if not obj:
        return jsonify({'error': 'Not found'}), 404
    rows = fetch_all(
        """SELECT * FROM object_well_surveys WHERE object_id = ? AND user_id = ?
           ORDER BY measured_at DESC, id DESC""",
        (obj_id, current_user.id),
    )
    return jsonify({'surveys': [_well_survey_public(r) for r in rows]})


@app.route('/api/objects/<int:obj_id>/well-surveys', methods=['POST'])
@login_required
@require_csrf
def add_object_well_survey(obj_id):
    obj = fetch_one('SELECT id FROM objects WHERE id = ? AND user_id = ?', (obj_id, current_user.id))
    if not obj:
        return jsonify({'error': 'Not found'}), 404
    data = request.json or {}
    measured_at = (data.get('measured_at') or '').strip()
    if not measured_at:
        measured_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    source = (data.get('source') or 'manual').strip() or 'manual'
    task_id = (data.get('task_id') or '').strip()
    title = (data.get('title') or '').strip()
    conclusion = (data.get('conclusion') or '').strip()
    inputs = _json_obj(data.get('inputs'), {})
    computed = _json_obj(data.get('computed'), {})
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    sid = execute(
        """INSERT INTO object_well_surveys
        (user_id, object_id, measured_at, source, task_id, title, inputs_json, computed_json, conclusion, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            current_user.id,
            obj_id,
            measured_at,
            source,
            task_id,
            title,
            json.dumps(inputs, ensure_ascii=False),
            json.dumps(computed, ensure_ascii=False),
            conclusion,
            now,
        ),
        return_id=True,
    )
    row = fetch_one(
        'SELECT * FROM object_well_surveys WHERE id = ? AND user_id = ?',
        (sid, current_user.id),
    )
    return jsonify(_well_survey_public(row)), 201


@app.route('/api/objects/<int:obj_id>/well-surveys/<int:survey_id>', methods=['DELETE'])
@login_required
@require_csrf
def delete_object_well_survey(obj_id, survey_id):
    rc = execute_rowcount(
        'DELETE FROM object_well_surveys WHERE id = ? AND object_id = ? AND user_id = ?',
        (survey_id, obj_id, current_user.id),
    )
    if not rc:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'ok': True})


def _object_and_client_for_survey(obj_id, user_id):
    obj = fetch_one('SELECT * FROM objects WHERE id = ? AND user_id = ?', (obj_id, user_id))
    if not obj:
        return None, None
    client = None
    cid = obj.get('client_id')
    if cid is not None and str(cid).strip() != '':
        client = fetch_one(
            'SELECT * FROM clients WHERE id = ? AND user_id = ?',
            (int(cid), user_id),
        )
    return obj, client


def _well_survey_document_response(generate_files, basename: str, fmt):
    fmt = (fmt or 'pdf').strip().lower()
    if fmt not in ('pdf', 'docx'):
        fmt = 'pdf'
    try:
        docx_path, pdf_path = generate_files(
            want_pdf=(fmt == 'pdf'), basename=basename
        )
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 500
    tmp_dir = docx_path.parent
    try:
        if fmt == 'pdf':
            if not pdf_path or not pdf_path.is_file():
                return jsonify({
                    'error': 'Не удалось создать PDF. Скачайте DOCX или установите Word/LibreOffice.',
                    'docx_available': docx_path.is_file(),
                }), 503
            payload = pdf_path.read_bytes()
            mime = 'application/pdf'
            filename = f'{basename}.pdf'
        else:
            payload = docx_path.read_bytes()
            mime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            filename = f'{basename}.docx'
        return send_file(
            BytesIO(payload),
            as_attachment=True,
            download_name=filename,
            mimetype=mime,
        )
    finally:
        try:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


def _passport_overrides_from_request_args():
    keys = (
        'address', 'region', 'district', 'settlement', 'street', 'house',
        'casing_diameter', 'pipe_material', 'filter_interval', 'sump_interval',
        'pipe', 'pump_mark', 'executor', 'conclusion', 'measured_at',
    )
    overrides = {}
    for key in keys:
        v = request.args.get(key)
        if v is not None and str(v).strip() != '':
            overrides[key] = str(v).strip()
    return overrides


@app.route('/api/objects/<int:obj_id>/well-surveys/<int:survey_id>/passport', methods=['GET'])
@login_required
def export_well_passport_from_survey(obj_id, survey_id):
    """Паспорт скважины по сохранённой записи замера (?format=pdf|docx)."""
    obj, client = _object_and_client_for_survey(obj_id, current_user.id)
    if not obj:
        return jsonify({'error': 'Not found'}), 404
    row = fetch_one(
        """SELECT * FROM object_well_surveys
           WHERE id = ? AND object_id = ? AND user_id = ?""",
        (survey_id, obj_id, current_user.id),
    )
    if not row:
        return jsonify({'error': 'Survey not found'}), 404
    ctx = build_passport_context(
        object_row=dict(obj),
        client_row=dict(client) if client else None,
        survey_row=survey_row_for_passport(dict(row)),
        overrides=_passport_overrides_from_request_args(),
    )
    basename = f'pasport_skvazhiny_{obj_id}'
    return _well_survey_document_response(
        lambda want_pdf, basename=basename: generate_passport_files(
            ctx, want_pdf=want_pdf, basename=basename
        ),
        basename,
        request.args.get('format'),
    )


@app.route('/api/objects/<int:obj_id>/passport', methods=['POST'])
@login_required
@require_csrf
def export_well_passport_preview(obj_id):
    """Паспорт по данным формы или survey_id в теле. Query: format=pdf|docx."""
    obj, client = _object_and_client_for_survey(obj_id, current_user.id)
    if not obj:
        return jsonify({'error': 'Not found'}), 404
    data = request.json or {}
    survey_row = None
    sid = data.get('survey_id')
    if sid is not None and str(sid).strip() != '':
        row = fetch_one(
            """SELECT * FROM object_well_surveys
               WHERE id = ? AND object_id = ? AND user_id = ?""",
            (int(sid), obj_id, current_user.id),
        )
        if not row:
            return jsonify({'error': 'Survey not found'}), 404
        survey_row = survey_row_for_passport(dict(row))
    overrides = _json_obj(data.get('overrides'), {})
    ctx = build_passport_context(
        object_row=dict(obj),
        client_row=dict(client) if client else None,
        survey_row=survey_row,
        overrides=overrides,
        inline_inputs=_json_obj(data.get('inputs'), {}),
        inline_computed=_json_obj(data.get('computed'), {}),
        inline_conclusion=data.get('conclusion'),
    )
    basename = f'pasport_skvazhiny_{obj_id}'
    return _well_survey_document_response(
        lambda want_pdf, basename=basename: generate_passport_files(
            ctx, want_pdf=want_pdf, basename=basename
        ),
        basename,
        request.args.get('format'),
    )


@app.route('/api/integration/from-taskmgr/lead-lost', methods=['POST'])
def integration_lead_lost_from_taskmgr():
    """
    Диспетчер: заявка в «Отказ» → последняя смета объекта «Отказ клиента».
    JSON: task_id и/или customer_phone, опционально reason.
    """
    try:
        if not _integration_api_key_matches():
            return jsonify({'error': 'Unauthorized'}), 401
        uid_raw = (os.environ.get('INTEGRATION_USER_ID') or '').strip()
        if not uid_raw:
            return jsonify({'error': 'INTEGRATION_USER_ID is not configured'}), 503
        try:
            target_user_id = int(uid_raw)
        except ValueError:
            return jsonify({'error': 'INTEGRATION_USER_ID must be an integer'}), 503
        if not fetch_one('SELECT id FROM users WHERE id = ?', (target_user_id,)):
            return jsonify({'error': 'User for INTEGRATION_USER_ID not found'}), 503

        data = request.json or {}
        task_id = (data.get('task_id') or '').strip()
        phone = str(data.get('customer_phone') or '').strip()
        reason = (data.get('reason') or '').strip() or 'Отказ клиента'
        lost_status = 'Отказ клиента'

        object_id = None
        if task_id:
            row = fetch_one(
                'SELECT id FROM objects WHERE user_id = ? AND integration_source = ?',
                (target_user_id, f'taskmgr:{task_id}'),
            )
            if row:
                object_id = row['id']

        if object_id is None and phone:
            phone_digits = re.sub(r'\D', '', phone)
            if phone_digits:
                objs = fetch_all(
                    'SELECT id, client FROM objects WHERE user_id = ? ORDER BY updated_at DESC LIMIT 200',
                    (target_user_id,),
                )
                for o in objs or []:
                    if re.sub(r'\D', '', str(o.get('client') or '')) == phone_digits:
                        object_id = o['id']
                        break

        if object_id is None:
            return jsonify({'ok': True, 'updated': False, 'reason': 'object_not_found'})

        est = fetch_one(
            """SELECT id, status, notes FROM estimates
               WHERE user_id = ? AND object_id = ?
               ORDER BY updated_at DESC, id DESC LIMIT 1""",
            (target_user_id, object_id),
        )
        if not est:
            return jsonify({'ok': True, 'updated': False, 'reason': 'no_estimate', 'objectId': object_id})

        prev_status = (est.get('status') or '').strip()
        if prev_status == lost_status:
            return jsonify({'ok': True, 'updated': False, 'estimateId': est['id'], 'objectId': object_id})

        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        note_line = f'[{now}] {reason}'
        prev_notes = (est.get('notes') or '').strip()
        notes = f'{prev_notes}\n{note_line}'.strip() if prev_notes else note_line
        execute(
            'UPDATE estimates SET status = ?, notes = ?, updated_at = ? WHERE id = ? AND user_id = ?',
            (lost_status, notes, now, est['id'], target_user_id),
        )
        return jsonify({'ok': True, 'updated': True, 'estimateId': est['id'], 'objectId': object_id})
    except Exception:
        logging.exception('integration: lead-lost failed')
        return jsonify({'error': 'Internal error'}), 500


@app.route('/api/integration/from-taskmgr/well-survey', methods=['POST'])
def integration_add_well_survey_from_taskmgr():
    """
    Запись замера/расчёта скважины из диспетчера (Bearer / X-Integration-Key = INTEGRATION_API_KEY).
    Владелец данных — INTEGRATION_USER_ID. Объект: object_id или task_id (как у /from-taskmgr/object).
    """
    try:
        if not _integration_api_key_matches():
            return jsonify({'error': 'Unauthorized'}), 401
        uid_raw = (os.environ.get('INTEGRATION_USER_ID') or '').strip()
        if not uid_raw:
            return jsonify({'error': 'INTEGRATION_USER_ID is not configured'}), 503
        try:
            target_user_id = int(uid_raw)
        except ValueError:
            return jsonify({'error': 'INTEGRATION_USER_ID must be an integer'}), 503
        if not fetch_one('SELECT id FROM users WHERE id = ?', (target_user_id,)):
            return jsonify({'error': 'User for INTEGRATION_USER_ID not found'}), 503

        data = request.json or {}
        obj_id, err = _integration_resolve_object_for_survey(target_user_id, data)
        if err:
            return jsonify({'error': err}), 400

        measured_at = (data.get('measured_at') or '').strip()
        if not measured_at:
            measured_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        source = (data.get('source') or 'dispatcher').strip() or 'dispatcher'
        task_id = (data.get('task_id') or '').strip()
        title = (data.get('title') or '').strip()
        conclusion = (data.get('conclusion') or '').strip()
        inputs = _json_obj(data.get('inputs'), {})
        computed = _json_obj(data.get('computed'), {})
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        sid = execute(
            """INSERT INTO object_well_surveys
            (user_id, object_id, measured_at, source, task_id, title, inputs_json, computed_json, conclusion, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                target_user_id,
                obj_id,
                measured_at,
                source,
                task_id,
                title,
                json.dumps(inputs, ensure_ascii=False),
                json.dumps(computed, ensure_ascii=False),
                conclusion,
                now,
            ),
            return_id=True,
        )
        row = fetch_one(
            'SELECT * FROM object_well_surveys WHERE id = ? AND user_id = ?',
            (sid, target_user_id),
        )
        return jsonify({'ok': True, 'survey': _well_survey_public(row)}), 201
    except Exception as exc:
        logging.exception('integration: unhandled error in /api/integration/from-taskmgr/well-survey')
        return jsonify({'error': 'Internal error', 'detail': str(exc)}), 500


@app.route('/api/objects/recalc-all-salaries', methods=['POST'])
@login_required
@require_csrf
def recalc_all_salaries():
    """Пересчитать зарплаты ВСЕХ объектов пользователя по новой логике."""
    try:
        uid = current_user.id
        n = _recalc_salaries_for_user_id(uid)
        return jsonify({"ok": True, "objects_recalc": n})
    except Exception as e:
        import logging, traceback
        logging.error(f"recalc_all_salaries error: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

def recalc_objects_for_dates(dates):
    """Пересчитать зарплаты при затронутых календарных датах (делитель n меняется у всех на эти дни)."""
    if not dates:
        return
    _recalc_salaries_for_user_id(current_user.id)

def recalc_object_salary(obj_id, user_id=None, calendar_counts=None):
    """Пересчитать поле objects.salary: сумма по каждому дню из work_dates (доля бригады на этот день).

    Режим salary_allocation_mode:
      all_workers — для каждого дня выезда: (Σ daily_rate всех активных рабочих) / число объектов в этот день;
      assigned_workers — то же, но Σ только по рабочим с назначением на объект;
      manual — не менять salary.

    user_id: для фонового пересчёта при старте (без контекста Flask current_user).
    calendar_counts: опционально словарь дата -> число объектов (один раз на пакет пересчётов).
    """
    try:
        uid = user_id
        if uid is None:
            if not hasattr(current_user, 'id') or not current_user.id:
                logging.error("recalc_object_salary: current_user.id недоступен")
                return
            uid = current_user.id

        obj = fetch_one(
            "SELECT id, work_dates, date_start, date_end, status, salary_allocation_mode FROM objects WHERE id = ? AND user_id = ?",
            (obj_id, uid),
        )
        if not obj:
            return

        work_days = object_work_days_from_row(obj)
        if not work_days:
            execute("UPDATE objects SET salary = 0 WHERE id = ? AND user_id = ?", (obj_id, uid))
            return

        valid_statuses = OBJECT_STATUSES_SALARY
        if obj.get('status') not in valid_statuses:
            execute("UPDATE objects SET salary = 0 WHERE id = ? AND user_id = ?", (obj_id, uid))
            return

        mode = _norm_salary_allocation_mode(obj.get('salary_allocation_mode'))
        if mode == SALARY_ALLOCATION_MANUAL:
            return

        if calendar_counts is None:
            calendar_counts = _build_salary_calendar_counts(uid)

        if mode == SALARY_ALLOCATION_ASSIGNED_WORKERS:
            rows = fetch_all(
                """
                SELECT DISTINCT w.daily_rate
                FROM worker_assignments wa
                JOIN workers w ON w.id = wa.worker_id AND w.user_id = wa.user_id
                WHERE wa.object_id = ? AND wa.user_id = ? AND w.is_active = 1
                """,
                (obj_id, uid),
            )
            total_daily_rate = sum(float(r['daily_rate'] or 0) for r in rows)
        else:
            all_workers = fetch_all(
                "SELECT daily_rate FROM workers WHERE user_id = ? AND is_active = 1",
                (uid,),
            )
            if not all_workers:
                execute("UPDATE objects SET salary = 0 WHERE id = ? AND user_id = ?", (obj_id, uid))
                return
            total_daily_rate = sum(float(w['daily_rate'] or 0) for w in all_workers)

        if total_daily_rate <= 0:
            execute("UPDATE objects SET salary = 0 WHERE id = ? AND user_id = ?", (obj_id, uid))
            return

        total_salary = 0.0
        for d in work_days:
            n = int(calendar_counts.get(d, 0))
            if n <= 0:
                continue
            total_salary += total_daily_rate / n

        execute(
            "UPDATE objects SET salary = ? WHERE id = ? AND user_id = ?",
            (round(total_salary, 2), obj_id, uid),
        )
    except Exception as e:
        import logging

        logging.error(f"recalc_object_salary error: {e}")

# ============================
# API: КЛИЕНТЫ
# ============================
@app.route('/api/clients/integrity', methods=['GET'])
@login_required
def clients_integrity_report():
    """
    Объекты с одним client_id — для поиска ошибочной массовой привязки заказчика.
    """
    objects = _fetch_objects_with_financials(current_user.id)
    by_cid = {}
    for obj in objects:
        cid = obj.get('client_id')
        try:
            cid_int = int(cid) if cid not in (None, '', 0, '0') else None
        except (TypeError, ValueError):
            cid_int = None
        if not cid_int:
            continue
        if cid_int not in by_cid:
            by_cid[cid_int] = {
                'client_id': cid_int,
                'client_name': '',
                'objects': [],
                'total_revenue': 0.0,
            }
        by_cid[cid_int]['objects'].append({
            'id': obj.get('id'),
            'name': obj.get('name'),
            'client_text': obj.get('client'),
            'integration_source': obj.get('integration_source'),
        })
        by_cid[cid_int]['total_revenue'] += float(obj.get('total_revenue') or 0)
    cmap = _clients_map_for_user(current_user.id)
    groups = []
    for cid_int, g in by_cid.items():
        g['client_name'] = cmap.get(cid_int) or g['objects'][0].get('client_text') or ''
        g['object_count'] = len(g['objects'])
        g['total_revenue'] = round(g['total_revenue'], 2)
        groups.append(g)
    groups.sort(key=lambda x: x['object_count'], reverse=True)
    suspicious = [g for g in groups if g['object_count'] >= 3]
    return jsonify({
        'groups': groups,
        'suspicious': suspicious,
        'hint': 'Если у одного client_id много разных объектов — проверьте, не привязан ли чужой заказчик (например, после автопривязки по имени).',
    })


@app.route('/api/clients', methods=['GET'])
@login_required
def get_clients():
    return jsonify(fetch_all("SELECT * FROM clients WHERE user_id = ? ORDER BY name", (current_user.id,)))

@app.route('/api/clients', methods=['POST'])
@login_required
@require_csrf
def add_client():
    data = request.json
    cid = execute("INSERT INTO clients (user_id, name, phone, email, address) VALUES (?, ?, ?, ?, ?)",
                  (current_user.id, data['name'], data.get('phone'), data.get('email'), data.get('address')), return_id=True)
    return jsonify(fetch_one("SELECT * FROM clients WHERE id = ? AND user_id = ?", (cid, current_user.id))), 201

@app.route('/api/clients/<int:client_id>', methods=['PUT'])
@login_required
@require_csrf
def update_client(client_id):
    data = request.json
    if not data:
        return jsonify({"error": "Нет данных"}), 400
    execute("UPDATE clients SET name=?, phone=?, email=?, address=? WHERE id = ? AND user_id = ?",
            (data.get('name'), data.get('phone'), data.get('email'), data.get('address'), client_id, current_user.id))
    row = fetch_one("SELECT * FROM clients WHERE id = ? AND user_id = ?", (client_id, current_user.id))
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(row)

@app.route('/api/clients/<int:client_id>', methods=['DELETE'])
@login_required
@require_csrf
def delete_client(client_id):
    execute("DELETE FROM clients WHERE id = ? AND user_id = ?", (client_id, current_user.id))
    return jsonify({"ok": True})

# ============================
# API: СТАТИСТИКА И ДОЛГИ
# ============================
@app.route('/api/stats', methods=['GET'])
@login_required
def get_stats():
    total = fetch_one("SELECT COUNT(*) as c FROM objects WHERE user_id = ?", (current_user.id,))['c']
    in_progress = fetch_one(
        "SELECT COUNT(*) as c FROM objects WHERE user_id = ? AND status = ?",
        (current_user.id, OBJECT_STATUS_ACTIVE),
    )['c']
    _phf = ','.join(['?' for _ in OBJECT_STATUSES_FINISHED])
    completed = fetch_one(
        f"SELECT COUNT(*) as c FROM objects WHERE user_id = ? AND status IN ({_phf})",
        (current_user.id,) + OBJECT_STATUSES_FINISHED,
    )['c']

    objects = _fetch_objects_with_financials(current_user.id)
    p = _portfolio_financial_totals(objects)
    balance = round(p['total_revenue'] - p['total_advance'], 2)

    return jsonify({
        "total": total,
        "in_progress": in_progress,
        "completed": completed,
        "total_sum": p['total_revenue'],
        "total_expenses": p['total_expenses'],
        "total_salary": p['total_salary'],
        "total_advance": p['total_advance'],
        "total_material_profit": p['total_material_profit'],
        "profit": p['total_profit'],
        "balance": balance,
    })

def _stats_object_anchor_month(obj):
    """YYYY-MM для привязки объекта к месяцу на графиках."""
    days = object_work_days_from_row(obj)
    anchor = (days[0] if days else (obj.get('date_start') or '')[:10])
    if anchor and len(anchor) >= 7:
        return anchor[:7]
    return None


def _stats_prev_calendar_month(ym):
    """Предыдущий календарный месяц YYYY-MM."""
    y, m = int(ym[:4]), int(ym[5:7])
    m -= 1
    if m < 1:
        m, y = 12, y - 1
    return f'{y:04d}-{m:02d}'


def _stats_empty_month_bucket():
    return {
        'revenue': 0.0,
        'profit': 0.0,
        'expenses': 0.0,
        'debt': 0.0,
        'advance': 0.0,
        'objects': 0,
    }


@app.route('/api/stats/detailed', methods=['GET'])
@login_required
def get_detailed_stats():
    objects = _fetch_objects_with_financials(current_user.id)
    portfolio = _portfolio_financial_totals(objects)
    clients_map = _clients_map_for_user(current_user.id)

    status_dist = {}
    clients_stat = {}
    months = {}
    debtors = {}

    for obj in objects:
        s = obj.get('status', 'Неизвестно')
        status_dist[s] = status_dist.get(s, 0) + 1

        rev = float(obj.get('total_revenue') or 0)
        exp = float(obj.get('total_expenses') or 0)
        prof = float(obj.get('total_profit') or 0)
        adv = float(obj.get('advance') or 0)
        bal = float(obj.get('balance') or 0)
        emp = _float_object_field(obj, 'estimate_material_profit', 'est_mat_profit')

        gkey, clabel = _client_group_for_object(obj, clients_map)
        if gkey not in clients_stat:
            clients_stat[gkey] = {
                'name': clabel,
                'client_id': gkey[1] if gkey[0] == 'id' else None,
                'count': 0,
                'revenue': 0,
                'profit': 0,
                'mat_profit': 0,
                'debt': 0,
            }
        clients_stat[gkey]['count'] += 1
        clients_stat[gkey]['revenue'] += rev
        clients_stat[gkey]['profit'] += prof
        clients_stat[gkey]['mat_profit'] += emp

        if bal > 0 and obj.get('status') not in OBJECT_STATUSES_NOT_DEBT:
            if gkey not in debtors:
                debtors[gkey] = {'name': clabel, 'debt': 0, 'objects': 0}
            debtors[gkey]['debt'] += bal
            debtors[gkey]['objects'] += 1
            clients_stat[gkey]['debt'] += bal

        m = _stats_object_anchor_month(obj)
        if m:
            if m not in months:
                months[m] = {
                    'revenue': 0,
                    'profit': 0,
                    'expenses': 0,
                    'salary': 0,
                    'material_profit': 0,
                    'debt': 0,
                    'advance': 0,
                    'objects': 0,
                }
            months[m]['revenue'] += rev
            months[m]['profit'] += prof
            months[m]['expenses'] += exp
            months[m]['salary'] += float(obj.get('salary') or 0)
            months[m]['material_profit'] += emp
            months[m]['advance'] += adv
            months[m]['objects'] += 1
            if bal > 0 and obj.get('status') not in OBJECT_STATUSES_NOT_DEBT:
                months[m]['debt'] += bal

    for row in clients_stat.values():
        row['revenue'] = round(row['revenue'], 2)
        row['profit'] = round(row['profit'], 2)
        row['mat_profit'] = round(row['mat_profit'], 2)
        row['debt'] = round(row['debt'], 2)

    top_clients = sorted(clients_stat.values(), key=lambda x: x['revenue'], reverse=True)
    top_clients = [c for c in top_clients if c['revenue'] > 0.005][:10]
    unassigned = clients_stat.get(('name', ''))
    unassigned_revenue = round(float(unassigned['revenue']) if unassigned else 0.0, 2)
    top_debtors = sorted(debtors.values(), key=lambda x: x['debt'], reverse=True)[:10]

    from datetime import datetime
    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')
    month_str = now.strftime('%Y-%m')
    year_str = now.strftime('%Y')

    def _obj_touched_calendar_prefix(o, prefix):
        for d in object_work_days_from_row(o):
            if d.startswith(prefix):
                return True
        return False

    today_count = sum(1 for o in objects if _obj_touched_calendar_prefix(o, today_str))
    month_count = sum(1 for o in objects if _obj_touched_calendar_prefix(o, month_str))
    year_count = sum(1 for o in objects if _obj_touched_calendar_prefix(o, year_str))

    prev_month_str = _stats_prev_calendar_month(month_str)
    cur_bucket = months.get(month_str) or _stats_empty_month_bucket()
    prev_bucket = months.get(prev_month_str) or _stats_empty_month_bucket()
    month_comparison = {
        'current_month': month_str,
        'previous_month': prev_month_str,
        'current': cur_bucket,
        'previous': prev_bucket,
        'portfolio_debt': portfolio['total_debt'],
    }

    month_revenue = 0.0
    month_profit = 0.0
    month_expenses = 0.0
    month_advance = 0.0
    month_debt = 0.0
    for obj in objects:
        if not _obj_touched_calendar_prefix(obj, month_str):
            continue
        month_revenue += float(obj.get('total_revenue') or 0)
        month_profit += float(obj.get('total_profit') or 0)
        month_expenses += float(obj.get('total_expenses') or 0)
        month_advance += float(obj.get('advance') or 0)
        bal = float(obj.get('balance') or 0)
        if bal > 0 and obj.get('status') not in OBJECT_STATUSES_NOT_DEBT:
            month_debt += bal

    total_revenue = portfolio['total_revenue']
    total_profit = portfolio['total_profit']
    total_debt = portfolio['total_debt']
    total_advance = portfolio['total_advance']
    total_mat_profit = portfolio['total_material_profit']
    debt_objects = portfolio['debt_objects']

    margin_pct = round((total_profit / total_revenue) * 100, 1) if total_revenue > 0 else 0.0
    paid_pct = round((max(0.0, total_revenue - total_debt) / total_revenue) * 100, 1) if total_revenue > 0 else 0.0
    advance_over_revenue = round(max(0.0, total_advance - total_revenue), 2)

    return jsonify({
        'today': {'count': today_count},
        'month': {'count': month_count},
        'year': {'count': year_count},
        'calendar_month': {
            'key': month_str,
            'revenue': round(month_revenue, 2),
            'profit': round(month_profit, 2),
            'expenses': round(month_expenses, 2),
            'advance': round(month_advance, 2),
            'debt': round(month_debt, 2),
            'objects': month_count,
        },
        'summary': {
            'total_revenue': total_revenue,
            'total_expenses': portfolio['total_expenses'],
            'total_profit': total_profit,
            'total_profit_before_tax': portfolio['total_profit_before_tax'],
            'total_tax': portfolio['total_tax'],
            'total_salary': portfolio['total_salary'],
            'total_advance': total_advance,
            'total_debt': total_debt,
            'total_material_profit': total_mat_profit,
            'margin_pct': margin_pct,
            'paid_pct': paid_pct,
            'advance_over_revenue': advance_over_revenue,
            'debt_objects': debt_objects,
            'objects_total': len(objects),
            'unique_clients': len(clients_stat),
            'unassigned_client_revenue': unassigned_revenue,
        },
        'top_clients': top_clients,
        'status_distribution': status_dist,
        'monthly_trend': dict(sorted(months.items())),
        'top_materials': [],
        'top_debtors': top_debtors,
        'forecast_profit': round(total_profit, 2),
        'total_material_profit': round(total_mat_profit, 2),
        'avg_material_margin': 0,
        'month_comparison': month_comparison,
    })

@app.route('/api/debts', methods=['GET'])
@login_required
def get_debts():
    """Долги клиентов — balance после того же расчёта, что на главной и в статистике."""
    objects = _fetch_objects_with_financials(current_user.id)

    debts = {}
    for obj in objects:
        if obj.get('status') in OBJECT_STATUSES_NOT_DEBT:
            continue
        revenue = float(obj.get('total_revenue') or 0)
        advance = float(obj.get('advance') or 0)
        debt = float(obj.get('balance') or 0)
        if debt <= 0:
            continue

        client = obj.get('client', 'Без клиента')
        if client not in debts:
            debts[client] = {'name': client, 'objects': [], 'total_revenue': 0, 'total_paid': 0, 'total_debt': 0}

        debts[client]['objects'].append({'id': obj['id'], 'name': obj['name'], 'revenue': revenue, 'debt': debt})
        debts[client]['total_revenue'] += revenue
        debts[client]['total_paid'] += advance
        debts[client]['total_debt'] += debt

    return jsonify(sorted(debts.values(), key=lambda x: x['total_debt'], reverse=True))

@app.route('/api/report', methods=['GET'])
@login_required
def get_report():
    """Отчёт за период с учётом прибыли от материалов"""
    try:
        start = (request.args.get('start') or '').strip()[:10]
        end = (request.args.get('end') or '').strip()[:10]

        where_parts = []
        params = []
        eff_date = "COALESCE(NULLIF(TRIM(o.date_end), ''), o.date_start)"
        if start:
            where_parts.append(f" AND ({eff_date}) >= ?")
            params.append(start)
        if end:
            where_parts.append(" AND o.date_start <= ?")
            params.append(end)

        objects = _fetch_objects_with_financials(
            current_user.id, ''.join(where_parts), tuple(params),
        )
        p = _portfolio_financial_totals(objects)

        return jsonify({
            'objects': objects,
            'totals': {
                'revenue': p['total_revenue'],
                'expenses': p['total_expenses'],
                'salary': p['total_salary'],
                'profit': p['total_profit'],
                'profit_before_tax': p['total_profit_before_tax'],
                'tax': p['total_tax'],
                'debt': p['total_debt'],
                'material_profit': p['total_material_profit'],
            },
        })
    except Exception as e:
        logging.exception("get_report failed")
        return jsonify({'error': 'Не удалось сформировать отчёт', 'detail': str(e)}), 500

def _recalc_all_salaries_on_startup():
    """Пересчитать зарплаты при старте — отдельно для каждого user_id (мультиарендность)."""
    try:
        from database import fetch_all

        uid_rows = fetch_all(
            "SELECT user_id FROM objects WHERE user_id IS NOT NULL "
            "UNION SELECT user_id FROM workers WHERE user_id IS NOT NULL"
        )
        uids = sorted({r['user_id'] for r in uid_rows if r.get('user_id') is not None})

        for uid in uids:
            n = _recalc_salaries_for_user_id(uid)
            workers = fetch_all(
                "SELECT daily_rate FROM workers WHERE is_active = 1 AND user_id = ?",
                (uid,),
            )
            total_rate = sum(float(w['daily_rate'] or 0) for w in workers) if workers else 0
            if n <= 0:
                continue
            if total_rate > 0:
                print(f"[OK] user {uid}: пересчитаны зарплаты по {n} объектам, Σ ставок {total_rate}/день")
            else:
                print(
                    f"[WARN] user {uid}: нет активных рабочих — автозарплаты пересчитаны по {n} объектам "
                    f"(ручной режим salary не меняется)"
                )
    except Exception as e:
        print(f"[WARN] Ошибка пересчёта зарплат: {e}")


def _start_startup_recalc_if_enabled():
    """
    Не блокирует загрузку gunicorn: иначе тяжёлый пересчёт мешал бы healthcheck'у (Railway и др.)
    и при нескольких workers дублировал бы одну и ту же работу. Отключение: SKIP_STARTUP_RECALC=1.
    """
    if os.environ.get("SKIP_STARTUP_RECALC", "").lower() in ("1", "true", "yes"):
        return
    t = threading.Thread(
        target=_recalc_all_salaries_on_startup, name="recalc_salaries", daemon=True
    )
    t.start()


print("Запуск системы учёта...")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    print(f"Сервер запущен: http://127.0.0.1:{port}")
    app.run(host='0.0.0.0', port=port, debug=debug)
