from __future__ import annotations

import logging

from hlcopy.hl.errors import mask_secrets


class MaskingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = mask_secrets(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                mask_secrets(arg) if isinstance(arg, str) else arg for arg in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                key: mask_secrets(value) if isinstance(value, str) else value
                for key, value in record.args.items()
            }
        return True


def install_masking() -> None:
    targets = [logging.getLogger()]
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "hlcopy"):
        targets.append(logging.getLogger(name))
    for logger in targets:
        for handler in logger.handlers:
            if not any(isinstance(existing, MaskingFilter) for existing in handler.filters):
                handler.addFilter(MaskingFilter())
