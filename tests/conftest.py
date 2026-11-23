import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("BROKER", "synthetic")
os.environ.setdefault("TRADING_MODE", "paper")


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    from app import db
    from app.config import settings

    monkeypatch.setattr(settings, "db_path", str(tmp_path / "robo.db"))
    db.reset_connection()
    db.conn()
    yield db
    db.reset_connection()
