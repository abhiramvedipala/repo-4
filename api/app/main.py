"""Minimal ingest endpoint proving both OTLP/HTTP protobuf and SpanScope's JSON format
parse into the same SpanRecord shape (see otlp.py). No persistence yet — POST /v1/traces
here counts and discards. Phase 5 replaces the body of ingest_traces with real Pydantic
validation and a Postgres batch insert of the same SpanRecords, keeping otlp.py as-is.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from app.otlp import parse_json, parse_otlp_protobuf

app = FastAPI(title="SpanScope Ingest API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/traces")
async def ingest_traces(request: Request) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    body = await request.body()

    try:
        if "application/x-protobuf" in content_type:
            records = parse_otlp_protobuf(body)
        elif "application/json" in content_type:
            records = parse_json(json.loads(body))
        else:
            raise HTTPException(
                status_code=415,
                detail=(
                    f"unsupported content-type: {content_type!r}. "
                    "use application/x-protobuf (OTLP) or application/json."
                ),
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"failed to parse request body: {exc}"
        ) from exc

    # Phase 5: validate + batch-insert `records` into Postgres instead of discarding them.
    return {"received": len(records)}
