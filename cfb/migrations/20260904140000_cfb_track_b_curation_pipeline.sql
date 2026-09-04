-- CFB Track B (2026-09): the missing scoring-to-frontend pipeline.
--
-- NOT AUTO-APPLIED. This file lives in tasty-pick-ems (the pipeline repo,
-- this task's stated scope) because Supabase migrations only ever live in
-- tastypickems (the Lovable/frontend repo) in this project, and this task
-- was explicitly scoped to feldtsam/tasty-pick-ems only. Copy this file
-- into tastypickems/supabase/migrations/ and apply it via a manual Lovable
-- Cloud sync — do not assume it applies automatically just because it
-- exists here. (Same caveat the task itself already anticipated.)
--
-- Confirmed live, 2026-09-04, before writing this: a direct anon-key GET
-- against cfb_player_redzone_weekly returned HTTP 404 — "Could not find
-- the table 'public.cfb_player_redzone_weekly' in the schema cache"
-- (PostgREST's own error). Zero tables containing "cfb" exist anywhere in
-- tastypickems' tracked migrations. This is not a partial gap; nothing
-- CFB-related has ever been created in Supabase.
--
-- Five objects, in dependency order:
--   1. cfb_player_redzone_weekly            -- raw ingest A (TD Opportunity, §2)
--   2. cfb_defense_redzone_allowed_weekly   -- raw ingest B (Situation, §3)
--   3. cfb_player_role_weekly               -- raw ingest C (Role & Momentum, §4)
--   4. cfb_player_shelf_scores              -- NEW: this task's actual deliverable
--   5. get_published_cfb_shelf_scores(...)  -- NEW: the public read RPC
--
-- 1-3 restore what /api/ingest-and-write-redzone (table 1-2) and a future
-- Role & Momentum ingestion endpoint (table 3, not built by this task —
-- see cfb/api/curate_cfb_shelves.py's own module docstring, gap 2) need
-- somewhere real to write. 4-5 are the new curated/scored layer this
-- task was actually asked to build.

-- ===========================================================================
-- 1. cfb_player_redzone_weekly (TD Opportunity input, §2)
-- ===========================================================================
-- Column shape confirmed directly against cfb/redzone.py::
-- aggregate_redzone_game_cfb's real row-dict construction, not the
-- module's prose docstring alone (2026-09-04). Same RLS posture as NFL's
-- own nfl_player_redzone_weekly (see 20260828010000_create_nfl_player_
-- redzone_weekly.sql): pipeline-internal, not a Home-feed table --
-- service_role only, RLS enabled, zero anon/authenticated policies.
CREATE TABLE public.cfb_player_redzone_weekly (
  id uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  player_id text NOT NULL,
  season integer NOT NULL,
  week integer NOT NULL,
  game_id bigint,
  team_id integer,
  team text,
  opponent_team_id integer,
  opponent text,
  player_name text,
  position_group text,
  rz_touches integer NOT NULL DEFAULT 0,
  rz_rush_touches integer NOT NULL DEFAULT 0,
  rz_target_touches integer NOT NULL DEFAULT 0,
  rz_tds integer NOT NULL DEFAULT 0,
  i10_touches integer NOT NULL DEFAULT 0,
  i10_rush_touches integer NOT NULL DEFAULT 0,
  i10_target_touches integer NOT NULL DEFAULT 0,
  i10_tds integer NOT NULL DEFAULT 0,
  gl_touches integer NOT NULL DEFAULT 0,
  gl_rush_touches integer NOT NULL DEFAULT 0,
  gl_target_touches integer NOT NULL DEFAULT 0,
  gl_tds integer NOT NULL DEFAULT 0,
  team_rz_touches integer,
  rz_touch_share numeric,
  extra jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX cfb_player_redzone_weekly_player_season_week_uniq
  ON public.cfb_player_redzone_weekly (player_id, season, week);
CREATE INDEX cfb_player_redzone_weekly_season_week_idx
  ON public.cfb_player_redzone_weekly (season, week);

GRANT ALL ON public.cfb_player_redzone_weekly TO service_role;
ALTER TABLE public.cfb_player_redzone_weekly ENABLE ROW LEVEL SECURITY;

CREATE TRIGGER update_cfb_player_redzone_weekly_updated_at
BEFORE UPDATE ON public.cfb_player_redzone_weekly
FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

-- ===========================================================================
-- 2. cfb_defense_redzone_allowed_weekly (Situation input, §3)
-- ===========================================================================
-- Column shape confirmed directly against cfb/redzone.py::
-- aggregate_redzone_allowed_cfb's real row-dict construction.
CREATE TABLE public.cfb_defense_redzone_allowed_weekly (
  id uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  team_id integer,
  team text,
  position_group text,
  season integer NOT NULL,
  week integer NOT NULL,
  game_id bigint,
  opponent_team_id integer,
  opponent text,
  rz_touches_allowed integer NOT NULL DEFAULT 0,
  rz_rush_touches_allowed integer NOT NULL DEFAULT 0,
  rz_target_touches_allowed integer NOT NULL DEFAULT 0,
  rz_tds_allowed integer NOT NULL DEFAULT 0,
  i10_touches_allowed integer NOT NULL DEFAULT 0,
  i10_rush_touches_allowed integer NOT NULL DEFAULT 0,
  i10_target_touches_allowed integer NOT NULL DEFAULT 0,
  i10_tds_allowed integer NOT NULL DEFAULT 0,
  gl_touches_allowed integer NOT NULL DEFAULT 0,
  gl_rush_touches_allowed integer NOT NULL DEFAULT 0,
  gl_target_touches_allowed integer NOT NULL DEFAULT 0,
  gl_tds_allowed integer NOT NULL DEFAULT 0,
  extra jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX cfb_defense_rz_allowed_weekly_team_pos_season_week_uniq
  ON public.cfb_defense_redzone_allowed_weekly (team_id, position_group, season, week);
CREATE INDEX cfb_defense_rz_allowed_weekly_season_week_idx
  ON public.cfb_defense_redzone_allowed_weekly (season, week);

GRANT ALL ON public.cfb_defense_redzone_allowed_weekly TO service_role;
ALTER TABLE public.cfb_defense_redzone_allowed_weekly ENABLE ROW LEVEL SECURITY;

CREATE TRIGGER update_cfb_defense_rz_allowed_weekly_updated_at
BEFORE UPDATE ON public.cfb_defense_redzone_allowed_weekly
FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

-- ===========================================================================
-- 3. cfb_player_role_weekly (Role & Momentum input, §4)
-- ===========================================================================
-- Column shape confirmed directly against cfb/role_momentum.py's module
-- docstring's own explicit "Row shape" list. TABLE ONLY -- no deployed
-- endpoint writes this yet (cfb/role_momentum.py::build_role_momentum_
-- weekly is only ever called by the local, non-deployed cfb/scripts/
-- role_momentum_sanity.py). Created here so curate_cfb_shelves.py's read
-- of it degrades to a real "zero rows" response instead of a 404 — the
-- honest-degradation path it's built for, not a crash. Building the real
-- ingestion endpoint for this table is separate, out-of-scope work.
CREATE TABLE public.cfb_player_role_weekly (
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
  touches integer NOT NULL DEFAULT 0,
  team_touches integer,
  touch_share numeric,
  ppa numeric,
  is_returning boolean,
  extra jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX cfb_player_role_weekly_player_season_week_uniq
  ON public.cfb_player_role_weekly (player_id, season, week);
CREATE INDEX cfb_player_role_weekly_season_week_idx
  ON public.cfb_player_role_weekly (season, week);

GRANT ALL ON public.cfb_player_role_weekly TO service_role;
ALTER TABLE public.cfb_player_role_weekly ENABLE ROW LEVEL SECURITY;

CREATE TRIGGER update_cfb_player_role_weekly_updated_at
BEFORE UPDATE ON public.cfb_player_role_weekly
FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

-- ===========================================================================
-- 4. cfb_player_shelf_scores -- THIS TASK'S ACTUAL DELIVERABLE
-- ===========================================================================
-- One row per real scored (player_id, season, week), written by cfb/api/
-- curate_cfb_shelves.py. Column shape matches CFB_SHELF_SCORE_COLUMNS in
-- that module exactly. `shelf` is nullable and unused today -- CFB has no
-- shelf taxonomy yet (confirmed 2026-09-04: no CFB_SHELF_META exists
-- anywhere in tastypickems' frontend) -- reserved so a future shelf-
-- assignment task can populate it with no schema change.
--
-- RLS posture matches nfl_intelligence_stories/nfl_content_drafts (a
-- real Home-feed-shaped table, not pipeline-internal): admin SELECT for
-- an eventual review UI, service_role ALL, and a SECURITY DEFINER RPC
-- (below) for the real public/frontend read path.
CREATE TABLE public.cfb_player_shelf_scores (
  id uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  player_id text NOT NULL,
  player_name text,
  season integer NOT NULL,
  week integer NOT NULL,
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
  evidence_completeness numeric,
  evidence_convergence numeric,
  evidence_quality numeric,
  core_score numeric,
  confidence_multiplier numeric,
  tpe_score numeric,
  extra jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  UNIQUE (player_id, season, week)
);

CREATE INDEX cfb_player_shelf_scores_season_week_idx
  ON public.cfb_player_shelf_scores (season, week);
CREATE INDEX cfb_player_shelf_scores_shelf_idx
  ON public.cfb_player_shelf_scores (shelf);

GRANT SELECT ON public.cfb_player_shelf_scores TO authenticated;
GRANT ALL ON public.cfb_player_shelf_scores TO service_role;
ALTER TABLE public.cfb_player_shelf_scores ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Admins can view cfb player shelf scores"
ON public.cfb_player_shelf_scores FOR SELECT TO authenticated
USING (has_role(auth.uid(), 'admin'::app_role));

CREATE TRIGGER update_cfb_player_shelf_scores_updated_at
BEFORE UPDATE ON public.cfb_player_shelf_scores
FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

-- ===========================================================================
-- 5. get_published_cfb_shelf_scores -- the public read RPC
-- ===========================================================================
-- Same real SECURITY DEFINER + GRANT EXECUTE TO anon pattern already
-- used for get_published_nfl_intelligence_stories (20260822200333) and
-- get_published_shelf_picks -- confirmed 2026-09-04 as the established,
-- working access pattern every other real frontend read in this app
-- already relies on (direct table SELECT is authenticated/admin-only
-- everywhere; SECURITY DEFINER functions are how anon actually reads
-- anything). Both params optional/NULL-able, same convention as get_
-- published_nfl_intelligence_stories(p_season, p_week).
CREATE OR REPLACE FUNCTION public.get_published_cfb_shelf_scores(
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
    s.shelf, s.td_opportunity, s.td_opportunity_completeness, s.td_opportunity_gated,
    s.defensive_matchup_vulnerability, s.defensive_matchup_completeness,
    s.situation, s.situation_completeness,
    s.role_momentum, s.role_momentum_completeness,
    s.evidence_completeness, s.evidence_convergence, s.evidence_quality,
    s.core_score, s.confidence_multiplier, s.tpe_score,
    s.created_at
  FROM public.cfb_player_shelf_scores s
  WHERE (p_season IS NULL OR s.season = p_season)
    AND (p_week IS NULL OR s.week = p_week)
  ORDER BY s.season DESC, s.week DESC, s.tpe_score DESC NULLS LAST
$$;

GRANT EXECUTE ON FUNCTION public.get_published_cfb_shelf_scores(integer, integer) TO anon, authenticated, service_role;
