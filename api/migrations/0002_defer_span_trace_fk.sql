-- spans_trace_id_fkey (0001_init.sql) was IMMEDIATE — checked per-row, not at commit.
-- That breaks Phase 5's ingest order: a trace's aggregate row is computed FROM its
-- spans (queries.py), so spans must be insertable before the trace row exists, within
-- the same transaction. Phase 1 already solved this exact problem for the
-- self-referencing parent_span_id FK; this applies the identical DEFERRABLE fix to
-- trace_id. Idempotent: DROP IF EXISTS + re-ADD converges to the same state on rerun.

ALTER TABLE spans DROP CONSTRAINT IF EXISTS spans_trace_id_fkey;

ALTER TABLE spans ADD CONSTRAINT spans_trace_id_fkey
    FOREIGN KEY (trace_id) REFERENCES traces(id) ON DELETE CASCADE
    DEFERRABLE INITIALLY DEFERRED;
