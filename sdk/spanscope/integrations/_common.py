"""Shared span-lifecycle plumbing for LLM client wrappers.

The core rule for this whole module: instrumentation failing must never break the
caller's real LLM call. Three separate try/except zones enforce that —
- starting the span (safe_start_span)
- recording an error after the real call fails (safe_close_span_error)
- recording a completion after the real call succeeds (safe_close_span_success)
each catches and logs its own exceptions rather than letting them reach the caller. The
real API call itself is deliberately NOT wrapped here — a genuine OpenAI/Anthropic error
must still propagate; only OUR bookkeeping around it is disposable.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractContextManager
from typing import Any

from opentelemetry.trace import SpanKind

from spanscope.costs import cost_for
from spanscope.span import Span, SpanStatus
from spanscope.tracer import Tracer

logger = logging.getLogger("spanscope")


def safe_start_span(
    tracer: Tracer, name: str, provider: str, model: str, prompt: str | None
) -> tuple[AbstractContextManager[Span] | None, Span | None]:
    try:
        span_ctx = tracer.span(name, kind=SpanKind.CLIENT)
        span = span_ctx.__enter__()
        span.provider = provider
        span.model = model
        span.prompt = prompt
        return span_ctx, span
    except Exception:
        logger.exception("spanscope: failed to start span for %s", name)
        return None, None


def safe_close_span_error(
    span_ctx: AbstractContextManager[Span] | None, exc: BaseException
) -> None:
    if span_ctx is None:
        return
    try:
        span_ctx.__exit__(type(exc), exc, exc.__traceback__)
    except Exception:
        logger.exception("spanscope: failed to record error span")


def safe_close_span_success(
    span_ctx: AbstractContextManager[Span] | None,
    span: Span | None,
    completion: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
) -> None:
    if span_ctx is None or span is None:
        return
    try:
        span.status = SpanStatus.OK
        span.completion = completion
        span.input_tokens = input_tokens
        span.output_tokens = output_tokens
        span.cost_usd = cost_for(span.model or "", input_tokens, output_tokens)
        span_ctx.__exit__(None, None, None)
    except Exception:
        logger.exception("spanscope: failed to record completion span")


def wrap_stream(
    chunks: Iterable[Any],
    span_ctx: AbstractContextManager[Span] | None,
    span: Span | None,
    extract_delta_text: Callable[[Any], str | None],
    extract_usage: Callable[[Any], tuple[int | None, int | None] | None],
) -> Iterator[Any]:
    """Yields every chunk through untouched — instrumentation must never alter what the
    caller actually receives — while accumulating text/usage on the side. Runs in a
    `finally` so the span closes whether the caller exhausts the stream, breaks out of
    the loop early, or the generator gets garbage-collected (both trigger GeneratorExit,
    which `finally` still runs for).
    """
    if span_ctx is None or span is None:
        yield from chunks
        return

    accumulated: list[str] = []
    input_tokens: int | None = None
    output_tokens: int | None = None
    try:
        for chunk in chunks:
            try:
                delta = extract_delta_text(chunk)
                if delta:
                    accumulated.append(delta)
                usage = extract_usage(chunk)
                if usage is not None:
                    # Merge, don't overwrite: providers spread usage across multiple
                    # events (e.g. Anthropic's input_tokens on message_start,
                    # output_tokens on a later message_delta) — a later event with only
                    # output_tokens must not null out an input_tokens seen earlier.
                    new_input, new_output = usage
                    if new_input is not None:
                        input_tokens = new_input
                    if new_output is not None:
                        output_tokens = new_output
            except Exception:
                logger.exception("spanscope: failed to process stream chunk")
            yield chunk
    finally:
        safe_close_span_success(
            span_ctx, span, "".join(accumulated) or None, input_tokens, output_tokens
        )
