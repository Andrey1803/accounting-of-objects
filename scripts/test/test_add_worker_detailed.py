import http.client
import json
import sys
sys.stdout.reconfigure(encoding='utf-8')

# Сначала логинимся через браузер
import requests

session = requests.Session()

# Логин как Андрей Емельянов (временный пароль)
print("Logging in as user 1...")
r = session.post('http://127.0.0.1:5000/login', data={'username': 'Андрей Емельянов', 'password': 'temp1234'}, allow_redirects=False)
print(f"Login status: {r.status_code}")

if r.status_code in [301, 302, 303]:
    session.get('http://127.0.0.1:5000/')
    print("Logged in!")
else:
    print("Login failed!")
    sys.exit(1)

# Проверяем объекты
print("\nGetting objects...")
r = session.get('http://127.0.0.1:5000/api/objects')
print(f"Status: {r.status_code}")
if r.status_code == 200:
    objects = r.json()
    print(f"Objects count: {len(objects)}")
    for o in objects[:3]:
        print(f"  - {o['name']} (id={o['id']})")
    if objects:
        obj_id = objects[0]['id']
    else:
        print("No objects!")
        sys.exit(1)
else:
    print("Failed to get objects")
    sys.exit(1)

# Проверяем рабочих
print("\nGetting workers...")
r = session.get('http://127.0.0.1:5000/api/workers')
print(f"Status: {r.status_code}")
if r.status_code == 200:
    workers = r.json()
    print(f"Workers count: {len(workers)}")
    for w in workers:
        print(f"  - {w['full_name']} (id={w['id']}, rate={w['daily_rate']})")
    if workers:
        worker_id = workers[0]['id']
    else:
        print("No workers!")
        sys.exit(1)
else:
    print("Failed to get workers")
    sys.exit(1)

# Пробуем добавить рабочего
print(f"\nAdding worker {worker_id} to object {obj_id}...")
data = {
    'worker_id': worker_id,
    'work_date': '2026-04-10',
    'days_worked': 1
}
print(f"Data: {data}")

r = session.post(f'http://127.0.0.1:5000/api/objects/{obj_id}/workers', json=data)
print(f"Status: {r.status_code}")
print(f"Content-Type: {r.headers.get('Content-Type', 'unknown')}")

if r.status_code == 200 or r.status_code == 201:
    print(f"Response: {r.json()}")
elif r.status_code == 500:
    print(f"ERROR 500!")
    print(f"Response text (first 1000 chars):")
    print(r.text[:1000])
else:
    try:
        print(f"Response: {r.json()}")
    except:
        print(f"Response: {r.text[:500]}")

# Проверяем, добавился ли рабочий
print(f"\nChecking object {obj_id} workers...")
r = session.get(f'http://127.0.0.1:5000/api/objects/{obj_id}/workers')
if r.status_code == 200:
    data = r.json()
    print(f"Assignments: {len(data.get('assignments', []))}")
    print(f"Total salary: {data.get('total_salary', 0)}")
    for a in data.get('assignments', []):
        print(f"  - {a['full_name']}: {a['days_worked']} days, {a['total_pay']} rub")
