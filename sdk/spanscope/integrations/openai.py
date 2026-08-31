"""Instrument an OpenAI client: `client.chat.completions.create(...)` becomes a span
with zero change to call sites. Duck-typed against the response shape rather than
importing `openai` at runtime, so `spanscope` itself never depends on it — only the
type hints below need it, and only under TYPE_CHECKING.
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
    from openai import OpenAI


def instrument_openai(client: OpenAI, tracer: Tracer) -> OpenAI:
    """Patches `client.chat.completions.create` on this instance and returns the same
    client. Safe to call once per client; calling twice is a no-op on the second call.
    """
    original_create = client.chat.completions.create
    if getattr(original_create, "_spanscope_wrapped", False):
        return client

    @functools.wraps(original_create)
    def traced_create(*args: Any, **kwargs: Any) -> Any:
        model = kwargs.get("model", "unknown")
        stream = bool(kwargs.get("stream", False))
        prompt = _serialize_messages(kwargs.get("messages"))

        span_ctx, span = safe_start_span(
            tracer, "openai.chat.completions.create", "openai", model, prompt
        )

        try:
            response = original_create(*args, **kwargs)
        except Exception as exc:
            # The real OpenAI error always propagates — only our own span bookkeeping
            # is allowed to fail silently.
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
    client.chat.completions.create = traced_create  # type: ignore[method-assign]
    return client


def _serialize_messages(messages: Any) -> str | None:
    if not messages:
        return None
    try:
        return json.dumps(messages)
    except (TypeError, ValueError):
        return str(messages)


def _extract_completion_text(response: Any) -> str | None:
    choices = getattr(response, "choices", None)
    if not choices:
        return None
    message = getattr(choices[0], "message", None)
    return getattr(message, "content", None) if message else None


def _extract_delta_text(chunk: Any) -> str | None:
    choices = getattr(chunk, "choices", None)
    if not choices:
        return None
    delta = getattr(choices[0], "delta", None)
    return getattr(delta, "content", None) if delta else None


def _extract_usage(obj: Any) -> tuple[int | None, int | None] | None:
    usage = getattr(obj, "usage", None)
    if usage is None:
        return None
    return getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None)
