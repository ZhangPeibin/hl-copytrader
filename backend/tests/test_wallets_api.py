from __future__ import annotations

import json

import pytest
from eth_account import Account
from fastapi.testclient import TestClient

from hlcopy import crypto, db
from hlcopy.main import create_app

from conftest import make_settings


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, start_engine=False)) as test_client:
        yield test_client


def keypair() -> tuple[str, str]:
    account = Account.create()
    return account.key.hex(), account.address


def master() -> str:
    return Account.create().address


def payload(name="wallet-a") -> dict[str, str]:
    key, address = keypair()
    return {
        "name": name,
        "address": master(),
        "api_wallet_address": address,
        "private_key": key,
    }


def test_create_wallet_hides_secret_and_encrypts(client, settings):
    body = payload()

    response = client.post("/api/wallets", json=body)

    assert response.status_code == 201
    wallet = response.json()
    assert wallet["name"] == "wallet-a"
    assert wallet["key_state"] == "ok"
    assert "private_key" not in wallet
    assert "secret_enc" not in wallet
    assert body["private_key"] not in json.dumps(wallet)

    with db.session(settings.db_path) as conn:
        stored = db.get_wallet(conn, wallet["id"])

    assert stored is not None
    assert stored.secret_enc != body["private_key"].encode()
    assert crypto.decrypt_secret(settings.secret_enc_key, stored.secret_enc) == body["private_key"]


def test_list_wallets_returns_no_secrets(client):
    client.post("/api/wallets", json=payload("first"))
    client.post("/api/wallets", json=payload("second"))

    response = client.get("/api/wallets")

    assert response.status_code == 200
    wallets = response.json()
    assert [wallet["name"] for wallet in wallets] == ["first", "second"]
    assert all("private_key" not in wallet for wallet in wallets)
    assert all("secret_enc" not in wallet for wallet in wallets)


def test_duplicate_master_address_rejected(client):
    body = payload()
    assert client.post("/api/wallets", json=body).status_code == 201

    duplicate = payload("other-name")
    duplicate["address"] = body["address"]

    response = client.post("/api/wallets", json=duplicate)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "address_already_registered"
    assert len(client.get("/api/wallets").json()) == 1


def test_duplicate_name_rejected(client):
    body = payload("same-name")
    client.post("/api/wallets", json=body)

    response = client.post("/api/wallets", json=payload("same-name"))

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "name_already_registered"


def test_private_key_must_match_api_wallet_address(client):
    body = payload()
    other_key, _ = keypair()
    body["private_key"] = other_key

    response = client.post("/api/wallets", json=body)

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "private_key_address_mismatch"
    assert client.get("/api/wallets").json() == []


def test_invalid_private_key_rejected(client):
    body = payload()
    body["private_key"] = "0x1234"

    response = client.post("/api/wallets", json=body)

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_private_key"


def test_bad_master_address_checksum_rejected(client):
    body = payload()
    checksummed = body["address"]
    body["address"] = checksummed[:2] + checksummed[2:].swapcase()

    response = client.post("/api/wallets", json=body)

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_address_checksum"


def test_update_name_keeps_ciphertext(client, settings):
    body = payload()
    wallet = client.post("/api/wallets", json=body).json()

    with db.session(settings.db_path) as conn:
        before = db.get_wallet(conn, wallet["id"]).secret_enc

    response = client.put(f"/api/wallets/{wallet['id']}", json={"name": "renamed"})

    assert response.status_code == 200
    assert response.json()["name"] == "renamed"
    assert response.json()["api_wallet_address"] == body["api_wallet_address"]

    with db.session(settings.db_path) as conn:
        after = db.get_wallet(conn, wallet["id"]).secret_enc

    assert after == before


def test_update_with_new_key_reencrypts(client, settings):
    wallet = client.post("/api/wallets", json=payload()).json()
    new_key, new_address = keypair()

    response = client.put(
        f"/api/wallets/{wallet['id']}",
        json={"api_wallet_address": new_address, "private_key": new_key},
    )

    assert response.status_code == 200
    assert response.json()["api_wallet_address"] == new_address
    assert response.json()["key_state"] == "ok"

    with db.session(settings.db_path) as conn:
        stored = db.get_wallet(conn, wallet["id"])

    assert crypto.decrypt_secret(settings.secret_enc_key, stored.secret_enc) == new_key


def test_update_api_address_without_key_rejected(client):
    wallet = client.post("/api/wallets", json=payload()).json()
    _, other_address = keypair()

    response = client.put(
        f"/api/wallets/{wallet['id']}", json={"api_wallet_address": other_address}
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "api_wallet_requires_private_key"


def test_validation_error_does_not_echo_private_key(client):
    body = payload()
    del body["address"]

    response = client.post("/api/wallets", json=body)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"
    assert body["private_key"] not in response.text


def test_update_missing_wallet(client):
    response = client.put("/api/wallets/999", json={"name": "x"})
    assert response.status_code == 404


def test_delete_wallet(client):
    wallet = client.post("/api/wallets", json=payload()).json()

    response = client.delete(f"/api/wallets/{wallet['id']}")

    assert response.status_code == 204
    assert client.get("/api/wallets").json() == []
    assert client.delete(f"/api/wallets/{wallet['id']}").status_code == 404


def test_delete_wallet_with_tasks_rejected(client, settings):
    wallet = client.post("/api/wallets", json=payload()).json()

    with db.session(settings.db_path) as conn:
        conn.execute(
            """
            INSERT INTO tasks(wallet_id, leader, mode, ratio, max_notional_usd, max_leverage, status)
            VALUES (?, ?, 'sync', '0.1', '1000', '5', 'stopped')
            """,
            (wallet["id"], master()),
        )
        conn.commit()

    response = client.delete(f"/api/wallets/{wallet['id']}")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "wallet_in_use"
    assert len(client.get("/api/wallets").json()) == 1
