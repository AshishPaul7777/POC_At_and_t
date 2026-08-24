-- Per-org capability cache.
--
-- Everything in here was, at some point in development, a hardcoded constant
-- measured against one Developer Edition org. That is exactly the bug this table
-- fixes: API allowances differ by an order of magnitude between editions,
-- Event Monitoring is licensed separately, the Dependency API is Beta and may be
-- absent, and which objects answer on which endpoint is not identical
-- everywhere.
--
-- So capabilities are DISCOVERED per org and cached, never assumed. A stale or
-- missing row degrades to conservative defaults rather than to a wrong answer.

CREATE TABLE IF NOT EXISTS org_capabilities (
  org_id                TEXT PRIMARY KEY,
  alias                 TEXT,
  instance_url          TEXT NOT NULL,
  api_version           TEXT NOT NULL,
  edition               TEXT,
  org_name              TEXT,
  is_sandbox            BOOLEAN,

  -- API budget, read from the org rather than inferred from edition.
  api_limit_max         INT,
  api_limit_remaining   INT,

  -- Measured, not assumed. A composite/batch does NOT cost one API call; the
  -- per-subrequest cost is real and may vary, so each org gets its own figure.
  composite_compression REAL,
  composite_max_queries INT,

  -- Feature availability. NULL means "not yet probed", which is distinct from
  -- false ("probed, genuinely unavailable") - and that distinction decides
  -- whether a collector reports NOT_APPLICABLE or is simply missing.
  has_event_monitoring  BOOLEAN,
  has_dependency_api    BOOLEAN,
  dependency_api_types  TEXT[],
  has_field_history     BOOLEAN,
  has_code_coverage     BOOLEAN,

  -- Per-org routing corrections, discovered by probing. Shape:
  -- {"CustomPermission": "data", "SomeObject": "tooling"}
  routing_overrides     JSONB NOT NULL DEFAULT '{}'::jsonb,
  -- Objects this org cannot query at all, so collectors over them report
  -- UNAVAILABLE instead of a misleading zero.
  unavailable_objects   TEXT[] NOT NULL DEFAULT '{}',

  -- The identity the analysis runs as. Recorded because coverage is bounded by
  -- this user's permissions, and a restricted user looks exactly like a set of
  -- genuinely-unused components.
  run_as_username       TEXT,
  run_as_is_admin       BOOLEAN,

  probed_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  probe_api_cost        INT,
  probe_notes           JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS org_capabilities_alias_idx ON org_capabilities (alias);
