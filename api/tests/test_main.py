from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ingest_accepts_otlp_protobuf(otlp_request_bytes: bytes) -> None:
    response = client.post(
        "/v1/traces",
        content=otlp_request_bytes,
        headers={"content-type": "application/x-protobuf"},
    )
    assert response.status_code == 200
    assert response.json() == {"received": 1}


def test_ingest_accepts_json() -> None:
    payload = {
        "spans": [
            {
                "span_id": "02" * 8,
                "trace_id": "01" * 16,
                "name": "openai.chat.completions.create",
                "kind": "client",
                "start_time": "2023-11-14T22:13:20+00:00",
                "end_time": "2023-11-14T22:13:20.500000+00:00",
            }
        ]
    }
    response = client.post("/v1/traces", json=payload)  # httpx sets application/json
    assert response.status_code == 200
    assert response.json() == {"received": 1}


def test_ingest_rejects_unsupported_content_type() -> None:
    response = client.post(
        "/v1/traces", content=b"whatever", headers={"content-type": "text/plain"}
    )
    assert response.status_code == 415


def test_ingest_returns_400_on_malformed_otlp_protobuf() -> None:
    response = client.post(
        "/v1/traces",
        content=b"not valid protobuf",
        headers={"content-type": "application/x-protobuf"},
    )
    assert response.status_code == 400


def test_ingest_returns_400_on_malformed_json() -> None:
    response = client.post(
        "/v1/traces",
        content=b"{not valid json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400
