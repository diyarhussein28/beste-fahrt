"""Runs every migrations/NNNN_*.sql file in order, once, tracked in a
`schema_migrations` table. Simple on purpose — this project has one
developer and no need for Alembic's branching/autogenerate machinery.

Uses asyncpg directly rather than going through SQLAlchemy's async engine:
each .sql file has multiple statements (CREATE TABLE, CREATE INDEX, ...) in
one string, and SQLAlchemy's asyncpg dialect refuses that ("cannot insert
multiple commands into a prepared statement") because it always prepares
statements. asyncpg's own `Connection.execute()` runs a multi-statement
string fine via the simple query protocol as long as it takes no bind
parameters, which is all a schema migration needs.

Usage: python -m migrations.migrate
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import asyncpg

from shared.config import get_secrets

log = logging.getLogger("migrate")

MIGRATIONS_DIR = Path(__file__).parent


def _asyncpg_dsn(sqlalchemy_url: str) -> str:
    """shared.config's DATABASE_URL is a SQLAlchemy URL (postgresql+asyncpg://...);
    asyncpg.connect() wants a plain postgresql:// DSN.
    """
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", sqlalchemy_url)


async def run() -> None:
    conn = await asyncpg.connect(_asyncpg_dsn(get_secrets().database_url))
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        applied = {row["filename"] for row in await conn.fetch("SELECT filename FROM schema_migrations")}

        for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if sql_file.name in applied:
                continue
            log.info("applying migration", extra={"extra_fields": {"file": sql_file.name}})
            sql = sql_file.read_text(encoding="utf-8")
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute("INSERT INTO schema_migrations (filename) VALUES ($1)", sql_file.name)
        log.info("migrations up to date")
    finally:
        await conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
