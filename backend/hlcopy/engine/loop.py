from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from hlcopy import db
from hlcopy.config import Settings
from hlcopy.engine.executor import (
    ExecutionContext,
    cloid_for,
    execute_action,
    limit_price,
    log_skip,
)
from hlcopy.engine.planner import plan
from hlcopy.engine.state import EngineState
from hlcopy.engine.types import PlanInput
from hlcopy.hl.errors import HlError, mask_secrets
from hlcopy.crypto import SecretUnreadable
from hlcopy.hl.exchange import ExchangeClient, WalletKeyMismatch, WalletSigner
from hlcopy.hl.info import Fill, HlInfoClient
from hlcopy.numeric import canonical

log = logging.getLogger("hlcopy.engine")

UNKNOWN_COIN = "*"


@dataclass(frozen=True)
class EngineEvent:
    kind: str
    task_id: int | None = None
    source: str = "reconcile"
    coin: str | None = None
    leader: str | None = None
    fill_time_ms: int | None = None


def default_exchange_factory(settings: Settings) -> Callable[[db.WalletRecord], ExchangeClient]:
    def build(wallet: db.WalletRecord) -> ExchangeClient:
        signer = WalletSigner.from_secret(
            secret_enc_key=settings.secret_enc_key,
            secret_enc=wallet.secret_enc,
            master_address=wallet.address,
            api_wallet_address=wallet.api_wallet_address,
            network=settings.hl_network,
        )
        return ExchangeClient(signer)

    return build


class Engine:
    def __init__(
        self,
        settings: Settings,
        info: HlInfoClient,
        *,
        exchange_factory: Callable[[db.WalletRecord], ExchangeClient] | None = None,
        clock_ms: Callable[[], int] = db.now_ms,
        ws=None,
    ) -> None:
        self.settings = settings
        self.info = info
        self.state = EngineState()
        self._exchange_factory = exchange_factory or default_exchange_factory(settings)
        self._clock_ms = clock_ms
        self._queue: queue.Queue[EngineEvent] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.ws = ws
        self._pending_fills: set[tuple[str, str]] = set()
        self._pending_lock = threading.Lock()

    def start(self) -> None:
        self.state.running = True
        self._thread = threading.Thread(target=self._run, name="engine-loop", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
        self.state.running = False

    def attach_ws(self, ws) -> None:
        self.ws = ws

    def submit(self, event: EngineEvent) -> None:
        self._queue.put(event)
        self.state.queue_depth = self._queue.qsize()

    def submit_fill(self, leader: str, fill: Fill) -> bool:
        key = (leader.lower(), fill.coin)
        with self._pending_lock:
            if key in self._pending_fills:
                return False
            self._pending_fills.add(key)
        self.submit(
            EngineEvent(
                kind="fill",
                source="fill",
                coin=fill.coin,
                leader=leader,
                fill_time_ms=fill.ts,
            )
        )
        return True

    def _run(self) -> None:
        self.reconcile_all(source="start")
        interval = float(self.settings.reconcile_interval_sec)
        next_tick = time.monotonic() + interval

        while not self._stop.is_set():
            remaining = next_tick - time.monotonic()
            try:
                event = self._queue.get(timeout=max(0.05, min(0.5, remaining)))
            except queue.Empty:
                event = None

            if event is not None:
                try:
                    self._handle(event)
                except Exception:
                    log.exception("engine event failed")
                finally:
                    self.state.queue_depth = self._queue.qsize()

            if time.monotonic() >= next_tick:
                try:
                    self.tick()
                except Exception:
                    log.exception("engine tick failed")
                next_tick = time.monotonic() + interval

    def tick(self) -> None:
        self.reconcile_all(source="reconcile")

    def _handle(self, event: EngineEvent) -> None:
        try:
            if event.kind == "stop_close" and event.task_id is not None:
                self.close_task(event.task_id)
            elif event.kind == "fill" and event.leader and event.coin:
                self.apply_fill(
                    event.leader,
                    Fill(coin=event.coin, ts=event.fill_time_ms or 0),
                )
            elif event.kind == "command" and event.task_id is not None:
                self.reconcile_task(event.task_id, source=event.source)
            elif event.kind == "tick":
                self.reconcile_all(source="reconcile")
        finally:
            if event.kind == "fill" and event.leader and event.coin:
                with self._pending_lock:
                    self._pending_fills.discard((event.leader.lower(), event.coin))

    def apply_fill(self, leader: str, fill: Fill) -> None:
        with db.session(self.settings.db_path) as conn:
            tasks = db.list_running_tasks_by_leader(conn, leader)
            if fill.closes_position:
                for task in tasks:
                    db.set_locked(conn, task.id, fill.coin, False)
        for task in tasks:
            self.reconcile_task(
                task.id, source="fill", fill_time_ms=fill.ts, coin_hint=fill.coin
            )

    def reconcile_all(self, source: str = "reconcile") -> None:
        with db.session(self.settings.db_path) as conn:
            tasks = [task for task in db.list_tasks(conn) if task.status == "running"]

        if self.ws is not None:
            self.ws.sync({task.leader for task in tasks})

        task_ids = [task.id for task in tasks]

        for task_id in task_ids:
            try:
                self.reconcile_task(task_id, source=source)
            except Exception:
                log.exception("task %s failed during %s", task_id, source)

        self.state.last_tick_ms = self._clock_ms()

    def reconcile_task(
        self,
        task_id: int,
        *,
        source: str = "reconcile",
        fill_time_ms: int | None = None,
        coin_hint: str | None = None,
    ) -> None:
        with db.session(self.settings.db_path) as conn:
            task = db.get_task(conn, task_id)
            if task is None or task.status != "running":
                return
            wallet = db.get_wallet(conn, task.wallet_id)
            if wallet is None or wallet.key_state != "ok":
                return

            try:
                exchange = self._exchange_factory(wallet)
            except (SecretUnreadable, WalletKeyMismatch) as exc:
                log.warning("wallet %s key cannot be used: %s", wallet.id, exc)
                db.set_key_state(conn, wallet.id, "unreadable")
                return

            now_ms = self._clock_ms()
            latency_ms = (
                max(0, now_ms - fill_time_ms) if fill_time_ms is not None else None
            )
            try:
                meta = self.info.meta()
                mids = self.info.all_mids()
                leader = self.info.account(task.leader)
                follower = self.info.account(wallet.address)
            except HlError as exc:
                db.insert_exec_log(
                    conn,
                    task_id=task.id,
                    wallet_id=wallet.id,
                    ts=now_ms,
                    source=source,
                    coin=UNKNOWN_COIN,
                    result="skipped",
                    reason=exc.code if exc.code != "hl_unavailable" else "leader_state_unavailable",
                    raw=mask_secrets(exc.message),
                )
                return

            self._reconcile_snapshot(
                conn,
                task,
                wallet,
                exchange,
                meta,
                mids,
                leader,
                follower,
                source,
                now_ms,
                latency_ms,
                coin_hint,
            )

    def _reconcile_snapshot(
        self,
        conn,
        task: db.TaskRecord,
        wallet: db.WalletRecord,
        exchange: ExchangeClient,
        meta,
        mids,
        leader,
        follower,
        source: str,
        now_ms: int,
        latency_ms: int | None,
        coin_hint: str | None = None,
    ) -> None:
        coins = {coin.name: coin for coin in meta.coins}
        leader_positions = {position.coin: position for position in leader.positions}
        follower_positions = {position.coin: position for position in follower.positions}

        if source in ("start", "resume") and task.mode == "new_only":
            for position in leader.positions:
                if position.size != 0:
                    db.set_locked(conn, task.id, position.coin, True)

        states = db.list_coin_states(conn, task.id)

        ratio = Decimal(task.ratio)
        max_notional = Decimal(task.max_notional_usd)
        slippage = Decimal(task.max_slippage)

        watch = set(leader_positions) | set(follower_positions)
        watch |= {coin for coin, state in states.items() if state.locked}
        watch = sorted(watch)
        if coin_hint is not None:
            watch = [coin for coin in watch if coin == coin_hint]

        for coin in watch:
            coin_meta = coins.get(coin)
            if coin_meta is None:
                continue
            leader_position = leader_positions.get(coin)
            follower_position = follower_positions.get(coin)
            state = states.get(coin)
            mark_px = mids.get(coin)

            action = plan(
                PlanInput(
                    coin=coin,
                    task_status=task.status,
                    leader_size=leader_position.size if leader_position else Decimal(0),
                    follower_size=follower_position.size if follower_position else Decimal(0),
                    ratio=ratio,
                    max_notional=max_notional,
                    sz_decimals=coin_meta.sz_decimals,
                    mark_px=mark_px,
                    locked=state.locked if state else False,
                    next_retry_at_ms=state.next_retry_at if state else None,
                    now_ms=now_ms,
                    leverage_set=state.leverage_set if state else False,
                    leader_leverage=Decimal(leader_position.leverage) if leader_position else None,
                    leader_is_cross=leader_position.is_cross if leader_position else True,
                    max_leverage=coin_meta.max_leverage,
                )
            )

            if mark_px is None:
                db.insert_exec_log(
                    conn,
                    task_id=task.id,
                    wallet_id=wallet.id,
                    ts=now_ms,
                    source=source,
                    coin=coin,
                    leader_pos=canonical(leader_position.size if leader_position else Decimal(0)),
                    pos_before=canonical(follower_position.size if follower_position else Decimal(0)),
                    target=canonical(action.target),
                    result="skipped",
                    reason=action.reason,
                )
                continue

            ctx = ExecutionContext(
                task_id=task.id,
                wallet_id=wallet.id,
                coin=coin,
                coin_meta=coin_meta,
                slippage=slippage,
                source=source,
                leader_pos=leader_position.size if leader_position else Decimal(0),
                pos_before=follower_position.size if follower_position else Decimal(0),
                mark_px=mark_px,
                now_ms=now_ms,
                latency_ms=latency_ms,
            )

            if action.unlocked:
                db.set_locked(conn, task.id, coin, False)

            if action.orders or action.leverage is not None:
                execute_action(conn, exchange, ctx, action)
            elif action.reason != "backoff":
                log_skip(conn, ctx, action)


    def close_task(self, task_id: int) -> None:
        with db.session(self.settings.db_path) as conn:
            task = db.get_task(conn, task_id)
            if task is None:
                return
            wallet = db.get_wallet(conn, task.wallet_id)
            if wallet is None or wallet.key_state != "ok":
                return
            try:
                exchange = self._exchange_factory(wallet)
            except (SecretUnreadable, WalletKeyMismatch) as exc:
                log.warning("wallet %s key cannot be used: %s", wallet.id, exc)
                db.set_key_state(conn, wallet.id, "unreadable")
                return

            now_ms = self._clock_ms()
            try:
                meta = self.info.meta()
                mids = self.info.all_mids()
                follower = self.info.account(wallet.address)
            except HlError as exc:
                db.insert_exec_log(
                    conn,
                    task_id=task.id,
                    wallet_id=wallet.id,
                    ts=now_ms,
                    source="stop",
                    coin=UNKNOWN_COIN,
                    result="skipped",
                    reason="leader_state_unavailable",
                    raw=mask_secrets(exc.message),
                )
                return

            coins = {coin.name: coin for coin in meta.coins}
            slippage = Decimal(task.max_slippage)

            for position in follower.positions:
                coin_meta = coins.get(position.coin)
                if coin_meta is None or position.size == 0:
                    continue
                for order in follower.open_orders:
                    if order.coin == position.coin:
                        exchange.cancel(asset=coin_meta.asset_index, oid=order.oid)
                mark_px = mids.get(position.coin)
                if mark_px is None:
                    continue
                side = "sell" if position.size > 0 else "buy"
                try:
                    price = limit_price(mark_px, slippage, side, coin_meta.sz_decimals)
                except Exception as exc:
                    log.warning("cannot price close for %s: %s", position.coin, exc)
                    continue
                cloid = cloid_for(task.id, position.coin, Decimal(0), 0)
                response = exchange.place_ioc(
                    asset=coin_meta.asset_index,
                    is_buy=side == "buy",
                    size=abs(position.size),
                    price=price,
                    reduce_only=True,
                    cloid=cloid,
                )
                ok = response.ok or response.duplicate
                db.insert_exec_log(
                    conn,
                    task_id=task.id,
                    wallet_id=wallet.id,
                    ts=now_ms,
                    source="stop",
                    coin=position.coin,
                    leader_pos=None,
                    pos_before=canonical(position.size),
                    target="0",
                    side=side,
                    qty=canonical(abs(position.size)),
                    price=canonical(price),
                    result="filled" if ok else "failed",
                    reason="close_all",
                    oid=str(response.oid) if response.oid is not None else None,
                    cloid=cloid,
                    raw=None if ok else mask_secrets(response.error or "")[:2000],
                )
