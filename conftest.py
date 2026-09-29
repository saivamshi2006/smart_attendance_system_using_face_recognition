"""Pytest fixtures. Puts the project root on sys.path so `app` imports in CI."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Flask test client backed by a temporary SQLite database."""
    import database

    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "test.db")
    database.init_db()

    import app as app_module

    app_module.app.config["TESTING"] = True
    app_module.app.config["SECRET_KEY"] = "pytest-secret"
    with app_module.app.test_client() as test_client:
        yield test_client
