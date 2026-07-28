"""
Bulk current-season Statcast quality metrics from Baseball Savant's CSV
leaderboard endpoints, keyed by `player_id` (MLBAM ID — confirmed to match
the IDs returned by the MLB Stats API schedule/lineup/people endpoints, so
no name-matching is needed, unlike scraping Baseball-Reference).

Fetched with `min=0` (confirmed via a real request) so every player with
at least one batted-ball event is included, not just those clearing a
"qualified" batting/pitching title threshold — the sample-size gating for
whether to trust a given player's current-season number happens later, in
stat_selection.py, using the same min_pa/min_ip thresholds already
validated in backtest/scoring/config.py. Fetching unfiltered here and
gating downstream keeps this module a dumb, honest data pull.

Pure `csv` module, no pandas — this endpoint (unlike backtest/'s bulk
season parquet files) is small enough (~600-800 rows) that pandas would be
pure overhead for a Vercel serverless function.

KNOWN GAP (confirmed during investigation, not assumed): there is no Savant
CSV leaderboard for batted-ball profile (Pull%/FB%) for hitters. The
historical backtest computes these from raw pitch-by-pitch Statcast data
(backtest/scripts/fetch_statcast.py), which isn't practical to do live in a
serverless function. score_candidate() already tolerates missing
pull_pct/fb_pct (falls back to neutral within the contact-quality
component, documented in its own docstring) — so this endpoint simply omits
them rather than approximating.
"""
import csv
import io

import requests

LEADERBOARD_URL = "https://baseballsavant.mlb.com/leaderboard/statcast"
EXPECTED_STATS_URL = "https://baseballsavant.mlb.com/leaderboard/expected_statistics"
TIMEOUT_S = 20


def _fetch_csv(url: str, params: dict) -> list:
    resp = requests.get(url, params=params, timeout=TIMEOUT_S)
    resp.raise_for_status()
    # Savant's CSV has a UTF-8 BOM (confirmed: first header field arrives as
    # '﻿"last_name, first_name"') — utf-8-sig strips it so the first
    # column name comes out clean instead of BOM-prefixed and unmatchable.
    text = resp.content.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def fetch_batter_exitvelo_barrels(year: int) -> dict:
    """avg_hit_speed, ev95percent (hard-hit%), brl_percent (barrel%),
    anglesweetspotpercent (sweet-spot%) — keyed by player_id (str)."""
    rows = _fetch_csv(LEADERBOARD_URL, {"type": "batter", "year": year, "position": "", "team": "", "min": 0, "csv": "true"})
    return {r["player_id"]: r for r in rows if r.get("player_id")}


def fetch_pitcher_exitvelo_barrels(year: int) -> dict:
    """Same shape/columns as the batter leaderboard, but contact ALLOWED —
    keyed by player_id (str)."""
    rows = _fetch_csv(LEADERBOARD_URL, {"type": "pitcher", "year": year, "position": "", "team": "", "min": 0, "csv": "true"})
    return {r["player_id"]: r for r in rows if r.get("player_id")}


def fetch_batter_expected_stats(year: int) -> dict:
    """pa, est_slg (xSLG), est_woba (xwOBA) — keyed by player_id (str)."""
    rows = _fetch_csv(EXPECTED_STATS_URL, {"type": "batter", "year": year, "position": "", "team": "", "min": 0, "csv": "true"})
    return {r["player_id"]: r for r in rows if r.get("player_id")}


def fetch_pitcher_expected_stats(year: int) -> dict:
    """pa (batters faced), est_slg_allowed, est_woba_allowed — keyed by
    player_id (str). Column names in the raw CSV are the same as the
    batter version (est_slg, est_woba); "allowed" is implied by type=pitcher."""
    rows = _fetch_csv(EXPECTED_STATS_URL, {"type": "pitcher", "year": year, "position": "", "team": "", "min": 0, "csv": "true"})
    return {r["player_id"]: r for r in rows if r.get("player_id")}


def _to_float(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_batter_savant_row(player_id: str, ev_barrels: dict, expected: dict) -> dict:
    """Merges the two batter leaderboards for one player_id into the field
    names score_candidate() expects. Returns {} fields as None for any
    leaderboard the player is missing from (e.g. zero batted-ball events
    yet) rather than raising."""
    ev = ev_barrels.get(player_id, {})
    xs = expected.get(player_id, {})
    return {
        "avg_exit_velo": _to_float(ev.get("avg_hit_speed")),
        "sweet_spot_pct": _to_float(ev.get("anglesweetspotpercent")),
        "hard_hit_pct": _to_float(ev.get("ev95percent")),
        "barrel_pct": _to_float(ev.get("brl_percent")),
        "xslg": _to_float(xs.get("est_slg")),
        "xwoba": _to_float(xs.get("est_woba")),
    }


def build_pitcher_savant_row(player_id: str, ev_barrels: dict, expected: dict) -> dict:
    """Same merge as build_batter_savant_row, but for the *_allowed fields
    score_candidate() expects for the opposing pitcher."""
    ev = ev_barrels.get(player_id, {})
    xs = expected.get(player_id, {})
    return {
        "opp_hard_hit_pct_allowed": _to_float(ev.get("ev95percent")),
        "opp_barrel_pct_allowed": _to_float(ev.get("brl_percent")),
        "opp_xslg_allowed": _to_float(xs.get("est_slg")),
        "opp_xwoba_allowed": _to_float(xs.get("est_woba")),
    }
