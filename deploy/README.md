# deploy — Файлы для Railway

Содержит только файлы, необходимые для деплоя на Railway.

## Что внутри
```
deploy/
├── app_objects.py          # Точка входа (gunicorn запускает это)
├── auth.py                 # Авторизация
├── estimate_module.py      # Сметы и каталог
├── database.py             # DB (SQLite/PostgreSQL)
├── price_sync.py           # Синхронизация цен
├── templates/              # Jinja2 шаблоны
├── static/                 # Статика (CSS, JS, иконки)
├── requirements.txt        # Зависимости
└── Procfile                # Команда запуска для Railway
```

## Деплой на Railway
1. Откройте https://railway.com
2. Подключите GitHub или загрузите файлы из этой папки
3. Установите переменные:
   - `SECRET_KEY` — любой случайный ключ
   - `FLASK_DEBUG=0`
   - `DISABLE_REGISTER=1` — закрыть регистрацию (только вход)
   - `SESSION_COOKIE_SECURE=1` — если сайт открывается по HTTPS
4. Railway автоматически запустит `gunicorn` через Procfile

## Procfile
```
gunicorn app_objects:app --timeout 120 --bind 0.0.0.0:$PORT
```
