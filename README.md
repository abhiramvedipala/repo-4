# SpanScope -- a good project for improiving my ai skills as well aloing with SDE skiolsl 


A self-hostable observability layer for LLM applications. Instrument your app with a small
Python SDK, SpanScope captures every LLM call as an OpenTelemetry-compatible span (latency,
tokens, cost, model, provider, prompt/response, errors, parent-child relationships), ships it
to an ingest API, stores it in Postgres, and surfaces it in a Next.js dashboard — trace
filtering, a waterfall view of a single trace, and aggregate metrics.

**Status:** Phase 5 (ingest + query API) — the API now persists to real Postgres:
`POST /v1/traces` (batch, upserted, idempotent on retry), `GET /v1/traces` (paginated,
filtered), `GET /v1/traces/{id}` (full span tree), `GET /v1/metrics/summary` (p50/p95/p99
latency, cost, error rate, calls by model, time-bucketed). No dashboard yet.

## Stack

| Layer | Tech |
|---|---|
| SDK | Python 3.11+, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http` |
| Ingest API | Python, FastAPI, Pydantic v2, `asyncpg` |
| Database | PostgreSQL 16 |
| Queue | Redis + a small worker |
| Dashboard | Next.js 15 (App Router), React 19, TypeScript strict, Tailwind |
| Charts | Recharts |
| Tests | pytest + httpx (Python), Vitest + Testing Library (TS) |
| Local dev | Docker Compose |
| Lint/format | ruff + mypy (Python), ESLint + Prettier (TS) |

## Repo layout

```
/
├── sdk/                  # Python package: spanscope
│   ├── spanscope/
│   └── tests/
├── api/                  # FastAPI ingest + query service
│   ├── app/
│   ├── migrations/
│   └── tests/
├── web/                  # Next.js 15 dashboard
│   ├── app/
│   ├── components/
│   ├── lib/
│   └── __tests__/
├── examples/             # demo app that generates real traces (Phase 8)
├── docker-compose.yml
├── .env.example
└── Makefile
```

## Getting started

```bash
cp .env.example .env
make install   # uv sync (sdk, api) + npm install (web)
make dev       # docker compose up: postgres + redis
make migrate   # applies api/migrations/*.sql
make test
make lint
```

There's no dashboard yet — that's Phase 6. This section grows as each phase does.
