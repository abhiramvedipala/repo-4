from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    # `with` triggers the real lifespan (pool creation against DATABASE_URL, set to
    # spanscope_test by conftest.py) and tears it down cleanly after each test.
    with TestClient(app) as c:
        yield c


def _span_payload(
    *,
    trace_id: str,
    span_id: str,
    parent_span_id: str | None = None,
    name: str = "op",
    kind: str = "client",
    service: str = "svc-a",
    model: str | None = "gpt-4o",
    status: str = "ok",
    start: datetime,
    latency_ms: int,
    cost_usd: float | None = 0.001,
) -> dict[str, Any]:
    end = start + timedelta(milliseconds=latency_ms)
    return {
        "service": service,
        "spans": [
            {
                "span_id": span_id,
                "trace_id": trace_id,
                "parent_span_id": parent_span_id,
                "name": name,
                "kind": kind,
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "status": status,
                "provider": "openai" if model else None,
                "model": model,
                "input_tokens": 10 if model else None,
                "output_tokens": 5 if model else None,
                "cost_usd": cost_usd,
            }
        ],
    }


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ingest_accepts_otlp_protobuf_and_persists(
    client: TestClient, otlp_request_bytes: bytes
) -> None:
    response = client.post(
        "/v1/traces",
        content=otlp_request_bytes,
        headers={"content-type": "application/x-protobuf"},
    )
    assert response.status_code == 200
    assert response.json() == {"received": 1}

    trace_id = "01" * 16
    detail = client.get(f"/v1/traces/{trace_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["trace"]["service"] == "test-service"
    assert body["spans"][0]["model"] == "gpt-4o"


def test_ingest_rejects_unsupported_content_type(client: TestClient) -> None:
    response = client.post(
        "/v1/traces", content=b"whatever", headers={"content-type": "text/plain"}
    )
    assert response.status_code == 415


def test_ingest_returns_400_on_malformed_otlp_protobuf(client: TestClient) -> None:
    response = client.post(
        "/v1/traces",
        content=b"not valid protobuf",
        headers={"content-type": "application/x-protobuf"},
    )
    assert response.status_code == 400


def test_ingest_returns_400_on_malformed_json(client: TestClient) -> None:
    response = client.post(
        "/v1/traces",
        content=b"{not valid json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400


def test_ingest_retry_is_idempotent_not_additive(client: TestClient) -> None:
    """Sending the same span twice (a client retry after a network blip) must not
    double the trace's total_cost_usd/total_tokens — proves ON CONFLICT DO UPDATE, and
    that trace aggregates are recomputed from source rather than incremented.
    """
    start = datetime(2024, 1, 1, tzinfo=UTC)
    payload = _span_payload(
        trace_id="aa" * 16, span_id="bb" * 8, start=start, latency_ms=100, cost_usd=0.5
    )

    client.post("/v1/traces", json=payload)
    client.post("/v1/traces", json=payload)  # exact same span_id, sent again

    detail = client.get(f"/v1/traces/{'aa' * 16}")
    assert detail.status_code == 200
    body = detail.json()
    assert len(body["spans"]) == 1  # not two rows
    assert body["trace"]["total_cost_usd"] == pytest.approx(0.5)  # not 1.0
    assert body["trace"]["total_tokens"] == 15  # not 30


def test_get_trace_404_for_unknown_id(client: TestClient) -> None:
    response = client.get(f"/v1/traces/{'ff' * 16}")
    assert response.status_code == 404


def test_get_trace_returns_full_span_tree(client: TestClient) -> None:
    trace_id = "cc" * 16
    start = datetime(2024, 1, 1, tzinfo=UTC)
    root = _span_payload(
        trace_id=trace_id, span_id="d0" * 8, name="root", start=start, latency_ms=500
    )
    child = _span_payload(
        trace_id=trace_id,
        span_id="d1" * 8,
        parent_span_id="d0" * 8,
        name="child",
        start=start + timedelta(milliseconds=50),
        latency_ms=200,
    )
    client.post("/v1/traces", json=root)
    client.post("/v1/traces", json=child)

    response = client.get(f"/v1/traces/{trace_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["trace"]["root_span_id"] == "d0" * 8
    assert body["trace"]["duration_ms"] == 500  # min(start) to max(end) across spans
    names_by_parent = {s["name"]: s["parent_span_id"] for s in body["spans"]}
    assert names_by_parent == {"root": None, "child": "d0" * 8}


def test_list_traces_filters_by_service_status_and_min_latency(client: TestClient) -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    client.post(
        "/v1/traces",
        json=_span_payload(
            trace_id="11" * 16,
            span_id="a1" * 8,
            service="svc-a",
            status="ok",
            start=start,
            latency_ms=100,
        ),
    )
    client.post(
        "/v1/traces",
        json=_span_payload(
            trace_id="22" * 16,
            span_id="a2" * 8,
            service="svc-b",
            status="error",
            start=start,
            latency_ms=900,
        ),
    )

    only_svc_a = client.get("/v1/traces", params={"service": "svc-a"}).json()
    assert [t["id"] for t in only_svc_a["traces"]] == ["11" * 16]

    only_errors = client.get("/v1/traces", params={"status": "error"}).json()
    assert [t["id"] for t in only_errors["traces"]] == ["22" * 16]

    slow_only = client.get("/v1/traces", params={"min_latency_ms": 500}).json()
    assert [t["id"] for t in slow_only["traces"]] == ["22" * 16]

    everything = client.get("/v1/traces").json()
    assert everything["total"] == 2
    assert len(everything["traces"]) == 2


def test_list_traces_pagination(client: TestClient) -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    for i in range(3):
        client.post(
            "/v1/traces",
            json=_span_payload(
                trace_id=f"{i:02x}" * 16,
                span_id=f"{i:02x}" * 8,
                start=start + timedelta(seconds=i),
                latency_ms=100,
            ),
        )

    page1 = client.get("/v1/traces", params={"limit": 2, "offset": 0}).json()
    page2 = client.get("/v1/traces", params={"limit": 2, "offset": 2}).json()
    assert page1["total"] == 3
    assert len(page1["traces"]) == 2
    assert len(page2["traces"]) == 1
    # newest-first ordering, no overlap between pages
    assert {t["id"] for t in page1["traces"]}.isdisjoint({t["id"] for t in page2["traces"]})


def test_metrics_summary_percentiles_and_calls_by_model(client: TestClient) -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    # Four spans, one trace each, latencies 100/200/300/400ms -> percentile_cont(0.5)
    # linearly interpolates to 250, percentile_cont(0.95) to 385. Hand-computable.
    for i, latency in enumerate([100, 200, 300, 400]):
        client.post(
            "/v1/traces",
            json=_span_payload(
                trace_id=f"{i:02x}" * 16,
                span_id=f"{i:02x}" * 8,
                model="gpt-4o",
                start=start + timedelta(seconds=i),
                latency_ms=latency,
                cost_usd=1.0,
            ),
        )
    # One error call, different model, outside the latency set above.
    client.post(
        "/v1/traces",
        json=_span_payload(
            trace_id="ee" * 16,
            span_id="ee" * 8,
            model="claude-3-5-sonnet-20241022",
            status="error",
            start=start + timedelta(seconds=10),
            latency_ms=50,
            cost_usd=2.0,
        ),
    )

    response = client.get(
        "/v1/metrics/summary",
        params={
            "start_time": start.isoformat(),
            "end_time": (start + timedelta(days=1)).isoformat(),
        },
    )
    assert response.status_code == 200
    body = response.json()
    # p50/p95 are over ALL spans in range, not filtered by model — the error span's
    # 50ms latency is part of this set too: sorted [50,100,200,300,400].
    # p50: position 0.5*4=2.0 -> exactly index 2 -> 200.
    # p95: position 0.95*4=3.8 -> interpolate index 3 (300) and 4 (400) -> 380.
    assert body["p50_latency_ms"] == pytest.approx(200)
    assert body["p95_latency_ms"] == pytest.approx(380)
    assert body["total_calls"] == 5
    assert body["error_rate"] == pytest.approx(1 / 5)
    assert body["total_cost_usd"] == pytest.approx(6.0)

    by_model = {row["model"]: row for row in body["calls_by_model"]}
    assert by_model["gpt-4o"]["calls"] == 4
    assert by_model["claude-3-5-sonnet-20241022"]["calls"] == 1
    assert len(body["buckets"]) >= 1


def test_metrics_summary_rejects_invalid_bucket(client: TestClient) -> None:
    response = client.get("/v1/metrics/summary", params={"bucket": "fortnight"})
    assert response.status_code == 422
