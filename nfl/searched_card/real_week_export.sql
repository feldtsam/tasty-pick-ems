-- nfl/searched_card/real_week_export.sql
--
-- STAGE 1d DELIVERABLE (Task 1) -- design-only, READ-ONLY SQL. Nothing in
-- this repo executes this file. It is meant to be run BY HAND, by a human
-- with real DB access, against the real tastypickems Supabase database, to
-- produce one CSV per (:season, :week) for run_real_week_check.py (Task 2,
-- this same directory) to classify offline, with evidence_price_classifier.
-- classify_evidence_vs_price never touched during this export -- this file
-- only reads nfl_stub_weeks / nfl_price_history.
--
-- SCHEMA GROUNDING -- read-only investigation of /Users/samfeldt/tastypickems
-- (a separate repo; nothing there was written to for this task):
--
--   nfl_stub_weeks    -- supabase/migrations/20260901000000_create_nfl_stub_weeks.sql
--                         (real CREATE TABLE; the identical statement also
--                         appears in 20260901233933_...sql -- same real
--                         column set confirmed twice, not just once)
--   nfl_price_history -- supabase/migrations/20260814133716_...sql
--                         (real CREATE TABLE)
--
-- nfl_stub_weeks TYPED columns, confirmed directly from its CREATE TABLE
-- (not guessed): id, player_id, season, week, posteam, position_group,
-- td_opportunity, role_momentum, situation, market_value_score, tpe_score,
-- reconciled, extra (jsonb), created_at, updated_at. No ALTER TABLE anywhere
-- in supabase/migrations/ adds any further typed column to this table
-- (confirmed by grep -- the only later ALTERs touching either of these two
-- tables add `book_odds jsonb` to nfl_price_history, unrelated to this
-- export).
--
-- Everything else this query needs -- player_name, evidence_quality, and
-- every other field in SEARCHED_CARD_TOP_LEVEL_CITABLE_FIELDS that is not one
-- of the typed columns above -- is NOT a typed column on nfl_stub_weeks. It
-- lives inside the `extra` jsonb catch-all. This is confirmed by reading the
-- real WRITE path in the pipeline repo, not guessed:
--   nfl/scripts/stub_store.py:STUB_WEEKS_TYPED_COLUMNS -- the explicit typed
--     list (player_id, season, week, posteam, position_group, td_opportunity,
--     role_momentum, situation, market_value_score, tpe_score -- exactly the
--     typed columns above, minus the DB-managed ones)
--   nfl/scripts/stub_store.py:shape_stub_rows() -- everything NOT in that
--     list folds into `extra`, with keys preserved VERBATIM (it round-trips
--     the frame through stub_week.to_json(orient="records") and keeps every
--     other key as-is)
--   nfl/scripts/build_stub_week.py:128 confirms "player_name" is a real
--     column on the frame that becomes a stub row -- so it lands in `extra`
--   nfl/scripts/build_stub_week.py:14 ("...and, for free, evidence_quality /
--     core_score / tpe_score...") and nfl/scoring.py's score_evidence_quality
--     (which literally writes weekly["evidence_quality"]) confirm
--     "evidence_quality" is a real column on that same frame -- so it, too,
--     lands in `extra`, not typed
-- No live query against a real row confirmed the exact extra->>'...' key
-- spelling -- this is source-code-grounded, not DB-grounded. See UNKNOWN #2
-- at the bottom of this file.
--
-- nfl_price_history columns used here, confirmed from its own CREATE TABLE:
-- player_id, player_name_raw, season, week, matched, poll_timestamp,
-- consensus_price_american, consensus_implied_probability.
--
-- WHY `week = :week` on BOTH tables, exactly (not just season = :season):
-- this directly closes the risk Stage 1c's Task 4 flagged in
-- pool_size_report.md -- nfl_price_history.week is NULLABLE in its own
-- CREATE TABLE (resolved per-row via a schedule lookup that can return NULL
-- independently of player matching), so a row can carry a real player_id and
-- a real price while still having week = NULL. An exact `week = :week`
-- equality predicate happens to exclude NULLs, but this file states that
-- filter EXPLICITLY (AND week IS NOT NULL) rather than relying on the
-- incidental exclusion, per pool_size_report.md's own defensive note.
-- nfl_stub_weeks.week is NOT NULL in its own CREATE TABLE already, so the
-- equivalent risk does not exist on that side.
--
-- WHY `reconciled = false`: once a week's games are played, reconcile_week()
-- marks that week's stub rows reconciled, and they become stale (superseded
-- by real play-by-play elsewhere) -- stub_store.rows_to_stub_frame() already
-- filters these out of the real curation path
-- (`live = [r for r in rows if not r.get("reconciled")]`). This export
-- mirrors that same real filter, so the exported pool matches what curation
-- would actually see for a pre-game week.
--
-- WHY the latest-price CTE: mirrors pool_size_report.md's own "latest poll
-- per player" pattern (matched = true, player_id IS NOT NULL, one row per
-- player via the latest poll_timestamp), scoped here to one real
-- (:season, :week) and to a real :as_of_timestamp cutoff. See UNKNOWN #1.
--
-- No numeric CAST is applied to any `extra->>'...'` column below -- every
-- jsonb-sourced field is extracted as text, deliberately. This export feeds
-- a CSV either way (a CSV cell is text regardless of a SQL-side cast), and
-- casting here risks a hard query failure if this file's source-code-
-- grounded guess about a field's real stored JSON type (numeric vs. text)
-- turns out wrong for some row. run_real_week_check.py's own
-- load_pool_from_csv() does the numeric coercion on the Python side instead
-- -- the same division of labor test_example_cards.py already uses (CSV
-- DictReader + float() in Python, never a SQL-side cast).

WITH latest_price AS (
  SELECT DISTINCT ON (player_id)
    player_id,
    player_name_raw,
    consensus_price_american,
    consensus_implied_probability,
    poll_timestamp
  FROM nfl_price_history
  WHERE season = :season
    AND week = :week
    AND week IS NOT NULL                     -- Stage 1c, Task 4 defensive filter -- see pool_size_report.md
    AND matched = true
    AND player_id IS NOT NULL
    AND poll_timestamp <= :as_of_timestamp    -- UNKNOWN: verify/supply a real "end of slate" cutoff -- see UNKNOWN #1 below
  ORDER BY player_id, poll_timestamp DESC
)
SELECT
  -- Identity. posteam/position_group ARE real typed columns on
  -- nfl_stub_weeks; player_name is NOT (see schema note above) -- pulled
  -- from `extra`, with a fallback to nfl_price_history's own raw (pre-match)
  -- name so a player with a stub row but a missing extra->>'player_name'
  -- still gets some display name instead of nothing. player_name_raw is the
  -- PRE-match name (see nfl_price_history's own match_issue_type /matched
  -- columns), so treat it as a display fallback only, not canonical.
  s.player_id,
  COALESCE(s.extra->>'player_name', lp.player_name_raw) AS player_name,
  -- STAGE 1e FIX: was aliased "AS team" (a real naming mismatch against
  -- SEARCHED_CARD_TOP_LEVEL_CITABLE_FIELDS, whose field is literally named
  -- "posteam", not "team") -- exported under its own real column name now.
  s.posteam,
  s.position_group,

  -- Exactly what evidence_price_classifier.classify_evidence_vs_price needs
  -- (REQUIRED_ROW_FIELDS in evidence_price_classifier.py):
  s.td_opportunity,
  s.role_momentum,
  s.situation,
  s.extra->>'evidence_quality' AS evidence_quality,
  lp.consensus_price_american,

  -- DISPLAY/comparison only -- the classifier never reads either of these
  -- two (see test_circularity.py, which proves exactly that, by
  -- construction). Both ARE real typed columns on nfl_stub_weeks.
  s.tpe_score,
  s.market_value_score,

  -- The raw 0-1 fraction the classifier's own implied_probability_pct is
  -- independently re-derived from (classify_evidence_vs_price computes its
  -- own implied probability straight from consensus_price_american -- it
  -- does not read this column). Carried through only so a human reviewer
  -- can sanity-check the classifier's number against the real market
  -- fraction side by side.
  lp.consensus_implied_probability,

  -- Every remaining field in SEARCHED_CARD_TOP_LEVEL_CITABLE_FIELDS, read
  -- fresh from searched_card_citable_fields.py for this task (39 fields
  -- total there as of this revision; do not hand-copy this list again
  -- without re-reading that file -- it has changed shape across Stage
  -- 1/1b/1c already, and SEARCHED_CARD_TOP_LEVEL_CITABLE_FIELDS excludes
  -- tpe_score/market_value_score, which is why both are selected separately
  -- above instead of in this block). implied_probability_pct itself is
  -- deliberately NOT selected here -- it is a one-line 0-100 rescale of
  -- consensus_implied_probability that build_searched_card_candidate() /
  -- run_real_week_check.py's own pool-building logic already derive; this
  -- export hands the raw fraction through above instead of duplicating that
  -- rescale a second time in SQL.
  s.extra->>'proven_heat' AS proven_heat,
  s.extra->>'emerging_heat' AS emerging_heat,
  s.extra->>'recent_td_production_pct' AS recent_td_production_pct,
  s.extra->>'conversion_rate_pct' AS conversion_rate_pct,
  s.extra->>'touch_share_trend_pct' AS touch_share_trend_pct,
  s.extra->>'snap_share_trend_pct' AS snap_share_trend_pct,
  s.extra->>'touch_volume_trend_pct' AS touch_volume_trend_pct,
  s.extra->>'td_opportunity_completeness' AS td_opportunity_completeness,
  s.extra->>'i10_touches_trail3' AS i10_touches_trail3,
  s.extra->>'gl_touches_trail3' AS gl_touches_trail3,
  s.extra->>'rz_tds_trail3' AS rz_tds_trail3,
  s.extra->>'role_trend' AS role_trend,
  s.extra->>'external_opportunity' AS external_opportunity,
  s.extra->>'touch_share_trend_pct_role' AS touch_share_trend_pct_role,
  s.extra->>'snap_share_trend_pct_role' AS snap_share_trend_pct_role,
  s.extra->>'depth_chart_movement_pct' AS depth_chart_movement_pct,
  s.extra->>'role_momentum_completeness' AS role_momentum_completeness,
  s.extra->>'snap_share_last1_pct' AS snap_share_last1_pct,
  s.extra->>'snap_share_season_avg_pct' AS snap_share_season_avg_pct,
  s.extra->>'teammates_ahead_injury_status' AS teammates_ahead_injury_status,
  s.extra->>'defensive_matchup_vulnerability' AS defensive_matchup_vulnerability,
  s.extra->>'recent_tds_allowed_pct' AS recent_tds_allowed_pct,
  s.extra->>'conversion_rate_allowed_pct' AS conversion_rate_allowed_pct,
  s.extra->>'defensive_matchup_completeness' AS defensive_matchup_completeness,
  s.extra->>'environment_score' AS environment_score,
  s.extra->>'situation_completeness' AS situation_completeness,
  s.extra->>'temp' AS temp,
  s.extra->>'wind' AS wind,
  s.extra->>'roof' AS roof,
  s.extra->>'market_value_completeness' AS market_value_completeness

FROM nfl_stub_weeks s
LEFT JOIN latest_price lp ON lp.player_id = s.player_id
WHERE s.season = :season
  AND s.week = :week
  AND s.reconciled = false    -- mirrors stub_store.rows_to_stub_frame()'s own live-rows filter
ORDER BY s.player_id;

-- ============================================================================
-- UNKNOWNS, collected (each also called out inline above where it applies):
--
-- 1. UNKNOWN: verify/supply :as_of_timestamp. "The end of that week's slate"
--    has no local source of truth in either repo this task could read --
--    neither repo has a locally-queryable table of real kickoff/slate-end
--    times for an arbitrary (season, week). The caller must supply a real
--    value (e.g. the kickoff_utc of that week's last game, pulled from
--    whatever schedule source is authoritative in production, or simply
--    "now" for an early mid-week look). Passing NULL for this parameter is
--    valid Postgres (poll_timestamp <= NULL is never true) and would
--    silently return zero rows from latest_price for every player -- do not
--    leave it unbound.
--
-- 2. UNKNOWN: the exact extra->>'...' key spelling for every jsonb-sourced
--    column above is confirmed by READING stub_store.py / build_stub_week.py
--    source code (the write path preserves column names verbatim into
--    `extra`), NOT by a live query against a real row -- no DB access was
--    available in this task's environment. Recommended before trusting this
--    at scale: run `SELECT extra FROM nfl_stub_weeks LIMIT 1` once and
--    eyeball the real keys against this file's list.
--
-- 3. UNKNOWN: whether `role_trend` / `external_opportunity` /
--    `teammates_ahead_injury_status` are stored as plain scalars or as
--    nested JSON (list/object) inside `extra`. If any of them is nested,
--    `extra->>'key'` still returns valid text (the JSON-serialized form),
--    which round-trips through a CSV cell fine for this export's purposes,
--    but would need `extra->'key'` (no `>>`, staying jsonb) plus further
--    unpacking if a future consumer needed the structured value rather than
--    its text form. Not resolved here -- no local artifact states the real
--    shape of these three fields specifically.
--
-- 4. UNKNOWN (expected, not a defect): whether every player with a stub row
--    for (:season, :week) already has a matching nfl_price_history row.
--    pool_size_report.md's own "thin/empty coverage... is not a failure"
--    finding says this is routine early in the week. The LEFT JOIN above
--    handles it correctly either way (consensus_price_american /
--    consensus_implied_probability come back NULL rather than dropping the
--    row, and the classifier already excludes price-less pool members from
--    the price-ranking population only) -- flagged here only so a reviewer
--    reading the exported CSV isn't surprised by real, expected NULLs in
--    those two columns for an early-week export.
-- ============================================================================
