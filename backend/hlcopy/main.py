from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from hlcopy import db
from hlcopy.api import health, meta, tasks, wallets
from hlcopy.config import ConfigError, Settings, load_settings
from hlcopy.engine.state import EngineState, WsState
from hlcopy.hl.info import HlInfoClient
from hlcopy.security import BasicSessionMiddleware

VERSION = "0.1.0"

_FALLBACK_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>HL Copytrader</title></head>
<body style="font-family: system-ui; padding: 2rem">
<h1>HL Copytrader</h1>
<p>Frontend build not found. Run <code>npm install &amp;&amp; npm run build</code> in <code>frontend/</code>,
or use <code>make dev</code>.</p>
<p><a href="/api/health">/api/health</a></p>
</body></html>
"""


def create_app(settings: Settings | None = None, *, info_client: HlInfoClient | None = None) -> FastAPI:
    if settings is None:
        try:
            settings = load_settings()
        except ConfigError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(2) from exc

    logging.basicConfig(
        level=settings.log_level,
        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":%(message)r}',
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.init_db(settings.db_path)
        logging.getLogger("hlcopy").info(
            "startup network=%s data_dir=%s", settings.hl_network, settings.data_dir
        )
        yield

    app = FastAPI(title="HL Copytrader", version=VERSION, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine_state = EngineState()
    app.state.ws_state = WsState()
    app.state.info = info_client or HlInfoClient(settings.hl_network)

    if settings.app_password:
        app.add_middleware(
            BasicSessionMiddleware,
            password=settings.app_password,
            secret_enc_key=settings.secret_enc_key,
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"detail": {"code": "invalid_request", "message": "request body is invalid"}},
        )

    app.include_router(health.router)
    app.include_router(wallets.router)
    app.include_router(meta.router)
    app.include_router(tasks.router)
    _mount_frontend(app, settings.static_dir)
    return app


def _mount_frontend(app: FastAPI, static_dir: Path) -> None:
    index = static_dir / "index.html"
    if not index.is_file():
        @app.get("/", include_in_schema=False)
        def fallback() -> HTMLResponse:
            return HTMLResponse(_FALLBACK_PAGE)
        return

    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/", include_in_schema=False)
    def index_page() -> FileResponse:
        return FileResponse(index)

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404)
        candidate = (static_dir / path).resolve()
        if candidate.is_file() and static_dir.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)
