# -*- coding: utf-8 -*-
import requests
import sys
import io
import json

try:
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
except (io.UnsupportedOperation, AttributeError, OSError):
    pass

# Проверка API без авторизации
urls = [
    'http://127.0.0.1:5000/api/stats/detailed',
    'http://127.0.0.1:5000/api/objects-with-estimates',
    'http://127.0.0.1:5000/health'
]

for url in urls:
    try:
        r = requests.get(url, timeout=5)
        print(f"{url} -> HTTP {r.status_code}")
        if r.status_code == 200:
            print(f"   Response: {r.text[:200]}")
        elif r.status_code == 401:
            print("   Нужно авторизоваться")
    except Exception as e:
        print(f"{url} -> Error: {e}")
