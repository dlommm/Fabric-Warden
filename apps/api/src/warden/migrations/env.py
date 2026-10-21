from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

import warden.models  # noqa: F401 - registers tables
from warden.db import Base

config = context.config
target_metadata = Base.metadata


def _configure(conn) -> None:  # type: ignore[no-untyped-def]
    context.configure(
        connection=conn,
        target_metadata=target_metadata,
        render_as_batch=conn.dialect.name == "sqlite",
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


conn = config.attributes.get("connection")
if conn is not None:
    _configure(conn)
else:

    async def _main() -> None:
        engine = create_async_engine(config.get_main_option("sqlalchemy.url"))
        async with engine.begin() as c:
            await c.run_sync(_configure)
        await engine.dispose()

    asyncio.run(_main())
