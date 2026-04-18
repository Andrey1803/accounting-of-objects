import sqlite3
import sys
sys.stdout.reconfigure(encoding='utf-8')
from auth import hash_pw

# Создаём тестового пользователя
conn = sqlite3.connect('app_data.db')
c = conn.cursor()

try:
    c.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
              ('testuser2', hash_pw('test1234'), 'user', '2026-04-10'))
    conn.commit()
    print("OK: testuser2 / test1234")
except Exception as e:
    if 'UNIQUE' in str(e):
        print("EXISTS")
    else:
        print(f"ERROR: {e}")
finally:
    conn.close()
