# 🚀 Деплой на Railway

## 📁 Структура для деплоя
```
ObjectAccounting/
├── app_objects.py          # Главный сервер
├── auth.py                 # Аутентификация
├── estimate_module.py      # Модуль смет
├── templates/              # HTML шаблоны
├── requirements.txt        # Зависимости Python
├── Procfile                # Команда запуска для Railway
└── .gitignore              # Игнорирование БД и кэша
```

## ⚙️ Настройка в Railway Dashboard

### 1. Подключение репозитория
1. Зайдите в свой проект: https://railway.com/project/cfc00660-22af-44b7-9d44-7e95b270d139
2. Нажмите **"+ New"** → **"GitHub Repo"**
3. Выберите репозиторий с этим проектом

### 2. Persistent Volume (ВАЖНО!)
Так как каждый пользователь имеет свою SQLite БД в папке `user_data/`, нужно подключить постоянный диск:
1. В Railway нажмите **"+ New"** → **"Volume"**
2. Name: `user_data`
3. Mount Path: `/app/user_data`
4. Size: `1 GB` (или больше по необходимости)

### 3. Environment Variables
Нажмите на сервис → **"Variables"** → добавьте:
| Variable | Value | Описание |
|----------|-------|----------|
| `SECRET_KEY` | `сгенерируйте-случайную-строку` | Секрет для сессий Flask |
| `FLASK_DEBUG` | `0` | Выкл отладку в продакшене |

Генерация SECRET_KEY:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 4. Settings
- **Start Command**: оставьте пустым (Railway использует `Procfile`)
- **Root Directory**: оставьте пустым (если проект в корне репо)
- **Python Version**: автоматически определится из `requirements.txt`

## 🌐 После деплоя
1. Railway выдаст домен вида: `your-project.up.railway.app`
2. Откройте его в браузере
3. Зарегистрируйте первого пользователя (он станет админом автоматически)
4. Данные пользователей будут сохраняться в Persistent Volume

## ⚠️ Важные замечания
- **SQLite**: Хранит данные в `user_data/`. Volume гарантирует сохранность при перезапусках.
- **Масштабирование**: Для >1 пользователя рассмотрите переход на PostgreSQL.
- **Бэкапы**: Скачивайте папку `user_data/` вручную или настройте скрипт бэкапа в Railway Cron.

## 🔄 Обновление кода
1. Запушьте изменения в GitHub
2. Railway автоматически перезапустит сервис с новым кодом
3. Данные в `user_data/` сохранятся благодаря Volume
