from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from spanscope import SpanStatus, Tracer
from spanscope.exporters import InMemoryExporter
from spanscope.integrations.openai import instrument_openai


class _FakeCompletions:
    def __init__(self, factory: Any) -> None:
        self._factory = factory

    def create(self, **kwargs: Any) -> Any:
        return self._factory(**kwargs)


class _FakeClient:
    def __init__(self, factory: Any) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(factory))


def _tracer(flush_interval: float = 0.05) -> tuple[Tracer, InMemoryExporter]:
    exporter = InMemoryExporter()
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
    assert len(exporter.spans) == 1
    span = exporter.spans[0]
    assert span.provider == "openai"
    assert span.model == "gpt-4o"
    assert span.status == SpanStatus.OK
    assert span.input_tokens == 10
    assert span.output_tokens == 5
    assert span.completion == "Hello!"
    assert span.cost_usd is not None


def test_api_error_propagates_and_span_records_error() -> None:
    tracer, exporter = _tracer()

    def _raise(**kwargs: Any) -> Any:
        raise ConnectionError("openai is down")

    fake_client = _FakeClient(_raise)
    instrument_openai(fake_client, tracer)  # type: ignore[arg-type]

    with pytest.raises(ConnectionError, match="openai is down"):
        fake_client.chat.completions.create(model="gpt-4o", messages=[])

    tracer.shutdown()
    assert len(exporter.spans) == 1
    assert exporter.spans[0].status == SpanStatus.ERROR
    assert exporter.spans[0].error_type == "ConnectionError"


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
    assert len(exporter.spans) == 1
    span = exporter.spans[0]
    assert span.completion == "Hello!"
    assert span.input_tokens == 8
    assert span.output_tokens == 2


def test_broken_tracer_never_breaks_the_real_call() -> None:
    """The headline Phase 3 requirement: if SpanScope itself is failing, the caller's
    real LLM call and its real result must be completely unaffected.
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
