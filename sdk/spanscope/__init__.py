"""SpanScope tracing SDK. LLM instrumentation lands in Phase 3."""

from spanscope.exporters import ConsoleExporter, Exporter, InMemoryExporter
from spanscope.span import AttributeValue, Span, SpanKind, SpanStatus
from spanscope.tracer import Tracer

__version__ = "0.1.0"

__all__ = [
    "AttributeValue",
    "ConsoleExporter",
    "Exporter",
    "InMemoryExporter",
    "Span",
    "SpanKind",
    "SpanStatus",
    "Tracer",
    "__version__",
]
