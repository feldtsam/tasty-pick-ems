"""
Orchestrator: given a single game_pk, pulls that one game's lineup,
weather, and player stats, and assembles clean flat JSON with one
score_candidate()-ready dict per hitter — field names matching
api/live_scoring/score_candidate.py's documented input schema exactly, so
its output can be piped straight in.

PER-GAME BY DESIGN, not whole-slate. An earlier version fetched every
game's players in one batch and took ~19-20s for a 12-game day — over
Vercel's Hobby-tier serverless timeout. Profiling found the whole-slate
per-player MLB Stats API calls were the dominant cost (8.7s alone for 198
batters), not the Savant bulk CSVs (~0.6s total, flat regardless of player
count) or the schedule/feed calls. Restructured so each call handles one
game's ~18-20 players instead: measured at ~2.5-3.5s end to end per game,
comfortably under the 10s Hobby limit — see README for the actual
measurement. This also mirrors how `/api/flatten-and-forward` already
works (one call per event/game), so Make.com's existing Iterator-over-games
pattern — already built and proven for the odds pipeline — can call this
the same way once it's wired in, no new Make.com pattern needed.

Only a game with `lineup_status == "confirmed"` gets hitter candidates
built (no batting order = no opportunity-pillar input = not a real
candidate yet). A game with "not_yet_posted" or "not_happening" lineups
still returns full game info with an empty `candidates` list, so a caller
always knows why a given game has no picks yet rather than getting nothing
back.

KNOWN GAPS (see also api/live_scoring/score_candidate.py's own docstring
for pillars 1/2/4; this module doesn't change those, only sources what's
sourceable):
  - Pull%/FB% (batted-ball profile): no live Savant CSV source exists for
    this — confirmed during investigation, not assumed. Omitted; score_
    candidate() already tolerates missing fb_pct/pull_pct as neutral.
  - Bullpen quality (opp_bullpen_*): the backtest-validated methodology
    (scripts/fetch_bullpen_quality.py) has only been run for 2021-2023, no
    current-season bullpen reference table exists yet. Omitted here too.
  - odds: this module has no odds source wired in — that's flatten_hr_props.py's
    job, from a different upstream API (The Odds API), and is expected to
    be joined in by whatever caller combines the two.

STAT-SOURCE RECOMMENDATION: see stat_selection.py's module docstring for
the full reasoning — sample-size-gated fallback to the player's own real
2025 season stats when their current-season sample is below backtest's
validated qualification thresholds (min_pa=100, min_ip=20), not a weighted
blend.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from game_data import fetch_game_data  # noqa: E402
from mlb_schedule import fetch_schedule  # noqa: E402
from player_season_stats import fetch_handedness, fetch_hitting_stats, fetch_pitching_stats  # noqa: E402
from savant_stats import (  # noqa: E402
    build_batter_savant_row, build_pitcher_savant_row,
    fetch_batter_exitvelo_barrels, fetch_batter_expected_stats,
    fetch_pitcher_exitvelo_barrels, fetch_pitcher_expected_stats,
)
from stat_selection import select_batter_metrics, select_pitcher_metrics  # noqa: E402

_SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "live_scoring" / "reference_data" / "reference_snapshot_2025.json"


def _load_reference_snapshot() -> dict:
    with open(_SNAPSHOT_PATH) as f:
        return json.load(f)


def _build_batter_candidate(hitter: dict, team_abbr: str, opp_pitcher: dict, opp_matchup: dict,
                             opp_throws, home_team_abbr: str, weather: dict, batter_hand: dict,
                             current_savant: dict, current_hitting: dict,
                             batter_lookup_2025: dict, min_pa: int) -> tuple:
    mlbam_id = hitter["mlbam_id"]
    current = dict(current_savant.get(mlbam_id, {}))
    hitting_stat = current_hitting.get(mlbam_id, {})
    current.setdefault("hr_per_pa", hitting_stat.get("hr_per_pa"))
    current_pa = hitting_stat.get("plate_appearances")

    selected = select_batter_metrics(mlbam_id, current, current_pa, batter_lookup_2025, min_pa)
    source_note = selected.pop("_note")
    source = selected.pop("_source")

    candidate = {
        "player_name": hitter["full_name"],
        "mlbam_id": mlbam_id,
        "team": team_abbr,
        "batting_order_slot": hitter["batting_order_slot"],
        "batter_stand": batter_hand.get("bat_side"),
        **selected,
        "opp_pitcher_name": opp_pitcher["full_name"] if opp_pitcher else None,
        "opp_pitcher_mlbam_id": opp_pitcher["mlbam_id"] if opp_pitcher else None,
        "opp_throws": opp_throws,
        **opp_matchup,
        "home_team": home_team_abbr,
        "wind_speed_mph": weather.get("wind_speed_mph"),
        "wind_description": weather.get("wind_description"),
        "temp_f": weather.get("temp_f"),
        "roof_status": weather.get("roof_status"),
        "_stat_source": source,
        "_stat_source_note": source_note,
    }
    return candidate


def build_candidates_for_game(game_pk: int) -> dict:
    """
    The production entry point — one game_pk in, that game's full context
    plus a `candidates` list out. Everything needed comes from this game
    alone: one feed/live call (game_data.py) plus stat lookups scoped to
    just this game's ~18-20 players. No day-level schedule call, no
    cross-game batching.
    """
    g = fetch_game_data(game_pk)
    snapshot = _load_reference_snapshot()
    batter_lookup_2025 = snapshot["batter_lookup_by_id"]
    pitcher_lookup_2025 = snapshot["pitcher_lookup_by_id"]
    min_pa = snapshot["config"]["skill"]["min_pa"]
    min_ip = snapshot["config"]["matchup"]["min_ip"]
    season = g["season"] or int(g["official_date"][:4])

    notes = []
    entry = {
        "game_pk": g["game_pk"],
        "game_number": g["game_number"],
        "official_date": g["official_date"],
        "game_date_utc": g["game_date_utc"],
        "venue": g["venue"],
        "away_team": g["away_team"]["abbreviation"],
        "home_team": g["home_team"]["abbreviation"],
        "status": g["status"],
        "lineup_status": g["lineup_status"],
        "away_probable_pitcher": g["away_probable_pitcher"],
        "home_probable_pitcher": g["home_probable_pitcher"],
        "candidates": [],
        "notes": notes,
    }

    if g["lineup_status"] != "confirmed":
        return {"reference_season_for_fallback": snapshot["season"], "current_season": season, "game": entry}

    weather = g["weather"]
    away_pitcher = g["away_probable_pitcher"]
    home_pitcher = g["home_probable_pitcher"]

    for pitcher, side_label in ((home_pitcher, "home"), (away_pitcher, "away")):
        if pitcher is None:
            notes.append(f"no probable pitcher available for the {side_label} side — those hitters will be scored with a fully neutral matchup pillar.")

    all_batter_ids = [p["mlbam_id"] for p in g["away_lineup"] + g["home_lineup"]]
    all_pitcher_ids = [p["mlbam_id"] for p in (away_pitcher, home_pitcher) if p]

    batter_ev = fetch_batter_exitvelo_barrels(season)
    batter_xs = fetch_batter_expected_stats(season)
    pitcher_ev = fetch_pitcher_exitvelo_barrels(season)
    pitcher_xs = fetch_pitcher_expected_stats(season)

    current_savant_batters = {mid: build_batter_savant_row(str(mid), batter_ev, batter_xs) for mid in all_batter_ids}
    current_savant_pitchers = {mid: build_pitcher_savant_row(str(mid), pitcher_ev, pitcher_xs) for mid in all_pitcher_ids}

    current_hitting = fetch_hitting_stats(all_batter_ids, season)
    current_pitching = fetch_pitching_stats(all_pitcher_ids, season)
    handedness = fetch_handedness(all_batter_ids + all_pitcher_ids)

    def _pitcher_matchup(pitcher):
        if pitcher is None:
            return {}, None
        mlbam_id = pitcher["mlbam_id"]
        current = dict(current_savant_pitchers.get(mlbam_id, {}))
        pstat = current_pitching.get(mlbam_id, {})
        for field, key in (("opp_hr_per_9", "hr_per_9"), ("opp_k_per_9", "k_per_9")):
            current.setdefault(field, pstat.get(key))
        current_ip = pstat.get("innings_pitched")
        selected = select_pitcher_metrics(mlbam_id, current, current_ip, pitcher_lookup_2025, min_ip)
        note = selected.pop("_note")
        selected.pop("_source")
        notes.append(f"{pitcher['full_name']} (opposing pitcher): {note}")
        throws = handedness.get(mlbam_id, {}).get("throws")
        return selected, throws

    home_pitcher_matchup, home_pitcher_throws = _pitcher_matchup(home_pitcher)
    away_pitcher_matchup, away_pitcher_throws = _pitcher_matchup(away_pitcher)

    for hitter in g["away_lineup"]:
        entry["candidates"].append(_build_batter_candidate(
            hitter, g["away_team"]["abbreviation"], home_pitcher, home_pitcher_matchup, home_pitcher_throws,
            g["home_team"]["abbreviation"], weather, handedness.get(hitter["mlbam_id"], {}),
            current_savant_batters, current_hitting, batter_lookup_2025, min_pa,
        ))

    for hitter in g["home_lineup"]:
        entry["candidates"].append(_build_batter_candidate(
            hitter, g["home_team"]["abbreviation"], away_pitcher, away_pitcher_matchup, away_pitcher_throws,
            g["home_team"]["abbreviation"], weather, handedness.get(hitter["mlbam_id"], {}),
            current_savant_batters, current_hitting, batter_lookup_2025, min_pa,
        ))

    return {"reference_season_for_fallback": snapshot["season"], "current_season": season, "game": entry}


def build_candidates_for_date(date: str) -> dict:
    """
    DEV/TEST CONVENIENCE ONLY — not the production path. Discovers the
    day's game_pks via fetch_schedule(date), then calls
    build_candidates_for_game() once per game_pk, exactly the way Make.com's
    Iterator will call the real per-game endpoint. Exists so
    test_live_data.py can validate a whole real slate in one run without
    12+ manual calls; the per-request latency that actually matters for
    Vercel's timeout is build_candidates_for_game()'s alone, not this
    wrapper's total.
    """
    games = fetch_schedule(date)
    snapshot = _load_reference_snapshot()
    out_games = [build_candidates_for_game(g["game_pk"])["game"] for g in games]
    return {
        "date": date,
        "reference_season_for_fallback": snapshot["season"],
        "games": out_games,
    }


if __name__ == "__main__":
    import datetime
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        result = build_candidates_for_game(int(sys.argv[1]))
    else:
        target_date = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().isoformat()
        result = build_candidates_for_date(target_date)
    print(json.dumps(result, indent=2))
