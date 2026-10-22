"""Encrypted backup and restore of application state.

A backup is a portable, database-agnostic dump of every table (SQLite or
PostgreSQL in, either out), compressed and encrypted with a passphrase:

    b"FWBK" + version(1) + salt(16) + nonce(12) + AES-256-GCM(gzip(json))

The key is derived from the passphrase with scrypt (n=2**15, r=8, p=1); the
header is authenticated as associated data. Without the passphrase the archive
reveals nothing but its size.

The vault master key is included by default (inside the encryption), which
makes the archive self-contained: on restore every stored credential is
re-encrypted under the *current* installation's key, so restoring onto a new
host with a different ``WARDEN_SECRET_KEY`` works. Exclude it to keep the key
out of the archive; then the restoring installation must already use the same
key, and restore refuses otherwise.

Not included: sign-in sessions (everyone signs in again after a restore).
The schema revision must match: restore into the same Fabric Warden version
(migrations applied), then upgrade.
"""

from __future__ import annotations

import base64
import gzip
import json
import os
import struct
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from sqlalchemy import JSON, Table, delete, insert, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from warden import __version__
from warden.db import Base, utcnow

MAGIC = b"FWBK"
FORMAT_VERSION = 1
FORMAT_NAME = "fabric-warden-backup"
SALT_LEN = 16
NONCE_LEN = 12
HEADER_LEN = len(MAGIC) + 1 + SALT_LEN + NONCE_LEN
MIN_PASSPHRASE = 12
EXCLUDED_TABLES = frozenset({"auth_sessions"})
BATCH = 500


class BackupError(Exception):
    """Archive unreadable, wrong passphrase, incompatible, or unsafe to restore."""


# -- crypto -----------------------------------------------------------------------------


def _key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode())


def encrypt(payload: bytes, passphrase: str) -> bytes:
    if len(passphrase) < MIN_PASSPHRASE:
        raise BackupError(f"Use a passphrase of at least {MIN_PASSPHRASE} characters.")
    salt, nonce = os.urandom(SALT_LEN), os.urandom(NONCE_LEN)
    header = MAGIC + struct.pack("B", FORMAT_VERSION) + salt + nonce
    return header + AESGCM(_key(passphrase, salt)).encrypt(nonce, gzip.compress(payload, compresslevel=6), header)


def decrypt(blob: bytes, passphrase: str) -> bytes:
    if len(blob) < HEADER_LEN + 16 or not blob.startswith(MAGIC):
        raise BackupError("This is not a Fabric Warden backup.")
    version = blob[len(MAGIC)]
    if version != FORMAT_VERSION:
        raise BackupError(f"Unsupported backup format version {version}.")
    header = blob[:HEADER_LEN]
    salt = header[len(MAGIC) + 1 : len(MAGIC) + 1 + SALT_LEN]
    nonce = header[-NONCE_LEN:]
    try:
        compressed = AESGCM(_key(passphrase, salt)).decrypt(nonce, blob[HEADER_LEN:], header)
    except InvalidTag:
        raise BackupError("The passphrase is wrong or the archive is damaged.") from None
    return gzip.decompress(compressed)


# -- value encoding ---------------------------------------------------------------------


def _enc(v: Any) -> Any:
    if isinstance(v, datetime):
        return {"$dt": (v if v.tzinfo else v.replace(tzinfo=UTC)).isoformat()}
    if isinstance(v, date):
        return {"$d": v.isoformat()}
    if isinstance(v, bytes | bytearray | memoryview):
        return {"$b": base64.b64encode(bytes(v)).decode()}
    if isinstance(v, Decimal):
        return {"$n": str(v)}
    return v


def _dec(v: Any, coltype: Any) -> Any:
    if not isinstance(v, dict) or len(v) != 1 or isinstance(coltype, JSON):
        return v
    # Tagged scalars only appear for non-JSON columns (JSON columns keep their documents verbatim).
    ((tag, raw),) = v.items()
    decoders = {"$dt": datetime.fromisoformat, "$d": date.fromisoformat, "$b": base64.b64decode, "$n": Decimal}
    return decoders[tag](raw) if tag in decoders else v


def _tables() -> list[Table]:
    return [t for t in Base.metadata.sorted_tables if t.name not in EXCLUDED_TABLES]


async def schema_revision(conn: AsyncConnection) -> str | None:
    def _rev(sync_conn) -> str | None:  # type: ignore[no-untyped-def]
        from alembic.runtime.migration import MigrationContext

        return MigrationContext.configure(sync_conn).get_current_revision()

    return await conn.run_sync(_rev)


# -- export -----------------------------------------------------------------------------


async def export_state(engine: AsyncEngine, *, vault_key: bytes | None) -> tuple[bytes, dict[str, Any]]:
    """Plain JSON document of every table, plus a summary (row counts)."""
    tables: dict[str, Any] = {}
    counts: dict[str, int] = {}
    async with engine.connect() as conn:
        # One read transaction: a consistent snapshot on PostgreSQL (REPEATABLE READ) and SQLite.
        if engine.dialect.name == "postgresql":
            await conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        revision = await schema_revision(conn)
        for table in _tables():
            cols = [c.name for c in table.columns]
            result = await conn.execute(select(table))
            rows = [[_enc(v) for v in row] for row in result]
            tables[table.name] = {"columns": cols, "rows": rows}
            counts[table.name] = len(rows)
    summary = {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "app_version": __version__,
        "schema_revision": revision,
        "created_at": utcnow().isoformat(),
        "source_dialect": engine.dialect.name,
        "includes_vault_key": vault_key is not None,
        "row_counts": counts,
    }
    doc = {**summary, "vault_key": vault_key.decode() if vault_key else None, "tables": tables}
    return json.dumps(doc, separators=(",", ":"), default=str).encode(), summary


async def create_backup(engine: AsyncEngine, passphrase: str, *, vault_key: bytes | None) -> tuple[bytes, dict[str, Any]]:
    if len(passphrase) < MIN_PASSPHRASE:
        raise BackupError(f"Use a passphrase of at least {MIN_PASSPHRASE} characters.")
    payload, summary = await export_state(engine, vault_key=vault_key)
    return encrypt(payload, passphrase), summary


# -- restore ----------------------------------------------------------------------------


def read_backup(blob: bytes, passphrase: str) -> dict[str, Any]:
    try:
        doc = json.loads(decrypt(blob, passphrase))
    except (ValueError, OSError) as exc:
        raise BackupError(f"The archive could not be read: {exc}") from None
    if doc.get("format") != FORMAT_NAME or doc.get("format_version") != FORMAT_VERSION:
        raise BackupError("This is not a Fabric Warden backup of a supported format.")
    return doc


def _rekey(ciphertexts: list[str], old_key: bytes | None, new_fernet: Fernet) -> list[str]:
    """Re-encrypt credential ciphertexts under the current key; verify they decrypt."""
    out = []
    old = Fernet(old_key) if old_key else None
    for ct in ciphertexts:
        try:
            plain = (old or new_fernet).decrypt(ct.encode())
        except InvalidToken:
            if old is None:
                raise BackupError(
                    "This backup was made without its vault key, and this installation's key cannot decrypt its credentials. "
                    "Start Fabric Warden with the original WARDEN_SECRET_KEY (or vault.key) and restore again."
                ) from None
            raise BackupError("A stored credential in the backup could not be decrypted with the backup's vault key.") from None
        out.append(new_fernet.encrypt(plain).decode())
    return out


async def restore_state(engine: AsyncEngine, doc: dict[str, Any], *, current_key: bytes) -> dict[str, int]:
    """Replace every table's rows with the backup's, in one transaction."""
    async with engine.connect() as conn:
        revision = await schema_revision(conn)
    if doc.get("schema_revision") != revision:
        raise BackupError(
            f"The backup's schema revision ({doc.get('schema_revision')}) differs from this installation's ({revision}). "
            "Restore into the same Fabric Warden version it was made with, then upgrade."
        )
    tables = {t.name: t for t in _tables()}
    data: dict[str, Any] = doc.get("tables", {})
    unknown = sorted(set(data) - set(tables))
    if unknown:
        raise BackupError(f"The backup contains tables this installation doesn't know: {unknown}")

    # Credentials first: fail before touching anything if they can't be re-keyed.
    creds = data.get("credentials", {"columns": [], "rows": []})
    if creds["rows"]:
        idx = creds["columns"].index("ciphertext")
        old_key = doc["vault_key"].encode() if doc.get("vault_key") else None
        new = _rekey([r[idx] for r in creds["rows"]], old_key, Fernet(current_key))
        for r, ct in zip(creds["rows"], new, strict=True):
            r[idx] = ct

    counts: dict[str, int] = {}
    async with engine.begin() as conn:
        if engine.dialect.name == "sqlite":
            await conn.exec_driver_sql("PRAGMA defer_foreign_keys = ON")
        for table in reversed(list(tables.values())):
            await conn.execute(delete(table))
        await conn.execute(delete(Base.metadata.tables["auth_sessions"]))
        for name, table in tables.items():
            block = data.get(name)
            if not block or not block["rows"]:
                counts[name] = 0
                continue
            cols = [c for c in block["columns"] if c in table.columns]
            positions = [block["columns"].index(c) for c in cols]
            types = [table.columns[c].type for c in cols]
            rows = [{c: _dec(row[p], t) for c, p, t in zip(cols, positions, types, strict=True)} for row in block["rows"]]
            for i in range(0, len(rows), BATCH):
                await conn.execute(insert(table), rows[i : i + BATCH])
            counts[name] = len(rows)
        if engine.dialect.name == "postgresql":
            # Explicit values were inserted into serial columns; move their sequences past them.
            for table in tables.values():
                for col in table.columns:
                    if col.primary_key and col.autoincrement is True:
                        await conn.execute(
                            text(
                                f"SELECT setval(pg_get_serial_sequence('{table.name}', '{col.name}'), "  # noqa: S608 - names from metadata
                                f"COALESCE((SELECT MAX({col.name}) FROM {table.name}), 0) + 1, false)"
                            )
                        )
    return counts
