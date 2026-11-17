from __future__ import annotations

import sqlite3
from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict

from hlcopy import db
from hlcopy.addresses import ValidationError, normalize_address
from hlcopy.api.deps import SettingsDep
from hlcopy.api.errors import fail
from hlcopy.engine.loop import EngineEvent
from hlcopy.engine.planner import plan
from hlcopy.engine.types import PlanInput
from hlcopy.hl.errors import HlError
from hlcopy.numeric import canonical

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

Mode = Literal["sync", "new_only"]


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wallet_id: int
    leader: str
    mode: Mode = "sync"
    ratio: str
    max_notional_usd: str
    max_leverage: int = 10
    max_slippage: str | None = None
    close_on_stop: bool = False


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wallet_id: int | None = None
    leader: str | None = None
    mode: Mode | None = None
    ratio: str | None = None
    max_notional_usd: str | None = None
    max_leverage: int | None = None
    max_slippage: str | None = None
    close_on_stop: bool | None = None


class StopBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    close_positions: bool | None = None


class TaskOut(BaseModel):
    id: int
    wallet_id: int
    leader: str
    mode: str
    ratio: str
    max_notional_usd: str
    max_leverage: str
    max_slippage: str
    status: str
    close_on_stop: bool
    created_at: int
    updated_at: int


class CompareRow(BaseModel):
    coin: str
    leader_size: str
    follower_size: str
    target: str
    delta: str
    mark_px: str | None
    locked: bool
    truncated: bool
    reason: str


class CompareOut(BaseModel):
    task_id: int
    mode: str
    leader: str
    address: str
    fetched_at_ms: int
    rows: list[CompareRow]


def _decimal(raw: str, field: str, *, low: Decimal | None, high: Decimal) -> Decimal:
    try:
        value = Decimal(str(raw).strip())
    except InvalidOperation:
        fail(400, f"invalid_{field}", f"{field} must be a decimal string")
    if not value.is_finite():
        fail(400, f"invalid_{field}", f"{field} must be finite")
    if low is not None and value <= low:
        fail(400, f"invalid_{field}", f"{field} must be greater than {low}")
    if value > high:
        fail(400, f"invalid_{field}", f"{field} must be at most {high}")
    return value


def _leader(raw: str) -> str:
    try:
        return normalize_address(raw, "leader")
    except ValidationError as exc:
        fail(400, exc.code, exc.message)
        raise AssertionError from exc


def _wallet(conn: sqlite3.Connection, wallet_id: int) -> db.WalletRecord:
    record = db.get_wallet(conn, wallet_id)
    if record is None:
        fail(404, "wallet_not_found", "wallet does not exist")
    return record


def _leverage(value: int) -> int:
    if value < 1 or value > 100:
        fail(400, "invalid_max_leverage", "max_leverage must be between 1 and 100")
    return value


def _out(record: db.TaskRecord) -> TaskOut:
    return TaskOut(
        id=record.id,
        wallet_id=record.wallet_id,
        leader=record.leader,
        mode=record.mode,
        ratio=record.ratio,
        max_notional_usd=record.max_notional_usd,
        max_leverage=record.max_leverage,
        max_slippage=record.max_slippage,
        status=record.status,
        close_on_stop=record.close_on_stop,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.get("", response_model=list[TaskOut])
def list_tasks(settings: SettingsDep, wallet_id: int | None = None) -> list[TaskOut]:
    with db.session(settings.db_path) as conn:
        return [_out(record) for record in db.list_tasks(conn, wallet_id)]


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
def create_task(payload: TaskCreate, request: Request, settings: SettingsDep) -> TaskOut:
    leader = _leader(payload.leader)
    ratio = _decimal(payload.ratio, "ratio", low=Decimal(0), high=Decimal(1))
    max_notional = _decimal(payload.max_notional_usd, "max_notional_usd", low=Decimal(0), high=Decimal("1e12"))
    slippage = (
        _decimal(payload.max_slippage, "max_slippage", low=None, high=Decimal("0.1"))
        if payload.max_slippage is not None
        else settings.max_slippage_default
    )
    _leverage(payload.max_leverage)

    with db.session(settings.db_path) as conn:
        _wallet(conn, payload.wallet_id)
        task_id = db.insert_task(
            conn,
            wallet_id=payload.wallet_id,
            leader=leader,
            mode=payload.mode,
            ratio=canonical(ratio),
            max_notional_usd=canonical(max_notional),
            max_leverage=str(payload.max_leverage),
            max_slippage=canonical(slippage),
            close_on_stop=payload.close_on_stop,
        )
        record = db.get_task(conn, task_id)

    assert record is not None
    request.app.state.engine.submit(EngineEvent(kind="command", task_id=record.id, source="start"))
    return _out(record)


@router.get("/{task_id}", response_model=TaskOut)
def get_task(task_id: int, settings: SettingsDep) -> TaskOut:
    with db.session(settings.db_path) as conn:
        record = db.get_task(conn, task_id)
    if record is None:
        fail(404, "task_not_found", "task does not exist")
    return _out(record)


@router.put("/{task_id}", response_model=TaskOut)
def update_task(task_id: int, payload: TaskUpdate, request: Request, settings: SettingsDep) -> TaskOut:
    with db.session(settings.db_path) as conn:
        record = db.get_task(conn, task_id)
        if record is None:
            fail(404, "task_not_found", "task does not exist")

        wallet_id = payload.wallet_id if payload.wallet_id is not None else record.wallet_id
        _wallet(conn, wallet_id)
        leader = _leader(payload.leader) if payload.leader is not None else record.leader
        mode = payload.mode or record.mode
        ratio = (
            _decimal(payload.ratio, "ratio", low=Decimal(0), high=Decimal(1))
            if payload.ratio is not None
            else Decimal(record.ratio)
        )
        max_notional = (
            _decimal(payload.max_notional_usd, "max_notional_usd", low=Decimal(0), high=Decimal("1e12"))
            if payload.max_notional_usd is not None
            else Decimal(record.max_notional_usd)
        )
        slippage = (
            _decimal(payload.max_slippage, "max_slippage", low=None, high=Decimal("0.1"))
            if payload.max_slippage is not None
            else Decimal(record.max_slippage)
        )
        max_leverage = (
            _leverage(payload.max_leverage)
            if payload.max_leverage is not None
            else int(record.max_leverage)
        )
        close_on_stop = (
            payload.close_on_stop if payload.close_on_stop is not None else record.close_on_stop
        )

        db.update_task(
            conn,
            task_id,
            wallet_id=wallet_id,
            leader=leader,
            mode=mode,
            ratio=canonical(ratio),
            max_notional_usd=canonical(max_notional),
            max_leverage=str(max_leverage),
            max_slippage=format(slippage, "f"),
            close_on_stop=close_on_stop,
        )
        updated = db.get_task(conn, task_id)

    assert updated is not None
    request.app.state.engine.submit(EngineEvent(kind="command", task_id=updated.id, source="param"))
    return _out(updated)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: int, settings: SettingsDep) -> None:
    with db.session(settings.db_path) as conn:
        if db.get_task(conn, task_id) is None:
            fail(404, "task_not_found", "task does not exist")
        db.delete_task(conn, task_id)


@router.get("/{task_id}/compare", response_model=CompareOut)
def compare(task_id: int, request: Request, settings: SettingsDep) -> CompareOut:
    with db.session(settings.db_path) as conn:
        record = db.get_task(conn, task_id)
        if record is None:
            fail(404, "task_not_found", "task does not exist")
        wallet = _wallet(conn, record.wallet_id)
        states = db.list_coin_states(conn, task_id)

    info = request.app.state.info
    try:
        meta = info.meta()
        mids = info.all_mids()
        leader_snapshot = info.account(record.leader)
        follower_snapshot = info.account(wallet.address)
    except HlError as exc:
        fail(502, exc.code, exc.message)

    coins = {coin.name: coin for coin in meta.coins}
    leader_positions = {position.coin: position for position in leader_snapshot.positions}
    follower_positions = {position.coin: position for position in follower_snapshot.positions}

    ratio = Decimal(record.ratio)
    max_notional = Decimal(record.max_notional_usd)
    now_ms = db.now_ms()

    rows: list[CompareRow] = []
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
                task_status=record.status,
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
                leader_leverage=(
                    Decimal(leader_position.leverage) if leader_position else None
                ),
                leader_is_cross=leader_position.is_cross if leader_position else True,
                max_leverage=coin_meta.max_leverage,
            )
        )
        rows.append(
            CompareRow(
                coin=coin,
                leader_size=canonical(leader_position.size if leader_position else Decimal(0)),
                follower_size=canonical(
                    follower_position.size if follower_position else Decimal(0)
                ),
                target=canonical(action.target),
                delta=canonical(action.delta),
                mark_px=canonical(mark_px) if mark_px is not None else None,
                locked=state.locked if state else False,
                truncated=action.truncated,
                reason=action.reason,
            )
        )

    return CompareOut(
        task_id=record.id,
        mode=record.mode,
        leader=record.leader,
        address=wallet.address,
        fetched_at_ms=leader_snapshot.fetched_at_ms,
        rows=rows,
    )


def _transition(settings: SettingsDep, task_id: int, allowed: tuple[str, ...], target: str):
    with db.session(settings.db_path) as conn:
        task = db.get_task(conn, task_id)
        if task is None:
            fail(404, "task_not_found", "task does not exist")
        if task.status not in allowed:
            fail(409, "invalid_transition", f"cannot move a {task.status} task to {target}")
        db.set_task_status(conn, task_id, target)
        updated = db.get_task(conn, task_id)
    assert updated is not None
    return updated


@router.post("/{task_id}/start", response_model=TaskOut)
def start_task(task_id: int, request: Request, settings: SettingsDep) -> TaskOut:
    updated = _transition(settings, task_id, ("stopped",), "running")
    request.app.state.engine.submit(EngineEvent(kind="command", task_id=task_id, source="start"))
    return _out(updated)


@router.post("/{task_id}/pause", response_model=TaskOut)
def pause_task(task_id: int, settings: SettingsDep) -> TaskOut:
    return _out(_transition(settings, task_id, ("running",), "paused"))


@router.post("/{task_id}/resume", response_model=TaskOut)
def resume_task(task_id: int, request: Request, settings: SettingsDep) -> TaskOut:
    updated = _transition(settings, task_id, ("paused",), "running")
    request.app.state.engine.submit(EngineEvent(kind="command", task_id=task_id, source="resume"))
    return _out(updated)


@router.post("/{task_id}/stop", response_model=TaskOut)
def stop_task(
    task_id: int, body: StopBody, request: Request, settings: SettingsDep
) -> TaskOut:
    with db.session(settings.db_path) as conn:
        task = db.get_task(conn, task_id)
        if task is None:
            fail(404, "task_not_found", "task does not exist")
        close = body.close_positions if body.close_positions is not None else task.close_on_stop
        db.set_task_status(conn, task_id, "stopped")
        updated = db.get_task(conn, task_id)
    assert updated is not None

    if close:
        request.app.state.engine.submit(EngineEvent(kind="stop_close", task_id=task_id, source="stop"))
    return _out(updated)
