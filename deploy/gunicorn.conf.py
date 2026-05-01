# Конфиг Gunicorn: PORT задаёт Railway; 8080 — совпадение с default target port в панели Networking.
import logging
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"
worker_class = "gthread"
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
threads = 4
timeout = 300
graceful_timeout = 60


def post_worker_init(worker):
    """
    До приёма HTTP: миграции/схема БД. Иначе первый запрос к / блокировался на init_db(),
    а edge (Railway и др.) отдавал клиенту 503 с пустым телом.
    """
    try:
        from app_objects import ensure_db_initialized

        ensure_db_initialized()
    except Exception:
        logging.exception("gunicorn post_worker_init: ensure_db_initialized failed")
        raise
