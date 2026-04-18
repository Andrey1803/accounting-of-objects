import requests
import sys
sys.stdout.reconfigure(encoding='utf-8')

BASE_URL = 'http://127.0.0.1:5000'

session = requests.Session()

# 1. Логин
print("1. Логинимся...")
login_data = {'username': 'admin', 'password': 'admin'}
resp = session.post(f'{BASE_URL}/login', data=login_data, allow_redirects=False)
print(f"   Login status: {resp.status_code}")

# 2. Получаем CSRF токен
print("\n2. Получаем CSRF токен...")
csrf_resp = session.get(f'{BASE_URL}/api/csrf-token')
csrf_token = csrf_resp.json().get('csrf_token')
print(f"   CSRF token: {csrf_token[:20]}...")

# 3. Получаем рабочих
print("\n3. Получаем рабочих...")
workers_resp = session.get(f'{BASE_URL}/api/workers')
workers = workers_resp.json()
print(f"   Найдено рабочих: {len(workers)}")
for w in workers:
    print(f"   - {w['full_name']} (id={w['id']}, rate={w['daily_rate']})")

# 4. Получаем объекты
print("\n4. Получаем объекты...")
objs_resp = session.get(f'{BASE_URL}/api/objects')
objs = objs_resp.json()
print(f"   Найдено объектов: {len(objs)}")
for o in objs[:3]:
    print(f"   - {o['name']} (id={o['id']}, salary={o['salary']})")

if not objs:
    print("Нет объектов для теста!")
    sys.exit(1)

obj_id = objs[0]['id']

# 5. Получаем текущих рабочих объекта
print(f"\n5. Получаем рабочих объекта #{obj_id}...")
check_resp = session.get(f'{BASE_URL}/api/objects/{obj_id}/workers')
check_data = check_resp.json()
print(f"   Рабочих на объекте: {len(check_data.get('assignments', []))}")
print(f"   Общая зарплата: {check_data.get('total_salary', 0)}")

# 6. Добавляем рабочего
if workers:
    worker_id = workers[0]['id']
    print(f"\n6. Добавляем рабочего #{worker_id} к объекту #{obj_id}...")
    
    add_data = {
        'worker_id': worker_id,
        'work_date': '2026-04-10',
        'days_worked': 2
    }
    
    add_resp = session.post(f'{BASE_URL}/api/objects/{obj_id}/workers', json=add_data)
    print(f"   Add worker status: {add_resp.status_code}")
    print(f"   Response: {add_resp.json()}")
    
    # 7. Проверяем результат
    print(f"\n7. Проверяем рабочих объекта #{obj_id}...")
    check_resp2 = session.get(f'{BASE_URL}/api/objects/{obj_id}/workers')
    check_data2 = check_resp2.json()
    print(f"   Рабочих на объекте: {len(check_data2.get('assignments', []))}")
    print(f"   Общая зарплата: {check_data2.get('total_salary', 0)}")
    for a in check_data2.get('assignments', []):
        print(f"   - {a['full_name']}: {a['days_worked']} дней, {a['total_pay']} руб")
else:
    print("Нет рабочих для добавления!")

print("\n✅ Тест завершён!")
