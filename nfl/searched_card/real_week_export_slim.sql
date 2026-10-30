-- nfl/searched_card/real_week_export_slim.sql
--
-- STAGE 1e DELIVERABLE (Task 2) -- design-only, READ-ONLY SQL. A slimmer
-- sibling of real_week_export.sql (Stage 1d, Task 1): same real tables
-- (nfl_stub_weeks / nfl_price_history), same real joins and filters, but
-- selecting only the 12 columns actually required to run
-- evidence_price_classifier.classify_evidence_vs_price and review its
-- output on real favorites -- not the full 39-field citable-fields set.
-- Nothing in this repo executes this file; it is meant to be run BY HAND
-- against the real tastypickems Supabase database.
--
-- NO PLACEHOLDERS: :season / :week / :as_of_timestamp from
-- real_week_export.sql are hardcoded below to real literal values
-- (season=2026, week=7, as_of_timestamp='2026-10-25 17:00:00+00') so this
-- can be copy-pasted and run exactly as-is, with nothing left to bind.
--
-- SCHEMA GROUNDING -- same as real_week_export.sql (see that file for the
-- full citations): nfl_stub_weeks's only typed columns are id, player_id,
-- season, week, posteam, position_group, td_opportunity, role_momentum,
-- situation, market_value_score, tpe_score, reconciled, extra (jsonb),
-- created_at, updated_at -- confirmed from its real CREATE TABLE
-- (supabase/migrations/20260901000000_create_nfl_stub_weeks.sql in the
-- app repo, read-only). evidence_quality is NOT a typed column -- it is
-- read from extra->>'evidence_quality', confirmed from the real pipeline
-- write path (nfl/scripts/stub_store.py's STUB_WEEKS_TYPED_COLUMNS list
-- and nfl/scoring.py's score_evidence_quality). Same UNKNOWN as
-- real_week_export.sql's own UNKNOWN #2: the exact extra->>'...' key
-- spelling is source-code-grounded, not confirmed by a live query --
-- run `SELECT extra FROM nfl_stub_weeks LIMIT 1` once to eyeball it
-- before trusting this at scale.
--
-- READ-ONLY: a single SELECT statement (the WITH clause below is a
-- read-only CTE, not a write) -- no INSERT/UPDATE/DELETE/DDL anywhere in
-- this file.
--
-- WHY week IS NOT NULL (kept from real_week_export.sql): nfl_price_
-- history.week is NULLABLE in its own CREATE TABLE (resolved per-row via
-- a schedule lookup that can return NULL independently of player
-- matching -- see nfl/api/index.py's _week_lookup_for_season, cited in
-- real_week_export.sql and in pool_size_report.md's Stage 1c, Task 4
-- note). An exact `week = 7` predicate already excludes NULLs
-- incidentally; this file states it explicitly anyway, for the same
-- defensive reason real_week_export.sql does.

WITH latest_price AS (
  SELECT DISTINCT ON (player_id)
    player_id,
    player_name_raw,
    consensus_price_american,
    consensus_implied_probability,
    poll_timestamp
  FROM nfl_price_history
  WHERE season = 2026
    AND week = 7
    AND week IS NOT NULL
    AND matched = true
    AND player_id IS NOT NULL
    AND poll_timestamp <= '2026-10-25 17:00:00+00'
  ORDER BY player_id, poll_timestamp DESC
)
SELECT
  s.player_id,
  COALESCE(s.extra->>'player_name', lp.player_name_raw) AS player_name,
  s.posteam,
  s.position_group,
  s.td_opportunity,
  s.role_momentum,
  s.situation,
  s.extra->>'evidence_quality' AS evidence_quality,
  s.tpe_score,
  s.market_value_score,
  lp.consensus_price_american,
  lp.consensus_implied_probability
FROM nfl_stub_weeks s
LEFT JOIN latest_price lp ON lp.player_id = s.player_id
WHERE s.season = 2026
  AND s.week = 7
  AND s.reconciled = false
ORDER BY s.player_id;
