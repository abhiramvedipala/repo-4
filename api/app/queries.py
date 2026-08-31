"""All the raw SQL. Route handlers in main.py stay thin and call into this module.

Trace aggregates (total_tokens, total_cost_usd, status, start/end) are always
*recomputed from the spans table*, never incremented in place. A trace can be ingested
across multiple batches over time, and a span can be re-ingested (retry after a network
blip) — incrementally accumulating totals would double-count on any retry. Recomputing
from source on every ingest is a few extra queries, but it's correct regardless of how
many times or in what order a trace's spans arrive.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

import asyncpg

from app.otlp import ParsedBatch, SpanRecord

_UPSERT_SPAN_SQL = """
INSERT INTO spans (
    id, trace_id, parent_span_id, name, kind, provider, model,
    start_time, end_time, input_tokens, output_tokens, cost_usd,
    status, error_type, error_message, prompt, completion, attributes
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
ON CONFLICT (id) DO UPDATE SET
    parent_span_id = EXCLUDED.parent_span_id,
    name = EXCLUDED.name,
    kind = EXCLUDED.kind,
    provider = EXCLUDED.provider,
    model = EXCLUDED.model,
    start_time = EXCLUDED.start_time,
    end_time = EXCLUDED.end_time,
    input_tokens = EXCLUDED.input_tokens,
    output_tokens = EXCLUDED.output_tokens,
    cost_usd = EXCLUDED.cost_usd,
    status = EXCLUDED.status,
    error_type = EXCLUDED.error_type,
    error_message = EXCLUDED.error_message,
    prompt = EXCLUDED.prompt,
    completion = EXCLUDED.completion,
    attributes = EXCLUDED.attributes
"""

# Recomputes the trace row entirely from its current spans. Root span = the span with no
# parent; if none has arrived yet (children ingested before their root, in a partial or
# out-of-order batch), falls back to the earliest-starting span's name as a placeholder.
_UPSERT_TRACE_SQL = """
INSERT INTO traces (id, name, service, root_span_id, start_time, end_time, status,
                     total_tokens, total_cost_usd)
SELECT
    $1,
    COALESCE(
        (SELECT name FROM spans WHERE trace_id = $1 AND parent_span_id IS NULL LIMIT 1),
        (SELECT name FROM spans WHERE trace_id = $1 ORDER BY start_time LIMIT 1)
    ),
    $2,
    (SELECT id FROM spans WHERE trace_id = $1 AND parent_span_id IS NULL LIMIT 1),
    (SELECT MIN(start_time) FROM spans WHERE trace_id = $1),
    (SELECT MAX(end_time) FROM spans WHERE trace_id = $1),
    CASE
        WHEN EXISTS (SELECT 1 FROM spans WHERE trace_id = $1 AND status = 'error') THEN 'error'
        WHEN EXISTS (SELECT 1 FROM spans WHERE trace_id = $1 AND status = 'ok') THEN 'ok'
        ELSE 'unset'
    END,
    (SELECT COALESCE(SUM(COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)), 0)
     FROM spans WHERE trace_id = $1),
    (SELECT COALESCE(SUM(cost_usd), 0) FROM spans WHERE trace_id = $1)
ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name,
    service = EXCLUDED.service,
    root_span_id = EXCLUDED.root_span_id,
    start_time = EXCLUDED.start_time,
    end_time = EXCLUDED.end_time,
    status = EXCLUDED.status,
    total_tokens = EXCLUDED.total_tokens,
    total_cost_usd = EXCLUDED.total_cost_usd
"""


def _span_params(record: SpanRecord) -> tuple[Any, ...]:
    return (
        record.span_id,
        record.trace_id,
        record.parent_span_id,
        record.name,
        record.kind,
        record.provider,
        record.model,
        record.start_time,
        record.end_time,
        record.input_tokens,
        record.output_tokens,
        record.cost_usd,
        record.status,
        record.error_type,
        record.error_message,
        record.prompt,
        record.completion,
        record.attributes,  # jsonb codec (db.py) encodes this dict directly
    )


async def ingest_batches(pool: asyncpg.Pool, batches: list[ParsedBatch]) -> int:
    """Upserts every span, then recomputes each touched trace's aggregate row. Returns
    the number of spans written. One transaction per request: either the whole batch
    lands consistently, or none of it does.
    """
    total = 0
    async with pool.acquire() as conn, conn.transaction():
        for batch in batches:
            if not batch.spans:
                continue
            await conn.executemany(_UPSERT_SPAN_SQL, [_span_params(s) for s in batch.spans])
            total += len(batch.spans)
            for trace_id in {s.trace_id for s in batch.spans}:
                await conn.execute(_UPSERT_TRACE_SQL, trace_id, batch.service)
    return total


_LIST_TRACES_SQL = """
SELECT t.id, t.name, t.service, t.root_span_id, t.start_time, t.end_time,
       t.duration_ms, t.status, t.total_tokens, t.total_cost_usd
FROM traces t
WHERE ($1::timestamptz IS NULL OR t.start_time >= $1)
  AND ($2::timestamptz IS NULL OR t.start_time <= $2)
  AND ($3::text IS NULL OR t.service = $3)
  AND ($4::text IS NULL OR t.status = $4)
  AND ($5::integer IS NULL OR t.duration_ms >= $5)
  AND ($6::text IS NULL OR EXISTS (
        SELECT 1 FROM spans s WHERE s.trace_id = t.id AND s.model = $6
      ))
ORDER BY t.start_time DESC
LIMIT $7 OFFSET $8
"""

_COUNT_TRACES_SQL = """
SELECT COUNT(*) FROM traces t
WHERE ($1::timestamptz IS NULL OR t.start_time >= $1)
  AND ($2::timestamptz IS NULL OR t.start_time <= $2)
  AND ($3::text IS NULL OR t.service = $3)
  AND ($4::text IS NULL OR t.status = $4)
  AND ($5::integer IS NULL OR t.duration_ms >= $5)
  AND ($6::text IS NULL OR EXISTS (
        SELECT 1 FROM spans s WHERE s.trace_id = t.id AND s.model = $6
      ))
"""


async def list_traces(
    pool: asyncpg.Pool,
    *,
    start_time: datetime | None,
    end_time: datetime | None,
    service: str | None,
    status: str | None,
    min_latency_ms: int | None,
    model: str | None,
    limit: int,
    offset: int,
) -> tuple[list[asyncpg.Record], int]:
    # ponytail: offset pagination, not keyset — simplest correct thing for this data
    # volume. Upgrade to keyset (WHERE (start_time, id) < ($cursor_time, $cursor_id))
    # if a trace list ever gets deep enough that OFFSET's O(n) scan-and-discard matters.
    params = (start_time, end_time, service, status, min_latency_ms, model)
    async with pool.acquire() as conn:
        rows = await conn.fetch(_LIST_TRACES_SQL, *params, limit, offset)
        total = await conn.fetchval(_COUNT_TRACES_SQL, *params)
    return list(rows), int(total)


_GET_TRACE_SQL = """
SELECT id, name, service, root_span_id, start_time, end_time, duration_ms, status,
       total_tokens, total_cost_usd
FROM traces WHERE id = $1
"""

_GET_SPANS_FOR_TRACE_SQL = """
SELECT id, trace_id, parent_span_id, name, kind, provider, model, start_time, end_time,
       latency_ms, input_tokens, output_tokens, cost_usd, status, error_type,
       error_message, prompt, completion, attributes
FROM spans WHERE trace_id = $1
ORDER BY start_time ASC
"""


async def get_trace(
    pool: asyncpg.Pool, trace_id: str
) -> tuple[asyncpg.Record | None, list[asyncpg.Record]]:
    """Returns the trace header plus every span, flat and ordered by start_time. The
    frontend (Phase 6) builds the waterfall tree client-side from parent_span_id links —
    keeps this endpoint a straight two-query fetch instead of building tree JSON here.
    """
    async with pool.acquire() as conn:
        trace = await conn.fetchrow(_GET_TRACE_SQL, trace_id)
        spans = await conn.fetch(_GET_SPANS_FOR_TRACE_SQL, trace_id)
    return trace, list(spans)


_OVERALL_METRICS_SQL = """
SELECT
    percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50_latency_ms,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms,
    percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms) AS p99_latency_ms,
    COALESCE(SUM(cost_usd), 0)::float8 AS total_cost_usd,
    COUNT(*) AS total_calls,
    COUNT(*) FILTER (WHERE status = 'error') AS error_calls
FROM spans
WHERE start_time >= $1 AND start_time <= $2
"""

_CALLS_BY_MODEL_SQL = """
SELECT model, COUNT(*) AS calls, COALESCE(SUM(cost_usd), 0)::float8 AS cost_usd
FROM spans
WHERE start_time >= $1 AND start_time <= $2 AND model IS NOT NULL
GROUP BY model
ORDER BY calls DESC
"""

_BUCKETED_METRICS_SQL = """
SELECT
    date_trunc($3, start_time) AS bucket,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50_latency_ms,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms,
    COUNT(*) AS calls,
    COALESCE(SUM(cost_usd), 0)::float8 AS cost_usd,
    COUNT(*) FILTER (WHERE status = 'error') AS error_calls
FROM spans
WHERE start_time >= $1 AND start_time <= $2
GROUP BY bucket
ORDER BY bucket
"""

BucketGranularity = Literal["hour", "day"]


async def metrics_summary(
    pool: asyncpg.Pool,
    *,
    start_time: datetime,
    end_time: datetime,
    bucket: BucketGranularity,
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        overall = await conn.fetchrow(_OVERALL_METRICS_SQL, start_time, end_time)
        by_model = await conn.fetch(_CALLS_BY_MODEL_SQL, start_time, end_time)
        buckets = await conn.fetch(_BUCKETED_METRICS_SQL, start_time, end_time, bucket)

    assert overall is not None  # aggregate query always returns exactly one row
    total_calls = overall["total_calls"]
    error_calls = overall["error_calls"]
    return {
        "p50_latency_ms": overall["p50_latency_ms"],
        "p95_latency_ms": overall["p95_latency_ms"],
        "p99_latency_ms": overall["p99_latency_ms"],
        "total_cost_usd": overall["total_cost_usd"],
        "total_calls": total_calls,
        "error_rate": (error_calls / total_calls) if total_calls else 0.0,
        "calls_by_model": [
            {"model": row["model"], "calls": row["calls"], "cost_usd": row["cost_usd"]}
            for row in by_model
        ],
        "buckets": [
            {
                "bucket": row["bucket"],
                "p50_latency_ms": row["p50_latency_ms"],
                "p95_latency_ms": row["p95_latency_ms"],
                "calls": row["calls"],
                "cost_usd": row["cost_usd"],
                "error_rate": (row["error_calls"] / row["calls"]) if row["calls"] else 0.0,
            }
            for row in buckets
        ],
    }
