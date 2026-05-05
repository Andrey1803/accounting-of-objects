from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from database import fetch_one, fetch_all, execute, init_db, delete_user_data, IS_POSTGRES
from extensions import limiter
import bcrypt
from datetime import datetime, timedelta
import os
import secrets

auth_bp = Blueprint('auth', __name__)


def _register_disabled():
    return os.environ.get('DISABLE_REGISTER', '').lower() in ('1', 'true', 'yes')


def _user_count():
    row = fetch_one("SELECT COUNT(*) AS c FROM users")
    return int(row["c"]) if row else 0


def _find_valid_invitation(token: str):
    t = (token or "").strip()
    if not t:
        return None
    if IS_POSTGRES:
        q = (
            "SELECT * FROM invitations WHERE token = ? AND used_count < max_uses "
            "AND (expires_at IS NULL OR expires_at > NOW())"
        )
    else:
        q = (
            "SELECT * FROM invitations WHERE token = ? AND used_count < max_uses "
            "AND (expires_at IS NULL OR expires_at = '' OR datetime(expires_at) > datetime('now'))"
        )
    return fetch_one(q, (t,))


def _consume_invitation(inv_id: int):
    execute("UPDATE invitations SET used_count = used_count + 1 WHERE id = ?", (inv_id,))


def _ensure_admin_schema_ready():
    """
    Страховка для прода: если миграции не применились после деплоя,
    поднимем нужные таблицы/колонки перед админ-операциями.
    """
    try:
        init_db()
    except Exception:
        # Не валим запрос прямо здесь — ниже обработаем и покажем пользователю понятное сообщение.
        pass

class User:
    def __init__(self, id, username, role, created_at):
        self.id = id
        self.username = username
        self.role = role
        self.created_at = created_at
    def is_authenticated(self): return True
    def is_active(self): return True
    def is_anonymous(self): return False
    def get_id(self): return str(self.id)

def hash_pw(pw):
    return bcrypt.hashpw(pw.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def check_pw(pw, hashed):
    if not pw or not hashed:
        return False
    try:
        return bcrypt.checkpw(pw.encode('utf-8'), hashed.encode('utf-8'))
    except (ValueError, TypeError, AttributeError):
        return False

@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit('20 per minute', methods=['POST'])
def login():
    if current_user.is_authenticated:
        return redirect('/')
    if request.method == 'POST':
        user = fetch_one("SELECT * FROM users WHERE username = ?", (request.form['username'],))
        if user and check_pw(request.form['password'], user['password_hash']):
            login_user(User(user['id'], user['username'], user['role'], user['created_at']), remember=True)
            next_url = request.args.get('next') or '/'
            if not next_url.startswith('/') or next_url.startswith('//'):
                next_url = '/'
            return redirect(next_url)
        flash('Неверный логин или пароль', 'error')
    return render_template('auth/login.html')

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if _register_disabled():
        flash('Регистрация отключена администратором', 'error')
        return redirect(url_for('auth.login'))

    n_users = _user_count()
    needs_invite = n_users > 0
    invite_prefill = (request.args.get('invite') or '').strip()

    if request.method == 'POST':
        u = request.form['username'].strip()
        p = request.form['password']
        p2 = request.form.get('password_confirm') or ''
        invite_key = (request.form.get('invite_key') or '').strip()

        if not u or not p:
            flash('Заполните все поля', 'error')
            return render_template(
                'auth/register.html',
                needs_invite=needs_invite,
                invite_prefill=invite_key or invite_prefill,
            )

        if p != p2:
            flash('Пароли не совпадают', 'error')
            return render_template(
                'auth/register.html',
                needs_invite=needs_invite,
                invite_prefill=invite_key or invite_prefill,
            )

        if len(p) < 8:
            flash('Пароль минимум 8 символов', 'error')
            return render_template(
                'auth/register.html',
                needs_invite=needs_invite,
                invite_prefill=invite_key or invite_prefill,
            )

        if fetch_one("SELECT id FROM users WHERE username = ?", (u,)):
            flash('Пользователь уже существует', 'error')
            return render_template(
                'auth/register.html',
                needs_invite=needs_invite,
                invite_prefill=invite_key or invite_prefill,
            )

        inv_row = None
        if needs_invite:
            inv_row = _find_valid_invitation(invite_key)
            if not inv_row:
                flash('Неверный или просроченный ключ приглашения. Запросите новый у администратора.', 'error')
                return render_template(
                    'auth/register.html',
                    needs_invite=True,
                    invite_prefill=invite_key,
                )

        role = 'admin' if n_users == 0 else 'user'

        try:
            execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                (u, hash_pw(p), role, datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
            )
            if inv_row:
                _consume_invitation(int(inv_row['id']))
            flash('Регистрация успешна! Войдите.', 'success')
            return redirect('/login')
        except Exception as e:
            flash(f'Ошибка: {e}', 'error')

    return render_template(
        'auth/register.html',
        needs_invite=needs_invite,
        invite_prefill=invite_prefill,
    )

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect('/login')

# ============================
# Админ-панель — управление пользователями
# ============================
def require_admin(f):
    """Декоратор: доступ только для администраторов"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_user.role != 'admin':
            flash('Доступ запрещён', 'error')
            return redirect('/')
        return f(*args, **kwargs)
    return decorated_function

@auth_bp.route('/admin/users')
@login_required
@require_admin
def admin_users():
    _ensure_admin_schema_ready()
    try:
        users = fetch_all("SELECT * FROM users ORDER BY id")
        return render_template('auth/users.html', users=users)
    except Exception as e:
        flash(f'Не удалось открыть список пользователей: {e}', 'error')
        return redirect('/')

@auth_bp.route('/admin/invites')
@login_required
@require_admin
def admin_invites():
    _ensure_admin_schema_ready()
    try:
        rows = fetch_all(
            "SELECT i.*, u.username AS creator_name FROM invitations i "
            "LEFT JOIN users u ON u.id = i.created_by ORDER BY i.id DESC"
        )
        return render_template('auth/invites.html', invitations=rows)
    except Exception as e:
        flash(f'Не удалось открыть приглашения: {e}', 'error')
        return redirect('/admin/users')


@auth_bp.route('/admin/invites/create', methods=['POST'])
@login_required
@require_admin
def admin_invite_create():
    _ensure_admin_schema_ready()
    note = (request.form.get('note') or '').strip()[:500]
    days_raw = request.form.get('valid_days', '').strip()
    max_uses = 1
    try:
        mu = int(request.form.get('max_uses') or 1)
        if 1 <= mu <= 50:
            max_uses = mu
    except ValueError:
        pass

    expires_at = None
    if days_raw:
        try:
            d = int(days_raw)
            if 1 <= d <= 365:
                expires_at = datetime.now() + timedelta(days=d)
        except ValueError:
            pass

    token = secrets.token_urlsafe(32)
    created = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    exp_val = expires_at.strftime('%Y-%m-%d %H:%M:%S') if expires_at else None

    try:
        execute(
            "INSERT INTO invitations (token, created_at, created_by, expires_at, max_uses, used_count, note) "
            "VALUES (?, ?, ?, ?, ?, 0, ?)",
            (token, created, current_user.id, exp_val, max_uses, note),
        )
    except Exception as e:
        flash(f'Не удалось создать приглашение: {e}', 'error')
        return redirect(url_for('auth.admin_invites'))

    flash(
        f'Новый ключ приглашения (одноразово покажите и отправьте): {token}',
        'success',
    )
    return redirect(url_for('auth.admin_invites'))


@auth_bp.route('/admin/invites/<int:inv_id>/delete', methods=['POST'])
@login_required
@require_admin
def admin_invite_delete(inv_id):
    _ensure_admin_schema_ready()
    try:
        execute("DELETE FROM invitations WHERE id = ?", (inv_id,))
        flash('Приглашение удалено', 'success')
    except Exception as e:
        flash(f'Не удалось удалить приглашение: {e}', 'error')
    return redirect(url_for('auth.admin_invites'))


@auth_bp.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@login_required
@require_admin
def admin_delete_user(user_id):
    if user_id == current_user.id:
        flash('Нельзя удалить текущего пользователя', 'error')
        return redirect('/admin/users')
    if user_id == 1:
        flash('Нельзя удалить главного администратора', 'error')
        return redirect('/admin/users')
    try:
        delete_user_data(user_id)
    except Exception as e:
        flash(f'Ошибка удаления: {e}', 'error')
        return redirect('/admin/users')
    flash('Пользователь и все его данные удалены', 'success')
    return redirect('/admin/users')
