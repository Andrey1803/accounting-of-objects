"""Минимальные смоук-тесты (без БД с данными)."""
import os
import sys
import tempfile

# Корень проекта = родитель папки tests
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "source"))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-smoke-only")
_db = os.path.join(tempfile.gettempdir(), "oa_smoke_test.db")
if os.path.isfile(_db):
    try:
        os.remove(_db)
    except OSError:
        pass
os.environ["DB_FILE"] = _db


def test_health_and_extensions_import():
    import extensions  # noqa: F401
    import app_objects

    app = app_objects.app
    c = app.test_client()
    r = c.get("/health")
    assert r.status_code == 200
    assert r.get_json().get("status") == "ok"


def test_resolve_object_id_helper():
    import estimate_module

    assert callable(estimate_module._resolve_object_id_for_user)
