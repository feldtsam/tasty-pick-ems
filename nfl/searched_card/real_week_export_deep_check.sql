-- nfl/searched_card/real_week_export_deep_check.sql
--
-- STAGE 1f DELIVERABLE (Task 5) -- design-only, READ-ONLY SQL. Pulls the
-- underlying extra->>'...' fields that feed td_opportunity/role_momentum
-- for 8 named players (the 6 flagged backs + Gibbs/McCaffrey as clean
-- comparisons), for season=2026, week=7, so a human can eyeball whether
-- their low evidence ranks reflect real weak trailing data or a pipeline
-- gap. Nothing in this repo executes this file; run it BY HAND in Lovable
-- against the real tastypickems database. Read-only SELECT only.
--
-- SCHEMA GROUNDING -- same read-only investigation as real_week_export.sql
-- (see that file for the nfl_stub_weeks/nfl_price_history CREATE TABLE
-- citations). This file adds grounding specific to td_opportunity/
-- role_momentum's own real inputs, traced directly from the pipeline's
-- scoring code (not guessed):
--
--   nfl/scoring.py:494-583 (score_td_opportunity) stores, on the real
--   weekly frame (and therefore in extra, per shape_stub_rows -- see
--   below): recent_td_production_pct, conversion_rate_pct, proven_heat,
--   touch_share_trend_pct, snap_share_trend_pct, touch_volume_trend_pct,
--   emerging_heat, td_opportunity_completeness.
--
--   nfl/scoring.py:586-683 (score_role_momentum) stores: touch_share_
--   trend_pct_role, snap_share_trend_pct_role, depth_chart_movement_pct,
--   role_trend, external_opportunity, role_momentum_completeness.
--
--   nfl/scripts/stub_store.py:56-74 (shape_stub_rows) confirms the
--   mechanism: EVERY column on build_stub_week()'s final frame that
--   isn't in STUB_WEEKS_TYPED_COLUMNS lands in extra, verbatim by key --
--   not a curated subset. nfl/scripts/build_stub_week.py has exactly one
--   .drop(columns=...) call in the whole file (an early merge's redundant
--   "team" column, unrelated to scoring) -- no final column-pruning step
--   was found, which is why this file also lists the RAW trailing inputs
--   below (rz_touches/rz_tds/i10_touches/gl_touches and their _last1/
--   _last3/_last5/_season_avg suffixes, confirmed real names from
--   nfl/redzone.py and nfl/scoring.py:522-544) as PROBABLY present, not
--   just the derived _pct columns -- but see UNKNOWN #1 below: this is
--   inferred from "nothing drops them," not confirmed against a live row.
--
--   depth_rank itself: nfl/redzone.py:614,733 confirm this is a real
--   column name (not guessed). td_opportunity does NOT use depth_rank at
--   all (confirmed: score_td_opportunity never references it) --
--   depth_rank only feeds role_momentum's depth_chart_movement_pct, via
--   a week-over-week delta (current depth_rank vs. depth_rank_prev).
--   Requesting one row per player here only shows the CURRENT week's
--   depth_rank, not the prior week's -- see UNKNOWN #2.
--
--   rz_target_touches / i10_target_touches / gl_target_touches: real
--   columns per nfl/redzone.py's _band_agg (confirmed in Stage 1b's
--   grounding_audit.md), NOT used by td_opportunity or role_momentum at
--   all (those pillars use the COMBINED touch counts, e.g. rz_touches,
--   not the target-only split) -- included here anyway because the task
--   asked for them by name, for a human's own reference, not because
--   they feed either pillar.
--
--   ahead_injury_statuses: a list-typed field (nfl/redzone.py:904), not a
--   scalar -- extra->>'ahead_injury_statuses' returns its JSON-serialized
--   text form (e.g. '["Questionable"]' or '[]'), which is fine for this
--   export's purposes (a human reading a CSV cell) but would need
--   extra->'ahead_injury_statuses' (jsonb, no ->>) plus further parsing
--   for anything structured.
--
-- WHAT TD_OPPORTUNITY AND ROLE_MOMENTUM ACTUALLY MEASURE (from the code,
-- answering the task's own question directly):
--
--   td_opportunity = max(proven_heat, emerging_heat) + 0.3 * min(...) *
--   (1 - max(...)/100) (nfl/scoring.py:567-569). Proven Heat (60% recent
--   TD production + 40% shrinkage-adjusted conversion rate) answers "has
--   this player actually been scoring / converting red-zone touches into
--   TDs recently." Emerging Heat (touch-share/snap-share/touch-volume
--   TREND) answers "is this player's OWN usage trending up right now,"
--   not "is their usage high." A workhorse with a long-stable (flat)
--   role scores LOW on Emerging Heat by design -- _trend_delta measures
--   change, and a stable role has none to measure -- but that is only
--   HALF of td_opportunity; the max()-based combination means a stable
--   workhorse's real, high Proven Heat (if his conversion data is
--   populated) should still carry td_opportunity on its own, with
--   Emerging Heat adding only a small bonus on top, not replacing Proven
--   Heat's floor. A workhorse scoring LOW on overall td_opportunity would
--   mean Proven Heat ITSELF is low -- i.e. his own real rz_tds_last1/
--   last3/last5/season_avg and shrinkage-adjusted conversion rates are
--   computing low, not merely that his trend is flat. This export's
--   recent_td_production_pct/conversion_rate_pct/proven_heat columns are
--   exactly what would confirm or rule that out.
--
--   role_momentum = max(role_trend, external_opportunity) + 0.3 *
--   min(...) * (1 - max(...)/100) (nfl/scoring.py:669-671). Role Trend
--   (touch-share/snap-share trend + depth-chart movement) is, by
--   construction, ALSO a measure of CHANGE, not level -- "is this
--   player's role moving," not "how big is this player's role." External
--   Opportunity (worst injury status among teammates ranked ahead) is a
--   real 0 (not a missing-data fallback -- nfl/scoring.py:657-663,
--   "Empty list... -> 0, a real reading") when no one ahead is hurt.
--
--   DIRECT ANSWER TO "should a workhorse with a stable role score low on
--   role_momentum by design": YES, partially, and this is confirmed
--   directly in the code's own stated intent, not an inference from
--   odd-looking numbers. nfl/scoring.py's role_momentum docstring and
--   this pillar's very name ("Role & MOMENTUM") describe trend/change,
--   not role size or role quality -- an entrenched, UNCHANGING starter
--   with a stable role and no one ahead of him hurt will, by this
--   formula's own design, score near the LOW end of role_momentum (flat
--   trend + a real-zero external_opportunity, with nothing to trigger
--   the max()'s floor the way a rising or declining role would). This is
--   a real, confirmed, BY-DESIGN property of the pillar, not a bug this
--   investigation found -- but it does mean role_momentum alone is NOT
--   the right signal to ask "is this a real workhorse," and the
--   classifier's football-evidence composite currently blends it in at
--   20/70 of the composite weight regardless (FOOTBALL_EVIDENCE_WEIGHTS,
--   evidence_price_classifier.py) -- worth knowing when reading this
--   export's role_trend/role_momentum numbers for these specific players.

SELECT
  s.player_id,
  COALESCE(s.extra->>'player_name', lp.player_name_raw) AS player_name,
  s.posteam,
  s.position_group,
  s.td_opportunity,
  s.role_momentum,
  s.situation,
  s.extra->>'evidence_quality' AS evidence_quality,

  -- The REAL fill-flag mechanism (Task 1's answer) -- 0 means every one
  -- of that pillar's percentile-normalized inputs was a neutral-50
  -- fallback (no real reading at all); use these, not the 57.1/50
  -- heuristic, to settle whether each of these 8 rows is a genuine fill.
  s.extra->>'td_opportunity_completeness' AS td_opportunity_completeness,
  s.extra->>'role_momentum_completeness' AS role_momentum_completeness,

  -- td_opportunity's own intermediate components (Proven Heat / Emerging Heat).
  s.extra->>'recent_td_production_pct' AS recent_td_production_pct,
  s.extra->>'conversion_rate_pct' AS conversion_rate_pct,
  s.extra->>'proven_heat' AS proven_heat,
  s.extra->>'touch_share_trend_pct' AS touch_share_trend_pct,
  s.extra->>'snap_share_trend_pct' AS snap_share_trend_pct,
  s.extra->>'touch_volume_trend_pct' AS touch_volume_trend_pct,
  s.extra->>'emerging_heat' AS emerging_heat,

  -- role_momentum's own intermediate components (Role Trend / External Opportunity).
  s.extra->>'touch_share_trend_pct_role' AS touch_share_trend_pct_role,
  s.extra->>'snap_share_trend_pct_role' AS snap_share_trend_pct_role,
  s.extra->>'depth_chart_movement_pct' AS depth_chart_movement_pct,
  s.extra->>'role_trend' AS role_trend,
  s.extra->>'external_opportunity' AS external_opportunity,

  -- Requested by name: snap-share level/trend fields, trailing touch
  -- counts, defensive-matchup rate, and the current-week depth_rank
  -- snapshot (see UNKNOWN #2 -- this is ONE week's value, not a delta).
  s.extra->>'snap_share_last1_pct' AS snap_share_last1_pct,
  s.extra->>'snap_share_season_avg_pct' AS snap_share_season_avg_pct,
  s.extra->>'i10_touches_trail3' AS i10_touches_trail3,
  s.extra->>'gl_touches_trail3' AS gl_touches_trail3,
  s.extra->>'rz_tds_trail3' AS rz_tds_trail3,
  s.extra->>'recent_tds_allowed_pct' AS recent_tds_allowed_pct,
  s.extra->>'depth_rank' AS depth_rank,

  -- Target-only touch counts -- requested by name; NOT inputs to either
  -- pillar (see note above), included for reference only.
  s.extra->>'rz_target_touches' AS rz_target_touches,
  s.extra->>'i10_target_touches' AS i10_target_touches,
  s.extra->>'gl_target_touches' AS gl_target_touches,

  -- PROBABLY present (see UNKNOWN #1) -- the raw trailing inputs
  -- Proven Heat's recent_td_production_pct/conversion_rate_pct are
  -- themselves computed from. If any of these come back NULL while the
  -- _pct columns above are non-null, that would itself be informative
  -- (it would mean the raw inputs really were dropped somewhere, and the
  -- derived columns are the only trace left).
  s.extra->>'rz_tds_last1' AS rz_tds_last1,
  s.extra->>'rz_tds_last3' AS rz_tds_last3,
  s.extra->>'rz_tds_last5' AS rz_tds_last5,
  s.extra->>'rz_tds_season_avg' AS rz_tds_season_avg,
  s.extra->>'rz_touches' AS rz_touches,
  s.extra->>'gl_touches' AS gl_touches,
  s.extra->>'gl_tds' AS gl_tds,
  s.extra->>'i10_touches' AS i10_touches,
  s.extra->>'i10_tds' AS i10_tds,
  s.extra->>'ahead_injury_statuses' AS ahead_injury_statuses,

  lp.consensus_price_american,
  lp.consensus_implied_probability

FROM nfl_stub_weeks s
LEFT JOIN (
  SELECT DISTINCT ON (player_id)
    player_id, player_name_raw, consensus_price_american, consensus_implied_probability
  FROM nfl_price_history
  WHERE season = 2026 AND week = 7 AND week IS NOT NULL
    AND matched = true AND player_id IS NOT NULL
    AND poll_timestamp <= '2026-10-25 17:00:00+00'   -- same as_of cutoff as real_week_export_slim.sql
  ORDER BY player_id, poll_timestamp DESC
) lp ON lp.player_id = s.player_id
WHERE s.season = 2026
  AND s.week = 7
  AND s.player_id IN (
    '00-0038542',  -- Bijan Robinson
    '00-0032764',  -- Derrick Henry
    '00-0035685',  -- David Montgomery
    '00-0038597',  -- Chase Brown
    '00-0036275',  -- D'Andre Swift
    '00-0038120',  -- Breece Hall
    '00-0039139',  -- Jahmyr Gibbs (comparison)
    '00-0033280'   -- Christian McCaffrey (comparison)
  )
ORDER BY s.player_id;

-- ============================================================================
-- UNKNOWNS:
--
-- 1. UNKNOWN: whether the raw trailing-input columns (rz_tds_last1/last3/
--    last5/season_avg, rz_touches, gl_touches/gl_tds, i10_touches/i10_tds,
--    ahead_injury_statuses) actually survive onto build_stub_week()'s
--    FINAL frame (and therefore into extra). No column-pruning step was
--    found in build_stub_week.py (only one unrelated .drop(columns=...)
--    call, dropping a redundant merge column early on, nothing at the
--    end) -- this makes their presence likely, not confirmed. If any of
--    these come back as a literal missing key (NULL from extra->>'...'
--    on a key that was never set, indistinguishable in Postgres from a
--    key set to JSON null), that resolves this UNKNOWN directly.
--
-- 2. UNKNOWN: depth_rank as queried here is the CURRENT week's snapshot
--    only. depth_chart_movement_pct (already included above) is the real
--    week-over-week delta the pillar actually uses -- if you need the
--    PRIOR week's depth_rank too (to see the delta's two raw endpoints,
--    not just its percentile), that needs a second query against
--    week = 6 for the same 8 player_ids, not included here to keep this
--    file scoped to the single real (:season, :week) pattern the rest of
--    this package already uses.
--
-- 3. UNKNOWN (same as real_week_export.sql's own UNKNOWN #2): every
--    extra->>'...' key spelling above is confirmed by reading the real
--    pipeline source (scoring.py/redzone.py/stub_store.py), not by a
--    live query against a real row. Recommended before trusting this at
--    scale: run `SELECT extra FROM nfl_stub_weeks WHERE player_id =
--    '00-0032764' AND season = 2026 AND week = 7 LIMIT 1` once and
--    eyeball the real keys present against this file's list.
-- ============================================================================
