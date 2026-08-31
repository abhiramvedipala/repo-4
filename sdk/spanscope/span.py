"""Span data model. No LLM-specific fields yet — Phase 3 extends this with
provider/model/token/cost fields once there's an actual LLM call to capture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

# OTel's own attribute-value union (str/int/float/bool, no arbitrary objects) — narrower and
# more honest than `Any`, since these values eventually serialize into spans.attributes JSONB.
AttributeValue = str | int | float | bool


class SpanKind(StrEnum):
    """Mirrors the `kind` CHECK constraint on the spans table (api/migrations/0001_init.sql)."""

    INTERNAL = "internal"
    CLIENT = "client"
    SERVER = "server"
    PRODUCER = "producer"
    CONSUMER = "consumer"


class SpanStatus(StrEnum):
    """Mirrors the `status` CHECK constraint on both traces and spans."""

    UNSET = "unset"
    OK = "ok"
    ERROR = "error"


@dataclass
class Span:
    span_id: str
    trace_id: str
    parent_span_id: str | None
    name: str
    kind: SpanKind
    start_time: datetime
    end_time: datetime | None = None
    status: SpanStatus = SpanStatus.UNSET
    error_type: str | None = None
    error_message: str | None = None
    attributes: dict[str, AttributeValue] = field(default_factory=dict)

    def set_attribute(self, key: str, value: AttributeValue) -> None:
        self.attributes[key] = value

    def set_status(self, status: SpanStatus) -> None:
        self.status = status

    @property
    def latency_ms(self) -> float | None:
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time).total_seconds() * 1000
