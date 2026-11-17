from __future__ import annotations

from typing import NoReturn

from fastapi import HTTPException

from hlcopy.hl.errors import HlError


def fail(status_code: int, code: str, message: str) -> NoReturn:
    raise HTTPException(status_code=status_code, detail={"code": code, "message": message})


def hl_failure(exc: HlError) -> NoReturn:
    fail(502, exc.code, exc.message)
