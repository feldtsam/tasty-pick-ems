"""
CFB Role & Momentum ingestion — `cfb_player_role_weekly` rows.

Separate from the `/plays/stats` red-zone ingest the other two pillars
share. Three CFBD calls per week, all free-tier (feasibility audit
2026-09, both scored inputs Class A on a shared `athleteId` key):

    1. GET /games                        # team-string -> stable id, opponent, completed
    2. GET /games/players?classification=fbs   # whole-slate box scores, 1 call
    3. GET /ppa/players/games            # whole-slate averagePPA.all, 1 call

plus one season-cached GET /roster (RB/WR/TE filter) and, once per
backfill, a walk of the PRIOR season's /games/players for the
returning-player check (see prior_season_team_athletes).

Row shape (`cfb_player_role_weekly`), one per RB/WR/TE per game:
    player_id player_name position_group team_id team opponent_team_id
    opponent season week game_id
    touches team_touches touch_share ppa is_returning extra

  * touches      — this player's rush attempts (CAR) + receptions (REC)
  * team_touches — his offense's total CAR+REC that game, EVERYONE included
                   (QB keepers, unclassified) — the honest denominator,
                   same rule as cfb/redzone.py's rz_touch_share
  * touch_share  — touches / team_touches
  * ppa          — CFBD averagePPA.all for this player-game; NaN where CFBD
                   attributed no PPA (verified ~3% of player-games, almost
                   all 1-touch cameos — score_role_momentum_cfb renorms
                   those to 100% touch_share_trend)
  * is_returning — this athleteId appears in this team_id's PRIOR-season
                   box scores in ANY stat category. False = new to team
                   (transfer-in OR true freshman — deliberately conflated;
                   both a thin CFB-team sample, and the flag only discounts
                   role_momentum_completeness, never the score).

`statType` note (confirmed against a live week): a "Team" pseudo-athlete
row (`id` negative, name " Team") carries team-level rushing (kneels,
aborted snaps) — excluded from BOTH numerator and denominator.
"""
from __future__ import annotations

import pandas as pd

from ids import cfbd_get, team_id_map_from_games
from plays_stats import DEFAULT_CLASSIFICATION, DEFAULT_SEASON_TYPE, completed_games, fetch_games

# box-score categories that carry per-athlete touch counts
_TOUCH_CATEGORIES = {"rushing": "CAR", "receiving": "REC"}


def _is_real_athlete(a: dict) -> bool:
    aid = a.get("id")
    return aid is not None and not str(aid).startswith("-") and str(a.get("name", "")).strip() != "Team"


def fetch_player_game_stats(
    season: int, week: int, *, classification: str = DEFAULT_CLASSIFICATION, season_type: str = DEFAULT_SEASON_TYPE
) -> list[dict]:
    """Whole-slate /games/players for one (season, week) — one call, ~1-2 MB.
    `{id, teams:[{team, homeAway, points, categories:[{name, types:[{name, athletes:[{id,name,stat}]}]}]}]}`."""
    params = {"year": int(season), "week": int(week), "seasonType": season_type}
    if classification:
        params["classification"] = classification
    rows = cfbd_get("/games/players", params)
    if not isinstance(rows, list):
        raise TypeError(f"/games/players did not return a list: {type(rows)!r}")
    return rows


def fetch_player_ppa(season: int, week: int, *, season_type: str = DEFAULT_SEASON_TYPE) -> dict[str, float]:
    """{ athleteId -> averagePPA.all } for one (season, week) — one call.
    A player with no PPA row is simply absent (score_role_momentum_cfb
    treats a joined NaN as 'no PPA that game')."""
    rows = cfbd_get("/ppa/players/games", {"year": int(season), "week": int(week), "seasonType": season_type})
    out: dict[str, float] = {}
    for r in rows if isinstance(rows, list) else []:
        aid = r.get("id")
        val = (r.get("averagePPA") or {}).get("all")
        if aid is not None and val is not None:
            out[str(aid)] = float(val)
    return out


def _touches_from_game(game: dict, name_map: dict[str, int]) -> list[dict]:
    """Per-athlete CAR+REC for one game's two teams, plus each team's total
    CAR+REC. Returns rows carrying team_id / opponent_team_id already
    resolved (or None where a school string didn't map — never guessed)."""
    teams = game.get("teams") or []
    if len(teams) != 2:
        return []
    gid = game.get("id")
    team_ids = [name_map.get(str(t.get("team"))) for t in teams]

    out: list[dict] = []
    for i, t in enumerate(teams):
        per: dict[str, dict] = {}
        for cat in t.get("categories") or []:
            stat_name = _TOUCH_CATEGORIES.get(cat.get("name"))
            if stat_name is None:
                continue
            for typ in cat.get("types") or []:
                if typ.get("name") != stat_name:
                    continue
                for a in typ.get("athletes") or []:
                    if not _is_real_athlete(a):
                        continue
                    try:
                        v = int(str(a["stat"]).strip())
                    except (ValueError, TypeError):
                        v = 0
                    slot = per.setdefault(str(a["id"]), {"name": a.get("name"), "touches": 0})
                    slot["touches"] += v
                    slot["name"] = a.get("name") or slot["name"]
        team_total = sum(s["touches"] for s in per.values())
        for aid, s in per.items():
            out.append({
                "player_id": aid,
                "player_name": (s["name"] or "").strip() or None,
                "team_id": team_ids[i],
                "team": str(teams[i].get("team")) if teams[i].get("team") is not None else None,
                "opponent_team_id": team_ids[1 - i],
                "opponent": str(teams[1 - i].get("team")) if teams[1 - i].get("team") is not None else None,
                "game_id": int(gid) if gid is not None else None,
                "touches": s["touches"],
                "team_touches": team_total,
            })
    return out


def _all_box_score_athletes(player_game_stats: list[dict], name_map: dict[str, int]) -> dict[int, set[str]]:
    """{ team_id -> {athleteId, ...} } across EVERY stat category (rushing,
    receiving, defensive, kickReturns, ...) — the "appeared in this team's
    box score in any category" set the returning-player check needs."""
    out: dict[int, set[str]] = {}
    for game in player_game_stats:
        for t in game.get("teams") or []:
            tid = name_map.get(str(t.get("team")))
            if tid is None:
                continue
            bucket = out.setdefault(int(tid), set())
            for cat in t.get("categories") or []:
                for typ in cat.get("types") or []:
                    for a in typ.get("athletes") or []:
                        if _is_real_athlete(a):
                            bucket.add(str(a["id"]))
    return out


def prior_season_team_athletes(
    season: int, *, weeks: range | None = None, classification: str = DEFAULT_CLASSIFICATION,
    season_type: str = DEFAULT_SEASON_TYPE,
) -> dict[int, set[str]]:
    """
    { team_id -> {every athleteId with any box-score row for that team that
    season} }, built by walking one whole-slate /games/players per week
    (~15-16 calls, cache it per backfill). Feeds `is_returning`: an athlete
    in the CURRENT season whose id is NOT in this set for his current
    team_id is "new to team".
    """
    weeks = weeks or range(1, 17)
    merged: dict[int, set[str]] = {}
    for wk in weeks:
        games = fetch_games(season, wk, classification=classification, season_type=season_type)
        if not games:
            continue
        name_map = team_id_map_from_games(games)
        pgs = fetch_player_game_stats(season, wk, classification=classification, season_type=season_type)
        for tid, ids in _all_box_score_athletes(pgs, name_map).items():
            merged.setdefault(tid, set()).update(ids)
    return merged


def build_role_momentum_weekly(
    season: int,
    weeks,
    *,
    prior_team_athletes: dict[int, set[str]] | None = None,
    position_lookup: dict[str, str] | None = None,
    classification: str = DEFAULT_CLASSIFICATION,
    season_type: str = DEFAULT_SEASON_TYPE,
) -> tuple[list[dict], dict]:
    """
    Assemble `cfb_player_role_weekly` rows for `season` across `weeks`.

    `prior_team_athletes` — output of prior_season_team_athletes(season - 1);
        when None, `is_returning` is left None on every row (no completeness
        discount, and a diagnostic notes it).
    `position_lookup`     — { athleteId -> "RB"|"WR"|"TE" } (roster.position_
        lookup(season)); when None it is fetched here. Only these three
        position groups get rows; a box-score skill athlete not resolvable
        to one (or a QB) is excluded from rows but still counts toward
        team_touches.

    Only rows for completed games are emitted.
    """
    from roster import POSITION_GROUPS, position_lookup as _pos_lookup

    pos = position_lookup if position_lookup is not None else _pos_lookup(season)

    rows: list[dict] = []
    per_week_diag: list[dict] = []
    ppa_hits = ppa_total = 0
    unresolved_team = 0

    for wk in weeks:
        games = fetch_games(season, wk, classification=classification, season_type=season_type)
        done = {int(g["id"]) for g in completed_games(games)}
        if not done:
            per_week_diag.append({"week": wk, "completed_games": 0})
            continue
        name_map = team_id_map_from_games(games)
        pgs = [g for g in fetch_player_game_stats(season, wk, classification=classification, season_type=season_type)
               if g.get("id") is not None and int(g["id"]) in done]
        ppa = fetch_player_ppa(season, wk, season_type=season_type)

        wk_rows = 0
        for game in pgs:
            for tr in _touches_from_game(game, name_map):
                if tr["team_id"] is None:
                    unresolved_team += 1
                position_group = pos.get(tr["player_id"])
                if position_group not in POSITION_GROUPS:
                    continue  # QB / unresolved — counted in team_touches, not scored
                ppa_val = ppa.get(tr["player_id"])
                ppa_total += 1
                ppa_hits += ppa_val is not None
                is_returning = None
                if prior_team_athletes is not None and tr["team_id"] is not None:
                    is_returning = tr["player_id"] in prior_team_athletes.get(tr["team_id"], set())
                rows.append({
                    "player_id": tr["player_id"],
                    "player_name": tr["player_name"],
                    "position_group": position_group,
                    "team_id": tr["team_id"],
                    "team": tr["team"],
                    "opponent_team_id": tr["opponent_team_id"],
                    "opponent": tr["opponent"],
                    "season": int(season),
                    "week": int(wk),
                    "game_id": tr["game_id"],
                    "touches": int(tr["touches"]),
                    "team_touches": int(tr["team_touches"]) or None,
                    "touch_share": round(tr["touches"] / tr["team_touches"], 4) if tr["team_touches"] else None,
                    "ppa": ppa_val,
                    "is_returning": is_returning,
                    "extra": {},
                })
                wk_rows += 1
        per_week_diag.append({"week": wk, "completed_games": len(done), "role_rows": wk_rows})

    diagnostics = {
        "aggregation": "cfb_player_role_weekly",
        "season": int(season),
        "weeks": list(weeks),
        "role_rows_total": len(rows),
        "distinct_players": len({r["player_id"] for r in rows}),
        "ppa_join_rate": round(ppa_hits / ppa_total, 4) if ppa_total else None,
        "ppa_missing_rows": ppa_total - ppa_hits,
        "rows_with_unresolved_team_id": unresolved_team,
        "returning_flag_available": prior_team_athletes is not None,
        "returning_rate": (
            round(sum(1 for r in rows if r["is_returning"]) / len(rows), 4)
            if rows and prior_team_athletes is not None else None
        ),
        "per_week": per_week_diag,
    }
    return rows, diagnostics


def role_weekly_frame(rows: list[dict]) -> pd.DataFrame:
    """List[dict] -> DataFrame with the dtypes score_role_momentum_cfb wants
    (touch_share / ppa float incl. NaN, is_returning nullable bool)."""
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for c in ("touch_share", "ppa"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["touches"] = pd.to_numeric(df["touches"], errors="coerce").fillna(0).astype(int)
    return df


def estimate_backfill_cost(seasons: int, weeks_per_season: int = 15) -> dict:
    """CFBD call count for a Role & Momentum backfill: per season, one
    prior-season /games/players walk (~16) + 3 calls/week + 1 /roster."""
    per_season = 16 + weeks_per_season * 3 + 1
    total = per_season * seasons
    return {
        "seasons": seasons,
        "calls_per_season": per_season,
        "estimated_cfbd_calls": total,
        "free_tier_monthly_cap": 1000,
        "fits_free_tier": total <= 1000,
        "note": "steady-state weekly cost after backfill is ~3 calls/week + a cached roster/prior-season set",
    }


__all__ = [
    "build_role_momentum_weekly",
    "prior_season_team_athletes",
    "role_weekly_frame",
    "fetch_player_game_stats",
    "fetch_player_ppa",
    "estimate_backfill_cost",
]
