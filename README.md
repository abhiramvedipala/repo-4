# SpanScope

A self-hostable observability layer for LLM applications. Instrument your app with a small
Python SDK, SpanScope captures every LLM call as an OpenTelemetry-compatible span (latency,
tokens, cost, model, provider, prompt/response, errors, parent-child relationships), ships it
to an ingest API, stores it in Postgres, and surfaces it in a Next.js dashboard — trace
filtering, a waterfall view of a single trace, and aggregate metrics.

**Status:** Phase 3 (SDK LLM instrumentation) — Postgres schema, `Tracer`, and OpenAI/Anthropic
client wrappers (`spanscope.instrument_openai`, `spanscope.instrument_anthropic`) that
auto-capture model, tokens, cost, and errors with zero call-site changes. No ingest API or
dashboard yet.

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

There's no ingest API or dashboard yet — those land in Phases 5-6. This section grows as each
phase does.
