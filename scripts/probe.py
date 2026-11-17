from __future__ import annotations

import os
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from eth_account import Account

from hlcopy.hl.errors import action_for, classify_error, mask_secrets
from hlcopy.hl.exchange import BASE_URLS, ExchangeClient, HttpExchangeTransport, WalletSigner
from hlcopy.hl.info import HlInfoClient
from hlcopy.numeric import canonical


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    return value.strip() if value else value


def report(case: str, response) -> None:
    raw = mask_secrets(str(response.error or ""))
    category = response.category if not response.ok else "ok"
    action = "none" if response.ok else action_for(category)
    print(f"[{case}] ok={response.ok} category={category} action={action}")
    if raw:
        print(f"    raw: {raw[:300]}")


def main() -> int:
    key_hex = env("HL_PROBE_API_KEY")
    network = (env("HL_PROBE_NETWORK", "testnet") or "testnet").lower()
    coin = (env("HL_PROBE_COIN", "BTC") or "BTC").upper()

    if not key_hex:
        print("HL_PROBE_API_KEY is required (API wallet private key, hex, with or without 0x).")
        print("Optional: HL_PROBE_NETWORK=testnet|mainnet, HL_PROBE_COIN=BTC")
        return 2
    if network not in BASE_URLS:
        print(f"HL_PROBE_NETWORK must be one of {sorted(BASE_URLS)}")
        return 2

    account = Account.from_key(key_hex if key_hex.startswith("0x") else "0x" + key_hex)
    signer = WalletSigner(account, account.address, account.address, network)
    client = ExchangeClient(signer, HttpExchangeTransport(BASE_URLS[network]))
    info = HlInfoClient(network)

    meta = info.meta()
    meta_by_coin = {entry.name: entry for entry in meta.coins}
    if coin not in meta_by_coin:
        print(f"{coin} is not in the perp universe")
        return 2
    coin_meta = meta_by_coin[coin]
    mids = info.all_mids()
    mark = mids.get(coin)
    if mark is None:
        print(f"no mark price for {coin}")
        return 2

    print(f"network={network} coin={coin} mark={canonical(mark)} api_wallet={account.address}")
    print("no order is expected to fill: prices are far from the book and IOC\n")

    far_buy = (mark * Decimal("0.5")).quantize(Decimal("0.0001"))
    size_20 = max(Decimal("1"), (Decimal(20) / mark))

    report(
        "unauthorized-agent",
        ExchangeClient(
            WalletSigner(Account.create(), account.address, account.address, network),
            HttpExchangeTransport(BASE_URLS[network]),
        ).place_ioc(
            asset=coin_meta.asset_index,
            is_buy=True,
            size=size_20,
            price=far_buy,
            reduce_only=False,
        ),
    )

    shared_nonce = signer.nonce.next()
    client.place_ioc(
        asset=coin_meta.asset_index,
        is_buy=True,
        size=size_20,
        price=far_buy,
        reduce_only=False,
        nonce=shared_nonce,
    )
    report(
        "duplicate-nonce",
        client.place_ioc(
            asset=coin_meta.asset_index,
            is_buy=True,
            size=size_20,
            price=far_buy,
            reduce_only=False,
            nonce=shared_nonce,
        ),
    )

    report(
        "below-min-notional",
        client.place_ioc(
            asset=coin_meta.asset_index,
            is_buy=True,
            size=max(Decimal("0.00001"), Decimal("1") / mark),
            price=far_buy,
            reduce_only=False,
        ),
    )

    report(
        "invalid-price",
        client.place_ioc(
            asset=coin_meta.asset_index,
            is_buy=True,
            size=size_20,
            price=Decimal("0.0000001"),
            reduce_only=False,
        ),
    )

    report(
        "reduce-only-violation",
        client.place_ioc(
            asset=coin_meta.asset_index,
            is_buy=False,
            size=size_20,
            price=mark * Decimal("1.5"),
            reduce_only=True,
        ),
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
