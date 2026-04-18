# source — Исходный код для разработки

Содержит все файлы проекта для внесения изменений и разработки.

## Структура
```
source/
├── app_objects.py          # Главный Flask-сервер
├── auth.py                 # Модуль авторизации
├── estimate_module.py      # Модуль смет
├── database.py             # База данных
├── price_sync.py           # Синхронизация цен
├── scripts/                # Утилиты (check/fix/test/import/utils)
├── templates/              # HTML-шаблоны
├── static/                 # CSS/JS/иконки/PWA
├── requirements.txt        # Зависимости Python
├── package.json            # ESLint/Prettier
├── build.spec              # PyInstaller
├── *.bat                   # Скрипты запуска
└── ...                     # Конфиги (.gitignore, .eslintrc.json и т.д.)
```

## Запуск
```bat
python app_objects.py
```
или
```bat
ЗАПУСТИТЬ.bat
```
