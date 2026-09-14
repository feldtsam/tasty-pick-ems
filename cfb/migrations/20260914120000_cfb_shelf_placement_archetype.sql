-- CFB Story Archetype Resolver wiring (2026-09-14): adds `archetype` to
-- cfb_player_shelf_scores and changes that table's real identity from
-- "one row per scored player-week" to "one row per real (player, shelf)
-- placement" -- required for story_archetype.resolve_cfb_archetype()'s
-- own per-shelf-placement design (see that module's docstring): a player
-- on both Goal-Line Favorites and SEC TD Watch now legitimately needs
-- TWO rows, one per shelf, each with that shelf's own resolved
-- archetype -- which the CURRENT UNIQUE (player_id, season, week)
-- constraint structurally cannot hold (a second row for the same
-- player-week would conflict and overwrite the first).
--
-- NOT AUTO-APPLIED to a live Postgres/Supabase instance by writing this
-- file, and this file also cannot be applied by anything in this repo
-- (feldtsam/tasty-pick-ems has no Supabase credentials or CLI access) --
-- same caveat as 20260904140000/20260913150000. Copy this file into
-- tastypickems/supabase/migrations/ (a copy already exists there as of
-- this task, committed in the same session) and apply it via a manual
-- Lovable Cloud sync -- do not assume it applies automatically just
-- because it exists in either repo.
--
-- DEPENDS ON: 20260904140000_cfb_track_b_curation_pipeline.sql (creates
-- cfb_player_shelf_scores itself) having already been applied -- it has
-- (confirmed live via tastypickems/src/integrations/supabase/types.ts
-- already carrying this table, 2026-09-14). Does NOT depend on
-- 20260913150000_cfb_phase5_target_magnets_and_shelves.sql (that
-- migration's own target_magnets/shelves/shelf_count columns are
-- untouched here, and confirmed still NOT live as of this same check --
-- a separate, still-pending gap this migration does not close).
--
-- COMPANION CHANGE REQUIRED, same commit: tastypickems/src/routes/api/
-- public/cfb-player-shelf-scores-write.ts currently (a) dedupes incoming
-- rows by (player_id, season, week) BEFORE upserting -- would silently
-- drop every placement row but one for a multi-shelf player -- and (b)
-- upserts with onConflict: "player_id,season,week", which no longer
-- matches this table's real unique constraint after this migration.
-- Both fixed in that same file, same session, so the two changes land
-- together (a migration without the route fix would make every
-- multi-shelf player's write literally uncommittable -- ON CONFLICT
-- targeting a constraint that no longer exists is a real Postgres
-- error, not a silent no-op).

-- ===========================================================================
-- 1. Replace the (player_id, season, week) unique constraint with
--    (player_id, season, week, shelf) -- looked up dynamically rather
--    than assuming Postgres's default auto-generated name, since this
--    constraint's real name was never confirmed against a live
--    information_schema query (no DB access from this repo -- see
--    above). Robust to whatever it's actually named.
-- ===========================================================================
DO $$
DECLARE
  existing_constraint text;
BEGIN
  SELECT tc.constraint_name INTO existing_constraint
  FROM information_schema.table_constraints tc
  JOIN information_schema.key_column_usage kcu
    ON tc.constraint_name = kcu.constraint_name
   AND tc.table_schema = kcu.table_schema
  WHERE tc.table_schema = 'public'
    AND tc.table_name = 'cfb_player_shelf_scores'
    AND tc.constraint_type = 'UNIQUE'
  GROUP BY tc.constraint_name
  HAVING array_agg(kcu.column_name ORDER BY kcu.ordinal_position) = ARRAY['player_id', 'season', 'week']
  LIMIT 1;

  IF existing_constraint IS NOT NULL THEN
    EXECUTE format('ALTER TABLE public.cfb_player_shelf_scores DROP CONSTRAINT %I', existing_constraint);
  END IF;
END $$;

ALTER TABLE public.cfb_player_shelf_scores
  ADD CONSTRAINT cfb_player_shelf_scores_player_season_week_shelf_uniq
  UNIQUE (player_id, season, week, shelf);

-- ===========================================================================
-- 2. archetype column -- story_archetype.resolve_cfb_archetype()'s own
--    output for this exact placement row (one of the 9 values in
--    story_archetype.ARCHETYPES, or the fallback "SATURDAY_POSTER").
--    Nullable, no default -- same "optional, not every row will have it"
--    convention NFL's own archetype/position_variant columns use
--    (20260907000000_add_archetype_to_nfl_content_drafts.sql): never
--    silently defaulting to SATURDAY_POSTER at the DB layer -- that
--    decision belongs to resolve_cfb_archetype()'s own real logic,
--    already applied before the row reaches this table.
-- ===========================================================================
ALTER TABLE public.cfb_player_shelf_scores
  ADD COLUMN archetype text;

-- ===========================================================================
-- 3. get_published_cfb_shelf_scores -- exposes archetype through the
--    existing public read RPC. Based on the CURRENT real definition
--    (20260904140000's own CREATE, the only version ever applied -- Phase
--    5's target_magnets/shelves/shelf_count are NOT added here, since
--    they aren't live columns yet either; adding them is that still-
--    pending migration's own job, not this one's).
--
--    ORDER BY now leads with `shelf` (NULLS LAST, for any pre-existing
--    row written before this migration) -- grouping a season/week's rows
--    by shelf reads far more sensibly now that `shelf` is a real,
--    populated value per row instead of always null.
-- ===========================================================================
DROP FUNCTION IF EXISTS public.get_published_cfb_shelf_scores(integer, integer);

CREATE FUNCTION public.get_published_cfb_shelf_scores(
  p_season integer DEFAULT NULL,
  p_week integer DEFAULT NULL
)
RETURNS TABLE(
  id uuid,
  player_id text,
  player_name text,
  season integer,
  week integer,
  game_id bigint,
  team_id integer,
  team text,
  opponent_team_id integer,
  opponent text,
  position_group text,
  shelf text,
  archetype text,
  td_opportunity numeric,
  td_opportunity_completeness numeric,
  td_opportunity_gated boolean,
  defensive_matchup_vulnerability numeric,
  defensive_matchup_completeness numeric,
  situation numeric,
  situation_completeness numeric,
  role_momentum numeric,
  role_momentum_completeness numeric,
  evidence_completeness numeric,
  evidence_convergence numeric,
  evidence_quality numeric,
  core_score numeric,
  confidence_multiplier numeric,
  tpe_score numeric,
  created_at timestamptz
)
LANGUAGE sql
STABLE SECURITY DEFINER
SET search_path TO 'public'
AS $$
  SELECT
    s.id, s.player_id, s.player_name, s.season, s.week, s.game_id,
    s.team_id, s.team, s.opponent_team_id, s.opponent, s.position_group,
    s.shelf, s.archetype,
    s.td_opportunity, s.td_opportunity_completeness, s.td_opportunity_gated,
    s.defensive_matchup_vulnerability, s.defensive_matchup_completeness,
    s.situation, s.situation_completeness,
    s.role_momentum, s.role_momentum_completeness,
    s.evidence_completeness, s.evidence_convergence, s.evidence_quality,
    s.core_score, s.confidence_multiplier, s.tpe_score,
    s.created_at
  FROM public.cfb_player_shelf_scores s
  WHERE (p_season IS NULL OR s.season = p_season)
    AND (p_week IS NULL OR s.week = p_week)
  ORDER BY s.season DESC, s.week DESC, s.shelf NULLS LAST, s.tpe_score DESC NULLS LAST
$$;

GRANT EXECUTE ON FUNCTION public.get_published_cfb_shelf_scores(integer, integer) TO anon, authenticated, service_role;
