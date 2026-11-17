from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class SecretUnreadable(RuntimeError):
    pass


def cipher(secret_enc_key: str) -> Fernet:
    return Fernet(secret_enc_key.encode("ascii"))


def encrypt_secret(secret_enc_key: str, plaintext: str) -> bytes:
    return cipher(secret_enc_key).encrypt(plaintext.encode("utf-8"))


def decrypt_secret(secret_enc_key: str, blob: bytes) -> str:
    try:
        return cipher(secret_enc_key).decrypt(blob).decode("utf-8")
    except InvalidToken as exc:
        raise SecretUnreadable("secret cannot be decrypted with the configured key") from exc
