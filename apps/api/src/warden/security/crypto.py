"""Password hashing and opaque token helpers."""

from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()

MIN_PASSWORD_LENGTH = 12


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


# Verifying against a fixed hash keeps login timing similar for unknown users.
_DUMMY_HASH = _hasher.hash(secrets.token_hex(16))


def burn_password_check(password: str) -> None:
    verify_password(_DUMMY_HASH, password)


def new_token(prefix: str = "") -> str:
    return prefix + secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def validate_password_strength(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if password.lower() == password and password.isalpha():
        return "Use a longer passphrase or mix in digits, symbols or capitals."
    return None
