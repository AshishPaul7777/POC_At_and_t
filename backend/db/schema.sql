-- =============================================================================
-- Salesforce Org Cleanup Analyzer - schema
--
-- Two design rules drive most of what follows:
--
--   1. Negative evidence is first-class.  Every collector writes a row EVERY
--      time, including when it finds nothing.  "We searched here and found
--      zero" must be distinguishable from "we never checked" - that
--      distinction is the entire product.
--
--   2. A component may only be called UNUSED when every required collector
--      completed successfully.  Absence of evidence is never evidence of
--      absence.  evidence_gap rows are what enforce this.
-- =============================================================================

-- Extensions are installed by bootstrap.sql as superuser; this is a no-op guard
-- so the file stays runnable standalone against a database that already has them.
CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- fuzzy fallback matching
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ---------------------------------------------------------------------------
-- Vocabularies
-- ---------------------------------------------------------------------------
CREATE TYPE run_state AS ENUM (
  'QUEUED','RUNNING','PAUSED','AWAITING_BUDGET',
  'SUCCEEDED','DEGRADED','FAILED','CANCELLED');

-- DEGRADED is load-bearing: a stage that partially failed is neither a success
-- nor a total failure, and the difference changes downstream verdicts.
CREATE TYPE stage_state AS ENUM (
  'PENDING','RUNNING','SUCCEEDED','DEGRADED','FAILED','SKIPPED','PARKED');

CREATE TYPE task_state AS ENUM (
  'PENDING','RESERVED','RUNNING','SUCCEEDED','FAILED','SKIPPED');

CREATE TYPE component_type AS ENUM (
  'CustomObject','StandardObject','CustomField',
  'ApexClass','ApexTrigger','ApexMethod',
  'LightningComponentBundle','AuraDefinitionBundle');

CREATE TYPE verdict AS ENUM (
  'USED','UNUSED','NEEDS_REVIEW','OUT_OF_SCOPE');

-- A: deleting it breaks a deploy or a runtime path.
-- B: the org actually did something with it.
-- C: consistent with use, insufficient to prove it.
-- D: not evidence at all - an uncertainty flag that SUPPRESSES an UNUSED verdict.
CREATE TYPE evidence_tier AS ENUM ('A','B','C','D');

-- Deliberately not USED/UNUSED: a single collector is not entitled to a verdict.
CREATE TYPE collector_result AS ENUM (
  'EVIDENCE_OF_USE','NO_EVIDENCE_FOUND','INCONCLUSIVE','NOT_APPLICABLE','FAILED');

CREATE TYPE collector_status AS ENUM ('OK','ERROR','UNAVAILABLE','SKIPPED','PARTIAL');

-- ---------------------------------------------------------------------------
-- Run control
-- ---------------------------------------------------------------------------
CREATE TABLE runs (
  id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  org_id            TEXT        NOT NULL,
  org_alias         TEXT,
  org_edition       TEXT,
  api_version       TEXT        NOT NULL,
  state             run_state   NOT NULL DEFAULT 'QUEUED',
  config            JSONB       NOT NULL DEFAULT '{}'::jsonb,
  budget_plan       JSONB,             -- pre-flight cost estimate shown pre-run
  -- Provenance: which tool versions produced this run. Without these a result
  -- is not reproducible.
  tool_versions     JSONB,             -- {"sf":"2.146.3","apex_parser":"4.3",...}
  -- The API user the analysis ran as. Recorded because the analysis can only
  -- see what this user can see; a restricted user silently narrows coverage.
  run_as_username   TEXT,
  -- Gapless per-run event counter. Incremented by ONE writer coroutine in the
  -- same transaction as the event insert. BIGSERIAL cannot be used here: under
  -- concurrency it produces gaps and out-of-order commits, and a reader polling
  -- "seq > last_seen" would skip an event permanently.
  last_seq          BIGINT      NOT NULL DEFAULT 0,
  started_at        TIMESTAMPTZ,
  finished_at       TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by        TEXT,
  cancelled_by      TEXT,
  cancel_reason     TEXT,
  error             JSONB
);

-- At most one live run per org. Two workers racing on one run is a distributed
-- consensus problem we decline to have.
CREATE UNIQUE INDEX one_active_run_per_org ON runs (org_id)
  WHERE state IN ('QUEUED','RUNNING','PAUSED','AWAITING_BUDGET');
CREATE INDEX runs_org_created_idx ON runs (org_id, created_at DESC);

CREATE TABLE stages (
  id              BIGSERIAL PRIMARY KEY,
  run_id          UUID        NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  key             TEXT        NOT NULL,      -- 'probe.data_population'
  title           TEXT        NOT NULL,
  depends_on      TEXT[]      NOT NULL DEFAULT '{}',
  state           stage_state NOT NULL DEFAULT 'PENDING',
  ordinal         INT         NOT NULL DEFAULT 0,
  shards_total    INT,
  shards_done     INT         NOT NULL DEFAULT 0,
  shards_failed   INT         NOT NULL DEFAULT 0,
  checkpoint      JSONB,                     -- resume point, e.g. {"id_cursor":"00N..."}
  api_calls_used  INT         NOT NULL DEFAULT 0,
  skip_reason     TEXT,                      -- shown in the UI; never a silent skip
  started_at      TIMESTAMPTZ,
  finished_at     TIMESTAMPTZ,
  error           JSONB,
  UNIQUE (run_id, key)
);
CREATE INDEX stages_run_idx ON stages (run_id, ordinal);

CREATE TABLE tasks (
  id              BIGSERIAL  PRIMARY KEY,
  stage_id        BIGINT     NOT NULL REFERENCES stages(id) ON DELETE CASCADE,
  shard_key       TEXT       NOT NULL,       -- 'Account' | 'CustomField:0-2000'
  state           task_state NOT NULL DEFAULT 'PENDING',
  attempt         INT        NOT NULL DEFAULT 0,
  input           JSONB,
  output_summary  JSONB,
  api_calls_used  INT        NOT NULL DEFAULT 0,
  started_at      TIMESTAMPTZ,
  finished_at     TIMESTAMPTZ,
  error           JSONB,
  -- Idempotency: re-running a stage upserts and skips already-SUCCEEDED shards.
  UNIQUE (stage_id, shard_key)
);
CREATE INDEX tasks_stage_state_idx ON tasks (stage_id, state);

-- ---------------------------------------------------------------------------
-- Components and the metadata index
-- ---------------------------------------------------------------------------
CREATE TABLE components (
  id             BIGSERIAL      PRIMARY KEY,
  run_id         UUID           NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  ctype          component_type NOT NULL,
  api_name       TEXT           NOT NULL,     -- Customer_Order__c.Legacy_Code__c
  api_name_lc    TEXT GENERATED ALWAYS AS (lower(api_name)) STORED,
  label          TEXT,
  sf_id          TEXT,
  namespace      TEXT,                        -- non-NULL => managed => OUT_OF_SCOPE
  parent_id      BIGINT         REFERENCES components(id) ON DELETE CASCADE,
  parent_object  TEXT,
  in_scope       BOOLEAN        NOT NULL DEFAULT TRUE,
  out_of_scope_reason TEXT,
  source_path    TEXT,
  created_date   TIMESTAMPTZ,
  last_modified_date TIMESTAMPTZ,
  -- Type-specific: is_formula, is_external_id, track_history, business_status,
  -- modifiers, annotations, line span, data_type, key_prefix ...
  attrs          JSONB          NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (run_id, ctype, api_name)
);
CREATE INDEX components_run_type_idx ON components (run_id, ctype) WHERE in_scope;
CREATE INDEX components_name_lc_idx  ON components (run_id, api_name_lc);
CREATE INDEX components_parent_idx   ON components (parent_id);
CREATE INDEX components_attrs_idx    ON components USING GIN (attrs);

CREATE TABLE component_source (
  component_id BIGINT PRIMARY KEY REFERENCES components(id) ON DELETE CASCADE,
  body         TEXT,
  body_sha256  TEXT,
  line_count   INT
);

-- One component resolves to MANY textual forms. Getting this table wrong makes
-- every downstream verdict wrong: e.g. a field is written as Legacy_Code__c in
-- Apex, Customer_Order__c.Legacy_Code__c in a layout, Legacy_Code in report XML,
-- Customer_Order__c$Legacy_Code__c in report column refs, and 00N... in a
-- hardcoded button URL.
CREATE TABLE alias (
  id           BIGSERIAL PRIMARY KEY,
  run_id       UUID   NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  component_id BIGINT NOT NULL REFERENCES components(id) ON DELETE CASCADE,
  alias_lc     TEXT   NOT NULL,
  alias_kind   TEXT   NOT NULL,   -- api_name|qualified|relationship_name|bare_name
                                  -- |report_qualified|sf_id_15|sf_id_18|key_prefix|label
  generated_by TEXT   NOT NULL,   -- which rule produced it (auditability)
  UNIQUE (run_id, component_id, alias_lc, alias_kind)
);
-- The hot lookup path: given a token found in some file, which component is it?
CREATE INDEX alias_lookup_idx ON alias (run_id, alias_lc);

CREATE TABLE artifact (
  id            BIGSERIAL PRIMARY KEY,
  run_id        UUID    NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  metadata_type TEXT    NOT NULL,          -- Layout | Flow | Report | ApexClass ...
  member_name   TEXT    NOT NULL,
  file_path     TEXT,
  sha256        TEXT,
  parse_status  TEXT    NOT NULL DEFAULT 'OK',  -- OK | PARSE_FAILED | SKIPPED
  -- Consumer liveness. A reference from an INACTIVE flow or an obsolete version
  -- is Tier C, not Tier A - it cannot prove current use, but it blocks deletion
  -- because the consumer could be reactivated.
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  is_test       BOOLEAN NOT NULL DEFAULT FALSE,
  is_obsolete_version BOOLEAN NOT NULL DEFAULT FALSE,
  folder        TEXT,
  last_run_date     TIMESTAMPTZ,   -- Report.LastRunDate etc: recency weighting
  last_viewed_date  TIMESTAMPTZ,
  attrs         JSONB   NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (run_id, metadata_type, member_name)
);
CREATE INDEX artifact_run_type_idx ON artifact (run_id, metadata_type);

-- Exact, boundary-respecting token matches. Drives USED verdicts.
CREATE TABLE artifact_token (
  run_id      UUID   NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  artifact_id BIGINT NOT NULL REFERENCES artifact(id) ON DELETE CASCADE,
  token_lc    TEXT   NOT NULL,
  occurrences INT    NOT NULL DEFAULT 1,
  PRIMARY KEY (artifact_id, token_lc)
);
CREATE INDEX artifact_token_lookup_idx ON artifact_token (run_id, token_lc);

-- Every string literal from every source, matched against alias. This is the
-- single highest-value guard against false UNUSED: it catches dynamic access
-- like sObj.get('Legacy_Code__c'), Database.query('SELECT ...'),
-- Type.forName('MyClass') and Aura's component.get("c.myMethod") with no
-- semantic understanding whatsoever.
CREATE TABLE artifact_literal (
  id          BIGSERIAL PRIMARY KEY,
  run_id      UUID   NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  artifact_id BIGINT NOT NULL REFERENCES artifact(id) ON DELETE CASCADE,
  literal_lc  TEXT   NOT NULL,
  locator     JSONB                        -- {"file":"...","line":42}
);
CREATE INDEX artifact_literal_lookup_idx ON artifact_literal (run_id, literal_lc);

-- Full-text recall net for fragments and concatenation. Tier C only: it exists
-- to force NEEDS_REVIEW, never to prove use.
CREATE TABLE artifact_text (
  artifact_id BIGINT PRIMARY KEY REFERENCES artifact(id) ON DELETE CASCADE,
  run_id      UUID   NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  content     TEXT   NOT NULL
);
CREATE INDEX artifact_text_trgm_idx ON artifact_text USING GIN (content gin_trgm_ops);

CREATE TABLE reference_edges (
  id            BIGSERIAL PRIMARY KEY,
  run_id        UUID    NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  from_artifact_id BIGINT REFERENCES artifact(id) ON DELETE CASCADE,
  to_component_id  BIGINT NOT NULL REFERENCES components(id) ON DELETE CASCADE,
  tier          evidence_tier NOT NULL,
  match_kind    TEXT    NOT NULL,   -- ast_node|xml_element|merge_field|schema_import
                                    -- |string_literal|formula_ref|key_prefix_url
                                    -- |fragment|token_sweep|label_only
  collector_id  TEXT    NOT NULL,
  -- Denormalised from artifact so scoring does not need a join on the hot path.
  consumer_active  BOOLEAN NOT NULL DEFAULT TRUE,
  consumer_is_test BOOLEAN NOT NULL DEFAULT FALSE,
  locator       JSONB,
  snippet       TEXT,
  tags          TEXT[]  NOT NULL DEFAULT '{}',  -- e.g. {LAYOUT_ONLY}
  produced_by   BIGINT  REFERENCES tasks(id) ON DELETE SET NULL
);
CREATE INDEX ref_edges_to_idx   ON reference_edges (run_id, to_component_id, tier);
CREATE INDEX ref_edges_from_idx ON reference_edges (from_artifact_id);

-- ---------------------------------------------------------------------------
-- Evidence  (the heart of the system)
-- ---------------------------------------------------------------------------

-- One row per collector per scope, written EVERY time - hit or miss. This is
-- what powers "we looked in 27 places; here are all 27". Build and populate
-- this before writing the first scoring rule: retrofitted negative evidence is
-- always incomplete.
CREATE TABLE collector_run (
  id                BIGSERIAL PRIMARY KEY,
  run_id            UUID   NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  collector_id      TEXT   NOT NULL,     -- 'C09_reports'
  collector_family  TEXT   NOT NULL,     -- 'static_reference' | 'data_population' ...
  scope_key         TEXT   NOT NULL,     -- component type, object, or '*'
  status            collector_status NOT NULL,
  -- The literal query/XPath/regex used, surfaced verbatim in the UI and report.
  -- Without this, "0 hits" is an unfalsifiable claim.
  method            TEXT   NOT NULL,
  query_text        TEXT,
  artifacts_searched INT   NOT NULL DEFAULT 0,
  hits              INT    NOT NULL DEFAULT 0,
  unavailable_reason TEXT,               -- 'EventLogFile requires Shield'
  duration_ms       INT,
  api_calls_used    INT    NOT NULL DEFAULT 0,
  error_text        TEXT,
  ran_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (run_id, collector_id, scope_key)
);
CREATE INDEX collector_run_status_idx ON collector_run (run_id, status);

CREATE TABLE evidence (
  id            BIGSERIAL PRIMARY KEY,
  run_id        UUID   NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  component_id  BIGINT NOT NULL REFERENCES components(id) ON DELETE CASCADE,
  collector_id  TEXT   NOT NULL,
  result        collector_result NOT NULL,
  tier          evidence_tier,
  weight        REAL   NOT NULL DEFAULT 0,
  -- {"total":1284,"populated":0,"soql":"SELECT COUNT(Id), COUNT(F__c) ..."}
  payload       JSONB  NOT NULL DEFAULT '{}'::jsonb,
  produced_by   BIGINT REFERENCES tasks(id) ON DELETE SET NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (component_id, collector_id)
);
CREATE INDEX evidence_component_idx ON evidence (component_id, result);
CREATE INDEX evidence_payload_idx   ON evidence USING GIN (payload);

-- A collector that failed, was unavailable, or was skipped leaves a gap here.
-- The classifier MUST NOT emit UNUSED for a component with a gap against a
-- decisive collector. This is what makes failure safe instead of silent.
CREATE TABLE evidence_gaps (
  id           BIGSERIAL PRIMARY KEY,
  run_id       UUID   NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  component_id BIGINT NOT NULL REFERENCES components(id) ON DELETE CASCADE,
  collector_id TEXT   NOT NULL,
  stage_key    TEXT   NOT NULL,
  reason       TEXT   NOT NULL,   -- STAGE_FAILED|BUDGET_EXHAUSTED|UNAVAILABLE
                                  -- |TIMEOUT|OOM|SKIPPED_BY_CONFIG|TRUNCATED
  is_decisive  BOOLEAN NOT NULL DEFAULT TRUE,
  detail       TEXT,
  UNIQUE (component_id, collector_id)
);
CREATE INDEX evidence_gaps_run_idx ON evidence_gaps (run_id, component_id);

-- Tier-D signals. Not evidence; these SUPPRESS an UNUSED verdict.
CREATE TABLE uncertainty_flags (
  id           BIGSERIAL PRIMARY KEY,
  run_id       UUID   NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  component_id BIGINT NOT NULL REFERENCES components(id) ON DELETE CASCADE,
  code         TEXT   NOT NULL,   -- DYNAMIC_APEX_IN_SCOPE|PARSE_FAILED
                                  -- |DEPENDENCY_API_TRUNCATED|RECENTLY_CHANGED
                                  -- |EVENT_MONITORING_UNAVAILABLE|EXTERNAL_ID
  severity     TEXT   NOT NULL DEFAULT 'medium',
  detail       TEXT,
  source_ref   JSONB,             -- {"file":"OrderService.cls","line":44}
  UNIQUE (component_id, code)
);
CREATE INDEX uncertainty_run_idx ON uncertainty_flags (run_id, code);

-- ---------------------------------------------------------------------------
-- Classification
-- ---------------------------------------------------------------------------
CREATE TABLE classifications (
  id            BIGSERIAL PRIMARY KEY,
  run_id        UUID    NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  component_id  BIGINT  NOT NULL REFERENCES components(id) ON DELETE CASCADE,
  label         verdict NOT NULL,
  -- For sorting the review queue ONLY. No weighted sum is ever permitted to
  -- cross the threshold into UNUSED; that is decided by the rules alone.
  confidence    REAL    NOT NULL DEFAULT 0,
  reason_codes  TEXT[]  NOT NULL DEFAULT '{}',
  -- Ordered list of rules evaluated with their inputs, so the UI can render
  -- "how this was computed". An unexplained score is a score nobody acts on.
  rule_trace    JSONB   NOT NULL DEFAULT '[]'::jsonb,
  -- Signed per-collector contributions to the confidence number.
  score_breakdown JSONB NOT NULL DEFAULT '[]'::jsonb,
  completeness  JSONB   NOT NULL DEFAULT '{}'::jsonb,
  had_gaps      BOOLEAN NOT NULL DEFAULT FALSE,
  -- Set by the delete-rehearsal stage: Salesforce's own dependency checker
  -- refusing a delete is conclusive proof of use, and outranks everything else.
  platform_blocked BOOLEAN NOT NULL DEFAULT FALSE,
  platform_block_detail TEXT,
  decided_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (run_id, component_id)
);
CREATE INDEX classifications_label_idx ON classifications (run_id, label, confidence DESC);

CREATE TABLE llm_artifacts (
  id             BIGSERIAL PRIMARY KEY,
  run_id         UUID   NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  component_id   BIGINT NOT NULL REFERENCES components(id) ON DELETE CASCADE,
  provider       TEXT   NOT NULL,
  model          TEXT   NOT NULL,
  prompt_sha256  TEXT   NOT NULL,     -- dedupe/cache key
  business_purpose  TEXT,
  unused_rationale  TEXT,
  evidence_recap    TEXT,
  risk_note         TEXT,
  request        JSONB,               -- exact context sent, for "show prompt"
  response       JSONB,
  input_tokens   INT,
  output_tokens  INT,
  latency_ms     INT,
  status         TEXT   NOT NULL,     -- OK|REFUSED|ERROR|SKIPPED
  error_text     TEXT,
  generated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (run_id, component_id)
);

-- ---------------------------------------------------------------------------
-- Budget ledger and event log
-- ---------------------------------------------------------------------------
CREATE TABLE api_budget_ledger (
  id              BIGSERIAL PRIMARY KEY,
  run_id          UUID   NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
  call_class      TEXT   NOT NULL,   -- TOOLING_QUERY|REST_QUERY|COMPOSITE|LONG_RUNNING
  stage_key       TEXT,
  cost            INT    NOT NULL,
  subrequests     INT    NOT NULL DEFAULT 1,
  remaining_after INT,
  -- TRUE when the figure came from a Sforce-Limit-Info header rather than our
  -- own counter. The header is authoritative for absolute position but CANNOT
  -- be used to attribute cost to an individual call: the rolling 24h window
  -- releases calls while other clients consume them, so consecutive reads can
  -- move backwards (measured -2 on this org).
  reconciled      BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX budget_ledger_run_idx ON api_budget_ledger (run_id, ts);

CREATE TABLE events (
  run_id     UUID        NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  seq        BIGINT      NOT NULL,     -- gapless; see runs.last_seq
  ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
  etype      TEXT        NOT NULL,     -- 'stage.shard_batch', 'budget.tick', ...
  stage_key  TEXT,
  payload    JSONB       NOT NULL DEFAULT '{}'::jsonb,
  v          SMALLINT    NOT NULL DEFAULT 1,
  PRIMARY KEY (run_id, seq)
);
CREATE INDEX events_type_idx ON events (run_id, etype);

-- Allocate the next gapless sequence number, persist the event, and wake any
-- listening SSE connections -- all in one transaction.
--
-- Called ONLY by the single per-run writer coroutine.  Serialising through one
-- writer is what makes seq gapless: BIGSERIAL under concurrency yields gaps and
-- out-of-order commits, so a reader polling "seq > last_seen" would skip an
-- event permanently and replay would be silently lossy.
--
-- The NOTIFY payload carries only run_id and seq, never the event body.
-- Postgres caps a notification at 8000 bytes and an oversized payload raises at
-- COMMIT time, which would abort the run for the sake of a UI nicety.  Listeners
-- treat the notification purely as a wake-up and SELECT the rows they are
-- missing, which also means a dropped notification costs nothing -- the table
-- remains the single source of truth.
CREATE OR REPLACE FUNCTION append_event(
  p_run_id UUID, p_etype TEXT, p_stage_key TEXT, p_payload JSONB)
RETURNS BIGINT AS $$
DECLARE next_seq BIGINT;
BEGIN
  UPDATE runs SET last_seq = last_seq + 1
   WHERE id = p_run_id
  RETURNING last_seq INTO next_seq;

  IF next_seq IS NULL THEN
    RAISE EXCEPTION 'append_event: no such run %', p_run_id;
  END IF;

  INSERT INTO events (run_id, seq, etype, stage_key, payload)
  VALUES (p_run_id, next_seq, p_etype, p_stage_key, p_payload);

  PERFORM pg_notify('sfc_events',
                    json_build_object('run_id', p_run_id, 'seq', next_seq)::text);

  RETURN next_seq;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- Convenience view: the results-explorer row shape, including the per-collector
-- counts the evidence strip renders. Negative evidence is surfaced here on
-- purpose - it must be legible at row level, not only on the detail page.
-- ---------------------------------------------------------------------------
CREATE VIEW component_summary AS
SELECT c.id, c.run_id, c.ctype, c.api_name, c.label, c.parent_object,
       c.namespace, c.in_scope, c.last_modified_date,
       cl.label       AS verdict,
       cl.confidence,
       cl.reason_codes,
       cl.had_gaps,
       cl.platform_blocked,
       COUNT(*) FILTER (WHERE e.result = 'EVIDENCE_OF_USE')   AS collectors_hit,
       COUNT(*) FILTER (WHERE e.result = 'NO_EVIDENCE_FOUND') AS collectors_clean,
       COUNT(*) FILTER (WHERE e.result = 'INCONCLUSIVE')      AS collectors_unclear,
       COUNT(*) FILTER (WHERE e.result = 'NOT_APPLICABLE')    AS collectors_na,
       COUNT(DISTINCT g.collector_id)                         AS collector_gaps,
       COUNT(DISTINCT u.code)                                 AS flag_count,
       (l.component_id IS NOT NULL)                           AS has_llm_summary
  FROM components c
  LEFT JOIN classifications  cl ON cl.component_id = c.id
  LEFT JOIN evidence         e  ON e.component_id  = c.id
  LEFT JOIN evidence_gaps    g  ON g.component_id  = c.id
  LEFT JOIN uncertainty_flags u ON u.component_id  = c.id
  LEFT JOIN llm_artifacts    l  ON l.component_id  = c.id
 GROUP BY c.id, cl.label, cl.confidence, cl.reason_codes, cl.had_gaps,
          cl.platform_blocked, l.component_id;
