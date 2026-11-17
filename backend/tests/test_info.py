from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from hlcopy.hl.errors import HlError
from hlcopy.hl.info import HlInfoClient, parse_account, parse_meta, parse_mids

META_RAW = {
    "universe": [
        {"name": "BTC", "szDecimals": 5, "maxLeverage": 50},
        {"name": "ETH", "szDecimals": 4, "maxLeverage": 50},
        {"name": "OLD", "szDecimals": 2, "maxLeverage": 10, "isDelisted": True},
        {"name": "SOL", "szDecimals": 2, "maxLeverage": 20},
    ]
}

STATE_RAW = {
    "marginSummary": {
        "accountValue": "1234.5678",
        "totalMarginUsed": "100.5",
        "totalNtlPos": "1000",
        "totalRawUsd": "1134.0678",
    },
    "withdrawable": "1134.0678",
    "assetPositions": [
        {
            "type": "oneWay",
            "position": {
                "coin": "BTC",
                "szi": "0.0123",
                "entryPx": "65000.5",
                "leverage": {"type": "cross", "value": 10},
                "liquidationPx": None,
                "marginUsed": "80",
                "positionValue": "799.5",
                "unrealizedPnl": "12.34",
            },
        },
        {
            "type": "oneWay",
            "position": {
                "coin": "ETH",
                "szi": "-1.5",
                "entryPx": "3000",
                "leverage": {"type": "isolated", "value": 5},
                "liquidationPx": "3500",
                "marginUsed": "900",
                "positionValue": "4500",
                "unrealizedPnl": "-20",
            },
        },
    ],
}

ORDERS_RAW = [
    {
        "coin": "BTC",
        "oid": 123,
        "side": "B",
        "limitPx": "60000",
        "sz": "0.01",
        "timestamp": 1700000000000,
        "reduceOnly": False,
    }
]


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


class FakeTransport:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []
        self.failures: dict[str, Exception] = {}

    def request(self, payload: dict[str, Any]) -> Any:
        self.calls.append(payload)
        kind = payload["type"]
        if kind in self.failures:
            raise self.failures.pop(kind)
        return self.responses[kind]


def make_client(transport: FakeTransport, clock: FakeClock, **kwargs) -> HlInfoClient:
    return HlInfoClient(
        "mainnet",
        transport=transport,
        clock=clock,
        sleep=clock.sleep,
        wall_ms=lambda: 1_700_000_000_000 + int(clock.t * 1000),
        **kwargs,
    )


def test_parse_meta_skips_delisted():
    meta = parse_meta(META_RAW, 1)

    assert [coin.name for coin in meta.coins] == ["BTC", "ETH", "SOL"]
    assert meta.coins[0].sz_decimals == 5
    assert meta.coins[2].max_leverage == 20
    assert [coin.asset_index for coin in meta.coins] == [0, 1, 3]
    assert meta.fetched_at_ms == 1


def test_parse_meta_rejects_bad_payload():
    with pytest.raises(HlError):
        parse_meta({"universe": "nope"}, 1)
    with pytest.raises(HlError):
        parse_meta({"error": "bad request"}, 1)


def test_parse_mids_skips_empty_values():
    mids = parse_mids({"BTC": "65000.5", "ETH": None, "SOL": "150"})

    assert mids == {"BTC": Decimal("65000.5"), "SOL": Decimal("150")}


def test_parse_account_extracts_positions_and_orders():
    snapshot = parse_account("0xabc", STATE_RAW, ORDERS_RAW, 42)

    assert snapshot.account_value == Decimal("1234.5678")
    assert snapshot.withdrawable == Decimal("1134.0678")
    assert snapshot.margin_used == Decimal("100.5")
    assert snapshot.fetched_at_ms == 42

    btc, eth = snapshot.positions
    assert btc.coin == "BTC"
    assert btc.size == Decimal("0.0123")
    assert btc.entry_px == Decimal("65000.5")
    assert btc.is_cross is True
    assert btc.leverage == 10
    assert btc.liquidation_px is None
    assert eth.size == Decimal("-1.5")
    assert eth.is_cross is False
    assert eth.liquidation_px == Decimal("3500")

    order = snapshot.open_orders[0]
    assert order.price == Decimal("60000")
    assert order.reduce_only is False


def test_parse_account_rejects_bad_state():
    with pytest.raises(HlError):
        parse_account("0xabc", {"assetPositions": []}, [], 1)
    with pytest.raises(HlError):
        parse_account("0xabc", STATE_RAW, "nope", 1)


def test_meta_is_cached_for_60_seconds():
    clock = FakeClock()
    transport = FakeTransport({"meta": META_RAW})
    client = make_client(transport, clock)

    first = client.meta()
    clock.t = 59
    second = client.meta()

    assert first is second
    assert len(transport.calls) == 1

    clock.t = 61
    client.meta()
    assert len(transport.calls) == 2


def test_mids_are_cached_for_one_second():
    clock = FakeClock()
    transport = FakeTransport({"allMids": {"BTC": "1", "ETH": "2"}})
    client = make_client(transport, clock)

    client.all_mids()
    clock.t = 0.9
    client.all_mids()
    assert len(transport.calls) == 1

    clock.t = 1.1
    client.all_mids()
    assert len(transport.calls) == 2


def test_accounts_are_cached_per_address():
    clock = FakeClock()
    transport = FakeTransport(
        {"clearinghouseState": STATE_RAW, "openOrders": ORDERS_RAW}
    )
    client = make_client(transport, clock)

    a = client.account("0xaaa")
    client.account("0xaaa")
    client.account("0xbbb")

    assert a.address == "0xaaa"
    assert len(transport.calls) == 4
    assert [call["type"] for call in transport.calls] == [
        "clearinghouseState",
        "openOrders",
        "clearinghouseState",
        "openOrders",
    ]

    clock.t = 4
    client.account("0xaaa")
    assert len(transport.calls) == 6


def test_failed_request_is_not_cached():
    clock = FakeClock()
    transport = FakeTransport({"meta": META_RAW})
    transport.failures["meta"] = RuntimeError("socket closed")
    client = make_client(transport, clock)

    with pytest.raises(HlError):
        client.meta()

    meta = client.meta()

    assert meta.coins[0].name == "BTC"
    assert len(transport.calls) == 2


def test_invalidate_drops_account_cache():
    clock = FakeClock()
    transport = FakeTransport(
        {"clearinghouseState": STATE_RAW, "openOrders": ORDERS_RAW}
    )
    client = make_client(transport, clock)

    client.account("0xaaa")
    client.invalidate("0xaaa")
    client.account("0xaaa")

    assert len(transport.calls) == 4


def test_rate_limit_applies_request_weights():
    clock = FakeClock()
    transport = FakeTransport({"meta": META_RAW})
    client = make_client(transport, clock, meta_ttl_sec=0, rate_per_min=60)

    client.meta()
    client.meta()
    client.meta()
    assert clock.t == 0

    client.meta()
    assert clock.t == pytest.approx(20.0)
