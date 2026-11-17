from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hlcopy import db
from hlcopy.main import create_app

from conftest import make_settings


@pytest.fixture
def settings(tmp_path):
    built = make_settings(tmp_path)
    db.init_db(built.db_path)
    return built


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, start_engine=False)) as test_client:
        yield test_client


def seed(settings, **overrides) -> None:
    values = dict(
        task_id=1,
        wallet_id=1,
        ts=1_700_000_000_000,
        source="reconcile",
        coin="BTC",
        result="filled",
        reason="place",
        side="buy",
        qty="0.1",
        price="65000.5",
        cloid="0x" + "ab" * 16,
        oid="42",
    )
    values.update(overrides)
    with db.session(settings.db_path) as conn:
        db.insert_exec_log(conn, **values)


def test_list_logs_newest_first(client, settings):
    seed(settings, ts=1, coin="BTC")
    seed(settings, ts=2, coin="ETH", result="skipped", reason="below_min_notional")

    response = client.get("/api/logs")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [item["coin"] for item in body["items"]] == ["ETH", "BTC"]
    assert body["items"][0]["ts"] == 2


def test_filter_logs(client, settings):
    seed(settings, task_id=1, coin="BTC")
    seed(settings, task_id=2, coin="ETH", result="failed")
    seed(settings, task_id=1, coin="ETH", result="failed")

    by_task = client.get("/api/logs", params={"task_id": 1}).json()
    assert by_task["total"] == 2

    by_coin = client.get("/api/logs", params={"coin": "ETH"}).json()
    assert by_coin["total"] == 2

    by_result = client.get("/api/logs", params={"result": "failed"}).json()
    assert by_result["total"] == 2

    combined = client.get("/api/logs", params={"task_id": 1, "coin": "ETH", "result": "failed"}).json()
    assert combined["total"] == 1


def test_time_window_filter(client, settings):
    seed(settings, ts=1_000)
    seed(settings, ts=2_000)
    seed(settings, ts=3_000)

    body = client.get("/api/logs", params={"from_ms": 1_500, "to_ms": 2_500}).json()

    assert body["total"] == 1
    assert body["items"][0]["ts"] == 2_000


def test_pagination(client, settings):
    for index in range(5):
        seed(settings, ts=index + 1)

    first = client.get("/api/logs", params={"limit": 2, "offset": 0}).json()
    second = client.get("/api/logs", params={"limit": 2, "offset": 2}).json()

    assert [item["ts"] for item in first["items"]] == [5, 4]
    assert [item["ts"] for item in second["items"]] == [3, 2]
    assert first["total"] == 5


def test_log_fields_round_trip(client, settings):
    seed(settings, latency_ms=350, raw="some error")

    item = client.get("/api/logs").json()["items"][0]

    assert item["task_id"] == 1
    assert item["result"] == "filled"
    assert item["qty"] == "0.1"
    assert item["price"] == "65000.5"
    assert item["latency_ms"] == 350
    assert item["raw"] == "some error"
    assert item["oid"] == "42"


@pytest.mark.parametrize("limit", [0, 501])
def test_invalid_limit(client, limit):
    response = client.get("/api/logs", params={"limit": limit})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_limit"


def test_invalid_offset(client):
    response = client.get("/api/logs", params={"offset": -1})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_offset"
