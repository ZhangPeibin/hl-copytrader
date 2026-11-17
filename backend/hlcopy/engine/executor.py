from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal

from hlcopy import db
from hlcopy.engine.types import Action
from hlcopy.hl.errors import mask_secrets
from hlcopy.hl.exchange import ExchangeClient, ExchangeResponse
from hlcopy.hl.info import CoinMeta
from hlcopy.hl.rounding import round_price
from hlcopy.numeric import canonical

MAX_RAW_CHARS = 2000


def cloid_for(task_id: int, coin: str, target: Decimal, leg: int) -> str:
    digest = hashlib.blake2b(
        f"{task_id}|{coin}|{canonical(target)}|{leg}".encode("utf-8"), digest_size=16
    )
    return "0x" + digest.hexdigest()


def limit_price(mark: Decimal, slippage: Decimal, side: str, sz_decimals: int) -> Decimal:
    factor = Decimal(1) + slippage if side == "buy" else Decimal(1) - slippage
    return round_price(mark * factor, sz_decimals, side=side)


def _raw_text(response: ExchangeResponse) -> str | None:
    if response.error is None:
        return None
    return mask_secrets(response.error)[:MAX_RAW_CHARS]


@dataclass(frozen=True)
class ExecutionContext:
    task_id: int
    wallet_id: int
    coin: str
    coin_meta: CoinMeta
    slippage: Decimal
    source: str
    leader_pos: Decimal
    pos_before: Decimal
    mark_px: Decimal
    now_ms: int
    latency_ms: int | None = None


def log_skip(conn, ctx: ExecutionContext, action: Action) -> None:
    db.insert_exec_log(
        conn,
        task_id=ctx.task_id,
        wallet_id=ctx.wallet_id,
        ts=ctx.now_ms,
        source=ctx.source,
        coin=ctx.coin,
        leader_pos=canonical(ctx.leader_pos),
        pos_before=canonical(ctx.pos_before),
        target=canonical(action.target),
        result="skipped",
        reason=action.reason,
    )


def execute_action(conn, exchange: ExchangeClient, ctx: ExecutionContext, action: Action) -> None:
    if action.leverage is not None:
        response = exchange.update_leverage(
            asset=ctx.coin_meta.asset_index,
            is_cross=action.leverage.is_cross,
            leverage=action.leverage.leverage,
        )
        if not response.ok:
            db.insert_exec_log(
                conn,
                task_id=ctx.task_id,
                wallet_id=ctx.wallet_id,
                ts=ctx.now_ms,
                source=ctx.source,
                coin=ctx.coin,
                leader_pos=canonical(ctx.leader_pos),
                pos_before=canonical(ctx.pos_before),
                target=canonical(action.target),
                result="failed",
                reason=action.reason,
                raw=_raw_text(response),
            )
            return
        db.mark_leverage_set(conn, ctx.task_id, ctx.coin)

    for order in action.orders:
        price = limit_price(ctx.mark_px, ctx.slippage, order.side, ctx.coin_meta.sz_decimals)
        cloid = cloid_for(ctx.task_id, ctx.coin, action.target, order.leg)
        response = exchange.place_ioc(
            asset=ctx.coin_meta.asset_index,
            is_buy=order.side == "buy",
            size=order.size,
            price=price,
            reduce_only=order.reduce_only,
            cloid=cloid,
        )
        ok = response.ok or response.duplicate
        db.insert_exec_log(
            conn,
            task_id=ctx.task_id,
            wallet_id=ctx.wallet_id,
            ts=ctx.now_ms,
            source=ctx.source,
            coin=ctx.coin,
            leader_pos=canonical(ctx.leader_pos),
            pos_before=canonical(ctx.pos_before),
            target=canonical(action.target),
            side=order.side,
            qty=canonical(order.size),
            price=canonical(price),
            result="filled" if ok else "failed",
            reason=action.reason,
            latency_ms=ctx.latency_ms,
            oid=str(response.oid) if response.oid is not None else None,
            cloid=cloid,
            raw=None if ok else _raw_text(response),
        )
        if not ok:
            return

    db.set_last_target(conn, ctx.task_id, ctx.coin, canonical(action.target))
