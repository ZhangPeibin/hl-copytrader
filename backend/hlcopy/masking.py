from __future__ import annotations

import logging

from hlcopy.hl.errors import mask_secrets


class MaskingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_secrets(record.getMessage())
        record.args = ()
        return True


def install_masking() -> None:
    targets = [logging.getLogger()]
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "hlcopy"):
        targets.append(logging.getLogger(name))
    for logger in targets:
        for handler in logger.handlers:
            if not any(isinstance(existing, MaskingFilter) for existing in handler.filters):
                handler.addFilter(MaskingFilter())
