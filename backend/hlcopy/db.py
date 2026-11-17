from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS wallets (
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
  address TEXT NOT NULL UNIQUE,
  api_wallet_address TEXT NOT NULL,
  secret_enc BLOB NOT NULL,
  key_state TEXT NOT NULL DEFAULT 'ok'
    CHECK (key_state IN ('ok','unreadable')),
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY, wallet_id INTEGER NOT NULL REFERENCES wallets(id),
  leader TEXT NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('sync','new_only')),
  ratio TEXT NOT NULL, max_notional_usd TEXT NOT NULL,
  max_leverage TEXT NOT NULL, max_slippage TEXT NOT NULL DEFAULT '0.01',
  status TEXT NOT NULL CHECK (status IN ('stopped','running','paused')),
  close_on_stop INTEGER NOT NULL DEFAULT 0, created_at INTEGER, updated_at INTEGER);

CREATE TABLE IF NOT EXISTS coin_states (
  task_id INTEGER, coin TEXT, locked INTEGER DEFAULT 0, leverage_set INTEGER DEFAULT 0,
  last_target TEXT, last_action_at INTEGER, fail_count INTEGER DEFAULT 0,
  next_retry_at INTEGER, last_error TEXT, PRIMARY KEY (task_id, coin));

CREATE TABLE IF NOT EXISTS exec_logs (
  id INTEGER PRIMARY KEY, task_id INTEGER, wallet_id INTEGER, ts INTEGER NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('fill','reconcile','param','start','resume','stop')),
  coin TEXT NOT NULL, leader_pos TEXT, pos_before TEXT, target TEXT,
  side TEXT, qty TEXT, price TEXT,
  result TEXT NOT NULL CHECK (result IN ('filled','partial','failed','skipped')),
  reason TEXT, latency_ms INTEGER, oid TEXT, cloid TEXT, raw TEXT);

CREATE INDEX IF NOT EXISTS idx_logs_task_ts ON exec_logs(task_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_logs_ts ON exec_logs(ts DESC);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def check(db_path: Path) -> bool:
    try:
        with connect(db_path) as conn:
            conn.execute("SELECT 1").fetchone()
    except sqlite3.Error:
        return False
    return True
