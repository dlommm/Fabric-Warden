"""`warden-backup`: export or restore application state from the command line.

Uses the same WARDEN_* settings as the server (database URL, secret key, data
directory). Stop the API before restoring. The passphrase is read from
WARDEN_BACKUP_PASSPHRASE or prompted for; it is never accepted as an argument
(arguments end up in shell history and process listings).

    warden-backup export /backups/warden.fwbk [--no-vault-key]
    warden-backup inspect /backups/warden.fwbk
    warden-backup restore /backups/warden.fwbk --yes
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
from pathlib import Path

from warden import backup
from warden.config import get_settings
from warden.db import Database
from warden.security.vault import load_master_key


def _passphrase(confirm: bool) -> str:
    env = os.environ.get("WARDEN_BACKUP_PASSPHRASE")
    if env:
        return env
    first = getpass.getpass("Backup passphrase: ")
    if confirm and getpass.getpass("Repeat passphrase: ") != first:
        sys.exit("Passphrases don't match.")
    return first


async def _export(path: Path, include_key: bool) -> None:
    settings = get_settings()
    db = Database(settings.database_url)
    try:
        key = load_master_key(settings.secret_key, settings.data_dir) if include_key else None
        blob, summary = await backup.create_backup(db.engine, _passphrase(confirm=True), vault_key=key)
    finally:
        await db.dispose()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(blob)
    print(f"Wrote {path} ({len(blob)} bytes, {sum(summary['row_counts'].values())} rows, revision {summary['schema_revision']}).")


async def _restore(doc: dict) -> None:
    settings = get_settings()
    db = Database(settings.database_url)
    try:
        async with db.engine.connect() as conn:
            current = await backup.schema_revision(conn)
        if current is None:
            from warden.migrations import upgrade_head

            await upgrade_head(db.engine)
        counts = await backup.restore_state(db.engine, doc, current_key=load_master_key(settings.secret_key, settings.data_dir))
    finally:
        await db.dispose()
    print(f"Restored {sum(counts.values())} rows from the backup made {doc.get('created_at')}. Start Fabric Warden and sign in again.")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="warden-backup", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="write an encrypted backup")
    e.add_argument("path", type=Path)
    e.add_argument("--no-vault-key", action="store_true", help="leave the vault key out (restore then needs the same key)")
    i = sub.add_parser("inspect", help="decrypt and summarise a backup without restoring it")
    i.add_argument("path", type=Path)
    r = sub.add_parser("restore", help="replace this installation's data with a backup (stop the API first)")
    r.add_argument("path", type=Path)
    r.add_argument("--yes", action="store_true", help="confirm that every current row will be replaced")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "export":
            asyncio.run(_export(args.path, not args.no_vault_key))
        elif args.cmd == "inspect":
            doc = backup.read_backup(args.path.read_bytes(), _passphrase(confirm=False))
            doc.pop("tables", None)
            doc["vault_key"] = "(included)" if doc.get("vault_key") else None
            print(json.dumps(doc, indent=2))
        else:
            if not args.yes:
                sys.exit("Restore replaces every row in this installation's database. Re-run with --yes to continue.")
            asyncio.run(_restore(backup.read_backup(args.path.read_bytes(), _passphrase(confirm=False))))
    except backup.BackupError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
