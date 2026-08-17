"""
Tasty Pick Ems — NFL Market Value poller (Phase 2 of the stub-row work,
see scripts/build_stub_week.py for Phase 1).

Fetches live player_anytime_td odds for one (season, week)'s real
games, scores them via the existing, already-validated market_value.py
+ scoring.score_market_value() pipeline (nothing reimplemented here —
this is the "future polling script" market_value.py's own module
docstring already said would eventually call it), and merges the
result onto that week's stub file (scripts/build_stub_week.py's
output) by player_id — WITHOUT touching td_opportunity/role_momentum/
situation, which Phase 1 already populated.

Scheduling/deployment (Make.com or otherwise) is explicitly out of
scope — this is a function you call once, ad hoc, and it's correct.
Same full-file-rewrite storage convention Phase 1 established
(scripts/build_stub_week.py's own module docstring) — no partial
patching, no database.

Usage:
    python scripts/poll_market_value_for_stub.py SEASON WEEK
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor"))

import pandas as pd
import requests

import nfl_data_py as nfl
from market_value import match_attd_players, parse_attd_event, snapshot_scoring_inputs
from scoring import CONFIG, score_market_value

EVENTS_URL = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/events/"
ODDS_URL = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/events/{event_id}/odds"
STUB_DIR = Path(__file__).resolve().parent.parent / "data" / "stub_weeks"

# The columns this poller owns end to end — added fresh on every run,
# dropped first (see poll_market_value_for_stub) so re-running never
# leaves a stale value or a duplicate _x/_y column behind. player_id/
# season/week are the join key, not owned columns, and are never dropped.
MARKET_VALUE_COLUMNS = [
    "n_books", "best_price", "best_book",
    "consensus_implied_probability", "consensus_price_american",
    "market_value_score", "market_value_completeness",
]

# Columns backfilled onto a genuinely NEW row (a player the live odds
# found who wasn't in the stub's original roster pull — see "merge in
# cleanly" in poll_market_value_for_stub). Everything else (td_
# opportunity, role_momentum, situation, evidence_quality, tpe_score,
# ...) is correctly left NaN for such a row: this poller only ever
# computes Market Value, so a brand-new row genuinely has no red-zone-
# pillar data yet, not a bug to paper over. Re-run scripts/build_stub_
# week.py to pick up a roster change properly.
#
# market_value's own "position_group" column shares a name with stub's
# — merged in under a distinct prefix (_new_*) rather than relying on
# pandas' automatic _x/_y suffixing, so the backfill step below always
# knows exactly which column it's reading from, not a suffix that
# depends on merge order.
NEW_ROW_IDENTITY_COLUMNS = {"_new_player_name": "player_name", "_new_posteam": "posteam", "_new_position_group": "position_group"}
_MARKET_VALUE_RAW_RENAME = {"player_name_raw": "_new_player_name", "team": "_new_posteam", "position_group": "_new_position_group"}


def _load_api_key() -> str:
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        print("ODDS_API_KEY not set in the environment — source .env first.", file=sys.stderr)
        raise SystemExit(1)
    return api_key


def fetch_week_events(season: int, week: int, schedules: pd.DataFrame, team_desc: pd.DataFrame, api_key: str) -> list[dict]:
    """
    Fetch The Odds API's current NFL events list and filter to just the
    real games scheduled for (season, week) — cheap (one request,
    events-list-only, no per-game odds cost yet) and avoids spending
    Odds API budget probing games outside the target week, same
    "cross-check against the real schedule before spending a request"
    discipline pipeline/scripts/build_shelf_test_pool.py already uses
    for the MLB side.

    The Odds API's events use full team names (e.g. "Seattle Seahawks");
    schedules uses nflverse abbreviations — resolved via team_desc, the
    same translation match_attd_players already does for the per-event
    home_team/away_team fields.
    """
    events_resp = requests.get(EVENTS_URL, params={"apiKey": api_key}, timeout=15)
    events_resp.raise_for_status()
    events = events_resp.json()

    name_to_abbr = dict(zip(team_desc["team_name"], team_desc["team_abbr"]))
    week_games = schedules[(schedules["season"] == season) & (schedules["week"] == week)]
    # The SPECIFIC (home, away) pairing for this week, not just "both
    # teams happen to play somewhere in this week" -- every one of the
    # ~32 teams has exactly one game in any given week, so a per-team
    # membership check (is home_abbr in this week's team set, is
    # away_abbr in this week's team set) is nearly always true for ANY
    # two-team matchup regardless of what week it's actually scheduled
    # for. Caught this directly: an earlier per-team version of this
    # filter matched 255 of 272 total events instead of the ~16 real
    # week-1 games.
    #
    # Exact (home, away) order only, NOT both orientations -- confirmed
    # directly against real 2026 Week 1 data that The Odds API's home_
    # team/away_team already matches nflverse's own schedule convention
    # exactly (SEA/NE, PIT/ATL, KC/DEN all matched without flipping).
    # Allowing the flipped orientation as a defensive fallback was tried
    # first and caused real contamination: divisional rivals (e.g.
    # DEN@KC) play twice a season with home/away swapped, so the flipped
    # pairing incorrectly matched that SECOND, later-season meeting as
    # if it were Week 1's game too.
    week_matchups = set(zip(week_games["home_team"], week_games["away_team"]))

    return [
        e for e in events
        if (name_to_abbr.get(e.get("home_team")), name_to_abbr.get(e.get("away_team"))) in week_matchups
    ]


def fetch_and_score_market_value(
    season: int, week: int, events: list[dict], seasonal_rosters: pd.DataFrame, team_desc: pd.DataFrame,
    api_key: str = None,
) -> pd.DataFrame:
    """
    Parses/matches/scores `events` (already-fetched raw Odds API event
    objects, each carrying its own 'bookmakers' data) via the existing,
    unmodified market_value.py + scoring.score_market_value pipeline —
    exactly the same three-function sequence nfl/api/index.py's live
    endpoint already runs. Returns a player_id/season/week-keyed frame
    with MARKET_VALUE_COLUMNS — empty (correctly shaped, zero rows) if
    no event in `events` has a posted player_anytime_td market yet, not
    an error.

    Takes already-fetched events rather than fetching internally — the
    network fetch (poll_market_value_for_stub / fetch_week_events) and
    the pure parse/match/score logic here are deliberately separable, so
    an idempotency check can replay the exact same live snapshot twice
    without a second network round-trip (two separate live fetches
    minutes apart aren't a fair idempotency test, since real odds can
    genuinely move between them).
    """
    parsed_parts = [parse_attd_event(e) for e in events]
    parsed = pd.concat(parsed_parts, ignore_index=True) if parsed_parts else pd.DataFrame()

    empty = pd.DataFrame(columns=["player_id", "season", "week", "player_name_raw", "team", "position_group"] + MARKET_VALUE_COLUMNS)
    if len(parsed) == 0:
        return empty

    matched, _unmatched = match_attd_players(parsed, seasonal_rosters, team_desc, season)
    if len(matched) == 0:
        return empty

    snap = snapshot_scoring_inputs(matched)
    snap["season"] = season
    snap["week"] = week
    return score_market_value(snap, CONFIG)


def poll_market_value_for_stub(
    season: int,
    week: int,
    events: list[dict] = None,
    seasonal_rosters: pd.DataFrame = None,
    team_desc: pd.DataFrame = None,
    schedules: pd.DataFrame = None,
    api_key: str = None,
    stub_dir: Path = None,
) -> pd.DataFrame:
    """
    Read this week's stub file, merge in fresh Market Value, write it
    back. See this module's docstring for the storage convention (full-
    file rewrite, no partial patch, no database — same as Phase 1).

    events: optional pre-fetched list of raw Odds API event objects for
    this week's games (each with a 'bookmakers' key). Pass this to
    replay a FIXED snapshot without a live fetch — exactly what the
    idempotency validation does (two identical inputs must produce two
    identical outputs; two separate LIVE fetches several seconds apart
    are not a fair idempotency test, since real odds can genuinely move
    between them). Omit to fetch fresh live odds for the week's real
    games (the ad hoc, single-call production usage this task asks for).

    IDEMPOTENCY: MARKET_VALUE_COLUMNS are dropped from the stub before
    merging fresh — not merged on top of whatever's already there — so
    re-running with the same events/odds always overwrites to the same
    values rather than compounding. td_opportunity/role_momentum/
    situation/evidence_quality/etc. are never touched: they're not in
    MARKET_VALUE_COLUMNS and this function never drops or recomputes
    them.

    NEW PLAYERS: a live snapshot can contain a player_id the stub never
    had (a market posted for someone outside the stub's original
    roster pull). Handled with an OUTER merge, not a left merge, so
    that row is added rather than silently discarded — backfilled with
    just enough identity (player_name, posteam, position_group) to be
    usable; every other pillar column is correctly NaN for it, since
    this poller only ever computes Market Value (see NEW_ROW_IDENTITY_
    COLUMNS). Re-run scripts/build_stub_week.py to get that player a
    real td_opportunity/role_momentum/situation read.

    MISSING ODDS: a stub row with no matching live-odds row keeps
    MARKET_VALUE_COLUMNS as NaN — score_universal_tpe's existing
    present-columns-only renormalization (commit 49a6d12) already
    handles that per row; nothing new needed here.
    """
    stub_dir = stub_dir or STUB_DIR
    stub_path = stub_dir / f"{season}_wk{week}.csv"
    stub = pd.read_csv(stub_path)

    if seasonal_rosters is None:
        seasonal_rosters = nfl.import_seasonal_rosters([season])
    if team_desc is None:
        team_desc = nfl.import_team_desc()
    if schedules is None:
        schedules = nfl.import_schedules([season])

    if events is None:
        api_key = api_key or _load_api_key()
        week_events = fetch_week_events(season, week, schedules, team_desc, api_key)
        events = []
        for e in week_events:
            odds_resp = requests.get(
                ODDS_URL.format(event_id=e["id"]),
                params={"apiKey": api_key, "regions": "us", "markets": "player_anytime_td", "oddsFormat": "american"},
                timeout=15,
            )
            odds_resp.raise_for_status()
            events.append(odds_resp.json())

    market_value = fetch_and_score_market_value(season, week, events, seasonal_rosters, team_desc)

    # Drop any Market Value columns already on the stub (from a prior
    # poll) before merging fresh -- see IDEMPOTENCY above. player_id/
    # season/week (the join key) are never dropped.
    stub = stub.drop(columns=MARKET_VALUE_COLUMNS, errors="ignore")

    raw_cols = ["player_id", "season", "week"] + MARKET_VALUE_COLUMNS + list(_MARKET_VALUE_RAW_RENAME.keys())
    market_value_for_merge = market_value[[c for c in raw_cols if c in market_value.columns]].rename(
        columns=_MARKET_VALUE_RAW_RENAME
    )

    merged = stub.merge(market_value_for_merge, on=["player_id", "season", "week"], how="outer", indicator=True)

    new_rows = merged["_merge"] == "right_only"
    if new_rows.any():
        print(f"  {new_rows.sum()} player(s) had live odds but weren't in the original stub -- adding as new rows")
        for raw_col, stub_col in NEW_ROW_IDENTITY_COLUMNS.items():
            if raw_col in merged.columns:
                merged.loc[new_rows, stub_col] = merged.loc[new_rows, raw_col]

    merged = merged.drop(columns=["_merge"] + [c for c in NEW_ROW_IDENTITY_COLUMNS if c in merged.columns])
    merged = merged.sort_values(["player_id"]).reset_index(drop=True)

    merged.to_csv(stub_path, index=False)
    print(f"Wrote {len(merged)} rows to {stub_path} "
          f"({market_value['player_id'].notna().sum() if len(market_value) else 0} with live Market Value data)")

    return merged


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python scripts/poll_market_value_for_stub.py SEASON WEEK", file=sys.stderr)
        raise SystemExit(1)
    season_arg, week_arg = int(sys.argv[1]), int(sys.argv[2])
    poll_market_value_for_stub(season_arg, week_arg)
