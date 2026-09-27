"""Runs every migrations/NNNN_*.sql file in order, once, tracked in a
`schema_migrations` table. Simple on purpose — this project has one
developer and no need for Alembic's branching/autogenerate machinery.

Usage: python -m migrations.migrate
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy import text

from shared.db import get_engine

log = logging.getLogger("migrate")

MIGRATIONS_DIR = Path(__file__).parent


async def run() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    filename TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        applied = {row[0] for row in (await conn.execute(text("SELECT filename FROM schema_migrations"))).all()}

    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if sql_file.name in applied:
            continue
        log.info("applying migration", extra={"extra_fields": {"file": sql_file.name}})
        sql = sql_file.read_text(encoding="utf-8")
        async with engine.begin() as conn:
            await conn.execute(text(sql))
            await conn.execute(
                text("INSERT INTO schema_migrations (filename) VALUES (:f)"), {"f": sql_file.name}
            )
    log.info("migrations up to date")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
