# ObjectAccounting — Система учёта объектов и смет

## Обзор проекта

Flask-приложение для учёта строительных/ремонтных объектов, клиентов, рабочих и создания смет. Поддерживает многопользовательский режим с разграничением данных по пользователям.

**Основные возможности:**
- Управление объектами (создание, редактирование, статусы)
- Управление клиентами
- Управление рабочими и их привязка к объектам (расчёт зарплаты по дням)
- Создание и редактирование смет (работы + материалы)
- Каталог материалов и работ с древовидной структурой категорий
- Статистика, отчёты, аналитика по прибыли/долгам
- Оффлайн-режим через Service Worker + IndexedDB
- Синхронизация цен с внешним источником (opt-akvabreg.by)
- Парсинг PDF-прайслистов с автоматическим сопоставлением с каталогом

## Технологии и зависимости

| Технология | Назначение |
|---|---|
| **Python 3.x** | Основной язык |
| **Flask 3.0** | Web-фреймворк |
| **Flask-Login 0.6** | Аутентификация и сессии |
| **bcrypt 4.1** | Хеширование паролей |
| **SQLite** | Локальная база данных (WAL режим) |
| **PostgreSQL** | Продакшен (Railway) |
| **openpyxl 3.1** | Работа с Excel |
| **gunicorn 21.2** | WSGI-сервер для продакшена |
| **Service Worker + IndexedDB** | Оффлайн-режим в браузере |

## Структура проекта

```
ObjectAccounting/
├── app_objects.py          # Главный Flask-сервер (маршруты, API)
├── auth.py                 # Модуль аутентификации (Blueprint /auth)
├── database.py             # Универсальный DB-модуль (SQLite/PostgreSQL, thread-local pooling)
├── estimate_module.py      # Модуль смет, каталога, цен (Blueprint /estimate)
├── price_sync.py           # Синхронизация цен с opt-akvabreg.by
├── requirements.txt        # Зависимости Python
├── Procfile                # Команда запуска для Railway (gunicorn)
├── build.spec              # Спецификация PyInstaller для .exe
│
├── templates/              # Jinja2 HTML-шаблоны
│   ├── auth/               # Логин, регистрация, админка
│   ├── clients/            # Страница клиентов
│   ├── debts/              # Страница долгов
│   ├── estimate/           # Сметы, каталог, редактор
│   ├── objects/            # Страница объектов
│   ├── profit/             # Страница прибыли
│   ├── report/             # Страница отчётов
│   ├── stats/              # Страница статистики
│   ├── workers/            # Страница рабочих
│   └── offline.html        # Страница оффлайн-режима
│
├── static/                 # Статические файлы
│   ├── js/                 # JavaScript-файлы (offline.js, utils.js)
│   ├── icons/              # Иконки PWA
│   ├── images/             # Изображения
│   ├── manifest.json       # Web App Manifest
│   └── service-worker.js   # Service Worker (кэш, оффлайн, фоновая синхронизация)
│
├── data/                   # Данные (Excel-файлы и пр.)
├── backups/                # Бэкапы БД
│
└── *.bat                   # Windows-скрипты запуска/бэкапа/утилит
```

## Запуск и разработка

### Локальный запуск (Windows)

**Вариант 1 — BAT-файл:**
```bat
ЗАПУСТИТЬ.bat
```
Этот скрипт запускает `python app_objects.py` в фоновом окне и открывает браузер на `http://127.0.0.1:5000/`.

**Вариант 2 — вручную:**
```bat
python app_objects.py
```
Сервер запустится на `http://127.0.0.1:5000`.

**Вариант 3 — в фоне:**
```bat
ЗАПУСТИТЬ-ФОН.bat
```

### Установка зависимостей
```bat
pip install -r requirements.txt
```

### Продакшен (Railway)
1. Подключите GitHub-репозиторий к Railway
2. Добавьте Persistent Volume на `/app/user_data` (для SQLite)
3. Установите `SECRET_KEY` в переменных окружения
4. Railway автоматически использует `Procfile`: `gunicorn app_objects:app --timeout 120 --bind 0.0.0.0:$PORT`

### Переменные окружения
| Переменная | Описание |
|---|---|
| `SECRET_KEY` | Ключ сессий Flask (генерируется автоматически при первом запуске в `.secret_key`) |
| `FLASK_DEBUG` | `1` для режима отладки, `0` для продакшена |
| `DATABASE_URL` | URL PostgreSQL (если задан — используется вместо SQLite) |
| `PORT` | Порт сервера (по умолчанию 5000) |

## Архитектура базы данных

### Основные таблицы
- **users** — пользователи (username, password_hash, role: admin/user)
- **objects** — объекты строительства (дата, клиент, сумма, расходы, статус, зарплата рабочих)
- **clients** — клиенты
- **workers** — рабочие (ФИО, телефон, дневная ставка, дата найма)
- **worker_assignments** — привязка рабочих к объектам (дата, дни, оплата)
- **categories** — категории каталога (древовидная структура, type: material/work)
- **catalog_materials** — материалы (цена закупки/розница, категория, бренд, тип)
- **catalog_works** — работы (цена, описание)
- **estimates** — сметы (номер, дата, объект, статус, НДС, наценка, скидка)
- **estimate_items** — строки сметы (секция: work/material, количество, цена, итог, прибыль)

### Особенности database.py
- **Thread-local connection pooling** — одно соединение на поток, переиспользуется между запросами
- **Универсальность** — автоматически выбирает SQLite или PostgreSQL по `DATABASE_URL`
- **Транзакции** — `execute_many()` выполняет несколько запросов в одной транзакции
- **WAL режим** для SQLite — лучшая конкурентность

## API (основные эндпоинты)

### Аутентификация (`/auth`)
- `GET/POST /login` — вход
- `GET/POST /register` — регистрация (первый пользователь = admin)
- `GET /logout` — выход
- `GET /admin/users` — админ-панель

### Объекты (`/api/objects`)
- `GET /api/objects` — список объектов
- `GET /api/objects-with-estimates` — объекты с данными смет (один запрос с JOIN)
- `POST /api/objects` — создать объект
- `PUT /api/objects/<id>` — обновить объект
- `DELETE /api/objects/<id>` — удалить (каскадно: сметы + строки)
- `POST /api/objects/recalc-all-salaries` — пересчитать зарплаты всех объектов

### Рабочие (`/api/workers`)
- `GET/POST /api/workers` — список/создание
- `PUT/DELETE /api/workers/<id>` — обновление/удаление
- `GET/POST /api/objects/<id>/workers` — привязка к объекту
- `DELETE /api/objects/<id>/workers/<assignment_id>` — отвязать рабочего

### Клиенты (`/api/clients`)
- `GET/POST /api/clients` — список/создание
- `PUT/DELETE /api/clients/<id>` — обновление/удаление

### Статистика и отчёты
- `GET /api/stats` — общая статистика
- `GET /api/stats/detailed` — расширенная статистика (клиенты, тренды, должники)
- `GET /api/debts` — долги клиентов
- `GET /api/report?start=&end=` — отчёт за период

### Сметы (`/estimate/api`)
- `GET/POST /estimate/api/estimates` — список/создание сметы
- `GET/PUT/DELETE /estimate/api/estimates/<id>` — просмотр/обновление/удаление
- `POST /estimate/api/estimates/<id>/items` — добавить строку
- `PUT/DELETE /estimate/api/items/<id>` — обновить/удалить строку

### Каталог (`/estimate/api/catalog`)
- `GET /estimate/api/catalog/categories` — категории
- `GET /estimate/api/catalog/categories/tree` — древовидная структура (категория → тип → бренд)
- `POST/DELETE /estimate/api/catalog/categories/<id>` — управление категориями

### Синхронизация цен
- `POST /estimate/api/price-sync/config` — сохранить логин/пароль от opt-akvabreg.by
- `POST /estimate/api/price-sync/run` — запустить синхронизацию
- `POST /estimate/api/price-sync/apply` — применить новые цены

### Парсинг PDF
- `POST /estimate/api/parse-pdf` — загрузить PDF, извлечь таблицы, сопоставить с каталогом

## Безопасность

- **CSRF-токены** — все мутационные POST/PUT/DELETE запросы требуют CSRF-токен (в заголовке `X-CSRF-Token` или в форме)
- **bcrypt** — пароли хешируются bcrypt
- **Flask-Login** — сессии с `remember=True`
- **Многопользовательская изоляция** — все запросы фильтруются по `user_id = current_user.id`
- **Первый пользователь = admin** — при регистрации автоматически получает роль администратора

## Оффлайн-режим

Приложение поддерживает оффлайн-работу через:
- **Service Worker** (`static/service-worker.js`) — кэширование статики, API-запросы через сеть с фоллбэком в кэш
- **IndexedDB** (`AccountingDB`) — очередь оффлайн-действий (`syncQueue`)
- **Background Sync** — при восстановлении соединения данные автоматически синхронизируются
- **Offline страница** (`/offline`) — отображается при отсутствии сети

## Конвенции разработки

### Код
- Python: Flask с Blueprint для модульности
- База данных: параметризованные запросы (`?` для SQLite, `%s` для PostgreSQL)
- Thread-local connections для эффективности
- CSRF-защита на всех мутационных эндпоинтах
- CSRF-токен инжектится в шаблоны через `@app.context_processor`

### Оптимизация запросов
- Используется `LEFT JOIN` вместо N+1 запросов (объекты + сметы + строки)
- `execute_many()` для пакетных операций в одной транзакции
- Агрегация статистики за один проход по данным

## Утилиты и скрипты

| Файл | Назначение |
|---|---|
| `import_akvabreg.py` | Импорт каталога из Excel akvabreg_mega.xlsx |
| `import_excel_catalog.py` | Импорт каталога из Excel |
| `import_prices.py` | Импорт цен |
| `price_sync.py` | Синхронизация цен с opt-akvabreg.by |
| `sync_categories.py` | Синхронизация категорий |
| `check_*.py` | Различные скрипты для проверки данных |
| `fix_*.py` | Скрипты исправления данных |
| `БЭКАП-БАЗЫ.bat` | Бэкап базы данных |
| `СОЗДАТЬ-ЯРЛЫК.bat` | Создание ярлыка на рабочем столе |

## Важные заметки

- **SQLite WAL**: Файлы `.db-shm` и `.db-wal` — нормальное явление для WAL-режима. Не удаляйте их вручную.
- **Бэкапы**: Папка `backups/` хранит копии БД. Используйте `БЭКАП-БАЗЫ.bat` для создания.
- **Масштабирование**: Для многопользовательского режима рекомендуется PostgreSQL на Railway.
- **Сборка в .exe**: Используется PyInstaller со спецификацией `build.spec`.
