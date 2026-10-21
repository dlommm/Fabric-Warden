"""Credential vault: Fernet (AES-128-CBC + HMAC-SHA256) encryption at rest.

The master key comes from WARDEN_SECRET_KEY or, failing that, a key file
created with 0600 permissions in the data directory. Losing the key makes
stored credentials unrecoverable; back it up separately from the database.
"""

from __future__ import annotations

import base64
import hashlib
import os
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy.ext.asyncio import AsyncSession

from warden.db import utcnow
from warden.models import Credential

KEY_FILE = "vault.key"


class VaultError(Exception):
    pass


def _derive(secret: str) -> bytes:
    # Accept either a Fernet key or an arbitrary passphrase-like secret.
    try:
        raw = base64.urlsafe_b64decode(secret.encode())
        if len(raw) == 32:
            return secret.encode()
    except ValueError:
        pass
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


def load_master_key(secret: str | None, data_dir: Path) -> bytes:
    if secret:
        return _derive(secret)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / KEY_FILE
    if path.exists():
        return path.read_bytes().strip()
    key = Fernet.generate_key()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    return key


def fingerprint(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()[:12]


class Vault:
    def __init__(self, key: bytes, previous_keys: list[bytes] | None = None) -> None:
        self._fernet = MultiFernet([Fernet(key), *(Fernet(k) for k in previous_keys or [])])

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise VaultError("credential could not be decrypted with the configured key") from exc

    async def store(self, db: AsyncSession, *, kind: str, name: str, secret: str, expires_at: datetime | None = None) -> Credential:
        cred = Credential(
            kind=kind,
            name=name,
            ciphertext=self.encrypt(secret),
            fingerprint=fingerprint(secret),
            expires_at=expires_at,
        )
        db.add(cred)
        await db.flush()
        return cred

    async def rotate(self, db: AsyncSession, cred: Credential, new_secret: str) -> None:
        cred.ciphertext = self.encrypt(new_secret)
        cred.fingerprint = fingerprint(new_secret)
        cred.rotated_at = utcnow()
        await db.flush()

    async def reveal(self, db: AsyncSession, credential_id: str) -> str:
        cred = await db.get(Credential, credential_id)
        if cred is None:
            raise VaultError("credential not found")
        return self.decrypt(cred.ciphertext)
