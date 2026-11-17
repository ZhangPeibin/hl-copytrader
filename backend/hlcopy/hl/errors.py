from __future__ import annotations


class HlError(RuntimeError):
    def __init__(self, message: str, *, code: str = "hl_unavailable") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
