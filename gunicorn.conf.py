# Конфиг Gunicorn: PORT задаёт Railway; 8080 — совпадение с default target port в панели Networking.
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"
worker_class = "gthread"
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
threads = 4
timeout = 300
graceful_timeout = 60
