-- Chat agent: threads, messages, the tool trace, and proposed findings.
--
-- The important table here is agent_finding, and the important thing about it
-- is what it is NOT. It is not a verdict. The pipeline's credibility rests on
-- every verdict being reproducible from evidence by rules R0-R9, so the agent
-- is given no way to write `classifications`. It writes a proposal; a human
-- accepts it; accepting inserts an ordinary `evidence` row and re-runs the
-- classifier, which then re-decides on its own terms.
--
-- Tool calls are stored rather than treated as transient UI chrome. A claim the
-- agent makes is only as good as the searches behind it -- including the ones
-- that found nothing -- so the trace is part of the record, exactly as
-- collector_run is for the pipeline.
--
-- Deliberately absent: a token-level event log. Completed messages and tool
-- calls are durable; an in-flight reply is buffered in memory and replayed to a
-- reconnecting browser. A crash mid-reply loses only something re-runnable, and
-- the write volume of persisting every token buys nothing.

CREATE TABLE IF NOT EXISTS chat_thread (
  id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  -- A thread is usually about one run, but survives it being deleted: the
  -- conversation is still readable, it just loses its anchor.
  run_id      UUID        REFERENCES runs(id) ON DELETE SET NULL,
  title       TEXT        NOT NULL DEFAULT 'New conversation',
  state       TEXT        NOT NULL DEFAULT 'IDLE',   -- IDLE | RUNNING | ERROR
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chat_thread_recent_idx
  ON chat_thread (updated_at DESC);

CREATE TABLE IF NOT EXISTS chat_message (
  id            BIGSERIAL   PRIMARY KEY,
  thread_id     UUID        NOT NULL REFERENCES chat_thread(id) ON DELETE CASCADE,
  seq           INTEGER     NOT NULL,
  role          TEXT        NOT NULL,   -- user | assistant | system
  content       TEXT        NOT NULL DEFAULT '',
  input_tokens  INTEGER,
  output_tokens INTEGER,
  stop_reason   TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (thread_id, seq)
);
CREATE INDEX IF NOT EXISTS chat_message_thread_idx
  ON chat_message (thread_id, seq);

CREATE TABLE IF NOT EXISTS chat_tool_call (
  id          BIGSERIAL   PRIMARY KEY,
  message_id  BIGINT      NOT NULL REFERENCES chat_message(id) ON DELETE CASCADE,
  ordinal     INTEGER     NOT NULL DEFAULT 0,
  tool_name   TEXT        NOT NULL,
  args        JSONB       NOT NULL DEFAULT '{}'::jsonb,
  ok          BOOLEAN     NOT NULL DEFAULT TRUE,
  -- Summary rather than the whole payload: a grep over 700 files can return
  -- megabytes, and the point of keeping this is auditability, not caching.
  result      JSONB       NOT NULL DEFAULT '{}'::jsonb,
  duration_ms INTEGER,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chat_tool_call_message_idx
  ON chat_tool_call (message_id, ordinal);

CREATE TABLE IF NOT EXISTS agent_finding (
  id             BIGSERIAL   PRIMARY KEY,
  thread_id      UUID        NOT NULL REFERENCES chat_thread(id) ON DELETE CASCADE,
  run_id         UUID        NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  component_id   BIGINT      NOT NULL REFERENCES components(id) ON DELETE CASCADE,
  -- What the agent thinks the evidence supports. Intentionally a free-text
  -- suggestion and NOT the component_verdict enum: giving it the verdict type
  -- would invite code that copies it straight into classifications.
  recommendation TEXT        NOT NULL,
  rationale      TEXT        NOT NULL DEFAULT '',
  -- What the recommendation rests on: collector ids, file paths, queries. An
  -- uncited proposal is indistinguishable from a guess and must not be
  -- acceptable.
  citations      JSONB       NOT NULL DEFAULT '[]'::jsonb,
  status         TEXT        NOT NULL DEFAULT 'proposed',  -- proposed|accepted|rejected
  decided_by     TEXT,
  decided_at     TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT agent_finding_status_ck
    CHECK (status IN ('proposed', 'accepted', 'rejected'))
);
CREATE INDEX IF NOT EXISTS agent_finding_thread_idx
  ON agent_finding (thread_id);
-- One live proposal per component per thread: re-running triage should update
-- its suggestion, not stack duplicates for a reviewer to wade through.
CREATE UNIQUE INDEX IF NOT EXISTS agent_finding_open_uq
  ON agent_finding (thread_id, component_id)
  WHERE status = 'proposed';
