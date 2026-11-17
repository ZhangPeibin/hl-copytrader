from __future__ import annotations

import base64
from decimal import Decimal

from hlcopy.config import Settings


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
