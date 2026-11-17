from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from hlcopy import db
from hlcopy.config import Settings
from hlcopy.engine.executor import ExecutionContext, execute_action, log_skip
from hlcopy.engine.planner import plan
from hlcopy.engine.state import EngineState
from hlcopy.engine.types import PlanInput
from hlcopy.hl.errors import HlError, mask_secrets
from hlcopy.crypto import SecretUnreadable
from hlcopy.hl.exchange import ExchangeClient, WalletKeyMismatch, WalletSigner
from hlcopy.hl.info import HlInfoClient
from hlcopy.numeric import canonical

log = logging.getLogger("hlcopy.engine")

UNKNOWN_COIN = "*"


@dataclass(frozen=True)
class EngineEvent:
    kind: str
    task_id: int | None = None
    source: str = "reconcile"
    coin: str | None = None
    latency_ms: int | None = None


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
    ) -> None:
        self.settings = settings
        self.info = info
        self.state = EngineState()
        self._exchange_factory = exchange_factory or default_exchange_factory(settings)
        self._clock_ms = clock_ms
        self._queue: queue.Queue[EngineEvent] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

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

    def submit(self, event: EngineEvent) -> None:
        self._queue.put(event)
        self.state.queue_depth = self._queue.qsize()

    def _run(self) -> None:
        self.reconcile_all(source="start")
        while not self._stop.is_set():
            try:
                event = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._handle(event)
            except Exception:
                log.exception("engine event failed")
            finally:
                self.state.queue_depth = self._queue.qsize()

    def _handle(self, event: EngineEvent) -> None:
        if event.kind in ("command", "fill") and event.task_id is not None:
            self.reconcile_task(event.task_id, source=event.source, latency_ms=event.latency_ms)
        elif event.kind == "tick":
            self.reconcile_all(source="reconcile")

    def reconcile_all(self, source: str = "reconcile") -> None:
        with db.session(self.settings.db_path) as conn:
            task_ids = [task.id for task in db.list_tasks(conn) if task.status == "running"]

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
        latency_ms: int | None = None,
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
                conn, task, wallet, exchange, meta, mids, leader, follower, source, now_ms, latency_ms
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
    ) -> None:
        coins = {coin.name: coin for coin in meta.coins}
        leader_positions = {position.coin: position for position in leader.positions}
        follower_positions = {position.coin: position for position in follower.positions}
        states = db.list_coin_states(conn, task.id)

        ratio = Decimal(task.ratio)
        max_notional = Decimal(task.max_notional_usd)
        slippage = Decimal(task.max_slippage)

        for coin in sorted(set(leader_positions) | set(follower_positions)):
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

            if action.orders or action.leverage is not None:
                execute_action(conn, exchange, ctx, action)
            else:
                log_skip(conn, ctx, action)
