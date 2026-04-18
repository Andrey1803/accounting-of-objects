import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, 'd:\\Мои документы\\Рабочий стол\\hobby\\Projects\\ObjectAccounting')

from app_objects import app
from database import fetch_one, execute

print("Testing add_object_worker via Flask test client...")

with app.test_client() as client:
    # Логин
    r = client.post('/login', data={'username': 'Андрей Емельянов', 'password': 'temp1234'}, follow_redirects=True)
    print(f"Login: {r.status_code}")
    
    # Получаем объект 20
    r = client.get('/api/objects/20/workers')
    print(f"Before - workers count: {len(r.json.get('assignments', []))}")
    
    # Добавляем рабочего
    print("\nAdding worker 2 to object 20...")
    r = client.post('/api/objects/20/workers', 
                    json={'worker_id': 2, 'work_date': '2026-04-10', 'days_worked': 1},
                    content_type='application/json')
    print(f"Status: {r.status_code}")
    print(f"Response: {r.json}")
    
    if r.status_code != 200 and r.status_code != 201:
        print(f"\nFull response data: {r.get_json()}")
    
    # Проверяем результат
    r = client.get('/api/objects/20/workers')
    data = r.json
    print(f"\nAfter - workers count: {len(data.get('assignments', []))}")
    print(f"Total salary: {data.get('total_salary', 0)}")
    for a in data.get('assignments', []):
        print(f"  - {a['full_name']}: {a['days_worked']} days, {a['total_pay']} rub")
