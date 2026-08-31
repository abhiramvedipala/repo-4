-- Phase 1: traces + spans. Idempotent (IF NOT EXISTS everywhere) so re-running this file
-- after a partial apply, or after `make migrate` runs twice, is a safe no-op.

CREATE TABLE IF NOT EXISTS traces (
    id              TEXT PRIMARY KEY,       -- OTel trace_id: 32 lowercase hex chars (16 bytes).
                                             -- TEXT not UUID: OTel hex IDs don't carry RFC4122
                                             -- version/variant bits, and using the native OTel
                                             -- encoding now avoids a format migration in Phase 4.
    name            TEXT NOT NULL,          -- root operation name, e.g. "POST /chat"
    service         TEXT NOT NULL,          -- which instrumented app produced this trace
    root_span_id    TEXT,                   -- soft reference to spans.id (the span with no
                                             -- parent). Not a FK: traces and spans would need
                                             -- to reference each other circularly, which forces
                                             -- an insert order neither table can satisfy alone.
                                             -- Resolved by application code (Phase 5 ingest).
    start_time      TIMESTAMPTZ NOT NULL,
    end_time        TIMESTAMPTZ,            -- nullable: a trace can be ingested before every
                                             -- span in it has finished.
    duration_ms     INTEGER GENERATED ALWAYS AS (
                        CASE WHEN end_time IS NOT NULL
                             THEN ROUND(EXTRACT(EPOCH FROM (end_time - start_time)) * 1000)::INTEGER
                        END
                    ) STORED,               -- derived, never drifts from start/end. Backs the
                                             -- "min latency" filter on GET /v1/traces.
    status          TEXT NOT NULL DEFAULT 'unset'
                        CHECK (status IN ('ok', 'error', 'unset')),  -- OTel span status, rolled
                                             -- up: 'error' if any span in the trace errored.
    total_tokens    INTEGER NOT NULL DEFAULT 0 CHECK (total_tokens >= 0),
    total_cost_usd  NUMERIC(12, 6) NOT NULL DEFAULT 0 CHECK (total_cost_usd >= 0),
                                             -- NUMERIC not FLOAT: costs must add up exactly,
                                             -- no binary-float rounding drift over many spans.
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT traces_end_after_start CHECK (end_time IS NULL OR end_time >= start_time)
);

CREATE TABLE IF NOT EXISTS spans (
    id               TEXT PRIMARY KEY,      -- OTel span_id: 16 lowercase hex chars (8 bytes).
    trace_id         TEXT NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
    parent_span_id   TEXT REFERENCES spans(id) ON DELETE CASCADE
                        DEFERRABLE INITIALLY DEFERRED,  -- a batch insert writes a whole span
                                             -- tree in one transaction; DEFERRED checks the FK
                                             -- at COMMIT instead of per-row, so child rows can
                                             -- land before their parent within that transaction.
    name             TEXT NOT NULL,         -- e.g. "openai.chat.completions"
    kind             TEXT NOT NULL DEFAULT 'internal'
                        CHECK (kind IN ('internal', 'client', 'server', 'producer', 'consumer')),
    provider         TEXT,                  -- 'openai' | 'anthropic' | NULL for non-LLM spans
    model            TEXT,                  -- 'gpt-4o' etc., NULL for non-LLM spans
    start_time       TIMESTAMPTZ NOT NULL,
    end_time         TIMESTAMPTZ NOT NULL,
    latency_ms       INTEGER GENERATED ALWAYS AS (
                        ROUND(EXTRACT(EPOCH FROM (end_time - start_time)) * 1000)::INTEGER
                     ) STORED,               -- same derived-column reasoning as traces.duration_ms
    input_tokens     INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
    output_tokens    INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
    cost_usd         NUMERIC(12, 6) CHECK (cost_usd IS NULL OR cost_usd >= 0),
    status           TEXT NOT NULL DEFAULT 'unset'
                        CHECK (status IN ('ok', 'error', 'unset')),
    error_type       TEXT,
    error_message    TEXT,
    prompt           TEXT,
    completion       TEXT,
    attributes       JSONB NOT NULL DEFAULT '{}'::jsonb,  -- escape hatch for OTel gen_ai.* /
                                             -- custom attributes that don't warrant their own
                                             -- column; avoids a migration per new attribute.
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT spans_end_after_start CHECK (end_time >= start_time)
);

-- Postgres does NOT auto-index foreign key columns (unlike e.g. MySQL InnoDB). Without this,
-- fetching a trace's full span tree (GET /v1/traces/{id}) — and every FK check on spans
-- insert/delete — seq-scans the whole spans table. This is the single most-hit index here.
CREATE INDEX IF NOT EXISTS idx_spans_trace_id ON spans (trace_id);

-- Same reasoning, for walking the tree from a span down to its children (waterfall view).
CREATE INDEX IF NOT EXISTS idx_spans_parent_span_id ON spans (parent_span_id);

-- GET /v1/traces filtered by service + time range, most-recent-first: one composite index
-- scan instead of a filter pass followed by a separate sort.
CREATE INDEX IF NOT EXISTS idx_traces_service_start_time ON traces (service, start_time);

-- Same shape, for the status filter (e.g. "error traces in the last 24h") and the dashboard's
-- error-rate widget.
CREATE INDEX IF NOT EXISTS idx_traces_status_start_time ON traces (status, start_time);

-- Bare time-range scan for the common case with no service/status filter at all (the
-- overview page's default "last 24h" query). A btree on start_time serves ORDER BY DESC
-- scans just as well as ASC — no need for a second descending index.
CREATE INDEX IF NOT EXISTS idx_traces_start_time ON traces (start_time);

-- Backs GET /v1/traces' min-latency filter (`WHERE duration_ms >= $1`).
CREATE INDEX IF NOT EXISTS idx_traces_duration_ms ON traces (duration_ms);

-- Backs the model filter and /v1/metrics/summary's "calls by model" grouping. Partial: most
-- spans in a typical trace are plain internal spans with model IS NULL, and only the LLM-call
-- spans are ever queried by model, so indexing just the non-null rows keeps this small.
CREATE INDEX IF NOT EXISTS idx_spans_model_start_time ON spans (model, start_time)
    WHERE model IS NOT NULL;

-- Error-rate queries and the "show me the failing spans" drill-down only ever touch the
-- error subset, which should be a small fraction of rows — a partial index keeps that lookup
-- cheap without paying for a full index over a low-cardinality column ('ok'/'error'/'unset',
-- where 'ok' dominates and a full index would be nearly as large as the table itself).
CREATE INDEX IF NOT EXISTS idx_spans_status_error ON spans (status) WHERE status = 'error';
