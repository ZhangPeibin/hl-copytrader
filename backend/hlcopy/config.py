from __future__ import annotations

import base64
import os
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

KEY_COMMAND = (
    'python -c "from cryptography.fernet import Fernet; '
    'print(Fernet.generate_key().decode())"'
)

VALID_NETWORKS = ("mainnet", "testnet")


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    hl_network: str
    secret_enc_key: str
    app_password: str | None
    data_dir: Path
    db_path: Path
    static_dir: Path
    log_level: str
    reconcile_interval_sec: int
    leader_cache_ttl_sec: Decimal
    max_slippage_default: Decimal
    log_retention_days: int


def _secret_key(raw: str | None) -> str:
    if raw is None or not raw.strip():
        raise ConfigError(
            "HL_SECRET_ENC_KEY is required; there is no plaintext fallback.\n"
            f"Generate one with:\n  {KEY_COMMAND}"
        )
    value = raw.strip()
    try:
        decoded = base64.urlsafe_b64decode(value.encode("ascii"))
    except Exception as exc:
        raise ConfigError(
            f"HL_SECRET_ENC_KEY is not a valid Fernet key: {exc}\nGenerate one with:\n  {KEY_COMMAND}"
        ) from exc
    if len(decoded) != 32:
        raise ConfigError(
            f"HL_SECRET_ENC_KEY must decode to 32 bytes, got {len(decoded)}.\n"
            f"Generate one with:\n  {KEY_COMMAND}"
        )
    return value


def _decimal(env: Mapping[str, str], name: str, default: str) -> Decimal:
    raw = env.get(name, default)
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise ConfigError(f"{name} must be a decimal string, got {raw!r}") from exc


def _non_negative_int(env: Mapping[str, str], name: str, default: str) -> int:
    raw = env.get(name, default)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if value < 0:
        raise ConfigError(f"{name} must be >= 0, got {value}")
    return value


def _positive_int(env: Mapping[str, str], name: str, default: str) -> int:
    raw = env.get(name, default)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be positive, got {value}")
    return value


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    source: Mapping[str, str] = os.environ if env is None else env

    network = source.get("HL_NETWORK", "mainnet").strip().lower()
    if network not in VALID_NETWORKS:
        raise ConfigError(f"HL_NETWORK must be one of {VALID_NETWORKS}, got {network!r}")

    data_dir = Path(source.get("DATA_DIR", "/data")).expanduser()
    static_dir = Path(source.get("STATIC_DIR", str(ROOT / "frontend" / "dist"))).expanduser()

    password = source.get("APP_PASSWORD", "").strip() or None

    return Settings(
        hl_network=network,
        secret_enc_key=_secret_key(source.get("HL_SECRET_ENC_KEY")),
        app_password=password,
        data_dir=data_dir,
        db_path=data_dir / "app.db",
        static_dir=static_dir,
        log_level=source.get("LOG_LEVEL", "INFO").strip().upper(),
        reconcile_interval_sec=_positive_int(source, "RECONCILE_INTERVAL_SEC", "30"),
        leader_cache_ttl_sec=_decimal(source, "LEADER_CACHE_TTL_SEC", "2"),
        max_slippage_default=_decimal(source, "MAX_SLIPPAGE_DEFAULT", "0.01"),
        log_retention_days=_non_negative_int(source, "LOG_RETENTION_DAYS", "30"),
    )
