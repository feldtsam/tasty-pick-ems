"""
One-off (re-runnable) tool: pulls real Odds API events + real MLB live
data for today's evening slate, scores every candidate via the existing,
already-tested pipeline (game_lookup + scored_picks — no reimplementation),
and saves the pooled scored-picks list to /tmp for shelf_curation.py's test
suite to use.

NOT part of the automated test suite itself, and deliberately not called
automatically by test_shelf_curation.py — The Odds API's player-props tier
is a paid plan with a real request budget (~500/month on most plans), and
this script alone burns ~9-15 of those per run (one per game). Re-run this
by hand when you want a fresh real pool; the test suite reads whatever's
already on disk and skips gracefully with a clear message if nothing's
there yet.

Run: python3 pipeline/scripts/build_shelf_test_pool.py [OUTPUT_PATH]
(defaults to /tmp/shelf_test_pool.json)
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api" / "live_data"))

import requests  # noqa: E402

from game_lookup import resolve_game_pk  # noqa: E402
from mlb_schedule import fetch_schedule  # noqa: E402
from scored_picks import build_scored_picks_for_game  # noqa: E402

ODDS_EVENTS_URL = "https://api.the-odds-api.com/v4/sports/baseball_mlb/events/"
ODDS_EVENT_ODDS_URL = "https://api.the-odds-api.com/v4/sports/baseball_mlb/events/{event_id}/odds"


def main(output_path: str):
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        print("ODDS_API_KEY not set in the environment — source .env first.", file=sys.stderr)
        raise SystemExit(1)

    events_resp = requests.get(ODDS_EVENTS_URL, params={"apiKey": api_key}, timeout=15)
    events_resp.raise_for_status()
    events = events_resp.json()

    # Only games with confirmed MLB lineups right now are worth pulling
    # odds for — anything else scores zero candidates anyway. Cross-check
    # against today's real MLB schedule (cheap, free) before spending an
    # Odds API request on a game that isn't ready yet.
    today_games = fetch_schedule(events[0]["commence_time"][:10]) if events else []
    confirmed_pks = {g["game_pk"] for g in today_games if g["lineup_status"] == "confirmed"}

    pool = []
    summary = []
    for event in events:
        try:
            resolution = resolve_game_pk(event["home_team"], event["away_team"], event["commence_time"])
        except ValueError:
            continue
        if resolution["game_pk"] not in confirmed_pks:
            continue

        odds_resp = requests.get(
            ODDS_EVENT_ODDS_URL.format(event_id=event["id"]),
            params={"apiKey": api_key, "regions": "us", "markets": "batter_home_runs", "oddsFormat": "american"},
            timeout=15,
        )
        odds_resp.raise_for_status()
        odds_event = odds_resp.json()

        result = build_scored_picks_for_game(resolution["game_pk"], odds_event)
        n = len(result["scored_picks"])
        summary.append(f"{event['away_team']} @ {event['home_team']} (game_pk {resolution['game_pk']}): {n} scored")
        pool.extend(result["scored_picks"])

    Path(output_path).write_text(json.dumps(pool))
    print("\n".join(summary))
    print(f"\nTotal scored picks pooled: {len(pool)}")
    print(f"Saved -> {output_path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/shelf_test_pool.json")
