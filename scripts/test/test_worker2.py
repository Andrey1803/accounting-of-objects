import requests
import sys
sys.stdout.reconfigure(encoding='utf-8')

BASE_URL = 'http://127.0.0.1:5000'
session = requests.Session()

print("1. Логин как demo...")
resp = session.post(f'{BASE_URL}/login', data={'username': 'demo', 'password': 'demo'}, allow_redirects=False)
print(f"   Status: {resp.status_code}")
if resp.status_code in [301, 302, 303]:
    session.get(f'{BASE_URL}/')
    print("   OK")

print("\n2. Рабочие...")
r = session.get(f'{BASE_URL}/api/workers')
print(f"   Status: {r.status_code}")
workers = r.json() if r.status_code == 200 else []
for w in workers:
    print(f"   - {w['full_name']} (id={w['id']})")

print("\n3. Объекты...")
r = session.get(f'{BASE_URL}/api/objects')
print(f"   Status: {r.status_code}")
objs = r.json() if r.status_code == 200 else []
for o in objs[:2]:
    print(f"   - {o['name']} (id={o['id']})")

if not objs or not workers:
    print("Нет данных!")
    sys.exit(1)

obj_id = objs[0]['id']
worker_id = workers[0]['id']

print(f"\n4. Добавляем рабочего {worker_id} к объекту {obj_id}...")
add_data = {'worker_id': worker_id, 'work_date': '2026-04-10', 'days_worked': 2}
r = session.post(f'{BASE_URL}/api/objects/{obj_id}/workers', json=add_data)
print(f"   Status: {r.status_code}")
try:
    print(f"   Response: {r.json()}")
except:
    print(f"   Text: {r.text[:300]}")

print(f"\n5. Проверка...")
r = session.get(f'{BASE_URL}/api/objects/{obj_id}/workers')
if r.status_code == 200:
    data = r.json()
    print(f"   Рабочих: {len(data.get('assignments', []))}")
    print(f"   Зарплата: {data.get('total_salary', 0)}")
    for a in data.get('assignments', []):
        print(f"   - {a['full_name']}: {a['days_worked']} дн., {a['total_pay']} руб")
