from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from database import fetch_one, fetch_all, execute, init_db, delete_user_data
from extensions import limiter
import bcrypt
from datetime import datetime
import os

auth_bp = Blueprint('auth', __name__)


def _register_disabled():
    return os.environ.get('DISABLE_REGISTER', '').lower() in ('1', 'true', 'yes')

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
    if request.method == 'POST':
        u = request.form['username'].strip()
        p = request.form['password']
        
        if not u or not p:
            flash('Заполните все поля', 'error')
            return render_template('auth/register.html')
            
        if len(p) < 8:
            flash('Пароль минимум 8 символов', 'error')
            return render_template('auth/register.html')
        
        # Проверка занятости
        if fetch_one("SELECT id FROM users WHERE username = ?", (u,)):
            flash('Пользователь уже существует', 'error')
            return render_template('auth/register.html')

        # Первый юзер = админ
        count = fetch_one("SELECT COUNT(*) as c FROM users")['c']
        role = 'admin' if count == 0 else 'user'
        
        try:
            execute("INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                    (u, hash_pw(p), role, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            flash('Регистрация успешна! Войдите.', 'success')
            return redirect('/login')
        except Exception as e:
            flash(f'Ошибка: {e}', 'error')
            
    return render_template('auth/register.html')

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
    users = fetch_all("SELECT * FROM users ORDER BY id")
    return render_template('auth/users.html', users=users)

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
