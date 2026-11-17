from __future__ import annotations

import base64
import hashlib
import hmac

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

COOKIE_NAME = "hlcopy_session"
SESSION_MESSAGE = b"hlcopy-session-v1"
PUBLIC_PATHS = frozenset({"/api/health"})


def session_token(secret_enc_key: str) -> str:
    return hmac.new(secret_enc_key.encode("utf-8"), SESSION_MESSAGE, hashlib.sha256).hexdigest()


class BasicSessionMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, password: str, secret_enc_key: str) -> None:
        super().__init__(app)
        self.password = password
        self.token = session_token(secret_enc_key)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        if hmac.compare_digest(request.cookies.get(COOKIE_NAME, ""), self.token):
            return await call_next(request)

        header = request.headers.get("authorization", "")
        if header.lower().startswith("basic "):
            password = _decode_basic(header[6:])
            if password is not None and hmac.compare_digest(password, self.password):
                response = await call_next(request)
                response.set_cookie(
                    COOKIE_NAME,
                    self.token,
                    httponly=True,
                    samesite="lax",
                    max_age=43_200,
                    path="/",
                )
                return response

        return Response(
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="hl-copytrader"'},
        )


def _decode_basic(value: str) -> str | None:
    try:
        decoded = base64.b64decode(value, validate=True).decode("utf-8")
    except Exception:
        return None
    _, sep, password = decoded.partition(":")
    if not sep:
        return None
    return password
