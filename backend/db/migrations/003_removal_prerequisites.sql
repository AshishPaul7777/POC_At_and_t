-- Separate "is it used?" from "what must happen before it can be deleted?".
--
-- These are different questions and conflating them made the output worse. A
-- field that sits on a layout but that nothing references functionally, holding
-- no data, IS unused in the only sense anyone cares about. Layout presence does
-- not make it used - it makes it a two-step removal.
--
-- Previously such fields were held at NEEDS_REVIEW, which buried the finding and
-- forced whoever read the report to re-derive the distinction for each one. Now
-- the verdict answers the business question and prerequisites answer the
-- mechanical one, so the report can say: "delete these six, and here is the
-- preparation each needs."
--
-- The safety property is unchanged. Nothing is presented as one-click safe: a
-- component with prerequisites cannot be deleted until they are cleared, and
-- the delete rehearsal (validate-only destructive deploy) confirms that
-- independently against the org.

ALTER TABLE classifications
  ADD COLUMN IF NOT EXISTS removal_prerequisites JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN classifications.removal_prerequisites IS
  'Ordered steps required before this component can be deleted. Each entry: '
  '{step, reason, targets[], blocking}. blocking=true means a deploy will fail '
  'until the step is done.';

-- Fast filter for "deletable with no preparation at all".
CREATE INDEX IF NOT EXISTS classifications_ready_idx
  ON classifications (run_id, label)
  WHERE removal_prerequisites = '[]'::jsonb;
