"""Core tracer: parent-child span nesting, buffering, background flush.

Deliberately hand-rolled and stdlib-only rather than built on `opentelemetry-sdk` — Phase 4
is where this gets reconciled with the real OTel SDK and its GenAI semantic conventions.
Building on real OTel now would mean carrying a dependency (and its API) before we actually
need OTel's export machinery; the Span/Exporter shapes here are intentionally close enough
to OTel's that Phase 4 is a translation, not a rewrite.
"""

from __future__ import annotations

import atexit
import contextvars
import functools
import inspect
import logging
import os
import queue
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import ParamSpec, TypeVar

from spanscope.exporters import ConsoleExporter, Exporter
from spanscope.span import AttributeValue, Span, SpanKind, SpanStatus

logger = logging.getLogger("spanscope")

P = ParamSpec("P")
R = TypeVar("R")

DEFAULT_FLUSH_INTERVAL_SECONDS = 5.0
DEFAULT_MAX_BUFFER_SIZE = 512


def _generate_trace_id() -> str:
    return os.urandom(16).hex()  # 16 bytes -> 32 hex chars, matches OTel trace_id


def _generate_span_id() -> str:
    return os.urandom(8).hex()  # 8 bytes -> 16 hex chars, matches OTel span_id


class Tracer:
    def __init__(
        self,
        service_name: str,
        exporter: Exporter | None = None,
        flush_interval_seconds: float = DEFAULT_FLUSH_INTERVAL_SECONDS,
        max_buffer_size: int = DEFAULT_MAX_BUFFER_SIZE,
    ) -> None:
        self.service_name = service_name
        self._exporter = exporter or ConsoleExporter()
        self._flush_interval = flush_interval_seconds
        self._max_buffer_size = max_buffer_size

        # contextvars, not a plain thread-local or manual stack: a ContextVar's value is
        # copied into every new asyncio Task and every `contextvars.copy_context()` call,
        # so nesting stays correct across `await` points, not just across threads. An
        # instance attribute (not module-level) so two Tracer instances never see each
        # other's "current span".
        self._current_span: contextvars.ContextVar[Span | None] = contextvars.ContextVar(
            f"spanscope_current_span_{id(self)}", default=None
        )

        self._queue: queue.Queue[Span] = queue.Queue()
        self._stop_event = threading.Event()
        self._worker = threading.Thread(target=self._run, name="spanscope-flush", daemon=True)
        self._worker.start()
        atexit.register(self.shutdown)  # otherwise spans queued right before exit vanish

    @contextmanager
    def span(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: dict[str, AttributeValue] | None = None,
    ) -> Iterator[Span]:
        parent = self._current_span.get()
        span = Span(
            span_id=_generate_span_id(),
            trace_id=parent.trace_id if parent else _generate_trace_id(),
            parent_span_id=parent.span_id if parent else None,
            name=name,
            kind=kind,
            start_time=datetime.now(UTC),
            attributes=dict(attributes) if attributes else {},
        )
        token = self._current_span.set(span)
        try:
            yield span
        except Exception as exc:
            span.status = SpanStatus.ERROR
            span.error_type = type(exc).__name__
            span.error_message = str(exc)
            raise
        finally:
            span.end_time = datetime.now(UTC)
            self._current_span.reset(token)
            self._queue.put(span)

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

    def _run(self) -> None:
        while not self._stop_event.is_set():
            batch = self._drain(timeout=self._flush_interval)
            if batch:
                self._safe_export(batch)

    def _drain(self, timeout: float) -> list[Span]:
        batch: list[Span] = []
        try:
            batch.append(self._queue.get(timeout=timeout))
        except queue.Empty:
            return batch
        while len(batch) < self._max_buffer_size:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return batch

    def _safe_export(self, batch: list[Span]) -> None:
        try:
            self._exporter.export(batch)
        except Exception:
            # An exporter failure must never kill the flush thread — that would silently
            # stop all future flushing for the rest of the process. Log and drop this
            # batch; Phase 3 applies the same "never break the caller's app" rule to the
            # LLM call sites themselves.
            logger.exception("spanscope: exporter failed, dropping %d span(s)", len(batch))

    def shutdown(self) -> None:
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        self._worker.join(timeout=self._flush_interval + 1)
        remaining: list[Span] = []
        while True:
            try:
                remaining.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if remaining:
            self._safe_export(remaining)
