"""Per-model USD pricing for common OpenAI/Anthropic models, keyed by the model name
each provider's API actually returns.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    input_per_million: float  # USD per 1,000,000 input tokens
    output_per_million: float  # USD per 1,000,000 output tokens


# ponytail: hand-maintained snapshot, not a live feed — there's no pricing API to poll.
# Prices drift; update this table when a provider reprices rather than trying to
# auto-discover it. Unknown models fall through to cost_for() returning None (see below),
# never a silently-wrong 0.
MODEL_PRICING: dict[str, ModelPricing] = {
    # OpenAI
    "gpt-4o": ModelPricing(2.50, 10.00),
    "gpt-4o-mini": ModelPricing(0.15, 0.60),
    "gpt-4-turbo": ModelPricing(10.00, 30.00),
    "gpt-3.5-turbo": ModelPricing(0.50, 1.50),
    # Anthropic
    "claude-3-5-sonnet-20241022": ModelPricing(3.00, 15.00),
    "claude-3-5-haiku-20241022": ModelPricing(0.80, 4.00),
    "claude-3-opus-20240229": ModelPricing(15.00, 75.00),
}


def cost_for(model: str, input_tokens: int | None, output_tokens: int | None) -> float | None:
    """None (not 0) for an unknown model or missing token counts. A cost of 0 would read
    in the dashboard as "this call was free" — None reads as "couldn't price this",
    which is the truthful answer and is what a UI should render as unknown, not $0.00.
    """
    pricing = MODEL_PRICING.get(model)
    if pricing is None or input_tokens is None or output_tokens is None:
        return None
    return (
        input_tokens * pricing.input_per_million / 1_000_000
        + output_tokens * pricing.output_per_million / 1_000_000
    )
