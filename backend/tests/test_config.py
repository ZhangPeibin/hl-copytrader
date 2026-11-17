from __future__ import annotations

import base64

import pytest

from hlcopy.config import ConfigError, load_settings


def fernet_key() -> str:
    return base64.urlsafe_b64encode(b"k" * 32).decode("ascii")


def test_defaults(tmp_path):
    settings = load_settings({"HL_SECRET_ENC_KEY": fernet_key(), "DATA_DIR": str(tmp_path)})

    assert settings.hl_network == "mainnet"
    assert settings.db_path == tmp_path / "app.db"
    assert settings.app_password is None
    assert settings.reconcile_interval_sec == 30
    assert str(settings.leader_cache_ttl_sec) == "2"
    assert str(settings.max_slippage_default) == "0.01"
    assert settings.log_retention_days == 30


def test_missing_key_fails_with_generation_hint(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        load_settings({"DATA_DIR": str(tmp_path)})

    message = str(excinfo.value)
    assert "HL_SECRET_ENC_KEY" in message
    assert "Fernet.generate_key" in message


def test_key_must_decode_to_32_bytes(tmp_path):
    short = base64.urlsafe_b64encode(b"k" * 8).decode("ascii")
    with pytest.raises(ConfigError, match="32 bytes"):
        load_settings({"HL_SECRET_ENC_KEY": short, "DATA_DIR": str(tmp_path)})


def test_invalid_key_rejected(tmp_path):
    with pytest.raises(ConfigError, match="valid Fernet key"):
        load_settings({"HL_SECRET_ENC_KEY": "not-a-key!", "DATA_DIR": str(tmp_path)})


def test_unknown_network_rejected(tmp_path):
    with pytest.raises(ConfigError, match="HL_NETWORK"):
        load_settings(
            {"HL_SECRET_ENC_KEY": fernet_key(), "DATA_DIR": str(tmp_path), "HL_NETWORK": "devnet"}
        )


def test_blank_password_is_absent(tmp_path):
    settings = load_settings(
        {"HL_SECRET_ENC_KEY": fernet_key(), "DATA_DIR": str(tmp_path), "APP_PASSWORD": "   "}
    )
    assert settings.app_password is None


def test_bad_numeric_rejected(tmp_path):
    with pytest.raises(ConfigError, match="RECONCILE_INTERVAL_SEC"):
        load_settings(
            {
                "HL_SECRET_ENC_KEY": fernet_key(),
                "DATA_DIR": str(tmp_path),
                "RECONCILE_INTERVAL_SEC": "0",
            }
        )


def test_log_retention_days_validation(tmp_path):
    base = {"HL_SECRET_ENC_KEY": fernet_key(), "DATA_DIR": str(tmp_path)}

    assert load_settings({**base, "LOG_RETENTION_DAYS": "0"}).log_retention_days == 0
    with pytest.raises(ConfigError, match="LOG_RETENTION_DAYS"):
        load_settings({**base, "LOG_RETENTION_DAYS": "-1"})
    with pytest.raises(ConfigError, match="LOG_RETENTION_DAYS"):
        load_settings({**base, "LOG_RETENTION_DAYS": "many"})
