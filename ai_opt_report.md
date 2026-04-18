# AI Workflow Optimization Report

**Generated:** 2026-04-10 20:22:57 UTC
**Tool version:** 2.0.0

## Workflow Metrics

⏱ **Время выполнения:** 0.7 сек
📁 **Файлов просканировано:** 163
📊 **Строк кода:** 16,230
🔍 **Найдено:** 0 ошибок, 2 предупреждений, 180 info
📏 **Размер context.md:** 6,501 байт (~255 токенов*)

> \* оценка: 1 токен ≈ 4 байта UTF-8

## Project Scan Summary

- **Root:** `d:\Мои документы\Рабочий стол\hobby\Projects\ObjectAccounting`
- **Total files:** 163
- **Total lines of code:** 16,230
- **Total size:** 30763.1 KB
- **Scan time:** 222 ms

### File Types

- `.jpg`: 87
- `.py`: 39
- `.html`: 14
- `.js`: 5
- `.json`: 4
- `.md`: 3
- `.bat`: 3
- `(no ext)`: 2
- `.xlsx`: 1
- `.db-shm`: 1
- `.db-wal`: 1
- `.txt`: 1
- `.orig`: 1
- `.svg`: 1

### Key Files (6)

- `requirements.txt` — 8 lines, 119 bytes
- `database.py` — 324 lines, 13602 bytes
- `auth.py` — 112 lines, 4376 bytes
- `.gitignore` — 0 lines, 342 bytes
- `.qwen\settings.json` — 28 lines, 557 bytes
- `.qwen\settings.json.orig` — 0 lines, 69 bytes

## Code Validation

- **Files checked:** 39
- **Check time:** 351 ms
- **🔴 Errors:** 0
- **🟡 Warnings:** 2
- **ℹ️  Info:** 180

### Warnings

- 🟡 `test_add_worker_detailed.py:81` — [bare-except-py] Bare except перехватывает ВСЕ исключения. Укажи тип.
- 🟡 `test_worker2.py:42` — [bare-except-py] Bare except перехватывает ВСЕ исключения. Укажи тип.

### Info

- ℹ️  `ai_workflow_optimizer.py:1763` — [print-in-production] print() в продакшен-коде. Используй logging.
- ℹ️  `ai_workflow_optimizer.py:1775` — [print-in-production] print() в продакшен-коде. Используй logging.
- ℹ️  `ai_workflow_optimizer.py:1776` — [print-in-production] print() в продакшен-коде. Используй logging.
- ℹ️  `ai_workflow_optimizer.py:1777` — [print-in-production] print() в продакшен-коде. Используй logging.
- ℹ️  `ai_workflow_optimizer.py:1778` — [print-in-production] print() в продакшен-коде. Используй logging.
- ℹ️  `ai_workflow_optimizer.py:1784` — [print-in-production] print() в продакшен-коде. Используй logging.
- ℹ️  `ai_workflow_optimizer.py:1787` — [print-in-production] print() в продакшен-коде. Используй logging.
- ℹ️  `ai_workflow_optimizer.py:1789` — [print-in-production] print() в продакшен-коде. Используй logging.
- ℹ️  `ai_workflow_optimizer.py:1792` — [print-in-production] print() в продакшен-коде. Используй logging.
- ℹ️  `ai_workflow_optimizer.py:1794` — [print-in-production] print() в продакшен-коде. Используй logging.
- ℹ️  `ai_workflow_optimizer.py:1798` — [print-in-production] print() в продакшен-коде. Используй logging.
- ℹ️  `ai_workflow_optimizer.py:1799` — [print-in-production] print() в продакшен-коде. Используй logging.
- ℹ️  `ai_workflow_optimizer.py:1800` — [print-in-production] print() в продакшен-коде. Используй logging.
- ℹ️  `ai_workflow_optimizer.py:1801` — [print-in-production] print() в продакшен-коде. Используй logging.
- ℹ️  `ai_workflow_optimizer.py:1803` — [print-in-production] print() в продакшен-коде. Используй logging.
- ℹ️  `ai_workflow_optimizer.py:1804` — [print-in-production] print() в продакшен-коде. Используй logging.
- ℹ️  `ai_workflow_optimizer.py:1805` — [print-in-production] print() в продакшен-коде. Используй logging.
- ℹ️  `ai_workflow_optimizer.py:1811` — [print-in-production] print() в продакшен-коде. Используй logging.
- ℹ️  `ai_workflow_optimizer.py:1821` — [print-in-production] print() в продакшен-коде. Используй logging.
- ℹ️  `ai_workflow_optimizer.py:1825` — [print-in-production] print() в продакшен-коде. Используй logging.

> ... ещё 160 info-сообщений


## Session State

- **Session ID:** `39c5aa0488a3`
- **Started:** N/A
- **Last activity:** N/A
- **Prompts:** 0
- **Responses:** 0
- **Files touched:** 0
- **Decisions recorded:** 0


## Recommendations

### 🟡 Рекомендации
Найдено **2 предупреждений**. Рекомендую исправить пустые catch/except в первую очередь.

### 📦 Оптимизация контекста
Проект содержит **16,230 строк**. При работе с AI используй `--compress` для экономии контекстного окна.

## Context.md Preview

```markdown
# Project Context

**Generated:** 2026-04-10T20:22:57.005599+00:00
**Root:** `d:\Мои документы\Рабочий стол\hobby\Projects\ObjectAccounting`
**Total files:** 163
**Total lines:** 16,230
**Total size:** 30763.1 KB
**Scan time:** 222 ms

## Project Structure

```
[FILE] .gitignore
[DIR] .qwen
  [FILE] settings.json
  [FILE] settings.json.orig
[FILE] DEPLOY.md
[FILE] Procfile
[FILE] ai_opt_report.md
[FILE] ai_workflow_optimizer.py
[FILE] akvabreg_mega.xlsx
[FILE] app_data.db-shm
[FILE] app_data.db-wal
[FILE] app_objects.py
[FILE] auth.py
[FILE] catalog_images_mapping.json
[FILE] check_api.py
[FILE] check_categories.py
[FILE] check_data.py
[FILE] check_duplicates.py
[FILE] check_excel.py
[FILE] check_project.py
[FILE] check_schema.py
[FILE] check_workers_data.py
[FILE] clean_duplicates.py
[FILE] context.md
[FILE] create_test_user.py
[FILE] database.py
[FILE] editor_check.js
[FILE] estimate_module.py
[FILE] extract_images.py
[FILE] fix_all.py
[FILE] fix_encoding.py
[FILE] fix_images.py
[FIL
```