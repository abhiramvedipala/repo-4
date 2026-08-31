"""SpanScope tracing SDK."""

from spanscope.costs import MODEL_PRICING, ModelPricing, cost_for
from spanscope.exporters import ConsoleExporter, Exporter, InMemoryExporter
from spanscope.integrations.anthropic import instrument_anthropic
from spanscope.integrations.openai import instrument_openai
from spanscope.span import AttributeValue, Span, SpanKind, SpanStatus
from spanscope.tracer import Tracer

__version__ = "0.1.0"

__all__ = [
    "MODEL_PRICING",
    "AttributeValue",
    "ConsoleExporter",
    "Exporter",
    "InMemoryExporter",
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
