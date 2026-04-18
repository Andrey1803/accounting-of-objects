import sqlite3
import sys
sys.stdout.reconfigure(encoding='utf-8')
from auth import hash_pw

# Устанавливаем временный пароль для пользователя 1
conn = sqlite3.connect('app_data.db')
c = conn.cursor()

temp_pw = 'temp1234'
new_hash = hash_pw(temp_pw)

c.execute("UPDATE users SET password_hash = ? WHERE id = 1", (new_hash,))
conn.commit()

print(f"Password for user 1 changed to: {temp_pw}")
print("You can now login as 'Андрей Емельянов' / 'temp1234'")

conn.close()
