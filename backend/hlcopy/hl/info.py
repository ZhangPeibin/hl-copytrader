from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from hlcopy.hl.errors import HlError
from hlcopy.hl.ratelimit import TokenBucket

BASE_URLS = {
    "mainnet": "https://api.hyperliquid.xyz",
    "testnet": "https://api.hyperliquid-testnet.xyz",
}

WEIGHTS = {
    "meta": 20,
    "allMids": 2,
    "clearinghouseState": 2,
    "openOrders": 2,
}

log = logging.getLogger("hlcopy.hl.info")


class InfoTransport(Protocol):
    def request(self, payload: dict[str, Any]) -> Any: ...


class HttpInfoTransport:
    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        import requests

        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()

    def request(self, payload: dict[str, Any]) -> Any:
        response = self._session.post(
            f"{self._base_url}/info", json=payload, timeout=self._timeout
        )
        response.raise_for_status()
        return response.json()


@dataclass(frozen=True)
class CoinMeta:
    name: str
    sz_decimals: int
    max_leverage: int


@dataclass(frozen=True)
class Meta:
    coins: tuple[CoinMeta, ...]
    fetched_at_ms: int


@dataclass(frozen=True)
class Position:
    coin: str
    size: Decimal
    entry_px: Decimal | None
    position_value: Decimal
    unrealized_pnl: Decimal
    leverage: int
    is_cross: bool
    liquidation_px: Decimal | None


@dataclass(frozen=True)
class OpenOrder:
    coin: str
    oid: int
    side: str
    price: Decimal
    size: Decimal
    reduce_only: bool
    ts: int


@dataclass(frozen=True)
class AccountSnapshot:
    address: str
    account_value: Decimal
    withdrawable: Decimal
    margin_used: Decimal
    positions: tuple[Position, ...]
    open_orders: tuple[OpenOrder, ...]
    fetched_at_ms: int


def _dec(value: Any, *, default: Decimal | None = None) -> Decimal | None:
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise HlError(f"cannot parse decimal from {value!r}", code="hl_bad_payload") from exc


def _require_mapping(raw: Any, what: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise HlError(f"{what} payload is not an object", code="hl_bad_payload")
    if raw.get("error"):
        raise HlError(f"{what} rejected: {raw['error']}", code="hl_bad_payload")
    return raw


def parse_meta(raw: Any, fetched_at_ms: int) -> Meta:
    payload = _require_mapping(raw, "meta")
    universe = payload.get("universe")
    if not isinstance(universe, list):
        raise HlError("meta payload has no universe", code="hl_bad_payload")
    coins: list[CoinMeta] = []
    for entry in universe:
        if not isinstance(entry, dict) or "name" not in entry:
            continue
        if entry.get("isDelisted"):
            continue
        coins.append(
            CoinMeta(
                name=str(entry["name"]),
                sz_decimals=int(entry.get("szDecimals", 0)),
                max_leverage=int(entry.get("maxLeverage", 1)),
            )
        )
    return Meta(coins=tuple(coins), fetched_at_ms=fetched_at_ms)


def parse_mids(raw: Any) -> dict[str, Decimal]:
    payload = _require_mapping(raw, "allMids")
    mids: dict[str, Decimal] = {}
    for coin, value in payload.items():
        if not isinstance(coin, str) or not isinstance(value, (str, int, float)):
            continue
        parsed = _dec(value)
        if parsed is not None:
            mids[coin] = parsed
    return mids


def parse_account(
    address: str,
    state_raw: Any,
    orders_raw: Any,
    fetched_at_ms: int,
) -> AccountSnapshot:
    state = _require_mapping(state_raw, "clearinghouseState")
    summary = state.get("marginSummary")
    if not isinstance(summary, dict):
        raise HlError("clearinghouseState has no marginSummary", code="hl_bad_payload")

    positions: list[Position] = []
    for entry in state.get("assetPositions") or []:
        position = (entry or {}).get("position") if isinstance(entry, dict) else None
        if not isinstance(position, dict):
            continue
        size = _dec(position.get("szi"), default=Decimal(0)) or Decimal(0)
        leverage = position.get("leverage") or {}
        positions.append(
            Position(
                coin=str(position.get("coin", "")),
                size=size,
                entry_px=_dec(position.get("entryPx")),
                position_value=_dec(position.get("positionValue"), default=Decimal(0)) or Decimal(0),
                unrealized_pnl=_dec(position.get("unrealizedPnl"), default=Decimal(0)) or Decimal(0),
                leverage=int(leverage.get("value", 0)),
                is_cross=str(leverage.get("type", "cross")) == "cross",
                liquidation_px=_dec(position.get("liquidationPx")),
            )
        )

    if not isinstance(orders_raw, list):
        raise HlError("openOrders payload is not a list", code="hl_bad_payload")
    orders: list[OpenOrder] = []
    for entry in orders_raw:
        if not isinstance(entry, dict):
            continue
        orders.append(
            OpenOrder(
                coin=str(entry.get("coin", "")),
                oid=int(entry.get("oid", 0)),
                side=str(entry.get("side", "")),
                price=_dec(entry.get("limitPx"), default=Decimal(0)) or Decimal(0),
                size=_dec(entry.get("sz"), default=Decimal(0)) or Decimal(0),
                reduce_only=bool(entry.get("reduceOnly", False)),
                ts=int(entry.get("timestamp", 0)),
            )
        )

    return AccountSnapshot(
        address=address,
        account_value=_dec(summary.get("accountValue"), default=Decimal(0)) or Decimal(0),
        withdrawable=_dec(state.get("withdrawable"), default=Decimal(0)) or Decimal(0),
        margin_used=_dec(summary.get("totalMarginUsed"), default=Decimal(0)) or Decimal(0),
        positions=tuple(positions),
        open_orders=tuple(orders),
        fetched_at_ms=fetched_at_ms,
    )


class HlInfoClient:
    def __init__(
        self,
        network: str = "mainnet",
        *,
        transport: InfoTransport | None = None,
        account_ttl_sec: float = 3.0,
        meta_ttl_sec: float = 60.0,
        mids_ttl_sec: float = 1.0,
        rate_per_min: float = 900.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        wall_ms: Callable[[], int] | None = None,
    ) -> None:
        if network not in BASE_URLS:
            raise ValueError(f"unknown network {network!r}")
        self.network = network
        self._transport = transport or HttpInfoTransport(BASE_URLS[network])
        self._account_ttl = account_ttl_sec
        self._meta_ttl = meta_ttl_sec
        self._mids_ttl = mids_ttl_sec
        self._clock = clock
        self._wall_ms = wall_ms or (lambda: int(time.time() * 1000))
        self._limits = TokenBucket(rate_per_min, rate_per_min / 60.0, clock=clock, sleep=sleep)
        self._lock = threading.Lock()
        self._meta: tuple[float, Meta] | None = None
        self._mids: tuple[float, dict[str, Decimal]] | None = None
        self._accounts: dict[str, tuple[float, AccountSnapshot]] = {}

    def _cached(self, entry: tuple[float, Any] | None) -> Any | None:
        if entry is None:
            return None
        expires_at, value = entry
        return value if expires_at > self._clock() else None

    def _request(self, payload: dict[str, Any]) -> Any:
        weight = WEIGHTS.get(str(payload.get("type")), 2)
        waited = self._limits.acquire(weight)
        if waited > 0.5:
            log.info("rate limiter waited %.2fs for %s", waited, payload.get("type"))
        try:
            return self._transport.request(payload)
        except HlError:
            raise
        except Exception as exc:
            raise HlError(f"info request failed: {exc}") from exc

    def meta(self) -> Meta:
        with self._lock:
            cached = self._cached(self._meta)
        if cached is not None:
            return cached

        parsed = parse_meta(self._request({"type": "meta"}), self._wall_ms())
        with self._lock:
            self._meta = (self._clock() + self._meta_ttl, parsed)
        return parsed

    def all_mids(self) -> dict[str, Decimal]:
        with self._lock:
            cached = self._cached(self._mids)
        if cached is not None:
            return dict(cached)

        parsed = parse_mids(self._request({"type": "allMids"}))
        with self._lock:
            self._mids = (self._clock() + self._mids_ttl, parsed)
        return dict(parsed)

    def account(self, address: str) -> AccountSnapshot:
        with self._lock:
            cached = self._cached(self._accounts.get(address))
        if cached is not None:
            return cached

        state = self._request({"type": "clearinghouseState", "user": address})
        orders = self._request({"type": "openOrders", "user": address})
        snapshot = parse_account(address, state, orders, self._wall_ms())
        with self._lock:
            self._accounts[address] = (self._clock() + self._account_ttl, snapshot)
        return snapshot

    def invalidate(self, address: str | None = None) -> None:
        with self._lock:
            if address is None:
                self._accounts.clear()
            else:
                self._accounts.pop(address, None)
