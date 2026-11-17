from __future__ import annotations

import base64
import sqlite3
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from hlcopy.config import Settings
from hlcopy.main import create_app


def make_settings(tmp_path, **overrides) -> Settings:
    data_dir = tmp_path / "data"
    values = dict(
        hl_network="mainnet",
        secret_enc_key=base64.urlsafe_b64encode(b"k" * 32).decode("ascii"),
        app_password=None,
        data_dir=data_dir,
        db_path=data_dir / "app.db",
        static_dir=tmp_path / "dist",
        log_level="WARNING",
        reconcile_interval_sec=30,
        leader_cache_ttl_sec=Decimal("2"),
        max_slippage_default=Decimal("0.01"),
    )
    values.update(overrides)
    return Settings(**values)


def test_missing_secret_key_exits(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("HL_SECRET_ENC_KEY", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    with pytest.raises(SystemExit) as excinfo:
        create_app(start_engine=False)

    assert excinfo.value.code == 2
    assert "Fernet.generate_key" in capsys.readouterr().err


def test_health_reports_ready(tmp_path):
    app = create_app(make_settings(tmp_path), start_engine=False)

    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["network"] == "mainnet"
    assert body["version"] == "0.1.0"
    assert body["engine"] == {"running": False, "last_tick_ms": None, "queue_depth": 0}
    assert body["ws"] == {"connected": False, "subscriptions": 0}
    assert body["db"] == {"ok": True}


def test_startup_creates_schema_and_keeps_data(tmp_path):
    settings = make_settings(tmp_path)

    with TestClient(create_app(settings, start_engine=False)):
        pass

    assert settings.db_path.is_file()
    with sqlite3.connect(settings.db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]

    assert {"wallets", "tasks", "coin_states", "exec_logs"} <= tables
    assert journal.lower() == "wal", "WAL mode must survive reconnects"

    with TestClient(create_app(settings, start_engine=False)):
        pass

    assert settings.db_path.is_file()


def test_db_check_reports_broken_file(tmp_path):
    from hlcopy import db

    broken = tmp_path / "app.db"
    broken.write_text("not a database")

    assert db.check(broken) is False


def test_frontend_fallback_page(tmp_path):
    with TestClient(create_app(make_settings(tmp_path), start_engine=False)) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "HL Copytrader" in response.text


def test_password_gate_uses_session_cookie(tmp_path):
    settings = make_settings(tmp_path, app_password="s3cret")

    with TestClient(create_app(settings, start_engine=False)) as client:
        anonymous = client.get("/")
        assert anonymous.status_code == 401
        assert anonymous.headers["www-authenticate"].startswith("Basic")

        health = client.get("/api/health")
        assert health.status_code == 200

        login = client.get("/", auth=("app", "s3cret"))
        assert login.status_code == 200
        assert "hlcopy_session" in login.cookies

        assert client.get("/").status_code == 200

    with TestClient(create_app(settings, start_engine=False)) as other:
        assert other.get("/", auth=("app", "wrong")).status_code == 401
