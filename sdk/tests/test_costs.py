from __future__ import annotations

from spanscope.costs import cost_for


def test_known_model_computes_expected_cost() -> None:
    # gpt-4o: $2.50/1M in, $10.00/1M out
    cost = cost_for("gpt-4o", input_tokens=1000, output_tokens=500)
    assert cost is not None
    assert round(cost, 6) == round(1000 * 2.50 / 1_000_000 + 500 * 10.00 / 1_000_000, 6)


def test_unknown_model_returns_none_not_zero() -> None:
    assert cost_for("some-model-that-does-not-exist", 100, 100) is None


def test_missing_token_counts_return_none() -> None:
    assert cost_for("gpt-4o", None, 100) is None
    assert cost_for("gpt-4o", 100, None) is None
