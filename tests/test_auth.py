from fastapi.testclient import TestClient

from app import auth
from app.api import create_app
from app.brokers import PaperBroker, SyntheticFeed
from app.config import settings
from app.engine import Engine


def client():
    e = Engine(settings, PaperBroker(SyntheticFeed(), 10_000, 0.001, 0.0005))
    return TestClient(create_app(e))


def test_login_flow(fresh_db):
    pw = auth.ensure_admin("Pablo@Example.com", "Inicial-123")
    c = client()
    assert c.get("/api/status").status_code == 401
    assert c.get("/", follow_redirects=False).status_code in (302, 307)
    assert c.post("/api/login", json={"email": "pablo@example.com", "password": "errada"}).status_code == 401
    r = c.post("/api/login", json={"email": "pablo@example.com", "password": pw})
    assert r.json()["must_change"] is True
    assert c.get("/api/status").status_code == 403          # bloqueado até trocar a senha
    assert c.post("/api/password", json={"old": pw, "new": "curta"}).status_code == 400
    assert c.post("/api/password", json={"old": pw, "new": "MinhaSenhaNova!2026"}).status_code == 200
    assert c.get("/api/status").status_code == 200
    c.post("/api/logout")
    assert c.get("/api/status").status_code == 401
    assert c.post("/api/login", json={"email": "pablo@example.com", "password": pw}).status_code == 401


def test_bruteforce_limit(fresh_db):
    auth.ensure_admin("a@b.com", "Inicial-123")
    c = client()
    for _ in range(5):
        c.post("/api/login", json={"email": "a@b.com", "password": "x"})
    assert c.post("/api/login", json={"email": "a@b.com", "password": "Inicial-123"}).status_code == 429
    auth._fails.clear()
