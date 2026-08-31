"""Core tracer, now backed by the real opentelemetry-sdk: TracerProvider, real Span
objects, BatchSpanProcessor for buffering and background flush.

Phase 2 hand-rolled all of that (contextvars nesting, a queue.Queue buffer, a background
thread). All of it is deleted here in favor of OTel's own tested machinery — TracerProvider
generates real trace/span IDs and builds real spans, BatchSpanProcessor does the buffering
and background flush. What's still genuinely ours: the ergonomic Span wrapper (span.py),
and parent tracking via our own ContextVar rather than OTel's ambient Context — see the
comment on `_current_span` below for why that distinction matters.
"""

from __future__ import annotations

import atexit
import contextvars
import functools
import inspect
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import ParamSpec, TypeVar

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SpanExporter
from opentelemetry.trace import SpanKind

from spanscope.span import AttributeValue, Span, SpanStatus

P = ParamSpec("P")
R = TypeVar("R")

DEFAULT_FLUSH_INTERVAL_SECONDS = 5.0
DEFAULT_MAX_BUFFER_SIZE = 512


class Tracer:
    def __init__(
        self,
        service_name: str,
        exporter: SpanExporter | None = None,
        flush_interval_seconds: float = DEFAULT_FLUSH_INTERVAL_SECONDS,
        max_buffer_size: int = DEFAULT_MAX_BUFFER_SIZE,
    ) -> None:
        self.service_name = service_name
        self._provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        self._provider.add_span_processor(
            BatchSpanProcessor(
                exporter or ConsoleSpanExporter(),
                schedule_delay_millis=int(flush_interval_seconds * 1000),
                max_export_batch_size=max_buffer_size,
            )
        )
        self._otel_tracer = self._provider.get_tracer("spanscope")

        # Our own ContextVar for "current SpanScope span" — deliberately NOT OTel's
        # ambient Context (start_as_current_span). Streaming spans (see
        # integrations/_common.py) are opened in one call frame and closed much later in
        # another, possibly after other unrelated code has run on this same context. If
        # we used start_as_current_span, that in-flight streaming span would stay the
        # ambient "current span" — and wrongly become the parent of anything else that
        # runs while the stream is still being consumed. Tracking it ourselves avoids
        # that entirely. We still get interop with other OTel-instrumented libraries in
        # this process: start_span(context=None) below falls back to OTel's own ambient
        # current span whenever we have no SpanScope-tracked parent of our own.
        self._current_span: contextvars.ContextVar[Span | None] = contextvars.ContextVar(
            f"spanscope_current_span_{id(self)}", default=None
        )
        self._shutdown_called = False
        atexit.register(self.shutdown)

    @contextmanager
    def span(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: dict[str, AttributeValue] | None = None,
    ) -> Iterator[Span]:
        parent = self._current_span.get()
        parent_context = trace.set_span_in_context(parent._otel_span) if parent else None
        otel_span = self._otel_tracer.start_span(name, kind=kind, context=parent_context)

        wrapped = Span(otel_span, parent_span_id=parent.span_id if parent else None)
        if attributes:
            wrapped.attributes.update(attributes)

        token = self._current_span.set(wrapped)
        try:
            yield wrapped
        except Exception as exc:
            wrapped.status = SpanStatus.ERROR
            wrapped.error_type = type(exc).__name__
            wrapped.error_message = str(exc)
            otel_span.record_exception(exc)  # standard OTel exception event, in addition
            raise  # to our own error_type/error_message fields (see span.py _finish)
        finally:
            self._current_span.reset(token)
            wrapped._finish()

    def trace(self, name: str | None = None) -> Callable[[Callable[P, R]], Callable[P, R]]:
        """`@tracer.trace()` — wraps sync or async functions, span name defaults to the
        function's qualified name.
        """

        def decorator(func: Callable[P, R]) -> Callable[P, R]:
            span_name = name or func.__qualname__

            if inspect.iscoroutinefunction(func):

                @functools.wraps(func)
                async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                    with self.span(span_name):
                        return await func(*args, **kwargs)  # type: ignore[no-any-return]

                return async_wrapper  # type: ignore[return-value]

            @functools.wraps(func)
            def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                with self.span(span_name):
                    return func(*args, **kwargs)

            return sync_wrapper

        return decorator

    def shutdown(self) -> None:
        if self._shutdown_called:
            return
        self._shutdown_called = True
        self._provider.shutdown()  # flushes any buffered spans, stops the processor thread
