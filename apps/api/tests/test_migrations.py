from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from warden.db import Base


async def test_migrations_match_models(app):
    """Startup ran `alembic upgrade head`; the schema must equal the ORM models."""

    def diff(conn):
        return compare_metadata(MigrationContext.configure(conn), Base.metadata)

    async with app.state.warden.db.engine.connect() as conn:
        assert await conn.run_sync(diff) == []
