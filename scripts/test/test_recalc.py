import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, 'd:\\Мои документы\\Рабочий стол\\hobby\\Projects\\ObjectAccounting')

from app_objects import app, recalc_object_salary
from database import fetch_one, fetch_all, execute

# Тестируем recalc_object_salary напрямую
print("Testing recalc_object_salary for object 20...")

with app.app_context():
    # Имитируем авторизованного пользователя
    from flask_login import login_user
    from auth import User
    
    # Находим пользователя 1
    user_data = fetch_one("SELECT * FROM users WHERE id = 1")
    if user_data:
        user = User(user_data['id'], user_data['username'], user_data['role'], user_data['created_at'])
        login_user(user)
        print(f"Logged in as: {user_data['username']}")
        
        # Теперь вызываем recalc_object_salary
        try:
            recalc_object_salary(20)
            print("recalc_object_salary completed successfully!")
            
            # Проверяем результат
            obj = fetch_one("SELECT salary FROM objects WHERE id = 20")
            print(f"Object 20 salary: {obj['salary']}")
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f"ERROR: {e}")
            print(tb)
    else:
        print("User 1 not found!")
