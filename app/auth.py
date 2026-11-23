"""Login por e-mail e senha, com sessão por cookie.

- Senhas guardadas com scrypt (nunca em texto puro).
- No primeiro acesso a troca de senha é obrigatória.
- 5 tentativas erradas por IP em 15 minutos bloqueiam novas tentativas.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from . import db

SESSION_DAYS = 30
COOKIE = "robo_session"
_fails: dict[str, deque] = defaultdict(deque)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, pw TEXT, must_change INTEGER, created TEXT);
CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, email TEXT, expires TEXT, ip TEXT);
"""


def init() -> None:
    db.conn().executescript(SCHEMA)


def hash_pw(pw: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


def verify_pw(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        return hmac.compare_digest(hash_pw(pw, bytes.fromhex(salt)), stored)
    except Exception:
        return False


def ensure_admin(email: str, initial_password: str) -> str | None:
    """Cria o primeiro usuário se ainda não existir. Devolve a senha criada (ou None)."""
    init()
    if not email or db.one("SELECT 1 FROM users WHERE email=?", (email.lower(),)):
        return None
    pw = initial_password or secrets.token_urlsafe(9)
    db.execute("INSERT INTO users(email,pw,must_change,created) VALUES(?,?,1,?)",
               (email.lower(), hash_pw(pw), db.now()))
    db.log(f"Usuário {email.lower()} criado (troca de senha obrigatória no 1º acesso)")
    return pw


def blocked(ip: str) -> bool:
    q = _fails[ip]
    while q and q[0] < time.time() - 900:
        q.popleft()
    return len(q) >= 5


def login(email: str, pw: str, ip: str) -> str | None:
    if blocked(ip):
        raise PermissionError("Muitas tentativas. Aguarde 15 minutos.")
    u = db.one("SELECT * FROM users WHERE email=?", ((email or "").strip().lower(),))
    if not u or not verify_pw(pw or "", u["pw"]):
        _fails[ip].append(time.time())
        db.log(f"Login falhou para {email!r} (IP {ip})", "WARN")
        return None
    _fails[ip].clear()
    token = secrets.token_urlsafe(32)
    exp = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
    db.execute("INSERT INTO sessions(token,email,expires,ip) VALUES(?,?,?,?)", (token, u["email"], exp, ip))
    db.log(f"Login de {u['email']} (IP {ip})")
    return token


def user_for(token: str | None) -> dict | None:
    if not token:
        return None
    s = db.one("SELECT * FROM sessions WHERE token=?", (token,))
    if not s or s["expires"] < db.now():
        return None
    return db.one("SELECT email, must_change FROM users WHERE email=?", (s["email"],))


def logout(token: str) -> None:
    db.execute("DELETE FROM sessions WHERE token=?", (token,))


def change_password(email: str, old: str, new: str, keep_token: str | None = None) -> None:
    u = db.one("SELECT * FROM users WHERE email=?", (email,))
    if not u or not verify_pw(old, u["pw"]):
        raise ValueError("Senha atual incorreta")
    if len(new) < 10:
        raise ValueError("A nova senha precisa ter pelo menos 10 caracteres")
    if new == old:
        raise ValueError("A nova senha precisa ser diferente da atual")
    db.execute("UPDATE users SET pw=?, must_change=0 WHERE email=?", (hash_pw(new), email))
    # derruba as outras sessões abertas
    db.execute("DELETE FROM sessions WHERE email=? AND token != ?", (email, keep_token or ""))
    db.log(f"Senha de {email} alterada")
