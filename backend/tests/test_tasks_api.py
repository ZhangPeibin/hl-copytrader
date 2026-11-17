from __future__ import annotations

from decimal import Decimal

import pytest
from eth_account import Account
from fastapi.testclient import TestClient

from hlcopy import db
from hlcopy.hl.errors import HlError
from hlcopy.hl.info import AccountSnapshot, CoinMeta, Meta, Position
from hlcopy.main import create_app

from conftest import make_settings


def snapshot(address: str, positions: list[Position]) -> AccountSnapshot:
    return AccountSnapshot(
        address=address,
        account_value=Decimal("1000"),
        withdrawable=Decimal("1000"),
        margin_used=Decimal("0"),
        positions=tuple(positions),
        open_orders=(),
        fetched_at_ms=1_700_000_000_000,
    )


def position(coin: str, size: str, leverage: int = 10) -> Position:
    return Position(
        coin=coin,
        size=Decimal(size),
        entry_px=Decimal("1000"),
        position_value=Decimal("1000"),
        unrealized_pnl=Decimal("0"),
        leverage=leverage,
        is_cross=True,
        liquidation_px=None,
    )


class FakeInfo:
    def __init__(self, leader: str, leader_positions=None, follower_positions=None, fail=False):
        self.leader = leader
        self.leader_positions = leader_positions or []
        self.follower_positions = follower_positions or []
        self.fail = fail

    def meta(self) -> Meta:
        if self.fail:
            raise HlError("upstream unavailable")
        return Meta(
            coins=(
                CoinMeta(name="BTC", sz_decimals=3, max_leverage=20, asset_index=0),
                CoinMeta(name="ETH", sz_decimals=4, max_leverage=25, asset_index=1),
            ),
            fetched_at_ms=1_700_000_000_000,
        )

    def all_mids(self) -> dict[str, Decimal]:
        if self.fail:
            raise HlError("upstream unavailable")
        return {"BTC": Decimal("1000"), "ETH": Decimal("3000")}

    def account(self, address: str) -> AccountSnapshot:
        if self.fail:
            raise HlError("upstream unavailable")
        if address.lower() == self.leader.lower():
            return snapshot(address, self.leader_positions)
        return snapshot(address, self.follower_positions)


def new_wallet(client) -> dict:
    key = Account.create()
    master = Account.create()
    response = client.post(
        "/api/wallets",
        json={
            "name": f"w-{master.address[-6:]}",
            "address": master.address,
            "api_wallet_address": key.address,
            "private_key": key.key.hex(),
        },
    )
    assert response.status_code == 201
    return response.json()


def task_payload(wallet_id: int, leader: str, **overrides) -> dict:
    payload = {
        "wallet_id": wallet_id,
        "leader": leader,
        "mode": "sync",
        "ratio": "0.10",
        "max_notional_usd": "1000",
        "max_leverage": 10,
        "max_slippage": "0.01",
        "close_on_stop": False,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def leader():
    return Account.create().address


@pytest.fixture
def fake_info(leader):
    return FakeInfo(leader, leader_positions=[position("BTC", "1")])


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def client(settings, fake_info):
    with TestClient(create_app(settings, info_client=fake_info)) as test_client:
        yield test_client


def test_create_task_defaults(client, leader):
    wallet = new_wallet(client)

    response = client.post("/api/tasks", json=task_payload(wallet["id"], leader))

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "running"
    assert body["mode"] == "sync"
    assert body["ratio"] == "0.1"
    assert body["max_notional_usd"] == "1000"
    assert body["max_slippage"] == "0.01"
    assert body["leader"] == leader
    assert body["close_on_stop"] is False


def test_create_task_requires_wallet(client, leader):
    response = client.post("/api/tasks", json=task_payload(999, leader))

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "wallet_not_found"


@pytest.mark.parametrize("ratio", ["0", "-0.1", "1.5", "abc"])
def test_invalid_ratio_rejected(client, leader, ratio):
    wallet = new_wallet(client)

    response = client.post("/api/tasks", json=task_payload(wallet["id"], leader, ratio=ratio))

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_ratio"


def test_invalid_leader_rejected(client):
    wallet = new_wallet(client)

    response = client.post("/api/tasks", json=task_payload(wallet["id"], "0x1234"))

    assert response.status_code == 400


def test_ratio_of_one_is_allowed(client, leader):
    wallet = new_wallet(client)
    response = client.post("/api/tasks", json=task_payload(wallet["id"], leader, ratio="1"))
    assert response.status_code == 201


def test_list_get_update_delete(client, leader):
    wallet = new_wallet(client)
    task = client.post("/api/tasks", json=task_payload(wallet["id"], leader)).json()

    assert len(client.get("/api/tasks").json()) == 1
    assert client.get(f"/api/tasks/{task['id']}").json()["id"] == task["id"]

    updated = client.put(f"/api/tasks/{task['id']}", json={"ratio": "0.5", "mode": "new_only"})
    assert updated.status_code == 200
    assert updated.json()["ratio"] == "0.5"
    assert updated.json()["mode"] == "new_only"

    assert client.delete(f"/api/tasks/{task['id']}").status_code == 204
    assert client.get("/api/tasks").json() == []
    assert client.get(f"/api/tasks/{task['id']}").status_code == 404


def test_delete_task_removes_coin_states(client, settings, leader):
    wallet = new_wallet(client)
    task = client.post("/api/tasks", json=task_payload(wallet["id"], leader)).json()

    with db.session(settings.db_path) as conn:
        conn.execute(
            "INSERT INTO coin_states(task_id, coin, locked) VALUES (?, 'BTC', 1)", (task["id"],)
        )
        conn.commit()

    client.delete(f"/api/tasks/{task['id']}")

    with db.session(settings.db_path) as conn:
        assert db.list_coin_states(conn, task["id"]) == {}


def test_compare_shows_target_and_first_entry(client, leader):
    wallet = new_wallet(client)
    task = client.post("/api/tasks", json=task_payload(wallet["id"], leader)).json()

    response = client.get(f"/api/tasks/{task['id']}/compare")

    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == task["id"]
    assert body["mode"] == "sync"
    assert body["leader"] == leader
    assert body["address"] == wallet["address"]

    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["coin"] == "BTC"
    assert row["leader_size"] == "1"
    assert row["follower_size"] == "0"
    assert row["target"] == "0.1"
    assert row["delta"] == "0.1"
    assert row["mark_px"] == "1000"
    assert row["locked"] is False
    assert row["truncated"] is False
    assert row["reason"] == "set_leverage"


def test_compare_filters_coins_outside_meta(tmp_path, leader):
    fake = FakeInfo(leader, leader_positions=[position("BTC", "1"), position("XYZ", "5")])
    with TestClient(create_app(make_settings(tmp_path), info_client=fake)) as test_client:
        wallet = new_wallet(test_client)
        task = test_client.post("/api/tasks", json=task_payload(wallet["id"], leader)).json()
        rows = test_client.get(f"/api/tasks/{task['id']}/compare").json()["rows"]

    assert [row["coin"] for row in rows] == ["BTC"]


def test_compare_respects_locked_state(client, settings, leader):
    wallet = new_wallet(client)
    task = client.post("/api/tasks", json=task_payload(wallet["id"], leader)).json()

    with db.session(settings.db_path) as conn:
        conn.execute(
            "INSERT INTO coin_states(task_id, coin, locked) VALUES (?, 'BTC', 1)", (task["id"],)
        )
        conn.commit()

    rows = client.get(f"/api/tasks/{task['id']}/compare").json()["rows"]

    assert rows[0]["locked"] is True
    assert rows[0]["reason"] == "locked"


def test_compare_closes_orphan_follower_position(tmp_path, leader):
    fake = FakeInfo(leader, leader_positions=[], follower_positions=[position("ETH", "1")])
    with TestClient(create_app(make_settings(tmp_path), info_client=fake)) as test_client:
        wallet = new_wallet(test_client)
        task = test_client.post("/api/tasks", json=task_payload(wallet["id"], leader)).json()
        rows = test_client.get(f"/api/tasks/{task['id']}/compare").json()["rows"]

    assert rows[0]["coin"] == "ETH"
    assert rows[0]["target"] == "0"
    assert rows[0]["reason"] == "close_all"


def test_compare_truncates_by_max_notional(client, leader):
    wallet = new_wallet(client)
    task = client.post(
        "/api/tasks",
        json=task_payload(wallet["id"], leader, ratio="1", max_notional_usd="500"),
    ).json()

    row = client.get(f"/api/tasks/{task['id']}/compare").json()["rows"][0]

    assert row["target"] == "0.5"
    assert row["truncated"] is True


def test_compare_unknown_task(client):
    assert client.get("/api/tasks/999/compare").status_code == 404


def test_compare_upstream_failure(tmp_path, leader):
    fake = FakeInfo(leader, fail=True)
    with TestClient(create_app(make_settings(tmp_path), info_client=fake)) as test_client:
        wallet = new_wallet(test_client)
        task = test_client.post("/api/tasks", json=task_payload(wallet["id"], leader)).json()

        response = test_client.get(f"/api/tasks/{task['id']}/compare")

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "hl_unavailable"
