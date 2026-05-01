# Конфиг Gunicorn: PORT задаёт Railway; 8080 — совпадение с default target port в панели Networking.
# Не вызывать init_db() в post_worker_init: к моменту старта воркера PostgreSQL на Railway может быть
# ещё недоступен — воркер не слушает порт, healthcheck на /health падает (service unavailable).
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"
worker_class = "gthread"
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
threads = 4
timeout = 300
graceful_timeout = 60
