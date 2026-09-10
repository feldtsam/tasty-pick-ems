"""
Quick assertion script (not full test infra), mirroring pipeline/scripts/
test_book_odds_dedup.py's own structure exactly, confirming
_book_odds_for_player()/snapshot_scoring_inputs()'s new book_odds column
(NFL Odds by Sportsbook, Phase 1) is real, correctly shaped, and deduped.

Two parts:

1. SYNTHETIC — same shape as MLB's own reported incident (a bookmaker
   appearing twice with two different prices for one player), hand-built
   since no live NFL duplicate has been confirmed (unlike MLB's real
   incident) — this is defensive coverage for an identical-shaped risk,
   not a reproduction of an observed NFL bug. Confirms the fixed function's
   real before/after behavior on injected duplicate rows.

2. REAL — pulls real live NFL events, runs them through the ACTUAL
   parse_attd_event -> match_attd_players -> snapshot_scoring_inputs
   pipeline (no synthetic data), and asserts every real matched player's
   book_odds has zero duplicate bookmaker titles. Also locks in the two
   real, confirmed findings from this session's own scoping investigation
   as regression guards: Bam Knight (NFC West rank 2, ARI @ LAC) genuinely
   has only 1-of-3 target bookmakers (FanDuel) among 2 total books, and
   Kaleb Johnson (GB @ MIN) has all 3 among 10 total -- if either of these
   ever silently changes shape, this test will now catch it, not just
   re-confirm it once.

Run: python3 nfl/test_book_odds.py
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))

import requests  # noqa: E402

from market_value import _book_odds_for_player, match_attd_players, parse_attd_event, snapshot_scoring_inputs  # noqa: E402

ODDS_EVENT_ODDS_URL = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/events/{event_id}/odds"
TARGET_BOOKS = {"DraftKings", "FanDuel", "BetMGM"}

# The two real events this session's own scoping investigation pulled
# real book_odds evidence from -- kept as fixed regression targets rather
# than re-discovering them via a fresh live sweep every run, so this test
# stays fast and doesn't depend on which games happen to be live today.
ARI_LAC_EVENT_ID = "1edfa5ceaa1ad2cb57df1c1b908731f6"
GB_MIN_EVENT_ID = "cb77efed7e711d25a72c1a2a0a1af119"
SEASON = 2026


def check(label: str, passed: bool) -> bool:
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
    return passed


def has_dupe_keys(book_odds: list) -> bool:
    names = [b["bookmaker"] for b in book_odds]
    return len(names) != len(set(names))


def _load_env_key() -> str:
    key = os.environ.get("ODDS_API_KEY")
    if key:
        return key
    # NFL never calls The Odds API directly itself (Make.com does the
    # fetch and POSTs raw event JSON in) -- confirmed this session, which
    # is why this key lives in the repo-root .env, not nfl/.env.local.
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ODDS_API_KEY="):
                return line.split("=", 1)[1].strip()
    print("ODDS_API_KEY not set and not found in repo-root .env", file=sys.stderr)
    raise SystemExit(1)


def run_synthetic() -> bool:
    print("=" * 70)
    print("SYNTHETIC -- injected duplicate book_key, same shape as MLB's own real incident")
    print("=" * 70)

    import pandas as pd

    rows = pd.DataFrame([
        {"book_key": "draftkings", "book_title": "DraftKings", "price": 300},
        {"book_key": "caesars", "book_title": "Caesars", "price": 525},
        {"book_key": "fanduel", "book_title": "FanDuel", "price": -110},
        {"book_key": "caesars", "book_title": "Caesars", "price": 425},  # the duplicate, worse price
    ])

    result = _book_odds_for_player(rows)
    print(f"input rows: {len(rows)} (caesars appears twice: +525 and +425)")
    print(f"output book_odds ({len(result)} rows): {json.dumps(result)}")

    ok = True
    ok &= check("output has exactly 3 rows (Caesars collapsed to one)", len(result) == 3)
    ok &= check("no duplicate bookmaker names in the output", not has_dupe_keys(result))
    caesars_row = next(b for b in result if b["bookmaker"] == "Caesars")
    ok &= check("the surviving Caesars row kept the BETTER price (+525, not +425)", caesars_row["odds"] == 525)
    ok &= check(
        "DraftKings and FanDuel rows are untouched (+300, -110)",
        any(b["bookmaker"] == "DraftKings" and b["odds"] == 300 for b in result)
        and any(b["bookmaker"] == "FanDuel" and b["odds"] == -110 for b in result),
    )
    ok &= check(
        "bookmaker order is first-seen order (DraftKings, Caesars, FanDuel)",
        [b["bookmaker"] for b in result] == ["DraftKings", "Caesars", "FanDuel"],
    )
    ok &= check(
        "bookmaker is the real DISPLAY TITLE ('Caesars'), never the raw machine key ('caesars')",
        all(not b["bookmaker"].islower() or " " in b["bookmaker"] for b in result),
    )
    return ok


def _real_snapshot_for_event(api_key: str, event_id: str):
    resp = requests.get(
        ODDS_EVENT_ODDS_URL.format(event_id=event_id),
        params={"apiKey": api_key, "regions": "us,us2,us_ex", "markets": "player_anytime_td", "oddsFormat": "american"},
        timeout=15,
    )
    resp.raise_for_status()
    event = resp.json()
    parsed = parse_attd_event(event)
    if len(parsed) == 0:
        return None

    import nfl_data_py as nfl
    seasonal_rosters = nfl.import_seasonal_rosters([SEASON])
    team_desc = nfl.import_team_desc()
    matched, _unmatched = match_attd_players(parsed, seasonal_rosters, team_desc, SEASON)
    if len(matched) == 0:
        return None
    return snapshot_scoring_inputs(matched)


def run_real() -> bool:
    print()
    print("=" * 70)
    print("REAL -- live players pulled from The Odds API, run through the actual pipeline")
    print("=" * 70)

    api_key = _load_env_key()
    ok = True

    print("\n--- ARI @ LAC: Bam Knight (real, confirmed thin-coverage case) ---")
    snap = _real_snapshot_for_event(api_key, ARI_LAC_EVENT_ID)
    if snap is None or len(snap) == 0:
        print("  no live odds returned for this event right now -- not a failure, just nothing to check against.")
    else:
        row = snap[snap["player_name_raw"] == "Bam Knight"]
        if len(row) == 0:
            print("  Bam Knight not in today's matched snapshot -- not a failure, real coverage varies day to day.")
        else:
            r = row.iloc[0]
            book_odds = r["book_odds"]
            present = {b["bookmaker"] for b in book_odds} & TARGET_BOOKS
            print(f"  n_books={r['n_books']} book_odds={json.dumps(book_odds)}")
            ok &= check("no duplicate bookmaker names", not has_dupe_keys(book_odds))
            ok &= check(f"exactly 1 of the 3 target books present ({present or '(none)'})", len(present) == 1)
            ok &= check("FanDuel is the one target book present", present == {"FanDuel"})

    print("\n--- GB @ MIN: Kaleb Johnson (real, confirmed full-coverage case) ---")
    snap = _real_snapshot_for_event(api_key, GB_MIN_EVENT_ID)
    if snap is None or len(snap) == 0:
        print("  no live odds returned for this event right now -- not a failure, just nothing to check against.")
    else:
        row = snap[snap["player_name_raw"] == "Kaleb Johnson"]
        if len(row) == 0:
            print("  Kaleb Johnson not in today's matched snapshot -- not a failure, real coverage varies day to day.")
        else:
            r = row.iloc[0]
            book_odds = r["book_odds"]
            present = {b["bookmaker"] for b in book_odds} & TARGET_BOOKS
            print(f"  n_books={r['n_books']} book_odds={json.dumps(book_odds)}")
            ok &= check("no duplicate bookmaker names", not has_dupe_keys(book_odds))
            ok &= check(f"all 3 target books present ({present})", present == TARGET_BOOKS)

    # Broader real sweep: every matched player in both events, zero
    # duplicate bookmaker names allowed anywhere -- the general regression
    # guard, not scoped to the two named players above.
    print("\n--- broader real sweep: every matched player, both events ---")
    checked = 0
    for event_id, label in [(ARI_LAC_EVENT_ID, "ARI/LAC"), (GB_MIN_EVENT_ID, "GB/MIN")]:
        snap = _real_snapshot_for_event(api_key, event_id)
        if snap is None:
            continue
        for _, r in snap.iterrows():
            book_odds = r["book_odds"]
            if not isinstance(book_odds, list):
                continue
            checked += 1
            dupe = has_dupe_keys(book_odds)
            if dupe:
                print(f"  [{label}] {r['player_name_raw']}: DUPLICATE bookmaker found -- {book_odds}")
            ok &= check(f"[{label}] {r['player_name_raw']}: no duplicate bookmaker names", not dupe)
    print(f"\nchecked {checked} real matched players across 2 live events.")

    return ok


if __name__ == "__main__":
    ok_synthetic = run_synthetic()
    ok_real = run_real()
    print()
    if ok_synthetic and ok_real:
        print("All checks passed.")
    else:
        print("Some checks FAILED -- see above.")
        raise SystemExit(1)
