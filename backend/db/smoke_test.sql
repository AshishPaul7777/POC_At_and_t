-- Verifies the schema ENFORCES the design invariants, rather than merely
-- describing them. Safe to re-run: everything is rolled back at the end.
BEGIN;

\echo '=== 1. gapless event sequence ==='
-- BIGSERIAL would produce gaps under concurrency; append_event must not.
INSERT INTO runs (id, org_id, org_alias, api_version, state, run_as_username)
VALUES ('11111111-1111-1111-1111-111111111111', '00DTEST', 'test', '67.0',
        'RUNNING', 'probe@example.com');

SELECT append_event('11111111-1111-1111-1111-111111111111', 'run.started',  NULL, '{}');
SELECT append_event('11111111-1111-1111-1111-111111111111', 'stage.started','S01', '{}');
SELECT append_event('11111111-1111-1111-1111-111111111111', 'budget.tick',  NULL, '{"remaining":9774}');

SELECT seq, etype FROM events
 WHERE run_id = '11111111-1111-1111-1111-111111111111' ORDER BY seq;

SELECT CASE
  WHEN (SELECT array_agg(seq ORDER BY seq) FROM events
         WHERE run_id='11111111-1111-1111-1111-111111111111') = ARRAY[1::bigint,2,3]
  THEN 'PASS  seq is gapless 1,2,3'
  ELSE 'FAIL  seq has gaps' END AS result;

SELECT CASE WHEN last_seq = 3 THEN 'PASS  runs.last_seq tracks correctly'
            ELSE 'FAIL  runs.last_seq = ' || last_seq END AS result
  FROM runs WHERE id='11111111-1111-1111-1111-111111111111';

\echo ''
\echo '=== 2. only one active run per org ==='
-- Two workers racing on one run is a consensus problem we decline to have.
SAVEPOINT sp;
DO $$
BEGIN
  INSERT INTO runs (org_id, api_version, state)
  VALUES ('00DTEST', '67.0', 'RUNNING');
  RAISE NOTICE 'FAIL  a second active run was allowed';
EXCEPTION WHEN unique_violation THEN
  RAISE NOTICE 'PASS  second active run rejected by partial unique index';
END $$;
ROLLBACK TO SAVEPOINT sp;

-- ...but a *finished* run must not block a new one.
SAVEPOINT sp2;
DO $$
BEGIN
  INSERT INTO runs (org_id, api_version, state)
  VALUES ('00DTEST2', '67.0', 'SUCCEEDED');
  INSERT INTO runs (org_id, api_version, state)
  VALUES ('00DTEST2', '67.0', 'RUNNING');
  RAISE NOTICE 'PASS  a completed run does not block a new one';
EXCEPTION WHEN unique_violation THEN
  RAISE NOTICE 'FAIL  completed run wrongly blocks a new run';
END $$;
ROLLBACK TO SAVEPOINT sp2;

\echo ''
\echo '=== 3. evidence model: negative evidence and gaps are representable ==='
INSERT INTO components (id, run_id, ctype, api_name, label, parent_object)
VALUES (9001, '11111111-1111-1111-1111-111111111111', 'CustomField',
        'Customer_Order__c.Legacy_Code__c', 'Legacy Code', 'Customer_Order__c');

-- A collector that searched and found NOTHING. This row existing is the whole
-- point: it makes "we looked and found zero" distinct from "we never checked".
INSERT INTO evidence (run_id, component_id, collector_id, result, tier, payload)
VALUES ('11111111-1111-1111-1111-111111111111', 9001, 'C01_apex_ast',
        'NO_EVIDENCE_FOUND', 'C',
        '{"artifacts_searched":14,"hits":0,"method":"apex-parser AST"}'),
       ('11111111-1111-1111-1111-111111111111', 9001, 'C09_reports',
        'NO_EVIDENCE_FOUND', 'C',
        '{"artifacts_searched":30,"hits":0}'),
       ('11111111-1111-1111-1111-111111111111', 9001, 'C07_layout',
        'EVIDENCE_OF_USE', 'A', '{"hits":1}');

-- An unavailable collector leaves a gap, which must forbid an UNUSED verdict.
INSERT INTO evidence_gaps (run_id, component_id, collector_id, stage_key, reason, detail)
VALUES ('11111111-1111-1111-1111-111111111111', 9001, 'C21_event_monitoring',
        'probe.usage_signals', 'UNAVAILABLE',
        'EventLogFile returned 0 rows; Event Monitoring not licensed');

INSERT INTO uncertainty_flags (run_id, component_id, code, severity, detail)
VALUES ('11111111-1111-1111-1111-111111111111', 9001, 'DYNAMIC_APEX_IN_SCOPE',
        'high', 'OrderFulfillmentService.cls:44 uses sObj.put(dynamicName, v)');

INSERT INTO classifications (run_id, component_id, label, confidence,
                             reason_codes, had_gaps)
VALUES ('11111111-1111-1111-1111-111111111111', 9001, 'NEEDS_REVIEW', 62,
        ARRAY['DYNAMIC_APEX_IN_SCOPE'], TRUE);

\echo ''
\echo '=== 4. component_summary view (drives the results table) ==='
SELECT api_name, verdict, confidence,
       collectors_hit, collectors_clean, collector_gaps, flag_count
  FROM component_summary
 WHERE run_id = '11111111-1111-1111-1111-111111111111';

SELECT CASE WHEN collectors_clean = 2 AND collectors_hit = 1
             AND collector_gaps = 1 AND flag_count = 1
       THEN 'PASS  negative evidence, gaps and flags all surface in the view'
       ELSE 'FAIL  view counts wrong' END AS result
  FROM component_summary
 WHERE run_id = '11111111-1111-1111-1111-111111111111';

\echo ''
\echo '=== 5. cascade delete leaves no orphans ==='
DELETE FROM runs WHERE id = '11111111-1111-1111-1111-111111111111';
SELECT CASE WHEN (SELECT count(*) FROM evidence   WHERE component_id = 9001) = 0
             AND (SELECT count(*) FROM events     WHERE run_id = '11111111-1111-1111-1111-111111111111') = 0
             AND (SELECT count(*) FROM components WHERE id = 9001) = 0
       THEN 'PASS  run delete cascades cleanly'
       ELSE 'FAIL  orphan rows survived' END AS result;

ROLLBACK;
\echo ''
\echo 'All assertions evaluated; transaction rolled back (database unchanged).'
