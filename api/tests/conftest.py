from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.trace.v1.trace_pb2 import Span as PbSpan
from opentelemetry.proto.trace.v1.trace_pb2 import Status as PbStatus

# Separate database from whatever "dev" data might exist in spanscope (.env.example's
# default) — tests get their own DB so they never depend on or clobber it.
ADMIN_DATABASE_URL = "postgresql://spanscope:spanscope@localhost:5432/postgres"
TEST_DATABASE_URL = "postgresql://spanscope:spanscope@localhost:5432/spanscope_test"
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

# db.py reads DATABASE_URL at app-startup time (inside the lifespan), not at import
# time, so setting this before the TestClient is ever constructed is enough.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL


@pytest.fixture(scope="session", autouse=True)
async def _test_database() -> None:
    """Creates spanscope_test fresh and applies every migration, once per test session.
    Skips the whole DB-backed test session with a clear message if Postgres isn't
    reachable, instead of every single test failing with an opaque connection error.
    """
    try:
        admin_conn = await asyncpg.connect(ADMIN_DATABASE_URL, timeout=3)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"Postgres not reachable at localhost:5432 ({exc}) — skipping DB tests")
    try:
        await admin_conn.execute("DROP DATABASE IF EXISTS spanscope_test")
        await admin_conn.execute("CREATE DATABASE spanscope_test OWNER spanscope")
    finally:
        await admin_conn.close()

    test_conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            await test_conn.execute(migration.read_text())
    finally:
        await test_conn.close()


@pytest.fixture(autouse=True)
async def _clean_tables() -> AsyncIterator[None]:
    """Truncates before each test (not after) so a test that crashed mid-run never
    leaves the next one starting from dirty state.
    """
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await conn.execute("TRUNCATE spans, traces RESTART IDENTITY CASCADE")
    finally:
        await conn.close()
    yield


@pytest.fixture
def otlp_request_bytes() -> bytes:
    """A real, serialized OTLP ExportTraceServiceRequest carrying one client span with
    GenAI attributes — the exact wire format OTLPSpanExporter (Phase 4 SDK) sends.
    """
    request = ExportTraceServiceRequest()
    resource_spans = request.resource_spans.add()

    resource_kv = resource_spans.resource.attributes.add()
    resource_kv.key = "service.name"
    resource_kv.value.string_value = "test-service"

    span = resource_spans.scope_spans.add().spans.add()
    span.trace_id = b"\x01" * 16
    span.span_id = b"\x02" * 8
    span.name = "openai.chat.completions.create"
    span.kind = PbSpan.SPAN_KIND_CLIENT
    span.start_time_unix_nano = 1_700_000_000_000_000_000
    span.end_time_unix_nano = 1_700_000_000_500_000_000
    span.status.code = PbStatus.STATUS_CODE_OK

    def _set_str(key: str, value: str) -> None:
        kv = span.attributes.add()
        kv.key = key
        kv.value.string_value = value

    def _set_int(key: str, value: int) -> None:
        kv = span.attributes.add()
        kv.key = key
        kv.value.int_value = value

    _set_str("gen_ai.system", "openai")
    _set_str("gen_ai.request.model", "gpt-4o")
    _set_int("gen_ai.usage.input_tokens", 10)
    _set_int("gen_ai.usage.output_tokens", 5)
    _set_str("spanscope.completion", "Hello!")
    _set_str("custom.tag", "keep-me")  # proves unknown attrs survive into .attributes

    # protobuf's generated stubs type SerializeToString loosely; it always returns real
    # bytes at runtime, so an explicit local annotation is enough to satisfy strict mode.
    serialized: bytes = request.SerializeToString()
    return serialized
