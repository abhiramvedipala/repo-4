"""Postgres connection pool, created once at app startup and closed once at shutdown —
not per-request — via FastAPI's lifespan.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, Request


async def _init_connection(conn: asyncpg.Connection) -> None:
    # asyncpg doesn't serialize dict <-> jsonb automatically; without this codec every
    # query touching spans.attributes would need json.dumps/loads calls at every call
    # site instead of once, here.
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Read at startup time, not at import time — tests set DATABASE_URL before the
    # TestClient (and thus this lifespan) ever runs, without needing to reload modules.
    database_url = os.environ["DATABASE_URL"]
    app.state.pool = await asyncpg.create_pool(
        database_url, min_size=1, max_size=10, init=_init_connection
    )
    try:
        yield
    finally:
        await app.state.pool.close()


async def get_pool(request: Request) -> asyncpg.Pool:
    pool: asyncpg.Pool = request.app.state.pool
    return pool
