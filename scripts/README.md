# Скрипты ObjectAccounting

Эта директория содержит вспомогательные скрипты для обслуживания системы.

## Структура

| Директория | Назначение |
|---|---|
| `check/` | Скрипты проверки данных и состояния проекта |
| `fix/` | Скрипты исправления данных (миграции, фиксы) |
| `test/` | Тестовые скрипты для проверки API и функций |
| `import/` | Скрипты импорта данных (Excel, внешние источники) |
| `utils/` | Разные утилиты (создание БД, ярлыков, работа с изображениями) |

## Запуск

Все скрипты запускаются из корня проекта:

```bat
python scripts\check\check_project.py
python scripts\fix\fix_categories.py
python scripts\test\test_api.py
python scripts\import\import_excel_catalog.py
python scripts\utils\create_test_user.py
```

## Основные скрипты

### check/
| Файл | Описание |
|---|---|
| `check_project.py` | Полная проверка проекта (файлы, БД, шаблоны) |
| `check_api.py` | Проверка API-эндпоинтов |
| `check_categories.py` | Проверка категорий каталога |
| `check_data.py` | Общая проверка данных |
| `check_duplicates.py` | Поиск дубликатов в каталоге |
| `check_excel.py` | Проверка Excel-файла |
| `check_schema.py` | Проверка схемы БД |
| `check_tree.py` | Проверка древовидной структуры категорий |
| `check_workers_data.py` | Проверка данных рабочих |

### fix/
| Файл | Описание |
|---|---|
| `fix_all.py` | Комплексное исправление данных |
| `fix_all_item_types.py` | Исправление типов всех элементов сметы |
| `fix_categories.py` | Исправление категорий |
| `fix_categories_mass.py` | Массовое исправление категорий |
| `fix_encoding.py` | Исправление кодировки |
| `fix_images.py` | Исправление привязки изображений |
| `fix_item_types.py` | Исправление типов элементов |
| `fix_item_types_v2.py` | Исправление типов элементов (v2) |
| `fix_worker_rate.py` | Исправление ставок рабочих |

### import/
| Файл | Описание |
|---|---|
| `import_akvabreg.py` | Импорт каталога с сайта opt-akvabreg.by |
| `import_categories.py` | Импорт категорий |
| `import_excel_catalog.py` | Импорт каталога из Excel |
| `import_prices.py` | Импорт цен |
| `price_sync.py` | Синхронизация цен с opt-akvabreg.by |
| `sync_categories.py` | Синхронизация категорий |

### test/
| Файл | Описание |
|---|---|
| `test_api.py` | Тестирование API |
| `test_add_worker.py` | Тест добавления рабочего |
| `test_add_worker_detailed.py` | Детальный тест добавления рабочего |
| `test_direct_insert.py` | Прямая вставка в БД |
| `test_import.py` | Тест импорта |
| `test_recalc.py` | Тест перерасчёта |
| `test_route.py` | Тест маршрутов |
| `test_via_flask.py` | Тест через Flask test client |
| `test_worker_api.py` | Тест API рабочих |
| `test_worker_insert.py` | Тест вставки рабочего |

### utils/
| Файл | Описание |
|---|---|
| `ai_workflow_optimizer.py` | AI-оптимизатор рабочих процессов |
| `clean_duplicates.py` | Очистка дубликатов |
| `create_empty_db.py` | Создание пустой БД |
| `create_shortcut.py` | Создание ярлыка на рабочем столе |
| `create_test_user.py` | Создание тестового пользователя |
| `extract_images.py` | Извлечение изображений |
| `link_all_images.py` | Привязка всех изображений к каталогу |
| `link_estimates.py` | Привязка смет к объектам |
| `recalc_worker_costs.py` | Перерасчёт затрат на рабочих |
| `set_temp_pw.py` | Установка временного пароля |
| `update_catalog_html.py` | Обновление HTML каталога |
