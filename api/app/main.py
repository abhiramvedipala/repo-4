"""SpanScope ingest + query API. Route handlers stay thin — parsing lives in otlp.py,
SQL lives in queries.py, response shapes live in schemas.py.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, Query, Request

from app import queries
from app.db import get_pool, lifespan
from app.otlp import parse_json, parse_otlp_protobuf
from app.queries import BucketGranularity
from app.schemas import (
    IngestResponseOut,
    MetricsSummaryOut,
    SpanOut,
    TraceDetailOut,
    TraceOut,
    TracesPageOut,
)

app = FastAPI(title="SpanScope Ingest API", lifespan=lifespan)

Pool = Annotated[asyncpg.Pool, Depends(get_pool)]


@app.get("/health")
async def health(pool: Pool) -> dict[str, str]:
    # A health check that doesn't touch the DB would say "ok" while the DB is down —
    # exactly the failure a health check exists to catch. An uncaught exception here
    # naturally becomes a 500, which is a correct "unhealthy" signal on its own.
    async with pool.acquire() as conn:
        await conn.fetchval("SELECT 1")
    return {"status": "ok"}


@app.post("/v1/traces")
async def ingest_traces(request: Request, pool: Pool) -> IngestResponseOut:
    content_type = request.headers.get("content-type", "")
    body = await request.body()

    try:
        if "application/x-protobuf" in content_type:
            batches = parse_otlp_protobuf(body)
        elif "application/json" in content_type:
            batches = parse_json(json.loads(body))
        else:
            raise HTTPException(
                status_code=415,
                detail=(
                    f"unsupported content-type: {content_type!r}. "
                    "use application/x-protobuf (OTLP) or application/json."
                ),
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"failed to parse request body: {exc}"
        ) from exc

    received = await queries.ingest_batches(pool, batches)
    return IngestResponseOut(received=received)


@app.get("/v1/traces")
async def list_traces(
    pool: Pool,
    start_time: datetime | None = Query(None),
    end_time: datetime | None = Query(None),
    service: str | None = Query(None),
    status: Literal["ok", "error", "unset"] | None = Query(None),
    min_latency_ms: int | None = Query(None, ge=0),
    model: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> TracesPageOut:
    rows, total = await queries.list_traces(
        pool,
        start_time=start_time,
        end_time=end_time,
        service=service,
        status=status,
        min_latency_ms=min_latency_ms,
        model=model,
        limit=limit,
        offset=offset,
    )
    return TracesPageOut(
        traces=[TraceOut.model_validate(dict(row)) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@app.get("/v1/traces/{trace_id}")
async def get_trace(trace_id: str, pool: Pool) -> TraceDetailOut:
    trace, spans = await queries.get_trace(pool, trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"trace {trace_id!r} not found")
    return TraceDetailOut(
        trace=TraceOut.model_validate(dict(trace)),
        spans=[SpanOut.model_validate(dict(s)) for s in spans],
    )


@app.get("/v1/metrics/summary")
async def metrics_summary(
    pool: Pool,
    start_time: datetime | None = Query(None),
    end_time: datetime | None = Query(None),
    bucket: BucketGranularity = Query("hour"),
) -> MetricsSummaryOut:
    resolved_end = end_time or datetime.now(UTC)
    resolved_start = start_time or (resolved_end - timedelta(hours=24))
    data = await queries.metrics_summary(
        pool, start_time=resolved_start, end_time=resolved_end, bucket=bucket
    )
    return MetricsSummaryOut.model_validate(data)
