"""Where finished spans go. `Exporter` is the extension point Phase 4 plugs the real
OTLP/HTTP exporter into, and Phase 5 the SpanScope ingest-API exporter — nothing in
Tracer needs to change when those show up, they just implement this same protocol.
"""

from __future__ import annotations

import threading
from typing import Protocol

from spanscope.span import Span


class Exporter(Protocol):
    def export(self, spans: list[Span]) -> None: ...


class ConsoleExporter:
    """Zero-config default: one line per span to stdout, so `Tracer("app")` is useful
    before any real backend is wired up.
    """

    def export(self, spans: list[Span]) -> None:
        for s in spans:
            latency = f"{s.latency_ms:.1f}ms" if s.latency_ms is not None else "?"
            print(
                f"[spanscope] {s.name} trace={s.trace_id} span={s.span_id} "
                f"status={s.status.value} latency={latency}"
            )


class InMemoryExporter:
    """Collects spans in a list. Used by tests, and by anyone who wants to inspect
    captured spans directly instead of shipping them anywhere.
    """

    def __init__(self) -> None:
        self.spans: list[Span] = []
        # The flush thread writes here while a test/caller may read `.spans` concurrently.
        self._lock = threading.Lock()

    def export(self, spans: list[Span]) -> None:
        with self._lock:
            self.spans.extend(spans)
