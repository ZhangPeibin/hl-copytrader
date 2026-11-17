from __future__ import annotations

import base64
from decimal import Decimal

import pytest
from eth_account import Account

from hlcopy import crypto, db
from hlcopy.hl.errors import action_for, classify_error, mask_secrets
from hlcopy.hl.exchange import (
    ExchangeClient,
    NonceSource,
    SigningError,
    WalletKeyMismatch,
    WalletSigner,
    interpret,
)

SECRET_KEY = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")


class FakeTransport:
    def __init__(self, response=None) -> None:
        self.payloads: list[dict] = []
        self.response = response if response is not None else {"status": "ok", "response": {"type": "default"}}

    def post(self, path: str, payload: dict):
        self.payloads.append(payload)
        return self.response


def make_signer(nonce: NonceSource | None = None) -> WalletSigner:
    account = Account.create()
    return WalletSigner(
        account,
        master_address=Account.create().address,
        api_wallet_address=account.address,
        network="mainnet",
        nonce=nonce,
    )


class SteppingClock:
    def __init__(self) -> None:
        self.value = 1_700_000_000_000

    def __call__(self) -> int:
        return self.value


def test_nonce_is_strictly_increasing_within_same_millisecond():
    clock = SteppingClock()
    nonce = NonceSource(clock_ms=clock)

    values = [nonce.next() for _ in range(3)]

    assert values == [1_700_000_000_000, 1_700_000_000_001, 1_700_000_000_002]

    clock.value += 10
    assert nonce.next() == 1_700_000_000_010


def test_place_ioc_builds_decimal_wire_order():
    transport = FakeTransport()
    client = ExchangeClient(make_signer(), transport)

    client.place_ioc(
        asset=0,
        is_buy=True,
        size=Decimal("0.01230"),
        price=Decimal("65000.50"),
        reduce_only=False,
        cloid="0x" + "ab" * 16,
    )

    payload = transport.payloads[0]
    assert payload["vaultAddress"] is None
    assert payload["expiresAfter"] is None
    assert isinstance(payload["nonce"], int)
    assert set(payload["signature"]) == {"r", "s", "v"}

    action = payload["action"]
    assert action["type"] == "order"
    assert action["grouping"] == "na"
    order = action["orders"][0]
    assert order == {
        "a": 0,
        "b": True,
        "p": "65000.5",
        "s": "0.0123",
        "r": False,
        "t": {"limit": {"tif": "Ioc"}},
        "c": "0x" + "ab" * 16,
    }


def test_place_ioc_omits_cloid_when_absent():
    transport = FakeTransport()
    client = ExchangeClient(make_signer(), transport)

    client.place_ioc(
        asset=3, is_buy=False, size=Decimal("1"), price=Decimal("2"), reduce_only=True
    )

    order = transport.payloads[0]["action"]["orders"][0]
    assert "c" not in order
    assert order["r"] is True
    assert order["b"] is False


def test_update_leverage_action():
    transport = FakeTransport()
    client = ExchangeClient(make_signer(), transport)

    client.update_leverage(asset=7, is_cross=False, leverage=5)

    assert transport.payloads[0]["action"] == {
        "type": "updateLeverage",
        "asset": 7,
        "isCross": False,
        "leverage": 5,
    }


def test_cancel_action():
    transport = FakeTransport()
    client = ExchangeClient(make_signer(), transport)

    client.cancel(asset=2, oid=99)

    assert transport.payloads[0]["action"] == {"type": "cancel", "cancels": [{"a": 2, "o": 99}]}


def test_explicit_nonce_is_used_for_resigning():
    clock = SteppingClock()
    transport = FakeTransport()
    client = ExchangeClient(make_signer(NonceSource(clock_ms=clock)), transport)

    client.place_ioc(
        asset=0,
        is_buy=True,
        size=Decimal("1"),
        price=Decimal("1"),
        reduce_only=False,
        nonce=42,
    )

    assert transport.payloads[0]["nonce"] == 42


def test_interpret_resting_order():
    response = interpret(
        {
            "status": "ok",
            "response": {"type": "order", "data": {"statuses": [{"resting": {"oid": 12345}}]}},
        }
    )

    assert response.ok is True
    assert response.oid == 12345
    assert response.category == "none"


def test_interpret_order_error():
    response = interpret(
        {
            "status": "ok",
            "response": {
                "type": "order",
                "data": {"statuses": [{"error": "Insufficient margin to place order."}]},
            },
        }
    )

    assert response.ok is False
    assert response.category == "insufficient_margin"
    assert "Insufficient margin" in response.error


def test_interpret_top_level_error():
    response = interpret({"status": "err", "response": "Invalid nonce"})

    assert response.ok is False
    assert response.category == "invalid_nonce"


def test_interpret_unexpected_payload():
    assert interpret("nope").category == "unknown"


@pytest.mark.parametrize(
    ("text", "category", "action"),
    [
        ("User or API Wallet 0xabc does not exist.", "agent_unauthorized", "stop_task"),
        ("Invalid nonce", "invalid_nonce", "re_sign"),
        ("Insufficient margin to place order.", "insufficient_margin", "backoff"),
        ("Order has invalid price.", "price_out_of_range", "retry_price"),
        ("Reduce only order would increase position.", "reduce_only_violation", "reconcile"),
        ("Order minimum value is $10", "below_min_notional", "skip"),
        ("Duplicate cloid", "duplicate_cloid", "treat_as_success"),
        ("something else entirely", "unknown", "backoff"),
    ],
)
def test_error_classification(text, category, action):
    assert classify_error(text) == category
    assert action_for(category) == action


def test_mask_secrets_covers_prefixed_and_raw_hex():
    key = "ab" * 32
    assert mask_secrets(f"key={key}") == "key=0x<redacted>"
    assert mask_secrets(f"key=0x{key}") == "key=0x<redacted>"
    assert mask_secrets("address 0x" + "ab" * 20) == "address 0x" + "ab" * 20


def test_signer_from_secret_decrypts_and_verifies_address():
    account = Account.create()
    blob = crypto.encrypt_secret(SECRET_KEY, account.key.hex())

    signer = WalletSigner.from_secret(
        secret_enc_key=SECRET_KEY,
        secret_enc=blob,
        master_address=Account.create().address,
        api_wallet_address=account.address,
        network="testnet",
    )

    assert signer.account.address.lower() == account.address.lower()
    assert signer.network == "testnet"


def test_signer_rejects_mismatched_address():
    account = Account.create()
    blob = crypto.encrypt_secret(SECRET_KEY, account.key.hex())

    with pytest.raises(WalletKeyMismatch):
        WalletSigner.from_secret(
            secret_enc_key=SECRET_KEY,
            secret_enc=blob,
            master_address=Account.create().address,
            api_wallet_address=Account.create().address,
            network="mainnet",
        )


def test_signer_reports_unreadable_secret():
    account = Account.create()
    blob = crypto.encrypt_secret(SECRET_KEY, account.key.hex())
    other_key = base64.urlsafe_b64encode(b"z" * 32).decode("ascii")

    with pytest.raises(crypto.SecretUnreadable):
        WalletSigner.from_secret(
            secret_enc_key=other_key,
            secret_enc=blob,
            master_address=Account.create().address,
            api_wallet_address=account.address,
            network="mainnet",
        )


def test_unknown_network_rejected():
    with pytest.raises(SigningError):
        WalletSigner(Account.create(), "0x" + "1" * 40, "0x" + "2" * 40, "devnet")


def test_set_key_state(tmp_path):
    path = tmp_path / "app.db"
    db.init_db(path)

    with db.session(path) as conn:
        wallet_id = db.insert_wallet(
            conn,
            name="w",
            address="0x" + "1" * 40,
            api_wallet_address="0x" + "2" * 40,
            secret_enc=b"blob",
        )
        db.set_key_state(conn, wallet_id, "unreadable")
        assert db.get_wallet(conn, wallet_id).key_state == "unreadable"

        with pytest.raises(ValueError):
            db.set_key_state(conn, wallet_id, "broken")
