"""Ergonomic wrapper around a real `opentelemetry.trace.Span`.

Field-style access (`span.model = "gpt-4o"`) is the exact same surface Phase 2/3 code
already uses — nothing above this file needed to change to get real OTel spans
underneath. Values accumulate locally and get pushed onto the real span's attributes
(GenAI semantic convention keys where one exists — see semconv.py) in one batch right
before the span ends, since a real OTel SDK span is effectively write-only once
`.end()` has been called (later `set_attribute` calls are silently dropped).
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from opentelemetry.trace import Status, StatusCode

from spanscope import semconv

if TYPE_CHECKING:
    from opentelemetry.trace import Span as OTelSpan

AttributeValue = str | int | float | bool


class SpanStatus(StrEnum):
    """Mirrors the `status` CHECK constraint on both traces and spans
    (api/migrations/0001_init.sql). Kept as our own lowercase enum rather than OTel's
    StatusCode (UNSET/OK/ERROR, different casing) since this is what Phase 5 writes
    straight into Postgres.
    """

    UNSET = "unset"
    OK = "ok"
    ERROR = "error"


_STATUS_TO_OTEL = {
    SpanStatus.UNSET: StatusCode.UNSET,
    SpanStatus.OK: StatusCode.OK,
    SpanStatus.ERROR: StatusCode.ERROR,
}


class Span:
    def __init__(self, otel_span: OTelSpan, parent_span_id: str | None) -> None:
        self._otel_span = otel_span
        # parent_span_id is passed in, not read back off otel_span: the writable OTel
        # Span interface doesn't expose its own parent (only the exported ReadableSpan
        # does), but the Tracer already knows the parent at creation time.
        self.parent_span_id = parent_span_id
        self.status: SpanStatus = SpanStatus.UNSET
        self.error_type: str | None = None
        self.error_message: str | None = None
        self.provider: str | None = None
        self.model: str | None = None
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.cost_usd: float | None = None
        self.prompt: str | None = None
        self.completion: str | None = None
        self.attributes: dict[str, AttributeValue] = {}

    @property
    def span_id(self) -> str:
        return format(self._otel_span.get_span_context().span_id, "016x")

    @property
    def trace_id(self) -> str:
        return format(self._otel_span.get_span_context().trace_id, "032x")

    def set_attribute(self, key: str, value: AttributeValue) -> None:
        self.attributes[key] = value

    def set_status(self, status: SpanStatus) -> None:
        self.status = status

    def _finish(self) -> None:
        """Pushes every accumulated field onto the real OTel span, then ends it.
        Called exactly once by Tracer, after every field has been set.
        """
        if self.provider is not None:
            self._otel_span.set_attribute(semconv.GEN_AI_SYSTEM, self.provider)
        if self.model is not None:
            self._otel_span.set_attribute(semconv.GEN_AI_REQUEST_MODEL, self.model)
        if self.input_tokens is not None:
            self._otel_span.set_attribute(semconv.GEN_AI_USAGE_INPUT_TOKENS, self.input_tokens)
        if self.output_tokens is not None:
            self._otel_span.set_attribute(semconv.GEN_AI_USAGE_OUTPUT_TOKENS, self.output_tokens)
        if self.cost_usd is not None:
            self._otel_span.set_attribute(semconv.SPANSCOPE_COST_USD, self.cost_usd)
        if self.prompt is not None:
            self._otel_span.set_attribute(semconv.SPANSCOPE_PROMPT, self.prompt)
        if self.completion is not None:
            self._otel_span.set_attribute(semconv.SPANSCOPE_COMPLETION, self.completion)
        if self.error_type is not None:
            self._otel_span.set_attribute(semconv.ERROR_TYPE, self.error_type)
        if self.error_message is not None:
            self._otel_span.set_attribute(semconv.SPANSCOPE_ERROR_MESSAGE, self.error_message)
        for key, value in self.attributes.items():
            self._otel_span.set_attribute(key, value)

        self._otel_span.set_status(
            Status(_STATUS_TO_OTEL[self.status], description=self.error_message)
        )
        self._otel_span.end()
