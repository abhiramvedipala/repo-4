from __future__ import annotations

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.trace.v1.trace_pb2 import Span as PbSpan
from opentelemetry.proto.trace.v1.trace_pb2 import Status as PbStatus


@pytest.fixture
def otlp_request_bytes() -> bytes:
    """A real, serialized OTLP ExportTraceServiceRequest carrying one client span with
    GenAI attributes — the exact wire format OTLPSpanExporter (Phase 4 SDK) sends.
    """
    request = ExportTraceServiceRequest()
    span = request.resource_spans.add().scope_spans.add().spans.add()
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
