from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from hlcopy.api.errors import hl_failure
from hlcopy.hl.errors import HlError

router = APIRouter(tags=["meta"])


class CoinOut(BaseModel):
    name: str
    sz_decimals: int
    max_leverage: int


class MetaOut(BaseModel):
    coins: list[CoinOut]
    fetched_at_ms: int


@router.get("/api/meta", response_model=MetaOut)
def get_meta(request: Request) -> MetaOut:
    try:
        meta = request.app.state.info.meta()
    except HlError as exc:
        hl_failure(exc)

    return MetaOut(
        coins=[
            CoinOut(name=coin.name, sz_decimals=coin.sz_decimals, max_leverage=coin.max_leverage)
            for coin in meta.coins
        ],
        fetched_at_ms=meta.fetched_at_ms,
    )
