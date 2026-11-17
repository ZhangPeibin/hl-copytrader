from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from eth_account import Account
from eth_utils import to_checksum_address
from hyperliquid.utils.signing import sign_l1_action

from hlcopy import crypto
from hlcopy.addresses import same_address
from hlcopy.hl.errors import HlError, classify_error
from hlcopy.numeric import canonical

BASE_URLS = {
    "mainnet": "https://api.hyperliquid.xyz",
    "testnet": "https://api.hyperliquid-testnet.xyz",
}


class SigningError(RuntimeError):
    pass


class WalletKeyMismatch(SigningError):
    pass


class ExchangeTransport(Protocol):
    def post(self, path: str, payload: dict[str, Any]) -> Any: ...


class HttpExchangeTransport:
    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        import requests

        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        response = self._session.post(
            f"{self._base_url}{path}", json=payload, timeout=self._timeout
        )
        response.raise_for_status()
        return response.json()


class NonceSource:
    def __init__(self, clock_ms: Callable[[], int] | None = None) -> None:
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._last = 0
        self._lock = threading.Lock()

    def next(self) -> int:
        with self._lock:
            value = max(self._clock_ms(), self._last + 1)
            self._last = value
            return value

    def reset(self) -> None:
        with self._lock:
            self._last = self._clock_ms()


class WalletSigner:
    def __init__(
        self,
        account,
        master_address: str,
        api_wallet_address: str,
        network: str,
        nonce: NonceSource | None = None,
    ) -> None:
        if network not in BASE_URLS:
            raise SigningError(f"unknown network {network!r}")
        self.account = account
        self.master_address = master_address
        self.api_wallet_address = api_wallet_address
        self.network = network
        self.nonce = nonce or NonceSource()

    @classmethod
    def from_secret(
        cls,
        *,
        secret_enc_key: str,
        secret_enc: bytes,
        master_address: str,
        api_wallet_address: str,
        network: str,
        nonce: NonceSource | None = None,
    ) -> WalletSigner:
        key_hex = crypto.decrypt_secret(secret_enc_key, secret_enc)
        account = Account.from_key("0x" + key_hex)
        derived = to_checksum_address(account.address)
        if not same_address(derived, api_wallet_address):
            raise WalletKeyMismatch("stored key does not control the configured API wallet address")
        return cls(account, master_address, api_wallet_address, network, nonce)


@dataclass(frozen=True)
class ExchangeResponse:
    ok: bool
    raw: Any
    oid: int | None = None
    error: str | None = None
    category: str = "none"

    @property
    def duplicate(self) -> bool:
        return self.category == "duplicate_cloid"


def interpret(raw: Any) -> ExchangeResponse:
    if not isinstance(raw, dict):
        return ExchangeResponse(ok=False, raw=raw, error="unexpected exchange response", category="unknown")

    if raw.get("status") == "err":
        message = raw.get("response") or raw.get("error") or "exchange rejected the request"
        text = str(message)
        return ExchangeResponse(ok=False, raw=raw, error=text, category=classify_error(text))

    statuses = (((raw.get("response") or {}).get("data") or {}).get("statuses")) or []
    for status in statuses:
        if not isinstance(status, dict):
            continue
        if "error" in status:
            text = str(status["error"])
            return ExchangeResponse(ok=False, raw=raw, error=text, category=classify_error(text))
        for key in ("resting", "filled"):
            if key in status and isinstance(status[key], dict):
                oid = status[key].get("oid")
                return ExchangeResponse(ok=True, raw=raw, oid=int(oid) if oid is not None else None)

    if raw.get("status") != "ok":
        text = str(raw.get("response") or raw.get("error") or "unexpected exchange response")
        return ExchangeResponse(ok=False, raw=raw, error=text, category=classify_error(text))

    return ExchangeResponse(ok=True, raw=raw)


class ExchangeClient:
    def __init__(
        self,
        signer: WalletSigner,
        transport: ExchangeTransport | None = None,
    ) -> None:
        if signer.network not in BASE_URLS:
            raise SigningError(f"unknown network {signer.network!r}")
        self.signer = signer
        self.transport = transport or HttpExchangeTransport(BASE_URLS[signer.network])
        self.is_mainnet = signer.network == "mainnet"

    def _submit(self, action: dict[str, Any], nonce: int | None = None) -> ExchangeResponse:
        response = self._send(action, nonce)
        if nonce is None and not response.ok and response.category == "invalid_nonce":
            self.signer.nonce.reset()
            response = self._send(action, None)
        return response

    def _send(self, action: dict[str, Any], nonce: int | None) -> ExchangeResponse:
        value = nonce if nonce is not None else self.signer.nonce.next()
        signature = sign_l1_action(
            self.signer.account,
            action,
            None,
            value,
            None,
            self.is_mainnet,
        )
        payload = {
            "action": action,
            "nonce": value,
            "signature": signature,
            "vaultAddress": None,
            "expiresAfter": None,
        }
        try:
            raw = self.transport.post("/exchange", payload)
        except Exception as exc:
            raise HlError(f"exchange request failed: {exc}") from exc
        return interpret(raw)

    def place_ioc(
        self,
        *,
        asset: int,
        is_buy: bool,
        size: Decimal,
        price: Decimal,
        reduce_only: bool,
        cloid: str | None = None,
        nonce: int | None = None,
    ) -> ExchangeResponse:
        order: dict[str, Any] = {
            "a": asset,
            "b": is_buy,
            "p": canonical(price),
            "s": canonical(size),
            "r": reduce_only,
            "t": {"limit": {"tif": "Ioc"}},
        }
        if cloid:
            order["c"] = cloid
        action = {"type": "order", "orders": [order], "grouping": "na"}
        return self._submit(action, nonce)

    def update_leverage(
        self,
        *,
        asset: int,
        is_cross: bool,
        leverage: int,
        nonce: int | None = None,
    ) -> ExchangeResponse:
        action = {
            "type": "updateLeverage",
            "asset": asset,
            "isCross": is_cross,
            "leverage": leverage,
        }
        return self._submit(action, nonce)

    def cancel(self, *, asset: int, oid: int, nonce: int | None = None) -> ExchangeResponse:
        action = {"type": "cancel", "cancels": [{"a": asset, "o": oid}]}
        return self._submit(action, nonce)
