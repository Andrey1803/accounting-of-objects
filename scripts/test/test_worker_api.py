import requests
import sys
sys.stdout.reconfigure(encoding='utf-8')

# Авторизация
session = requests.Session()

# Логинимся как Андрей Емельянов (временный пароль)
login_data = {'username': 'Андрей Емельянов', 'password': 'temp1234'}
resp = session.post('http://127.0.0.1:5000/login', data=login_data, allow_redirects=False)
print(f"Login status: {resp.status_code}")

if resp.status_code in [301, 302, 303]:
    session.get('http://127.0.0.1:5000/', allow_redirects=True)
    print("Logged in!")
else:
    print("Login failed!")
    sys.exit(1)

# Получаем CSRF токен
csrf_resp = session.get('http://127.0.0.1:5000/api/csrf-token')
print(f"CSRF response: {csrf_resp.status_code}")
csrf_token = csrf_resp.json().get('csrf_token')
print(f"CSRF token: {csrf_token[:10]}...")

# Получаем рабочих
workers_resp = session.get('http://127.0.0.1:5000/api/workers')
print(f"Workers: {workers_resp.json()}")

# Пробуем добавить рабочего к объекту 20
add_data = {
    'worker_id': 2,
    'work_date': '2026-04-10',
    'days_worked': 1
}

# Без CSRF (как сейчас в фронтенде)
add_resp = session.post('http://127.0.0.1:5000/api/objects/20/workers', json=add_data)
print(f"Add worker (no CSRF): {add_resp.status_code}")
print(f"Response: {add_resp.json()}")

# Проверим worker_assignments
check_resp = session.get('http://127.0.0.1:5000/api/objects/20/workers')
print(f"Object workers: {check_resp.json()}")
