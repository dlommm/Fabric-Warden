"""`warden-admin`: account recovery from the server's own shell.

Whoever can run commands on the server already controls the database, so this
grants nothing new; it exists so a forgotten owner password doesn't mean
wiping the installation. The new password is prompted for (or read from
WARDEN_NEW_PASSWORD); it is never accepted as an argument.

    warden-admin reset-password admin [--clear-mfa]
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

from sqlalchemy import select

from warden import audit
from warden.audit import Actor
from warden.config import get_settings
from warden.db import Database, utcnow
from warden.models import AuthSession, User
from warden.security.crypto import hash_password, validate_password_strength


def _new_password() -> str:
    env = os.environ.get("WARDEN_NEW_PASSWORD")
    if env:
        return env
    first = getpass.getpass("New password: ")
    if getpass.getpass("Repeat new password: ") != first:
        sys.exit("Passwords don't match.")
    return first


async def reset_password(db: Database, username: str, password: str, *, clear_mfa: bool) -> int:
    """Set a new password, sign out every session, return how many were revoked."""
    async with db.sessionmaker() as s:
        user = await s.scalar(select(User).where(User.username == username))
        if user is None:
            raise LookupError(f"No account named {username!r}.")
        user.password_hash = hash_password(password)
        user.is_active = True
        if clear_mfa:
            user.mfa_enabled, user.totp_secret_ref = False, None
        revoked = 0
        for sess in await s.scalars(select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))):
            sess.revoked_at = utcnow()
            revoked += 1
        await audit.record(
            s,
            Actor.system("warden-admin"),
            "auth.password.reset",
            target_type="user",
            target_id=user.id,
            detail={"username": username, "mfa_cleared": clear_mfa, "sessions_revoked": revoked},
        )
        await s.commit()
    return revoked


async def _reset(username: str, password: str, clear_mfa: bool) -> int:
    db = Database(get_settings().database_url)
    try:
        return await reset_password(db, username, password, clear_mfa=clear_mfa)
    finally:
        await db.dispose()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="warden-admin", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("reset-password", help="set a new password for an account and sign out its sessions")
    r.add_argument("username")
    r.add_argument("--clear-mfa", action="store_true", help="also turn off two-factor sign-in (lost authenticator)")
    args = ap.parse_args(argv)
    password = _new_password()
    if err := validate_password_strength(password):
        sys.exit(err)
    try:
        revoked = asyncio.run(_reset(args.username, password, args.clear_mfa))
    except LookupError as exc:
        sys.exit(str(exc))
    print(f"Password for {args.username} reset; {revoked} session(s) signed out.")


if __name__ == "__main__":
    main()
