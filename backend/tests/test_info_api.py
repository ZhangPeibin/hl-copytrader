from __future__ import annotations

from decimal import Decimal

import pytest
from eth_account import Account
from fastapi.testclient import TestClient

from hlcopy.hl.errors import HlError
from hlcopy.hl.info import AccountSnapshot, CoinMeta, Meta, OpenOrder, Position
from hlcopy.main import create_app

from conftest import make_settings


def snapshot(address: str) -> AccountSnapshot:
    return AccountSnapshot(
        address=address,
        account_value=Decimal("1234.5678"),
        withdrawable=Decimal("1134.0678"),
        margin_used=Decimal("100.5"),
        positions=(
            Position(
                coin="BTC",
                size=Decimal("0.0123"),
                entry_px=Decimal("65000.5"),
                position_value=Decimal("799.5"),
                unrealized_pnl=Decimal("12.34"),
                leverage=10,
                is_cross=True,
                liquidation_px=None,
            ),
            Position(
                coin="ETH",
                size=Decimal("-1.5"),
                entry_px=Decimal("3000"),
                position_value=Decimal("4500"),
                unrealized_pnl=Decimal("-20"),
                leverage=5,
                is_cross=False,
                liquidation_px=Decimal("3500"),
            ),
        ),
        open_orders=(
            OpenOrder(
                coin="BTC",
                oid=123,
                side="B",
                price=Decimal("60000"),
                size=Decimal("0.01"),
                reduce_only=False,
                ts=1_700_000_000_000,
            ),
        ),
        fetched_at_ms=1_700_000_000_123,
    )


class FakeInfo:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.accounts: list[str] = []

    def meta(self) -> Meta:
        if self.fail:
            raise HlError("upstream unavailable")
        return Meta(
            coins=(
                CoinMeta(name="BTC", sz_decimals=5, max_leverage=50),
                CoinMeta(name="ETH", sz_decimals=4, max_leverage=50),
            ),
            fetched_at_ms=1_700_000_000_123,
        )

    def account(self, address: str) -> AccountSnapshot:
        self.accounts.append(address)
        if self.fail:
            raise HlError("upstream unavailable")
        return snapshot(address)


@pytest.fixture
def fake_info():
    return FakeInfo()


@pytest.fixture
def client(tmp_path, fake_info):
    with TestClient(create_app(make_settings(tmp_path), info_client=fake_info)) as test_client:
        yield test_client


def add_wallet(client, name="wallet-a") -> dict:
    account = Account.create()
    master = Account.create()
    response = client.post(
        "/api/wallets",
        json={
            "name": name,
            "address": master.address,
            "api_wallet_address": account.address,
            "private_key": account.key.hex(),
        },
    )
    assert response.status_code == 201
    return response.json()


def test_meta_endpoint(client):
    response = client.get("/api/meta")

    assert response.status_code == 200
    body = response.json()
    assert [coin["name"] for coin in body["coins"]] == ["BTC", "ETH"]
    assert body["coins"][0] == {"name": "BTC", "sz_decimals": 5, "max_leverage": 50}
    assert body["fetched_at_ms"] == 1_700_000_000_123


def test_get_wallet(client):
    wallet = add_wallet(client)

    response = client.get(f"/api/wallets/{wallet['id']}")

    assert response.status_code == 200
    assert response.json()["address"] == wallet["address"]
    assert "private_key" not in response.json()


def test_get_wallet_missing(client):
    assert client.get("/api/wallets/999").status_code == 404


def test_account_endpoint_serializes_numbers_as_strings(client, fake_info):
    wallet = add_wallet(client)

    response = client.get(f"/api/wallets/{wallet['id']}/account")

    assert response.status_code == 200
    body = response.json()
    assert body["address"] == wallet["address"]
    assert body["account_value"] == "1234.5678"
    assert body["withdrawable"] == "1134.0678"
    assert body["margin_used"] == "100.5"
    assert body["fetched_at_ms"] == 1_700_000_000_123

    btc, eth = body["positions"]
    assert btc["coin"] == "BTC"
    assert btc["size"] == "0.0123"
    assert btc["entry_px"] == "65000.5"
    assert btc["leverage"] == 10
    assert btc["is_cross"] is True
    assert btc["liquidation_px"] is None
    assert eth["size"] == "-1.5"
    assert eth["liquidation_px"] == "3500"

    assert body["open_orders"][0]["price"] == "60000"
    assert body["open_orders"][0]["reduce_only"] is False
    assert fake_info.accounts == [wallet["address"]]


def test_account_endpoint_requires_wallet(client):
    assert client.get("/api/wallets/999/account").status_code == 404


def test_upstream_failure_maps_to_502(tmp_path):
    fake = FakeInfo(fail=True)
    with TestClient(create_app(make_settings(tmp_path), info_client=fake)) as test_client:
        wallet = add_wallet(test_client)

        meta_response = test_client.get("/api/meta")
        account_response = test_client.get(f"/api/wallets/{wallet['id']}/account")

    assert meta_response.status_code == 502
    assert meta_response.json()["detail"]["code"] == "hl_unavailable"
    assert account_response.status_code == 502
    assert account_response.json()["detail"]["code"] == "hl_unavailable"
