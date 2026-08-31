"""GenAI semantic convention attribute keys
(https://opentelemetry.io/docs/specs/semconv/gen-ai/), plus a few SpanScope-specific
attributes that aren't part of the spec.

Hardcoded as plain strings rather than imported from opentelemetry.semconv's incubating
namespace — the GenAI convention is still marked "experimental" upstream, and that
package's import path for it has moved across releases. These are the literal spec key
strings, which is what actually matters for wire compatibility with real OTel backends.
"""

from __future__ import annotations

# --- GenAI semantic convention keys ---
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"

# Stable general OTel semconv (not GenAI-specific) — most backends index on this directly.
ERROR_TYPE = "error.type"

# --- SpanScope-specific, not part of any OTel spec ---
# Prompt/completion capture is itself still unstable in the GenAI spec (recent revisions
# model it as span *events*, not attributes) — using our own namespace here avoids
# claiming spec compliance we don't have. error.message isn't a stable attribute key
# upstream either (OTel keeps exception *text* in the exception event to avoid unbounded-
# cardinality attributes) — spanscope.error.message exists so Phase 5 can read the error
# text back directly without parsing the exception event.
SPANSCOPE_COST_USD = "spanscope.cost.usd"
SPANSCOPE_PROMPT = "spanscope.prompt"
SPANSCOPE_COMPLETION = "spanscope.completion"
SPANSCOPE_ERROR_MESSAGE = "spanscope.error.message"
