"""SpanScope tracing SDK, backed by the real opentelemetry-sdk (Phase 4).

Exporters: pass any real `opentelemetry.sdk.trace.export.SpanExporter` to `Tracer(...,
exporter=...)` — e.g. `opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter`
for any OTLP/HTTP endpoint, or `opentelemetry.sdk.trace.export.ConsoleSpanExporter` (the
default) for stdout. No SpanScope-specific exporter wrapper needed; Tracer accepts the real
thing directly.
"""

from opentelemetry.trace import SpanKind

from spanscope.costs import MODEL_PRICING, ModelPricing, cost_for
from spanscope.integrations.anthropic import instrument_anthropic
from spanscope.integrations.openai import instrument_openai
from spanscope.span import AttributeValue, Span, SpanStatus
from spanscope.tracer import Tracer

__version__ = "0.1.0"

__all__ = [
    "MODEL_PRICING",
    "AttributeValue",
    "ModelPricing",
    "Span",
    "SpanKind",
    "SpanStatus",
    "Tracer",
    "__version__",
    "cost_for",
    "instrument_anthropic",
    "instrument_openai",
]
