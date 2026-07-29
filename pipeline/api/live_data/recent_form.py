"""
Recent-form signals for SHELF CURATION ONLY — not part of the core
five-pillar scoring model in live_scoring/. "Hot Hitters" and "Cold
Pitchers to Attack" (see shelf_curation.py) need genuine recent-form data
— a real hot streak, a real recent slump — which the core model's
season-long Skill/Matchup pillars structurally cannot provide: those are
built entirely from season-to-date aggregates, and using season-long
pillar dominance to populate shelves named around RECENT form would be a
real mismatch between what the shelf promises and what it actually shows.

SOURCE: the MLB Stats API's per-player `gameLog` stats
(`/people/{id}/stats?stats=gameLog&group=hitting|pitching&season={year}`)
— the same API already used everywhere else in live_data/, NOT
pybaseball/Statcast. Deliberate, not a shortcut: this module stays as
dependency-light as the rest of live_data/ (no pandas, no pybaseball)
specifically because build_game_candidates.py already had to be
restructured once (whole-slate -> per-game) to stay inside Vercel
Hobby-tier's 10s timeout after profiling showed per-player MLB Stats API
calls were the dominant cost even at ~200 players. A genuine recent-WINDOW
Statcast pull (hard-hit%/barrel% allowed over a pitcher's last 5 starts
specifically, as opposed to the season-long aggregate Baseball Savant
leaderboards savant_stats.py already uses) means pulling raw pitch-by-pitch
data per player — the same multi-second, per-player operation backtest/
uses for full-season batch analysis, not something that fits a live
per-request latency budget multiplied across every candidate on a shelf.

KNOWN, DELIBERATE GAP as a result: pitcher recent-form here is
ERA/HR-per-9/K-per-9/BB-per-9 over their last N starts — all from official
box-score-level counting stats, fast and cheap — NOT recent hard-hit%/
barrel% allowed, which the original spec listed as an "and/or" alternative
to ERA/runs-allowed. Runs allowed is the standard, well-understood way to
describe a pitcher's recent struggles, and it's what's actually achievable
here without a much heavier data pull — flagged explicitly rather than
faking a "recent barrel%" number from data that isn't genuinely
recent-windowed.

WINDOW SIZE — deliberately NOT the same "10-15 games" for both hitters and
pitchers:
  - Hitters: last 15 games PLAYED (HITTER_RECENT_GAMES). A meaningful
    "hot streak" window (~2.5-3 weeks of games) while staying genuinely
    recent, not season-long.
  - Pitchers: last 5 starts (PITCHER_RECENT_STARTS), not "10-15 games".
    Confirmed against real data that starters appear in the box score
    roughly every 5th calendar day — "10-15 games" would literally span
    2+ months for a starter, defeating the entire point of a RECENT-form
    signal. 5 starts is ~3-4 weeks for a starter on a normal rotation
    turn — the much closer real-world analog to "15 games" for a hitter.

Every returned dict reports its own actual sample size
(`recent_games_sampled` / `recent_starts_sampled`) — shelf_curation.py
gates eligibility on a minimum sample rather than trusting a 1-2-game
"hot streak" that's really just noise.
"""
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from player_season_stats import parse_innings_pitched  # noqa: E402

PEOPLE_STATS_URL = "https://statsapi.mlb.com/api/v1/people/{id}/stats"
TIMEOUT_S = 15
MAX_WORKERS = 10

HITTER_RECENT_GAMES = 15
PITCHER_RECENT_STARTS = 5


def _fetch_gamelog(mlbam_id: int, group: str, season: int) -> list:
    """Real splits come back in ASCENDING date order (confirmed against
    real data) — so the most recent N is splits[-N:], not splits[:N]."""
    resp = requests.get(
        PEOPLE_STATS_URL.format(id=mlbam_id),
        params={"stats": "gameLog", "group": group, "season": season},
        timeout=TIMEOUT_S,
    )
    resp.raise_for_status()
    stats_blocks = resp.json().get("stats", [])
    if not stats_blocks:
        return []
    return stats_blocks[0].get("splits", [])


def _batter_recent_form_from_splits(splits: list, num_games: int) -> dict:
    recent = splits[-num_games:] if num_games else []
    if not recent:
        return {"recent_games_sampled": 0, "recent_ops": None, "recent_hr_per_pa": None, "recent_home_runs": 0}

    ab = h = bb = hbp = sf = tb = hr = pa = 0
    for s in recent:
        st = s["stat"]
        ab += st.get("atBats") or 0
        h += st.get("hits") or 0
        bb += st.get("baseOnBalls") or 0
        hbp += st.get("hitByPitch") or 0
        sf += st.get("sacFlies") or 0
        tb += st.get("totalBases") or 0
        hr += st.get("homeRuns") or 0
        pa += st.get("plateAppearances") or 0

    obp_denom = ab + bb + hbp + sf
    obp = (h + bb + hbp) / obp_denom if obp_denom else None
    slg = tb / ab if ab else None
    ops = (obp + slg) if (obp is not None and slg is not None) else None

    return {
        "recent_games_sampled": len(recent),
        "recent_plate_appearances": pa,
        "recent_ops": ops,
        "recent_hr_per_pa": (hr / pa) if pa else None,
        "recent_home_runs": hr,
        "recent_window_dates": {"first": recent[0].get("date"), "last": recent[-1].get("date")},
    }


def _pitcher_recent_form_from_splits(splits: list, num_starts: int) -> dict:
    # Only real starts (gamesStarted == 1) count toward the window — a
    # real bug caught by hand-checking a real "Cold Pitchers" result:
    # taking the last N *appearances* regardless of role pulled in a
    # pitcher used as a bullpen piece/opener (Caleb Ferguson, gamesStarted
    # 0 for nearly every real 2026 appearance, mostly 0.1-1.2 IP relief
    # stints) and produced a wildly distorted "5-start" ERA (9.64 over
    # just 4.7 total innings) that doesn't represent a struggling starter
    # at all. Filtering to real starts first means a true swingman/opener
    # correctly ends up with too few real starts to sample — which then
    # correctly fails MIN_PITCHER_RECENT_SAMPLE in shelf_curation.py
    # instead of ranking on relief-appearance noise.
    starts_only = [s for s in splits if s["stat"].get("gamesStarted") == 1]
    recent = starts_only[-num_starts:] if num_starts else []
    empty = {"recent_starts_sampled": 0, "recent_innings_pitched": 0.0, "recent_era": None,
              "recent_hr_per_9": None, "recent_k_per_9": None, "recent_bb_per_9": None}
    if not recent:
        return empty

    ip_total = er = hr = k = bb = 0.0
    for s in recent:
        st = s["stat"]
        ip_total += parse_innings_pitched(st.get("inningsPitched")) or 0.0
        er += st.get("earnedRuns") or 0
        hr += st.get("homeRuns") or 0
        k += st.get("strikeOuts") or 0
        bb += st.get("baseOnBalls") or 0

    if ip_total <= 0:
        return {**empty, "recent_starts_sampled": len(recent)}

    return {
        "recent_starts_sampled": len(recent),
        "recent_innings_pitched": round(ip_total, 1),
        "recent_era": er * 9 / ip_total,
        "recent_hr_per_9": hr * 9 / ip_total,
        "recent_k_per_9": k * 9 / ip_total,
        "recent_bb_per_9": bb * 9 / ip_total,
        "recent_window_dates": {"first": recent[0].get("date"), "last": recent[-1].get("date")},
    }


def fetch_batters_recent_form(mlbam_ids: list, season: int, num_games: int = HITTER_RECENT_GAMES) -> dict:
    """Concurrent, same ThreadPoolExecutor pattern as player_season_stats.py.
    Returns {mlbam_id (int): {...}} — every id gets an entry, zero-sample
    dict if the player has no current-season game log at all."""
    def _one(mid):
        return mid, _batter_recent_form_from_splits(_fetch_gamelog(mid, "hitting", season), num_games)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        return dict(ex.map(_one, mlbam_ids))


def fetch_pitchers_recent_form(mlbam_ids: list, season: int, num_starts: int = PITCHER_RECENT_STARTS) -> dict:
    def _one(mid):
        return mid, _pitcher_recent_form_from_splits(_fetch_gamelog(mid, "pitching", season), num_starts)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        return dict(ex.map(_one, mlbam_ids))
