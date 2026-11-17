from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from hlcopy import db

router = APIRouter()


@router.get("/api/health")
def health(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    engine = request.app.state.engine_state
    ws = request.app.state.ws_state

    db_ok = db.check(settings.db_path)
    payload = {
        "status": "ok" if db_ok else "degraded",
        "network": settings.hl_network,
        "version": request.app.version,
        "engine": {
            "running": engine.running,
            "last_tick_ms": engine.last_tick_ms,
            "queue_depth": engine.queue_depth,
        },
        "ws": {"connected": ws.connected, "subscriptions": ws.subscriptions},
        "db": {"ok": db_ok},
    }
    return JSONResponse(payload, status_code=200 if db_ok else 503)
