-- CFB Phase 5 (2026-09): Target Magnets signal + 8-shelf CFB Picks
-- curation. Same NOT-AUTO-APPLIED caveat as 20260904140000 -- this file
-- lives in tasty-pick-ems (the pipeline repo); copy it into
-- tastypickems/supabase/migrations/ and apply via a manual Lovable Cloud
-- sync.
--
-- Two objects, in dependency order:
--   1. cfb_player_receiving_weekly       -- raw ingest D (Target Magnets input)
--   2. cfb_player_shelf_scores columns   -- target_magnets/* + shelves/shelf_count
--
-- Same honest-gap posture as cfb_player_role_weekly (20260904140000):
-- table 1 has NO deployed ingestion endpoint yet -- cfb/redzone.py::
-- aggregate_receiving_game_cfb is a real, tested function, but nothing
-- forwards its rows to Lovable yet (same gap 2 category as Role &
-- Momentum). Created here so curate_cfb_shelves.py's read of it degrades
-- to a real "zero rows" response instead of a 404.

-- ===========================================================================
-- 1. cfb_player_receiving_weekly (Target Magnets input, Phase 5)
-- ===========================================================================
-- Column shape confirmed directly against cfb/redzone.py::
-- aggregate_receiving_game_cfb's real row-dict construction.
CREATE TABLE public.cfb_player_receiving_weekly (
  id uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  player_id text NOT NULL,
  player_name text,
  position_group text,
  team_id integer,
  team text,
  opponent_team_id integer,
  opponent text,
  season integer NOT NULL,
  week integer NOT NULL,
  game_id bigint,
  targets integer NOT NULL DEFAULT 0,
  receptions integer NOT NULL DEFAULT 0,
  team_targets integer,
  target_share numeric,
  extra jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX cfb_player_receiving_weekly_player_season_week_uniq
  ON public.cfb_player_receiving_weekly (player_id, season, week);
CREATE INDEX cfb_player_receiving_weekly_season_week_idx
  ON public.cfb_player_receiving_weekly (season, week);

GRANT ALL ON public.cfb_player_receiving_weekly TO service_role;
ALTER TABLE public.cfb_player_receiving_weekly ENABLE ROW LEVEL SECURITY;

CREATE TRIGGER update_cfb_player_receiving_weekly_updated_at
BEFORE UPDATE ON public.cfb_player_receiving_weekly
FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

-- ===========================================================================
-- 2. cfb_player_shelf_scores -- Target Magnets columns + shelf membership
-- ===========================================================================
-- target_magnets/_completeness/_gated mirror role_momentum's own three
-- columns exactly (score shape, see cfb/scoring.py::score_target_magnets_cfb).
--
-- shelves/shelf_count are NEW: convergence tracking ("N SHELVES AGREE").
-- The pre-existing `shelf` (singular, nullable) column cannot represent
-- this -- a player can now legitimately appear on multiple shelves at
-- once (explicit spec decision, no cross-shelf dedup), which one nullable
-- text column has no way to hold. `shelf` is left untouched (still
-- unused, still reserved) rather than repurposed, so nothing that reads
-- it today needs to change.
ALTER TABLE public.cfb_player_shelf_scores
  ADD COLUMN IF NOT EXISTS target_magnets numeric,
  ADD COLUMN IF NOT EXISTS target_magnets_completeness numeric,
  ADD COLUMN IF NOT EXISTS target_magnets_gated boolean,
  ADD COLUMN IF NOT EXISTS shelves jsonb NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS shelf_count integer NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS cfb_player_shelf_scores_shelf_count_idx
  ON public.cfb_player_shelf_scores (shelf_count);

-- Re-create get_published_cfb_shelf_scores with the new columns added to
-- SELECT/RETURNS TABLE. Based on the CURRENT real definition
-- (20260904140000) -- body otherwise byte-for-byte identical.
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
  td_opportunity numeric,
  td_opportunity_completeness numeric,
  td_opportunity_gated boolean,
  defensive_matchup_vulnerability numeric,
  defensive_matchup_completeness numeric,
  situation numeric,
  situation_completeness numeric,
  role_momentum numeric,
  role_momentum_completeness numeric,
  target_magnets numeric,
  target_magnets_completeness numeric,
  target_magnets_gated boolean,
  evidence_completeness numeric,
  evidence_convergence numeric,
  evidence_quality numeric,
  core_score numeric,
  confidence_multiplier numeric,
  tpe_score numeric,
  shelves jsonb,
  shelf_count integer,
  created_at timestamptz
)
LANGUAGE sql
STABLE SECURITY DEFINER
SET search_path TO 'public'
AS $$
  SELECT
    s.id, s.player_id, s.player_name, s.season, s.week, s.game_id,
    s.team_id, s.team, s.opponent_team_id, s.opponent, s.position_group,
    s.shelf, s.td_opportunity, s.td_opportunity_completeness, s.td_opportunity_gated,
    s.defensive_matchup_vulnerability, s.defensive_matchup_completeness,
    s.situation, s.situation_completeness,
    s.role_momentum, s.role_momentum_completeness,
    s.target_magnets, s.target_magnets_completeness, s.target_magnets_gated,
    s.evidence_completeness, s.evidence_convergence, s.evidence_quality,
    s.core_score, s.confidence_multiplier, s.tpe_score,
    s.shelves, s.shelf_count,
    s.created_at
  FROM public.cfb_player_shelf_scores s
  WHERE (p_season IS NULL OR s.season = p_season)
    AND (p_week IS NULL OR s.week = p_week)
  ORDER BY s.season DESC, s.week DESC, s.tpe_score DESC NULLS LAST
$$;

GRANT EXECUTE ON FUNCTION public.get_published_cfb_shelf_scores(integer, integer) TO anon, authenticated, service_role;
