"""
Точка входа WSGI для gunicorn / Railway Railpack (по умолчанию: main:app).
Основное приложение по-прежнему в app_objects.py.
"""
from app_objects import app

__all__ = ["app"]
