from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
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


def now_ms() -> int:
    return int(time.time() * 1000)


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def session(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with session(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def check(db_path: Path) -> bool:
    try:
        with session(db_path) as conn:
            conn.execute("SELECT 1").fetchone()
    except sqlite3.Error:
        return False
    return True


@dataclass(frozen=True)
class WalletRecord:
    id: int
    name: str
    address: str
    api_wallet_address: str
    secret_enc: bytes
    key_state: str
    created_at: int
    updated_at: int


def _wallet(row: sqlite3.Row) -> WalletRecord:
    return WalletRecord(
        id=row["id"],
        name=row["name"],
        address=row["address"],
        api_wallet_address=row["api_wallet_address"],
        secret_enc=row["secret_enc"],
        key_state=row["key_state"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def list_wallets(conn: sqlite3.Connection) -> list[WalletRecord]:
    rows = conn.execute("SELECT * FROM wallets ORDER BY id").fetchall()
    return [_wallet(row) for row in rows]


def get_wallet(conn: sqlite3.Connection, wallet_id: int) -> WalletRecord | None:
    row = conn.execute("SELECT * FROM wallets WHERE id = ?", (wallet_id,)).fetchone()
    return _wallet(row) if row else None


def insert_wallet(
    conn: sqlite3.Connection,
    *,
    name: str,
    address: str,
    api_wallet_address: str,
    secret_enc: bytes,
) -> int:
    ts = now_ms()
    cursor = conn.execute(
        """
        INSERT INTO wallets(name, address, api_wallet_address, secret_enc, key_state, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'ok', ?, ?)
        """,
        (name, address, api_wallet_address, secret_enc, ts, ts),
    )
    conn.commit()
    return int(cursor.lastrowid)


def update_wallet(
    conn: sqlite3.Connection,
    wallet_id: int,
    *,
    name: str,
    address: str,
    api_wallet_address: str,
    secret_enc: bytes,
    key_state: str,
) -> None:
    conn.execute(
        """
        UPDATE wallets
        SET name = ?, address = ?, api_wallet_address = ?, secret_enc = ?, key_state = ?, updated_at = ?
        WHERE id = ?
        """,
        (name, address, api_wallet_address, secret_enc, key_state, now_ms(), wallet_id),
    )
    conn.commit()


def delete_wallet(conn: sqlite3.Connection, wallet_id: int) -> None:
    conn.execute("DELETE FROM wallets WHERE id = ?", (wallet_id,))
    conn.commit()


def address_taken(conn: sqlite3.Connection, address: str, exclude_id: int | None = None) -> bool:
    row = conn.execute(
        "SELECT id FROM wallets WHERE lower(address) = lower(?) AND id IS NOT ?",
        (address, exclude_id),
    ).fetchone()
    return row is not None


def name_taken(conn: sqlite3.Connection, name: str, exclude_id: int | None = None) -> bool:
    row = conn.execute(
        "SELECT id FROM wallets WHERE name = ? AND id IS NOT ?",
        (name, exclude_id),
    ).fetchone()
    return row is not None


def wallet_task_count(conn: sqlite3.Connection, wallet_id: int) -> int:
    row = conn.execute("SELECT count(*) AS n FROM tasks WHERE wallet_id = ?", (wallet_id,)).fetchone()
    return int(row["n"])


@dataclass(frozen=True)
class TaskRecord:
    id: int
    wallet_id: int
    leader: str
    mode: str
    ratio: str
    max_notional_usd: str
    max_leverage: str
    max_slippage: str
    status: str
    close_on_stop: bool
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class CoinStateRecord:
    task_id: int
    coin: str
    locked: bool
    leverage_set: bool
    next_retry_at: int | None
    fail_count: int


def _task(row: sqlite3.Row) -> TaskRecord:
    return TaskRecord(
        id=row["id"],
        wallet_id=row["wallet_id"],
        leader=row["leader"],
        mode=row["mode"],
        ratio=row["ratio"],
        max_notional_usd=row["max_notional_usd"],
        max_leverage=row["max_leverage"],
        max_slippage=row["max_slippage"],
        status=row["status"],
        close_on_stop=bool(row["close_on_stop"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def list_tasks(conn: sqlite3.Connection, wallet_id: int | None = None) -> list[TaskRecord]:
    if wallet_id is None:
        rows = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    else:
        rows = conn.execute("SELECT * FROM tasks WHERE wallet_id = ? ORDER BY id", (wallet_id,)).fetchall()
    return [_task(row) for row in rows]


def get_task(conn: sqlite3.Connection, task_id: int) -> TaskRecord | None:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _task(row) if row else None


def insert_task(
    conn: sqlite3.Connection,
    *,
    wallet_id: int,
    leader: str,
    mode: str,
    ratio: str,
    max_notional_usd: str,
    max_leverage: str,
    max_slippage: str,
    close_on_stop: bool,
) -> int:
    ts = now_ms()
    cursor = conn.execute(
        """
        INSERT INTO tasks(
            wallet_id, leader, mode, ratio, max_notional_usd, max_leverage, max_slippage,
            status, close_on_stop, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?)
        """,
        (
            wallet_id,
            leader,
            mode,
            ratio,
            max_notional_usd,
            max_leverage,
            max_slippage,
            int(close_on_stop),
            ts,
            ts,
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def update_task(
    conn: sqlite3.Connection,
    task_id: int,
    *,
    wallet_id: int,
    leader: str,
    mode: str,
    ratio: str,
    max_notional_usd: str,
    max_leverage: str,
    max_slippage: str,
    close_on_stop: bool,
) -> None:
    conn.execute(
        """
        UPDATE tasks
        SET wallet_id = ?, leader = ?, mode = ?, ratio = ?, max_notional_usd = ?,
            max_leverage = ?, max_slippage = ?, close_on_stop = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            wallet_id,
            leader,
            mode,
            ratio,
            max_notional_usd,
            max_leverage,
            max_slippage,
            int(close_on_stop),
            now_ms(),
            task_id,
        ),
    )
    conn.commit()


def delete_task(conn: sqlite3.Connection, task_id: int) -> None:
    conn.execute("DELETE FROM coin_states WHERE task_id = ?", (task_id,))
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()


def list_coin_states(conn: sqlite3.Connection, task_id: int) -> dict[str, CoinStateRecord]:
    rows = conn.execute("SELECT * FROM coin_states WHERE task_id = ?", (task_id,)).fetchall()
    return {
        row["coin"]: CoinStateRecord(
            task_id=row["task_id"],
            coin=row["coin"],
            locked=bool(row["locked"]),
            leverage_set=bool(row["leverage_set"]),
            next_retry_at=row["next_retry_at"],
            fail_count=row["fail_count"],
        )
        for row in rows
    }


def get_coin_state(conn: sqlite3.Connection, task_id: int, coin: str) -> CoinStateRecord | None:
    row = conn.execute(
        "SELECT * FROM coin_states WHERE task_id = ? AND coin = ?", (task_id, coin)
    ).fetchone()
    if row is None:
        return None
    return CoinStateRecord(
        task_id=row["task_id"],
        coin=row["coin"],
        locked=bool(row["locked"]),
        leverage_set=bool(row["leverage_set"]),
        next_retry_at=row["next_retry_at"],
        fail_count=row["fail_count"],
    )
