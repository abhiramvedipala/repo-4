"""Pydantic response models for the read side (GET endpoints). POST /v1/traces
validation still goes through otlp.py's parse_json/parse_otlp_protobuf (Phase 4) —
these model the query responses.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class TraceOut(BaseModel):
    id: str
    name: str
    service: str
    root_span_id: str | None
    start_time: datetime
    end_time: datetime | None
    duration_ms: int | None
    status: str
    total_tokens: int
    total_cost_usd: float


class TracesPageOut(BaseModel):
    traces: list[TraceOut]
    total: int
    limit: int
    offset: int


class SpanOut(BaseModel):
    id: str
    trace_id: str
    parent_span_id: str | None
    name: str
    kind: str
    provider: str | None
    model: str | None
    start_time: datetime
    end_time: datetime
    latency_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    status: str
    error_type: str | None
    error_message: str | None
    prompt: str | None
    completion: str | None
    attributes: dict[str, Any]


class TraceDetailOut(BaseModel):
    trace: TraceOut
    spans: list[SpanOut]


class ModelCallsOut(BaseModel):
    model: str
    calls: int
    cost_usd: float


class MetricsBucketOut(BaseModel):
    bucket: datetime
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    calls: int
    cost_usd: float
    error_rate: float


class MetricsSummaryOut(BaseModel):
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    p99_latency_ms: float | None
    total_cost_usd: float
    total_calls: int
    error_rate: float
    calls_by_model: list[ModelCallsOut]
    buckets: list[MetricsBucketOut]


class IngestResponseOut(BaseModel):
    received: int
