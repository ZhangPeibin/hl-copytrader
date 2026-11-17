from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from hlcopy import db
from hlcopy.api.deps import SettingsDep
from hlcopy.api.errors import fail

router = APIRouter(tags=["logs"])


class LogOut(BaseModel):
    id: int
    task_id: int | None
    wallet_id: int | None
    ts: int
    source: str
    coin: str
    leader_pos: str | None
    pos_before: str | None
    target: str | None
    side: str | None
    qty: str | None
    price: str | None
    result: str
    reason: str | None
    latency_ms: int | None
    oid: str | None
    cloid: str | None
    raw: str | None


class LogsOut(BaseModel):
    items: list[LogOut]
    total: int
    limit: int
    offset: int


@router.get("/api/logs", response_model=LogsOut)
def get_logs(
    settings: SettingsDep,
    task_id: int | None = None,
    coin: str | None = None,
    result: str | None = None,
    from_ms: int | None = None,
    to_ms: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> LogsOut:
    if limit < 1 or limit > 500:
        fail(400, "invalid_limit", "limit must be between 1 and 500")
    if offset < 0:
        fail(400, "invalid_offset", "offset must be >= 0")

    with db.session(settings.db_path) as conn:
        rows, total = db.list_exec_logs(
            conn,
            task_id=task_id,
            coin=coin,
            result=result,
            from_ms=from_ms,
            to_ms=to_ms,
            limit=limit,
            offset=offset,
        )

    return LogsOut(
        items=[LogOut(**dict(row)) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
