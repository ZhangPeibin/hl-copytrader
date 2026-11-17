from __future__ import annotations

import re


class HlError(RuntimeError):
    def __init__(self, message: str, *, code: str = "hl_unavailable") -> None:
        super().__init__(message)
        self.code = code
        self.message = message

RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("duplicate_cloid", ("duplicate cloid",)),
    ("invalid_nonce", ("invalid nonce", "nonce too")),
    ("agent_unauthorized", ("does not exist", "not approved", "unauthorized agent", "api wallet")),
    ("below_min_notional", ("minimum value", "minimum size", "min value", "less than $10", "$10")),
    ("reduce_only_violation", ("reduce only",)),
    ("price_out_of_range", ("invalid price", "tick size", "price too")),
    ("insufficient_margin", ("insufficient margin", "insufficient balance")),
)

ACTIONS = {
    "duplicate_cloid": "treat_as_success",
    "invalid_nonce": "re_sign",
    "agent_unauthorized": "stop_task",
    "below_min_notional": "skip",
    "reduce_only_violation": "reconcile",
    "price_out_of_range": "retry_price",
    "insufficient_margin": "backoff",
    "unknown": "backoff",
}

KEY_PATTERN = re.compile(r"(?<![0-9a-fA-F])(?:0x)?[0-9a-fA-F]{64}(?![0-9a-fA-F])")


def classify_error(text: str | None) -> str:
    if not text:
        return "unknown"
    lowered = text.lower()
    for category, patterns in RULES:
        if any(pattern in lowered for pattern in patterns):
            return category
    return "unknown"


def action_for(category: str) -> str:
    return ACTIONS.get(category, "backoff")


def mask_secrets(text: str) -> str:
    return KEY_PATTERN.sub("0x<redacted>", text)


def exchange_error(text: str) -> HlError:
    category = classify_error(text)
    return HlError(text, code=category)
