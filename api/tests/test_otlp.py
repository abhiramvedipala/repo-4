from __future__ import annotations

from datetime import UTC, datetime

from app.otlp import parse_json, parse_otlp_protobuf


def test_parse_otlp_protobuf_extracts_genai_fields_and_service(
    otlp_request_bytes: bytes,
) -> None:
    batches = parse_otlp_protobuf(otlp_request_bytes)
    assert len(batches) == 1
    batch = batches[0]
    assert batch.service == "test-service"  # resource-level, not per-span
    assert len(batch.spans) == 1
    record = batch.spans[0]
    assert record.trace_id == "01" * 16
    assert record.span_id == "02" * 8
    assert record.parent_span_id is None  # root span: empty bytes -> None, not ""
    assert record.name == "openai.chat.completions.create"
    assert record.kind == "client"
    assert record.status == "ok"
    assert record.provider == "openai"
    assert record.model == "gpt-4o"
    assert record.input_tokens == 10
    assert record.output_tokens == 5
    assert record.completion == "Hello!"
    assert record.start_time == datetime.fromtimestamp(1_700_000_000_000_000_000 / 1e9, tz=UTC)
    # unknown attributes survive into the generic bucket, known ones don't duplicate there
    assert record.attributes == {"custom.tag": "keep-me"}


def test_parse_json_produces_the_same_shape_as_otlp() -> None:
    payload = {
        "service": "test-service",
        "spans": [
            {
                "span_id": "02" * 8,
                "trace_id": "01" * 16,
                "parent_span_id": None,
                "name": "openai.chat.completions.create",
                "kind": "client",
                "start_time": "2023-11-14T22:13:20+00:00",
                "end_time": "2023-11-14T22:13:20.500000+00:00",
                "status": "ok",
                "provider": "openai",
                "model": "gpt-4o",
                "input_tokens": 10,
                "output_tokens": 5,
                "completion": "Hello!",
                "attributes": {"custom.tag": "keep-me"},
            }
        ],
    }
    batches = parse_json(payload)
    assert len(batches) == 1
    batch = batches[0]
    assert batch.service == "test-service"
    record = batch.spans[0]
    assert record.trace_id == "01" * 16
    assert record.span_id == "02" * 8
    assert record.kind == "client"
    assert record.provider == "openai"
    assert record.model == "gpt-4o"
    assert record.input_tokens == 10
    assert record.output_tokens == 5
    assert record.completion == "Hello!"
    assert record.attributes == {"custom.tag": "keep-me"}


def test_json_and_otlp_normalize_the_same_span_identically(otlp_request_bytes: bytes) -> None:
    """The actual point of Phase 4/5's parsing layer: two different wire formats
    describing the same span must produce equivalent records.
    """
    otlp_batch = parse_otlp_protobuf(otlp_request_bytes)[0]
    otlp_record = otlp_batch.spans[0]
    json_batch = parse_json(
        {
            "service": otlp_batch.service,
            "spans": [
                {
                    "span_id": otlp_record.span_id,
                    "trace_id": otlp_record.trace_id,
                    "name": otlp_record.name,
                    "kind": otlp_record.kind,
                    "start_time": otlp_record.start_time.isoformat(),
                    "end_time": otlp_record.end_time.isoformat(),
                    "status": otlp_record.status,
                    "provider": otlp_record.provider,
                    "model": otlp_record.model,
                    "input_tokens": otlp_record.input_tokens,
                    "output_tokens": otlp_record.output_tokens,
                    "completion": otlp_record.completion,
                    "attributes": otlp_record.attributes,
                }
            ],
        }
    )
    assert json_batch[0].service == otlp_batch.service
    assert json_batch[0].spans[0] == otlp_record


def test_parse_json_defaults_kind_and_status_when_omitted() -> None:
    payload = {
        "spans": [
            {
                "span_id": "aa" * 8,
                "trace_id": "bb" * 16,
                "name": "internal-op",
                "start_time": "2023-11-14T22:13:20+00:00",
                "end_time": "2023-11-14T22:13:20.100000+00:00",
            }
        ]
    }
    batch = parse_json(payload)[0]
    assert batch.service == "unknown"  # no "service" key -> default
    record = batch.spans[0]
    assert record.kind == "internal"
    assert record.status == "unset"
