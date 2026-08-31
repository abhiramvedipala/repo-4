"""Wire-format parsing for the ingest endpoint: real OTLP/HTTP protobuf, and
SpanScope's own simpler JSON. Both normalize to the same SpanRecord shape — everything
downstream of parsing works from SpanRecord, never from either wire format directly.

Phase 5 replaces the caller of this module (currently: count and discard) with real
Pydantic request validation and a Postgres batch insert of the same SpanRecord objects
produced here; this parsing logic itself doesn't change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import Span as PbSpan
from opentelemetry.proto.trace.v1.trace_pb2 import Status as PbStatus

# Same GenAI semantic convention keys the SDK writes (sdk/spanscope/semconv.py). sdk/ and
# api/ are separate installable packages with no dependency between them, so these are
# duplicated string constants rather than a shared import — safe to duplicate because
# they're spec-defined wire-format strings, not our own design that could drift.
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
ERROR_TYPE = "error.type"
SPANSCOPE_COST_USD = "spanscope.cost.usd"
SPANSCOPE_PROMPT = "spanscope.prompt"
SPANSCOPE_COMPLETION = "spanscope.completion"
SPANSCOPE_ERROR_MESSAGE = "spanscope.error.message"

_PB_KIND_TO_STR = {
    PbSpan.SPAN_KIND_INTERNAL: "internal",
    PbSpan.SPAN_KIND_SERVER: "server",
    PbSpan.SPAN_KIND_CLIENT: "client",
    PbSpan.SPAN_KIND_PRODUCER: "producer",
    PbSpan.SPAN_KIND_CONSUMER: "consumer",
    PbSpan.SPAN_KIND_UNSPECIFIED: "internal",  # matches the DB column's own default
}

_PB_STATUS_TO_STR = {
    PbStatus.STATUS_CODE_UNSET: "unset",
    PbStatus.STATUS_CODE_OK: "ok",
    PbStatus.STATUS_CODE_ERROR: "error",
}


@dataclass
class SpanRecord:
    """Normalized shape both wire formats parse into — mirrors the spans table columns
    from api/migrations/0001_init.sql.
    """

    span_id: str
    trace_id: str
    parent_span_id: str | None
    name: str
    kind: str
    start_time: datetime
    end_time: datetime
    status: str = "unset"
    provider: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    prompt: str | None = None
    completion: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


def _any_value_to_python(value: AnyValue) -> Any:
    # Deliberately Any, not a narrower union: this is a trust boundary — a client
    # speaking raw OTLP protobuf can put any AnyValue variant behind any attribute key,
    # and nothing here enforces that gen_ai.usage.input_tokens is actually an int. Phase
    # 5's Pydantic validation is where that gets enforced before anything is persisted;
    # this parsing layer only normalizes shape, not correctness.
    kind = value.WhichOneof("value")
    if kind is None:
        return None
    if kind == "array_value":
        return [_any_value_to_python(v) for v in value.array_value.values]
    if kind == "kvlist_value":
        return {kv.key: _any_value_to_python(kv.value) for kv in value.kvlist_value.values}
    return getattr(value, kind)


def _attrs_to_dict(attrs: list[KeyValue]) -> dict[str, Any]:
    return {kv.key: _any_value_to_python(kv.value) for kv in attrs}


def _nanos_to_datetime(nanos: int) -> datetime:
    return datetime.fromtimestamp(nanos / 1_000_000_000, tz=UTC)


def parse_otlp_protobuf(body: bytes) -> list[SpanRecord]:
    """Decodes a real OTLP/HTTP protobuf ExportTraceServiceRequest — the exact wire
    format the Phase 4 SDK (or any other OTel SDK/collector) sends via OTLPSpanExporter.
    """
    request = ExportTraceServiceRequest()
    request.ParseFromString(body)

    records: list[SpanRecord] = []
    for resource_spans in request.resource_spans:
        for scope_spans in resource_spans.scope_spans:
            for pb_span in scope_spans.spans:
                attrs = _attrs_to_dict(list(pb_span.attributes))
                records.append(
                    SpanRecord(
                        span_id=pb_span.span_id.hex(),
                        trace_id=pb_span.trace_id.hex(),
                        parent_span_id=pb_span.parent_span_id.hex() or None,
                        name=pb_span.name,
                        kind=_PB_KIND_TO_STR.get(pb_span.kind, "internal"),
                        start_time=_nanos_to_datetime(pb_span.start_time_unix_nano),
                        end_time=_nanos_to_datetime(pb_span.end_time_unix_nano),
                        status=_PB_STATUS_TO_STR.get(pb_span.status.code, "unset"),
                        provider=attrs.pop(GEN_AI_SYSTEM, None),
                        model=attrs.pop(GEN_AI_REQUEST_MODEL, None),
                        input_tokens=attrs.pop(GEN_AI_USAGE_INPUT_TOKENS, None),
                        output_tokens=attrs.pop(GEN_AI_USAGE_OUTPUT_TOKENS, None),
                        cost_usd=attrs.pop(SPANSCOPE_COST_USD, None),
                        prompt=attrs.pop(SPANSCOPE_PROMPT, None),
                        completion=attrs.pop(SPANSCOPE_COMPLETION, None),
                        error_type=attrs.pop(ERROR_TYPE, None),
                        error_message=attrs.pop(SPANSCOPE_ERROR_MESSAGE, None),
                        attributes=attrs,  # whatever's left after pulling out known keys
                    )
                )
    return records


def parse_json(payload: dict[str, Any]) -> list[SpanRecord]:
    """SpanScope's own simpler JSON ingest format — a flat {"spans": [...]} list.
    Deliberately a draft: Phase 5 formalizes this exact shape with real Pydantic models
    (field validation, required-field errors, etc.); this proves both wire formats
    normalize to the same SpanRecord.
    """
    return [
        SpanRecord(
            span_id=item["span_id"],
            trace_id=item["trace_id"],
            parent_span_id=item.get("parent_span_id"),
            name=item["name"],
            kind=item.get("kind", "internal"),
            start_time=datetime.fromisoformat(item["start_time"]),
            end_time=datetime.fromisoformat(item["end_time"]),
            status=item.get("status", "unset"),
            provider=item.get("provider"),
            model=item.get("model"),
            input_tokens=item.get("input_tokens"),
            output_tokens=item.get("output_tokens"),
            cost_usd=item.get("cost_usd"),
            prompt=item.get("prompt"),
            completion=item.get("completion"),
            error_type=item.get("error_type"),
            error_message=item.get("error_message"),
            attributes=item.get("attributes", {}),
        )
        for item in payload.get("spans", [])
    ]
