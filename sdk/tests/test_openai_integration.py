from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from spanscope import Tracer, semconv
from spanscope.integrations.openai import instrument_openai


def _attrs(span: ReadableSpan) -> Mapping[str, Any]:
    # ReadableSpan.attributes is typed Mapping | None (a span can carry zero
    # attributes) — every span in these tests always has some, so narrow once here
    # instead of asserting at every call site.
    assert span.attributes is not None
    return span.attributes


class _FakeCompletions:
    def __init__(self, factory: Any) -> None:
        self._factory = factory

    def create(self, **kwargs: Any) -> Any:
        return self._factory(**kwargs)


class _FakeClient:
    def __init__(self, factory: Any) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(factory))


def _tracer(flush_interval: float = 0.05) -> tuple[Tracer, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    tracer = Tracer("test-service", exporter=exporter, flush_interval_seconds=flush_interval)
    return tracer, exporter


# instrument_openai's declared return type is the real `openai.OpenAI` — capturing that
# return value would make mypy check every later call against the real SDK's strict
# overloads instead of this fake's. Call it only for the patching side effect and keep
# working through `fake_client`, whose static type stays `_FakeClient` throughout.


def test_non_streaming_success_records_span_and_returns_real_response() -> None:
    tracer, exporter = _tracer()
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="Hello!"))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )
    fake_client = _FakeClient(lambda **kwargs: response)
    instrument_openai(fake_client, tracer)  # type: ignore[arg-type]

    result = fake_client.chat.completions.create(
        model="gpt-4o", messages=[{"role": "user", "content": "hi"}]
    )
    assert result is response  # instrumentation never touches the real return value

    tracer.shutdown()
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    attrs = _attrs(span)
    assert attrs[semconv.GEN_AI_SYSTEM] == "openai"
    assert attrs[semconv.GEN_AI_REQUEST_MODEL] == "gpt-4o"
    assert span.status.status_code == StatusCode.OK
    assert attrs[semconv.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert attrs[semconv.GEN_AI_USAGE_OUTPUT_TOKENS] == 5
    assert attrs[semconv.SPANSCOPE_COMPLETION] == "Hello!"
    assert semconv.SPANSCOPE_COST_USD in attrs


def test_api_error_propagates_and_span_records_error() -> None:
    tracer, exporter = _tracer()

    def _raise(**kwargs: Any) -> Any:
        raise ConnectionError("openai is down")

    fake_client = _FakeClient(_raise)
    instrument_openai(fake_client, tracer)  # type: ignore[arg-type]

    with pytest.raises(ConnectionError, match="openai is down"):
        fake_client.chat.completions.create(model="gpt-4o", messages=[])

    tracer.shutdown()
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].status.status_code == StatusCode.ERROR
    assert _attrs(spans[0])[semconv.ERROR_TYPE] == "ConnectionError"


def test_streaming_accumulates_text_and_usage_without_altering_chunks() -> None:
    tracer, exporter = _tracer()
    chunks = [
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="Hel"))]),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="lo!"))]),
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=None))],
            usage=SimpleNamespace(prompt_tokens=8, completion_tokens=2),
        ),
    ]
    fake_client = _FakeClient(lambda **kwargs: iter(chunks))
    instrument_openai(fake_client, tracer)  # type: ignore[arg-type]

    received = list(
        fake_client.chat.completions.create(model="gpt-4o", messages=[], stream=True)
    )
    assert received == chunks  # every chunk forwarded through untouched

    tracer.shutdown()
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = _attrs(spans[0])
    assert attrs[semconv.SPANSCOPE_COMPLETION] == "Hello!"
    assert attrs[semconv.GEN_AI_USAGE_INPUT_TOKENS] == 8
    assert attrs[semconv.GEN_AI_USAGE_OUTPUT_TOKENS] == 2


def test_broken_tracer_never_breaks_the_real_call() -> None:
    """The headline Phase 3 requirement: if SpanScope itself is failing, the caller's
    real LLM call and its real result must be completely unaffected. Still holds true
    now that spans are real OTel objects underneath.
    """
    tracer, _ = _tracer()

    def _poison_span(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("spanscope internal failure")

    tracer.span = _poison_span  # type: ignore[method-assign]

    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="still works"))],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )
    fake_client = _FakeClient(lambda **kwargs: response)
    instrument_openai(fake_client, tracer)  # type: ignore[arg-type]

    result = fake_client.chat.completions.create(model="gpt-4o", messages=[])
    assert result is response  # real call still succeeded, real result still returned
