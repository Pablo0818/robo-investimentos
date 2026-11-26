"""Banco de dados SQLite (arquivo único, sem servidor)."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DEFAULT_RUNTIME, settings

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_conn_path: str | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, symbol TEXT, side TEXT, qty REAL, price REAL, stop REAL, take REAL,
    strategy TEXT, reason TEXT, status TEXT, expires_at TEXT, note TEXT,
    market TEXT DEFAULT 'main', notional REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, symbol TEXT, side TEXT, qty REAL, price REAL, fee REAL,
    pnl REAL DEFAULT 0, signal_id INTEGER, strategy TEXT, reason TEXT,
    market TEXT DEFAULT 'main'
);
CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY, qty REAL, avg_price REAL, stop REAL, take REAL,
    opened_ts TEXT, strategy TEXT, highest REAL, market TEXT DEFAULT 'main'
);
CREATE TABLE IF NOT EXISTS equity (ts TEXT, equity REAL, cash REAL, flows REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS equity_live (ts TEXT, equity REAL, cash REAL);
CREATE TABLE IF NOT EXISTS strategies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT, name TEXT, params TEXT,
    train_score REAL, oos_sharpe REAL, oos_return REAL, oos_maxdd REAL, oos_trades INTEGER,
    status TEXT, created TEXT
);
CREATE TABLE IF NOT EXISTS cash_flows (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, amount REAL, note TEXT);
CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, source TEXT, title TEXT, body TEXT, url TEXT);
CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, level TEXT, msg TEXT);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    global _conn, _conn_path
    with _lock:
        if _conn is None or _conn_path != settings.db_path:
            Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(settings.db_path, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript(SCHEMA)
            try:  # bancos antigos: adiciona coluna de aportes acumulados
                _conn.execute("ALTER TABLE equity ADD COLUMN flows REAL DEFAULT 0")
                # histórico antigo: sem dados de aportes, assume resultado zero (evita trava falsa)
                _conn.execute("UPDATE equity SET flows = equity")
            except sqlite3.OperationalError:
                pass
            for tbl in ("trades", "positions"):  # dinheiro fictício (paper) ou real (live)
                try:
                    _conn.execute(f"ALTER TABLE {tbl} ADD COLUMN mode TEXT DEFAULT 'paper'")
                except sqlite3.OperationalError:
                    pass
            try:  # ordem de venda no alvo deixada no livro da corretora (ordem passiva, taxa menor)
                _conn.execute("ALTER TABLE positions ADD COLUMN take_order TEXT")
            except sqlite3.OperationalError:
                pass
            _conn_path = settings.db_path
            for k, v in DEFAULT_RUNTIME.items():
                _conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
            _conn.commit()
        return _conn


def reset_connection() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None


def execute(sql: str, params: tuple = ()) -> int:
    with _lock:
        c = conn()
        cur = c.execute(sql, params)
        c.commit()
        return cur.lastrowid


def query(sql: str, params: tuple = ()) -> list[dict]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, params).fetchall()]


def one(sql: str, params: tuple = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


# ---------- configurações operacionais ----------
def get_setting(key: str) -> str:
    r = one("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else DEFAULT_RUNTIME.get(key, "")


def get_float(key: str) -> float:
    return float(get_setting(key))


def get_bool(key: str) -> bool:
    return get_setting(key).lower() in ("1", "true", "sim", "yes", "on")


def set_setting(key: str, value: Any) -> None:
    if isinstance(value, bool):
        value = "true" if value else "false"
    execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, str(value)))


def all_settings() -> dict[str, str]:
    return {r["key"]: r["value"] for r in query("SELECT key,value FROM settings")}


def log(msg: str, level: str = "INFO") -> None:
    print(f"[{level}] {msg}", flush=True)
    execute("INSERT INTO logs(ts,level,msg) VALUES(?,?,?)", (now(), level, msg))


def dumps(o: Any) -> str:
    return json.dumps(o, ensure_ascii=False)
