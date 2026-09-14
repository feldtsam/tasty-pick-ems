-- CFB ATTD player-matching layer (2026-09-14) -- storage for real
-- matched anytime-touchdown odds, one row per (player_id, season, week).
--
-- Investigated before assuming a shape: MLB's real book_odds column
-- (pipeline/api/scored_picks._book_odds_for_match) is [{"bookmaker",
-- "odds", "implied_prob"}, ...]; NFL's own real book_odds column
-- (20260910022730_add_book_odds_to_nfl_tables.sql, confirmed by reading
-- that migration directly) is [{"bookmaker", "odds"}, ...] -- no
-- implied_prob at all. The two sports' existing schemas do NOT agree.
-- This table follows MLB's fuller shape (per this task's own explicit
-- instruction), via cfb/attd_match.py's shape_cfb_attd_odds_rows() --
-- a real, flagged choice, not an assumption either sport's shape
-- transfers automatically.
--
-- SAME 5-object raw-ingest pattern every other CFB signal in this
-- codebase uses (cfb_player_redzone_weekly, cfb_defense_redzone_
-- allowed_weekly, cfb_player_role_weekly, cfb_player_receiving_weekly):
-- pipeline-internal, service_role only, RLS enabled, zero anon/
-- authenticated policies -- this is raw matched-odds data, not a
-- Home-feed-shaped table.
--
-- NO DEPLOYED INGESTION ENDPOINT WRITES THIS YET -- same honest gap
-- posture as cfb_player_role_weekly/cfb_player_receiving_weekly before
-- their own ingestion endpoints existed. This task built the matching
-- LOGIC only (cfb/attd_match.py: match_cfb_attd_players + shape_cfb_
-- attd_odds_rows), explicitly not a live scheduled poller or shelf-
-- eligibility wiring (deferred until real captured odds data validates
-- the matcher against something other than fixtures -- see that
-- module's own docstring). This table exists so a future ingestion
-- endpoint has somewhere real to write, matching the exact same
-- sequencing this whole CFB build has followed for every other signal.
--
-- team_id/opponent_team_id/game_id are included for column-shape parity
-- with every sibling *_weekly table, but shape_cfb_attd_odds_rows()
-- does NOT populate them today -- match_cfb_attd_players() only ever
-- sees the Odds API's own team NAME strings (resolved to a CFBD school
-- name, not a real integer team_id), and resolving those requires the
-- same week's /games response, which is outside this task's own scope.
-- Nullable, honestly unpopulated for now, not silently backfilled with
-- a guess -- a future ingestion endpoint (the one this table is staged
-- for) is the natural place to thread /games through and fill these in.
CREATE TABLE public.cfb_player_attd_odds_weekly (
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
  book_odds jsonb NOT NULL DEFAULT '[]'::jsonb,
  extra jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX cfb_player_attd_odds_weekly_player_season_week_uniq
  ON public.cfb_player_attd_odds_weekly (player_id, season, week);
CREATE INDEX cfb_player_attd_odds_weekly_season_week_idx
  ON public.cfb_player_attd_odds_weekly (season, week);

GRANT ALL ON public.cfb_player_attd_odds_weekly TO service_role;
ALTER TABLE public.cfb_player_attd_odds_weekly ENABLE ROW LEVEL SECURITY;

CREATE TRIGGER update_cfb_player_attd_odds_weekly_updated_at
BEFORE UPDATE ON public.cfb_player_attd_odds_weekly
FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();
