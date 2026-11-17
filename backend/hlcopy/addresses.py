from __future__ import annotations

import re

from eth_account import Account
from eth_utils import is_checksum_address, to_checksum_address

PRIVATE_KEY_RE = re.compile(r"^[0-9a-fA-F]{64}$")
ADDRESS_RE = re.compile(r"^[0-9a-fA-F]{40}$")


class ValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def normalize_private_key(raw: str) -> str:
    value = raw.strip()
    if value[:2].lower() == "0x":
        value = value[2:]
    if not PRIVATE_KEY_RE.match(value):
        raise ValidationError(
            "invalid_private_key",
            "private key must be 32 bytes of hex (64 characters), with or without 0x",
        )
    return value.lower()


def normalize_address(raw: str, field: str = "address") -> str:
    value = raw.strip()
    if not value.lower().startswith("0x"):
        value = "0x" + value
    body = value[2:]
    if not ADDRESS_RE.match(body):
        raise ValidationError(f"invalid_{field}", f"{field} must be a 20-byte hex address")
    mixed_case = body != body.lower() and body != body.upper()
    if mixed_case and not is_checksum_address(value):
        raise ValidationError(
            "invalid_address_checksum",
            f"{field} has an invalid EIP-55 checksum; paste the address exactly as issued",
        )
    return to_checksum_address(value)


def derive_address(private_key: str) -> str:
    account = Account.from_key("0x" + private_key)
    return to_checksum_address(account.address)


def same_address(left: str, right: str) -> bool:
    return left.lower() == right.lower()


def verify_key_matches(private_key: str, api_wallet_address: str) -> str:
    derived = derive_address(private_key)
    if not same_address(derived, api_wallet_address):
        raise ValidationError(
            "private_key_address_mismatch",
            "the private key does not control the given API wallet address",
        )
    return derived
