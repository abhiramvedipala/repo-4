from __future__ import annotations

import time

import pytest

from spanscope import SpanStatus, Tracer
from spanscope.exporters import InMemoryExporter


def _tracer(flush_interval: float = 0.05) -> tuple[Tracer, InMemoryExporter]:
    exporter = InMemoryExporter()
    tracer = Tracer("test-service", exporter=exporter, flush_interval_seconds=flush_interval)
    return tracer, exporter


def test_span_nesting_shares_trace_id_and_sets_parent() -> None:
    tracer, _ = _tracer()
    with tracer.span("outer") as outer:
        with tracer.span("inner") as inner:
            assert inner.trace_id == outer.trace_id
            assert inner.parent_span_id == outer.span_id
    assert outer.parent_span_id is None
    tracer.shutdown()


def test_sibling_spans_do_not_nest() -> None:
    tracer, _ = _tracer()
    with tracer.span("first") as first:
        pass
    with tracer.span("second") as second:
        pass
    assert second.parent_span_id is None
    assert first.trace_id != second.trace_id  # each top-level span starts its own trace
    tracer.shutdown()


def test_exception_in_span_sets_error_status_and_propagates() -> None:
    tracer, _ = _tracer()
    with pytest.raises(ValueError), tracer.span("failing") as span:
        raise ValueError("boom")
    assert span.status == SpanStatus.ERROR
    assert span.error_type == "ValueError"
    assert span.error_message == "boom"
    tracer.shutdown()


def test_decorator_wraps_sync_function() -> None:
    tracer, exporter = _tracer()

    @tracer.trace("do_work")
    def do_work(x: int) -> int:
        return x * 2

    assert do_work(3) == 6
    tracer.shutdown()
    assert any(s.name == "do_work" for s in exporter.spans)


async def test_decorator_wraps_async_function() -> None:
    tracer, exporter = _tracer()

    @tracer.trace("do_async_work")
    async def do_async_work(x: int) -> int:
        return x * 3

    result = await do_async_work(3)
    assert result == 9
    tracer.shutdown()
    assert any(s.name == "do_async_work" for s in exporter.spans)


def test_background_thread_flushes_without_shutdown() -> None:
    tracer, exporter = _tracer(flush_interval=0.05)
    with tracer.span("bg-flush-test"):
        pass
    time.sleep(0.2)
    assert any(s.name == "bg-flush-test" for s in exporter.spans)
    tracer.shutdown()


def test_shutdown_is_idempotent() -> None:
    tracer, _ = _tracer()
    tracer.shutdown()
    tracer.shutdown()  # must not raise
