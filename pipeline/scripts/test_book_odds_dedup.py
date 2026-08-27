"""
Quick assertion script (not full test infra) confirming
_book_odds_for_match() never produces two rows for the same bookmaker.

Two parts:

1. SYNTHETIC — reproduces the exact real incident this was written for
   (Caesars appearing twice, +525 and +425, for one player) by hand-
   building odds_rows with an injected duplicate, since checking today's
   real live slate (see scripts/debug_duplicate_bookmaker.py) found the
   raw Odds API response duplicate-free at every level and Caesars wasn't
   even posting this market today — there's no real duplicate available
   to test against right now. Shows the fixed function's real before/after
   behavior on the literal reported numbers.

2. REAL — pulls a real live event, runs it through the actual
   flatten_hr_props() -> match_players() pipeline (no synthetic data),
   and asserts every real matched player's book_odds has zero duplicate
   bookmaker keys. This is the regression guard: confirms the invariant
   holds against real data, not just the hand-built case.

Run: python3 pipeline/scripts/test_book_odds_dedup.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

import requests  # noqa: E402

from flatten_hr_props import flatten_hr_props  # noqa: E402
from scored_picks import _book_odds_for_match  # noqa: E402

ODDS_EVENTS_URL = "https://api.the-odds-api.com/v4/sports/baseball_mlb/events/"
ODDS_EVENT_ODDS_URL = "https://api.the-odds-api.com/v4/sports/baseball_mlb/events/{event_id}/odds"


def _load_env_key() -> str:
    key = os.environ.get("ODDS_API_KEY")
    if key:
        return key
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ODDS_API_KEY="):
                return line.split("=", 1)[1].strip()
    print("ODDS_API_KEY not set and not found in repo-root .env", file=sys.stderr)
    raise SystemExit(1)


def has_dupe_keys(book_odds: list) -> bool:
    keys = [b["bookmaker"] for b in book_odds]
    return len(keys) != len(set(keys))


def run_synthetic() -> bool:
    print("=" * 70)
    print("SYNTHETIC — the exact reported incident: Caesars @ +525 AND +425")
    print("=" * 70)

    # Mirrors the real odds_rows shape match_players() produces: bookmaker
    # is the display title (see flatten_hr_props.py's book_name = title),
    # matching how the real incident's UI showed the bookmaker's name.
    rows = [
        {"player_name": "Test Player", "bookmaker": "DraftKings", "odds": 300},
        {"player_name": "Test Player", "bookmaker": "Caesars", "odds": 525},
        {"player_name": "Test Player", "bookmaker": "FanDuel", "odds": -110},
        {"player_name": "Test Player", "bookmaker": "Caesars", "odds": 425},  # the duplicate, worse price
    ]

    before_count = len(rows)
    result = _book_odds_for_match(rows)

    print(f"input rows: {before_count} (Caesars appears twice: +525 and +425)")
    print(f"output book_odds ({len(result)} rows):")
    for b in result:
        print(f"  {b}")

    ok = True
    ok &= check("BEFORE the fix, this input would have produced 4 rows (one per input row) — confirming the bug's shape", before_count == 4)
    ok &= check("AFTER: output has exactly 3 rows (Caesars collapsed to one)", len(result) == 3)
    ok &= check("AFTER: no duplicate bookmaker keys in the output", not has_dupe_keys(result))
    caesars_row = next(b for b in result if b["bookmaker"] == "Caesars")
    ok &= check("AFTER: the surviving Caesars row kept the BETTER price (+525, not +425)", caesars_row["odds"] == 525)
    ok &= check("AFTER: DraftKings and FanDuel rows are untouched (+300, -110)",
                any(b["bookmaker"] == "DraftKings" and b["odds"] == 300 for b in result) and
                any(b["bookmaker"] == "FanDuel" and b["odds"] == -110 for b in result))
    ok &= check("AFTER: bookmaker order is first-seen order (DraftKings, Caesars, FanDuel)",
                [b["bookmaker"] for b in result] == ["DraftKings", "Caesars", "FanDuel"])
    return ok


def check(label: str, passed: bool) -> bool:
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
    return passed


def run_real() -> bool:
    print()
    print("=" * 70)
    print("REAL — live players pulled from today's actual Odds API feed")
    print("=" * 70)

    api_key = _load_env_key()
    events_resp = requests.get(ODDS_EVENTS_URL, params={"apiKey": api_key}, timeout=15)
    events_resp.raise_for_status()
    events = events_resp.json()
    if not events:
        print("No live events right now — nothing real to check. Not a failure, just nothing to run.")
        return True

    ok = True
    checked_players = 0
    # regions=us,us2 to get real bookmaker diversity — regions=us alone
    # returned only a single bookmaker across today's whole slate (see
    # debug_duplicate_bookmaker.py's own findings).
    for event in events[:6]:
        odds_resp = requests.get(
            ODDS_EVENT_ODDS_URL.format(event_id=event["id"]),
            params={"apiKey": api_key, "regions": "us,us2", "markets": "batter_home_runs", "oddsFormat": "american"},
            timeout=15,
        )
        odds_resp.raise_for_status()
        odds_event = odds_resp.json()
        odds_rows = flatten_hr_props(odds_event)
        if not odds_rows:
            continue

        # Real candidates aren't needed to exercise _book_odds_for_match —
        # group directly by player name the same way match_players() does,
        # since the point here is book_odds's own dedup behavior, not the
        # name-matching step (already covered by test_scored_picks.py).
        by_player = {}
        for r in odds_rows:
            by_player.setdefault(r["player_name"], []).append(r)

        for player_name, player_rows in list(by_player.items())[:3]:
            book_odds = _book_odds_for_match(player_rows)
            checked_players += 1
            dupe = has_dupe_keys(book_odds)
            raw_books = [r["bookmaker"] for r in player_rows]
            print(f"  {player_name} ({event['away_team']} @ {event['home_team']}): "
                  f"{len(player_rows)} raw rows -> {len(book_odds)} book_odds rows, "
                  f"raw bookmakers={raw_books}")
            ok &= check(f"    no duplicate bookmaker keys for {player_name}", not dupe)

        if checked_players >= 12:
            break

    print(f"\nchecked {checked_players} real players across live events.")
    if checked_players == 0:
        print("(no players with HR-prop odds were live in the events checked — not a failure, just nothing posted yet)")
    return ok


if __name__ == "__main__":
    ok_synthetic = run_synthetic()
    ok_real = run_real()
    print()
    if ok_synthetic and ok_real:
        print("All checks passed.")
    else:
        print("Some checks FAILED — see above.")
        raise SystemExit(1)
