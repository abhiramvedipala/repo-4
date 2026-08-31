"""Instrument an Anthropic client: `client.messages.create(...)` becomes a span with
zero change to call sites. Same duck-typing approach as the OpenAI wrapper — see
spanscope/integrations/openai.py and _common.py for the shared reasoning.
"""

from __future__ import annotations

import functools
import json
from typing import TYPE_CHECKING, Any

from spanscope.integrations._common import (
    safe_close_span_error,
    safe_close_span_success,
    safe_start_span,
    wrap_stream,
)
from spanscope.tracer import Tracer

if TYPE_CHECKING:
    from anthropic import Anthropic


def instrument_anthropic(client: Anthropic, tracer: Tracer) -> Anthropic:
    original_create = client.messages.create
    if getattr(original_create, "_spanscope_wrapped", False):
        return client

    @functools.wraps(original_create)
    def traced_create(*args: Any, **kwargs: Any) -> Any:
        model = kwargs.get("model", "unknown")
        stream = bool(kwargs.get("stream", False))
        prompt = _serialize_messages(kwargs.get("messages"))

        span_ctx, span = safe_start_span(
            tracer, "anthropic.messages.create", "anthropic", model, prompt
        )

        try:
            response = original_create(*args, **kwargs)
        except Exception as exc:
            safe_close_span_error(span_ctx, exc)
            raise

        if stream:
            return wrap_stream(response, span_ctx, span, _extract_delta_text, _extract_usage)

        completion = _extract_completion_text(response)
        usage = _extract_usage(response)
        input_tokens, output_tokens = usage if usage else (None, None)
        safe_close_span_success(span_ctx, span, completion, input_tokens, output_tokens)
        return response

    traced_create._spanscope_wrapped = True  # type: ignore[attr-defined]
    client.messages.create = traced_create  # type: ignore[method-assign]
    return client


def _serialize_messages(messages: Any) -> str | None:
    if not messages:
        return None
    try:
        return json.dumps(messages)
    except (TypeError, ValueError):
        return str(messages)


def _extract_completion_text(response: Any) -> str | None:
    content = getattr(response, "content", None)
    if not content:
        return None
    text_parts = [t for block in content if (t := getattr(block, "text", None))]
    return "".join(text_parts) if text_parts else None


def _extract_delta_text(event: Any) -> str | None:
    # Anthropic streams typed events; only content_block_delta events carry text.
    if getattr(event, "type", None) != "content_block_delta":
        return None
    delta = getattr(event, "delta", None)
    return getattr(delta, "text", None) if delta else None


def _extract_usage(obj: Any) -> tuple[int | None, int | None] | None:
    # Non-streaming responses carry usage directly; streaming events carry it either
    # directly (message_delta) or nested under .message (message_start) — usage is
    # split across events, wrap_stream() merges rather than overwrites for that reason.
    usage = getattr(obj, "usage", None)
    if usage is None:
        message = getattr(obj, "message", None)
        usage = getattr(message, "usage", None) if message else None
    if usage is None:
        return None
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    if input_tokens is None and output_tokens is None:
        return None
    return input_tokens, output_tokens
