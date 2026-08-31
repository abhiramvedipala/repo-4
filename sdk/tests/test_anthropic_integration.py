from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from spanscope import SpanStatus, Tracer
from spanscope.exporters import InMemoryExporter
from spanscope.integrations.anthropic import instrument_anthropic


class _FakeMessages:
    def __init__(self, factory: Any) -> None:
        self._factory = factory

    def create(self, **kwargs: Any) -> Any:
        return self._factory(**kwargs)


class _FakeClient:
    def __init__(self, factory: Any) -> None:
        self.messages = _FakeMessages(factory)


def _tracer(flush_interval: float = 0.05) -> tuple[Tracer, InMemoryExporter]:
    exporter = InMemoryExporter()
    tracer = Tracer("test-service", exporter=exporter, flush_interval_seconds=flush_interval)
    return tracer, exporter


# Same reasoning as test_openai_integration.py: keep working through `fake_client` (typed
# as `_FakeClient`), not instrument_anthropic's return value (typed as the real `Anthropic`).


def test_non_streaming_success_records_span_and_returns_real_response() -> None:
    tracer, exporter = _tracer()
    response = SimpleNamespace(
        content=[SimpleNamespace(text="Hello!")],
        usage=SimpleNamespace(input_tokens=12, output_tokens=6),
    )
    fake_client = _FakeClient(lambda **kwargs: response)
    instrument_anthropic(fake_client, tracer)  # type: ignore[arg-type]

    result = fake_client.messages.create(
        model="claude-3-5-sonnet-20241022", messages=[{"role": "user", "content": "hi"}]
    )
    assert result is response

    tracer.shutdown()
    assert len(exporter.spans) == 1
    span = exporter.spans[0]
    assert span.provider == "anthropic"
    assert span.status == SpanStatus.OK
    assert span.input_tokens == 12
    assert span.output_tokens == 6
    assert span.completion == "Hello!"
    assert span.cost_usd is not None


def test_api_error_propagates_and_span_records_error() -> None:
    tracer, exporter = _tracer()

    def _raise(**kwargs: Any) -> Any:
        raise TimeoutError("anthropic timed out")

    fake_client = _FakeClient(_raise)
    instrument_anthropic(fake_client, tracer)  # type: ignore[arg-type]

    with pytest.raises(TimeoutError, match="anthropic timed out"):
        fake_client.messages.create(model="claude-3-5-sonnet-20241022", messages=[])

    tracer.shutdown()
    assert exporter.spans[0].status == SpanStatus.ERROR
    assert exporter.spans[0].error_type == "TimeoutError"


def test_streaming_merges_usage_across_events_without_altering_events() -> None:
    """Anthropic spreads usage across events: input_tokens on message_start,
    output_tokens on a later message_delta. A later event must not null out the
    earlier one — this is the merge behavior in _common.wrap_stream.
    """
    tracer, exporter = _tracer()
    events = [
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(usage=SimpleNamespace(input_tokens=20, output_tokens=0)),
        ),
        SimpleNamespace(
            type="content_block_delta", delta=SimpleNamespace(text="Hel")
        ),
        SimpleNamespace(
            type="content_block_delta", delta=SimpleNamespace(text="lo!")
        ),
        SimpleNamespace(
            type="message_delta", usage=SimpleNamespace(output_tokens=7)
        ),
    ]
    fake_client = _FakeClient(lambda **kwargs: iter(events))
    instrument_anthropic(fake_client, tracer)  # type: ignore[arg-type]

    received = list(
        fake_client.messages.create(model="claude-3-5-sonnet-20241022", messages=[], stream=True)
    )
    assert received == events

    tracer.shutdown()
    span = exporter.spans[0]
    assert span.completion == "Hello!"
    assert span.input_tokens == 20  # from message_start, not overwritten by message_delta
    assert span.output_tokens == 7  # from message_delta


def test_broken_tracer_never_breaks_the_real_call() -> None:
    tracer, _ = _tracer()

    def _poison_span(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("spanscope internal failure")

    tracer.span = _poison_span  # type: ignore[method-assign]

    response = SimpleNamespace(
        content=[SimpleNamespace(text="still works")],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    fake_client = _FakeClient(lambda **kwargs: response)
    instrument_anthropic(fake_client, tracer)  # type: ignore[arg-type]

    result = fake_client.messages.create(model="claude-3-5-sonnet-20241022", messages=[])
    assert result is response
