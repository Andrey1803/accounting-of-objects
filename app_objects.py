"""
Главный файл приложения (Web-интерфейс и API)
Автоматически адаптируется под SQLite (локально) или PostgreSQL (Railway).
"""
import atexit
import os
import re
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
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_file
from flask_login import LoginManager, login_required, current_user, logout_user

# Импорт ядра базы данных и модулей
from database import init_db, fetch_all, fetch_one, execute, execute_many, IS_POSTGRES, close_all_connections
from auth import auth_bp, User, hash_pw, check_pw
from estimate_module import estimate_bp
from extensions import limiter

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
# Первый «боевой» запрос инициализирует схему; /health обслуживается без БД.
_db_init_state = {"ready": False}
_db_init_lock = threading.Lock()


@app.before_request
def _ensure_db_before_request():
    if _db_init_state["ready"]:
        return
    p = request.path or ""
    if p.rstrip("/") == "/health" or p == "/favicon.ico" or p.startswith("/static/"):
        return
    with _db_init_lock:
        if _db_init_state["ready"]:
            return
        init_db()
        _db_init_state["ready"] = True
        _start_startup_recalc_if_enabled()
        _log_integration_env_once()


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

# Подотчёт рабочих: выручка у клиента, расходы, сдача
CASHBOOK_KINDS = frozenset({'client_payment', 'expense', 'handover'})
CASHBOOK_EXPENSE_CATS = frozenset({'lunch', 'fuel', 'repair', 'other'})

# Порядок в списках: сначала ожидает старта и в работе, затем остальные
_SQL_OBJECTS_ORDER = (
    f"CASE o.status WHEN '{OBJECT_STATUS_WAITING}' THEN 0 WHEN 'Запланирован' THEN 0 "
    f"WHEN '{OBJECT_STATUS_ACTIVE}' THEN 1 ELSE 2 END, o.date_start DESC"
)


def _sql_objects_estimate_aggregates(as_work, as_mat, as_profit):
    """Общий фрагмент SELECT + JOIN для объектов с агрегатами по сметам."""
    return (
        f"SELECT o.*, "
        f"COALESCE(SUM(CASE WHEN ei.section = 'work' THEN ei.total ELSE 0 END), 0) AS {as_work}, "
        f"COALESCE(SUM(CASE WHEN ei.section = 'material' THEN ei.total ELSE 0 END), 0) AS {as_mat}, "
        f"COALESCE(SUM(CASE WHEN ei.section = 'material' THEN ei.material_profit ELSE 0 END), 0) AS {as_profit} "
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


def _compute_object_financials(sum_work, estimate_works, estimate_materials, estimate_material_profit, expenses, salary):
    """
    Выручка, затраты и прибыль объекта без двойного учёта материалов по смете.

    Поле objects.expenses часто равно сумме позиций материалов в смете (розница) — тому же, что
    агрегат estimate_materials (em). Старая формула делала total_expenses = expenses + em и ещё
    вычитала зарплату, а material_profit прибавляла отдельно; при expenses ≈ em это эквивалентно
    двойному вычитанию розницы материалов и занижало прибыль.

    Сейчас:
    - Выручка = max(sum_work, работы_по_смете) + розница_материалов_по_смете (em)
    - Закуп материалов = em - material_profit (суммарная наценка на материалы = material_profit)
    - Прочие затраты: если expenses >= em и em > 0 — считаем, что expenses включает дубль сметы,
      прочие = max(0, expenses - em); иначе весь expenses — прочие затраты
    - Прибыль = Выручка - закуп_мат - прочие - зарплата
    """
    try:
        ew = float(estimate_works or 0)
        em = float(estimate_materials or 0)
        emp = float(estimate_material_profit or 0)
        raw_exp = float(expenses or 0)
        sal = float(salary or 0)
    except (TypeError, ValueError):
        ew = em = emp = raw_exp = sal = 0.0

    work_rev = _work_revenue_once(sum_work, ew)
    total_revenue = work_rev + em
    mat_cogs = max(0.0, em - emp)
    if em > 0 and raw_exp + 1e-9 >= em:
        other_exp = max(0.0, raw_exp - em)
    else:
        other_exp = raw_exp

    total_expenses = mat_cogs + other_exp
    total_profit = total_revenue - total_expenses - sal
    return total_revenue, total_expenses, total_profit


def _object_client_fields_from_payload(user_id, data, existing=None):
    """Имя клиента и client_id для сохранения объекта (различение одноимённых клиентов)."""
    existing = existing or {}
    ex_cli = existing.get('client_id')
    ex_name = existing.get('client') or ''

    if 'client_id' in data:
        raw = data.get('client_id')
        if raw is None or raw == '':
            name = data.get('client') if 'client' in data else ex_name
            return (str(name or '').strip(), None)
        try:
            cid = int(raw)
        except (TypeError, ValueError):
            cid = None
        if cid:
            row = fetch_one("SELECT name FROM clients WHERE id = ? AND user_id = ?", (cid, user_id))
            if row:
                return (str(row['name'] or '').strip(), cid)
        name = data.get('client') if 'client' in data else ex_name
        return (str(name or '').strip(), None)

    if 'client' in data:
        name = str(data.get('client') or '').strip()
        return (name, ex_cli)

    return (str(ex_name).strip(), ex_cli)


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


def _integration_find_or_create_client(target_user_id, card_name: str, phone: str, address: str):
    """
    Карточка в таблице clients: создать или найти по телефону (7+ цифр) / по точному имени карточки.
    При совпадении дополняем пустые телефон, адрес и имя (если было «—» или пусто).
    """
    phone = str(phone or '').strip()
    address = str(address or '').strip()
    norm = _normalize_phone_digits(phone)
    rows = fetch_all('SELECT * FROM clients WHERE user_id = ?', (target_user_id,))
    if norm and len(norm) >= 7:
        for r in rows:
            if _normalize_phone_digits(r.get('phone')) == norm:
                cid = r['id']
                ex_name = (r.get('name') or '').strip()
                ex_phone = (r.get('phone') or '').strip()
                ex_addr = (r.get('address') or '').strip()
                # Телефон совпал, но имя другое: не переиспользуем чужую карточку автоматически.
                # Иначе объект из новой задачи «прилипает» к старому клиенту с тем же номером.
                if card_name and card_name != '—' and ex_name and ex_name != card_name:
                    logging.warning(
                        "integration: phone match but name differs — skip reuse (client_id=%s, db_name=%r, incoming=%r)",
                        cid,
                        ex_name,
                        card_name,
                    )
                    continue
                # Миграция "на лету": раньше имя могло записываться как "ФИО — Компания".
                # Если пришёл нормальный контакт и видим старую склейку, заменяем на контакт.
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
@login_required
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
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()}), 200


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
    objects = fetch_all(
        _sql_objects_estimate_aggregates('estimate_works', 'estimate_materials', 'estimate_material_profit')
        + "WHERE o.user_id = ? GROUP BY o.id ORDER BY " + _SQL_OBJECTS_ORDER,
        (current_user.id,),
    )

    for obj in objects:
        ew = obj.get('estimate_works', 0) or 0
        em = obj.get('estimate_materials', 0) or 0
        emp = obj.get('estimate_material_profit', 0) or 0

        tr, te, tp = _compute_object_financials(
            obj.get('sum_work'), ew, em, emp, obj.get('expenses'), obj.get('salary')
        )
        obj['total_revenue'] = tr
        obj['total_expenses'] = te
        obj['total_profit'] = tp
        obj['balance'] = tr - float(obj.get('advance', 0) or 0)

    # Подсчёт по статусам
    in_progress = sum(1 for o in objects if o.get('status') == OBJECT_STATUS_ACTIVE)
    completed = sum(1 for o in objects if o.get('status') in OBJECT_STATUSES_FINISHED)

    return jsonify({
        'objects': objects,
        'total': len(objects),
        'in_progress': in_progress,
        'completed': completed,
        'page': 1,
        'per_page': 50,
        'total_pages': 1
    })

@app.route('/api/objects', methods=['GET'])
@login_required
def get_objects():
    objects = fetch_all(
        "SELECT * FROM objects WHERE user_id = ? ORDER BY CASE status WHEN ? THEN 0 "
        "WHEN 'Запланирован' THEN 0 WHEN ? THEN 1 ELSE 2 END, date_start DESC",
        (current_user.id, OBJECT_STATUS_WAITING, OBJECT_STATUS_ACTIVE),
    )
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
    oid = execute("""INSERT INTO objects 
        (user_id, date_start, date_end, name, client, client_id, sum_work, expenses, status, advance, salary, notes, created_at, updated_at) 
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", 
        (current_user.id, data.get('date_start'), data.get('date_end'), name,
         client_name, client_id, data.get('sum_work', 0), data.get('expenses', 0), data.get('status'),
         data.get('advance', 0), data.get('salary', 0), data.get('notes'), now, now), return_id=True)
    obj = fetch_one("SELECT * FROM objects WHERE id = ? AND user_id = ?", (oid, current_user.id))
    return jsonify(obj), 201


@app.route('/api/integration/from-taskmgr/object', methods=['POST'])
def integration_create_object_from_taskmgr():
    """
    Создать объект по событию из диспетчера задач (без браузерной сессии).
    Окружение: INTEGRATION_API_KEY, INTEGRATION_USER_ID — id пользователя в этой БД (владелец данных).
    Повтор с тем же task_id не создаёт дубликат (поле integration_source).
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
        advance_payload = _integration_parse_money(data.get('advance'))
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
            phone = str(data.get('customer_phone') or '').strip()
            address = str(data.get('object_address') or '').strip()
            has_payload = bool(contact_in or company_in or phone or address or advance_payload is not None)
            card_name = _integration_client_card_name(contact_in, company_in)
            client_id, client_name = _integration_find_or_create_client(
                target_user_id, card_name, phone, address
            )
            ex_client = (existing.get('client') or '').strip()
            ex_client_id = existing.get('client_id')
            mismatch = (ex_client_id != client_id) or (ex_client != (client_name or '').strip())
            if not has_payload and not mismatch:
                return jsonify({'ok': True, 'idempotent': True, 'object': existing}), 200
            name_new = (data.get('name') or '').strip() or (existing.get('name') or '')
            advance_new = existing.get('advance', 0) if advance_payload is None else advance_payload
            execute(
                'UPDATE objects SET name=?, client=?, client_id=?, advance=? WHERE id=? AND user_id=?',
                (name_new, client_name, client_id, advance_new, existing['id'], target_user_id),
            )
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
        client_id, client_name = _integration_find_or_create_client(
            target_user_id, card_name, phone, address
        )
        status_val = data.get('status')
        if status_val is not None and str(status_val).strip():
            obj_status = str(status_val).strip()
        else:
            obj_status = OBJECT_STATUS_WAITING
        advance_new = 0 if advance_payload is None else advance_payload

        extra_notes = (data.get('notes') or '').strip()
        line = f'Задача диспетчера: {task_id}'
        notes = f'{extra_notes}\n{line}'.strip() if extra_notes else line

        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        oid = execute(
            """INSERT INTO objects
            (user_id, date_start, date_end, name, client, client_id, sum_work, expenses, status, advance, salary, notes, created_at, updated_at, integration_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                target_user_id,
                data.get('date_start'),
                data.get('date_end'),
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
        obj = fetch_one('SELECT * FROM objects WHERE id = ? AND user_id = ?', (oid, target_user_id))
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
    execute("""UPDATE objects SET date_start=?, date_end=?, name=?, client=?, client_id=?, sum_work=?,
           expenses=?, status=?, advance=?, salary=?, notes=?, updated_at=? WHERE id=? AND user_id=?""",
        (pick('date_start'), pick('date_end'), name, client_name, client_id,
         pickf('sum_work'), pickf('expenses'), pick('status'), pickf('advance'),
         pickf('salary'), pick('notes'), now, obj_id, current_user.id))

    if 'status' in data:
        recalc_object_salary(obj_id)

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
    queries.append(("DELETE FROM objects WHERE id = ? AND user_id = ?", (obj_id, current_user.id)))

    # Выполняем всё в одной транзакции
    execute_many(queries)
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
    return jsonify(w)

@app.route('/api/workers/<int:worker_id>', methods=['DELETE'])
@login_required
@require_csrf
def delete_worker(worker_id):
    # Сначала назначения (при включённых FK в PostgreSQL иначе нарушение ссылки)
    execute("DELETE FROM worker_account_entries WHERE worker_id = ? AND user_id = ?", (worker_id, current_user.id))
    execute("DELETE FROM worker_assignments WHERE worker_id = ? AND user_id = ?", (worker_id, current_user.id))
    execute("DELETE FROM workers WHERE id = ? AND user_id = ?", (worker_id, current_user.id))
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

@app.route('/api/objects/recalc-all-salaries', methods=['POST'])
@login_required
@require_csrf
def recalc_all_salaries():
    """Пересчитать зарплаты ВСЕХ объектов пользователя по новой логике."""
    try:
        uid = current_user.id

        # Находим все уникальные date_start объектов
        all_dates = fetch_all(
            "SELECT DISTINCT date_start FROM objects WHERE user_id = ? AND date_start != ''",
            (uid,)
        )
        dates = [d['date_start'][:10] for d in all_dates]

        # Находим все объекты пользователя
        all_objs = fetch_all("SELECT id FROM objects WHERE user_id = ?", (uid,))

        # Сначала обнуляем все зарплаты
        for obj in all_objs:
            execute("UPDATE objects SET salary = 0 WHERE id = ? AND user_id = ?", (obj['id'], uid))

        # Пересчитываем по датам date_start
        for date in dates:
            recalc_objects_for_dates([date])

        return jsonify({"ok": True, "dates_processed": len(dates), "objects_recalc": len(all_objs)})
    except Exception as e:
        import logging, traceback
        logging.error(f"recalc_all_salaries error: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

def recalc_objects_for_dates(dates):
    """Пересчитать зарплаты объектов за указанные даты.
    Формула: (Σ daily_rate всех активных рабочих) / кол-во активных объектов за день.
    """
    if not dates:
        return
    uid = current_user.id

    valid_statuses = OBJECT_STATUSES_SALARY
    placeholders = ','.join(['?' for _ in dates])
    placeholders2 = ','.join(['?' for _ in valid_statuses])

    affected_objs = fetch_all(
        f"SELECT id FROM objects WHERE user_id = ? AND date_start IN ({placeholders}) AND status IN ({placeholders2})",
        [uid] + list(dates) + list(valid_statuses)
    )

    for obj in affected_objs:
        recalc_object_salary(obj['id'])

def recalc_object_salary(obj_id):
    """Пересчитать зарплату объекта.
    Формула: (Σ daily_rate всех активных рабочих) / (количество объектов за этот день)
    Только для статусов: В работе, Выполнен, Закрыт.
    """
    try:
        if not hasattr(current_user, 'id') or not current_user.id:
            logging.error("recalc_object_salary: current_user.id недоступен")
            return

        uid = current_user.id

        # 1. Проверяем статус — только для активных объектов
        obj = fetch_one("SELECT id, date_start, status FROM objects WHERE id = ? AND user_id = ?", (obj_id, uid))
        if not obj or not obj.get('date_start'):
            return

        valid_statuses = OBJECT_STATUSES_SALARY
        if obj.get('status') not in valid_statuses:
            execute("UPDATE objects SET salary = 0 WHERE id = ? AND user_id = ?", (obj_id, uid))
            return

        obj_date = obj['date_start'][:10]

        # 2. Находим только активные объекты, которые НАЧАЛИСЬ в этот же день
        placeholders = ','.join(['?' for _ in valid_statuses])
        objects_same_day = fetch_all(
            f"SELECT id FROM objects WHERE user_id = ? AND date_start LIKE ? AND status IN ({placeholders})",
            [uid, obj_date + '%'] + list(valid_statuses)
        )
        num_objects = len(objects_same_day)
        if num_objects == 0:
            return

        # 3. Берём ВСЕХ активных рабочих пользователя
        all_workers = fetch_all(
            "SELECT daily_rate FROM workers WHERE user_id = ? AND is_active = 1",
            (uid,)
        )
        if not all_workers:
            execute("UPDATE objects SET salary = 0 WHERE id = ? AND user_id = ?", (obj_id, uid))
            return

        # 4. Суммируем ставки всех активных рабочих
        total_daily_rate = sum(w['daily_rate'] for w in all_workers)

        # 5. Делим на количество активных объектов за этот день
        salary_per_object = total_daily_rate / num_objects

        # 6. Обновляем salary объекта
        execute("UPDATE objects SET salary = ? WHERE id = ? AND user_id = ?",
                (round(salary_per_object, 2), obj_id, uid))
    except Exception as e:
        import logging
        logging.error(f"recalc_object_salary error: {e}")

# ============================
# API: КЛИЕНТЫ
# ============================
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

    objects = fetch_all(
        _sql_objects_estimate_aggregates('estimate_works', 'estimate_materials', 'estimate_material_profit')
        + "WHERE o.user_id = ? GROUP BY o.id",
        (current_user.id,),
    )
    total_revenue = 0.0
    total_expenses = 0.0
    total_material_profit = 0.0
    salary = 0.0
    advance = 0.0
    profit = 0.0
    for obj in objects:
        ew = obj.get('estimate_works', 0) or 0
        em = float(obj.get('estimate_materials', 0) or 0)
        emp = float(obj.get('estimate_material_profit', 0) or 0)
        rev, exp, prof = _compute_object_financials(
            obj.get('sum_work'), ew, em, emp, obj.get('expenses'), obj.get('salary')
        )
        total_revenue += rev
        total_expenses += exp
        total_material_profit += emp
        profit += prof
        salary += float(obj.get('salary', 0) or 0)
        advance += float(obj.get('advance', 0) or 0)
    balance = total_revenue - advance

    return jsonify({
        "total": total,
        "in_progress": in_progress,
        "completed": completed,
        "total_sum": total_revenue,
        "total_expenses": total_expenses,
        "total_salary": salary,
        "total_advance": advance,
        "total_material_profit": total_material_profit,
        "profit": profit,
        "balance": balance,
    })

@app.route('/api/stats/detailed', methods=['GET'])
@login_required
def get_detailed_stats():
    # ОДИН запрос с LEFT JOIN вместо N+1 (для клиентов)
    objects_with_est = fetch_all(
        _sql_objects_estimate_aggregates('est_works', 'est_materials', 'est_mat_profit')
        + "WHERE o.user_id = ? GROUP BY o.id ORDER BY " + _SQL_OBJECTS_ORDER,
        (current_user.id,),
    )

    # Статистика по статусам
    status_dist = {}
    for obj in objects_with_est:
        s = obj.get('status', 'Неизвестно')
        status_dist[s] = status_dist.get(s, 0) + 1

    # Статистика по клиентам — ОДИН проход, без дополнительных запросов
    clients_stat = {}
    for obj in objects_with_est:
        c = obj.get('client', 'Без клиента')
        if c not in clients_stat:
            clients_stat[c] = {'name': c, 'count': 0, 'revenue': 0, 'profit': 0, 'mat_profit': 0}
        clients_stat[c]['count'] += 1

        ew = obj.get('est_works', 0) or 0
        em = obj.get('est_materials', 0) or 0
        emp = obj.get('est_mat_profit', 0) or 0

        rev, exp, prof = _compute_object_financials(
            obj.get('sum_work'), ew, em, emp, obj.get('expenses'), obj.get('salary')
        )

        clients_stat[c]['revenue'] += rev
        clients_stat[c]['profit'] += prof
        clients_stat[c]['mat_profit'] += emp

    top_clients = sorted(clients_stat.values(), key=lambda x: x['revenue'], reverse=True)[:10]

    # Выручка по месяцам — ОДИН проход, без дополнительных запросов
    months = {}
    for obj in objects_with_est:
        if obj['date_start'] and len(obj['date_start']) >= 7:
            m = obj['date_start'][:7]
            if m not in months:
                months[m] = {'revenue': 0, 'profit': 0, 'expenses': 0, 'salary': 0, 'material_profit': 0}

            ew = obj.get('est_works', 0) or 0
            em = obj.get('est_materials', 0) or 0
            emp = obj.get('est_mat_profit', 0) or 0

            rev, exp, prof = _compute_object_financials(
                obj.get('sum_work'), ew, em, emp, obj.get('expenses'), obj.get('salary')
            )

            months[m]['revenue'] += rev
            months[m]['profit'] += prof
            months[m]['expenses'] += exp
            months[m]['salary'] += obj.get('salary', 0)
            months[m]['material_profit'] += emp
    
    # Подсчёт за сегодня, месяц, год
    from datetime import datetime
    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')
    month_str = now.strftime('%Y-%m')
    year_str = now.strftime('%Y')
    
    today_count = sum(1 for o in objects_with_est if o.get('date_start', '').startswith(today_str))
    month_count = sum(1 for o in objects_with_est if o.get('date_start', '').startswith(month_str))
    year_count = sum(1 for o in objects_with_est if o.get('date_start', '').startswith(year_str))

    # Топ должников
    debtors = {}
    for obj in objects_with_est:
        ew = obj.get('est_works', 0) or 0
        em = obj.get('est_materials', 0) or 0
        rev = _work_revenue_once(obj.get('sum_work'), ew) + em
        debt = rev - (obj.get('advance', 0) or 0)
        if debt > 0 and obj.get('status') not in OBJECT_STATUSES_NOT_DEBT:
            c = obj.get('client', 'Без клиента')
            if c not in debtors:
                debtors[c] = {'name': c, 'debt': 0}
            debtors[c]['debt'] += debt
    
    top_debtors = sorted(debtors.values(), key=lambda x: x['debt'], reverse=True)[:5]
    
    # Сравнение месяцев
    month_comparison = {}
    sorted_months = sorted(months.keys(), reverse=True)
    if len(sorted_months) >= 2:
        month_comparison = {
            'current': months[sorted_months[0]],
            'previous': months[sorted_months[1]]
        }
    
    total_mat_profit = sum(m['material_profit'] for m in months.values())
    
    return jsonify({
        'today': {'count': today_count},
        'month': {'count': month_count},
        'year': {'count': year_count},
        'top_clients': top_clients,
        'status_distribution': status_dist,
        'monthly_trend': dict(sorted(months.items())),
        'top_materials': [],
        'top_debtors': top_debtors,
        'forecast_profit': sum(m['profit'] for m in months.values()),
        'total_material_profit': total_mat_profit,
        'avg_material_margin': 0,
        'month_comparison': month_comparison
    })

@app.route('/api/debts', methods=['GET'])
@login_required
def get_debts():
    # Выручка как в списке объектов: sum_work + работы и материалы по сметам
    objects = fetch_all(
        _sql_objects_estimate_aggregates('estimate_works', 'estimate_materials', 'estimate_material_profit')
        + "WHERE o.user_id = ? GROUP BY o.id ORDER BY " + _SQL_OBJECTS_ORDER,
        (current_user.id,),
    )

    debts = {}
    for obj in objects:
        if obj.get('status') in OBJECT_STATUSES_NOT_DEBT:
            continue
        ew = obj.get('estimate_works', 0) or 0
        em = obj.get('estimate_materials', 0) or 0
        revenue = _work_revenue_once(obj.get('sum_work'), ew) + em
        advance = obj.get('advance', 0) or 0
        debt = revenue - advance
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
    start = request.args.get('start', '')
    end = request.args.get('end', '')

    base = (
        _sql_objects_estimate_aggregates('estimate_works', 'estimate_materials', 'estimate_material_profit')
        + "WHERE o.user_id = ? "
    )
    params = [current_user.id]

    if start:
        base += " AND o.date_start >= ?"
        params.append(start)
    if end:
        base += " AND o.date_start <= ?"
        params.append(end + ' 23:59:59')

    base += " GROUP BY o.id ORDER BY " + _SQL_OBJECTS_ORDER
    objects = fetch_all(base, tuple(params))

    total_revenue = 0
    total_expenses = 0
    total_salary = 0
    total_profit = 0
    total_debt = 0
    total_material_profit = 0

    for obj in objects:
        ew = obj.get('estimate_works', 0) or 0
        em = obj.get('estimate_materials', 0) or 0
        emp = obj.get('estimate_material_profit', 0) or 0

        tr, te, tp = _compute_object_financials(
            obj.get('sum_work'), ew, em, emp, obj.get('expenses'), obj.get('salary')
        )
        obj['total_revenue'] = tr
        obj['total_expenses'] = te
        obj['total_profit'] = tp
        obj['balance'] = tr - float(obj.get('advance', 0) or 0)
        
        total_revenue += obj['total_revenue']
        total_expenses += obj['total_expenses']
        total_salary += obj.get('salary', 0)
        total_profit += obj['total_profit']
        total_material_profit += emp
        if obj['balance'] > 0 and obj.get('status') not in OBJECT_STATUSES_NOT_DEBT:
            total_debt += obj['balance']
    
    return jsonify({
        'objects': objects,
        'totals': {
            'revenue': total_revenue,
            'expenses': total_expenses,
            'salary': total_salary,
            'profit': total_profit,
            'debt': total_debt,
            'material_profit': total_material_profit
        }
    })

def _recalc_all_salaries_on_startup():
    """Пересчитать зарплаты при старте — отдельно для каждого user_id (мультиарендность)."""
    try:
        from collections import defaultdict
        from database import fetch_all, execute

        valid_statuses = OBJECT_STATUSES_SALARY
        placeholders = ','.join(['?' for _ in valid_statuses])
        placeholders2 = ','.join(['?' for _ in valid_statuses])

        uid_rows = fetch_all(
            "SELECT user_id FROM objects WHERE user_id IS NOT NULL "
            "UNION SELECT user_id FROM workers WHERE user_id IS NOT NULL"
        )
        uids = sorted({r['user_id'] for r in uid_rows if r.get('user_id') is not None})

        for uid in uids:
            objs = fetch_all(
                f"""SELECT id, date_start FROM objects
                    WHERE user_id = ? AND date_start != '' AND status IN ({placeholders})""",
                [uid] + list(valid_statuses),
            )
            by_date = defaultdict(list)
            for o in objs:
                d = o['date_start'][:10]
                by_date[d].append(o['id'])

            workers = fetch_all(
                "SELECT daily_rate FROM workers WHERE is_active = 1 AND user_id = ?",
                (uid,),
            )
            total_rate = sum(w['daily_rate'] for w in workers) if workers else 0

            if total_rate > 0:
                for _date, ids in by_date.items():
                    num = len(ids)
                    salary = round(total_rate / num, 2) if num > 0 else 0
                    for oid in ids:
                        execute(
                            "UPDATE objects SET salary = ? WHERE id = ? AND user_id = ?",
                            (salary, oid, uid),
                        )
                print(f"[OK] user {uid}: пересчитаны зарплаты {len(objs)} активных объектов, Σ ставок {total_rate}/день")
            else:
                for o in objs:
                    execute("UPDATE objects SET salary = 0 WHERE id = ? AND user_id = ?", (o['id'], uid))
                if objs:
                    print(f"[WARN] user {uid}: нет активных рабочих — обнулены зарплаты активных объектов")

            inactive = fetch_all(
                f"""SELECT id FROM objects
                    WHERE user_id = ? AND status NOT IN ({placeholders2})""",
                [uid] + list(valid_statuses),
            )
            for obj in inactive:
                execute(
                    "UPDATE objects SET salary = 0 WHERE id = ? AND user_id = ?",
                    (obj['id'], uid),
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
