"""
Ad hoc, run-by-hand tool: checks which bookmakers The Odds API is actually
returning for a given market on today's live slate, right now, for any
sport The Odds API covers. Built to answer the same question scripts/
debug_duplicate_bookmaker.py's sweep answered once for MLB (do DraftKings/
FanDuel/BetMGM show up for batter_home_runs today?), but as something
cheap and trivial to re-run by hand over several days rather than a
one-off investigation script.

NOT wired into Make.com, Vercel, or any scheduler -- deliberately manual.
Run it whenever you're at your computer:

    python pipeline/scripts/check_market_bookmakers.py
    python pipeline/scripts/check_market_bookmakers.py --market h2h
    python pipeline/scripts/check_market_bookmakers.py --market player_anytime_td --sport americanfootball_nfl

Each run:
  - Pulls today's live events (UTC calendar date -- see NOTE below), capped
    at EVENTS_TO_CHECK to keep API usage cheap enough to run several times
    a day without denting a real budget.
  - Requests regions=us,us2,us_ex (the same widest-region set the original
    investigation used) for the given --market, against the given --sport.
  - Prints a one-line-per-bookmaker summary, dated.
  - Appends one row to bookmaker_coverage_log.csv (created on first run) --
    never overwritten, so results accumulate across every run, every sport,
    every market, in the same file (see LOG_HEADER: sport and market are
    both real columns, so later rows stay comparable against earlier ones
    regardless of which sport/market a given run checked).

--sport defaults to "baseball_mlb" -- the original MLB investigation's own
behavior is unchanged when this flag is omitted, on purpose, not just as a
side effect of adding the flag. Pass any other Odds API sport key (see
https://the-odds-api.com/sports-odds-data/sports-apis.html) --
americanfootball_nfl confirmed working directly (see the market-key
verification note below).

MARKET KEY VERIFICATION (NFL anytime touchdown scorer, confirmed live, not
assumed): "player_anytime_td" is the real, current Odds API key. Verified
by requesting a broad candidate set (player_anytime_td, player_1st_td,
player_last_td, player_tds_over) against one real live NFL event and
confirming all four came back with real market data, not silently dropped
-- the same "don't trust a name, hit the real endpoint" discipline
debug_duplicate_bookmaker.py's own investigation already established for
this repo, applied here before ever passing --market to this script.

NOTE ON "TODAY": bucketed by commence_time's UTC calendar date, not local
time. The Odds API returns commence_time in UTC, and a late West Coast
game (or, for a weekly sport like NFL, a slate that simply doesn't have
any games on today's specific date) can land on the next UTC date, or on
no date at all -- a known, accepted simplification for a quick diagnostic
tool, not a bug. Zero events today is reported plainly, not an error; for
a weekly sport, re-run on an actual game day rather than expecting this
tool to reach across the whole week on its own.
"""
import argparse
import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ODDS_EVENTS_URL_TMPL = "https://api.the-odds-api.com/v4/sports/{sport}/events/"
ODDS_EVENT_ODDS_URL_TMPL = "https://api.the-odds-api.com/v4/sports/{sport}/events/{event_id}/odds"
REGIONS = "us,us2,us_ex"
DEFAULT_SPORT = "baseball_mlb"

# Cap on how many of today's events to actually pull odds for -- one
# events-list call is free-ish, but each event's own odds call costs a
# real Odds API request. 5 was enough to get a reliable, real signal in
# the original investigation without burning a meaningful chunk of a
# ~500/month budget across several re-runs a day over several days.
EVENTS_TO_CHECK = 5

# The named books this is specifically tracking, in the order the output
# always prints them. Anything else seen is reported separately, not
# silently dropped.
TRACKED_BOOKMAKERS = ["draftkings", "fanduel", "betmgm", "caesars", "betrivers"]

LOG_PATH = Path(__file__).resolve().parent / "bookmaker_coverage_log.csv"
LOG_HEADER = ["date_utc", "time_utc", "sport", "market", "events_checked"] + TRACKED_BOOKMAKERS + ["other_bookmakers_seen"]


def _load_env_key() -> str:
    key = os.environ.get("ODDS_API_KEY")
    if key:
        return key
    # Same standalone-friendly fallback debug_duplicate_bookmaker.py uses --
    # reads the repo-root .env directly so this still works without the
    # caller having sourced it first.
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ODDS_API_KEY="):
                return line.split("=", 1)[1].strip()
    print("ODDS_API_KEY not set in the environment and not found in repo-root .env", file=sys.stderr)
    raise SystemExit(1)


def todays_events(api_key: str, sport: str) -> list[dict]:
    """Every live/upcoming event for `sport`, filtered to today's UTC
    calendar date, capped at EVENTS_TO_CHECK -- see module docstring for
    both caveats."""
    resp = requests.get(ODDS_EVENTS_URL_TMPL.format(sport=sport), params={"apiKey": api_key}, timeout=15)
    resp.raise_for_status()
    events = resp.json()

    today_utc = datetime.now(timezone.utc).date()
    todays = [
        e for e in events
        if datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00")).date() == today_utc
    ]
    return todays[:EVENTS_TO_CHECK]


def bookmakers_for_market(api_key: str, event_id: str, market: str, sport: str) -> set[str]:
    """Every bookmaker key that has ANY entry for `market` on this one
    event, regardless of the specific outcome/point/line -- a coverage
    check, not an odds check, so this stays correct for any market key,
    not just batter_home_runs' own point==0.5/Over shape."""
    resp = requests.get(
        ODDS_EVENT_ODDS_URL_TMPL.format(sport=sport, event_id=event_id),
        params={"apiKey": api_key, "regions": REGIONS, "markets": market, "oddsFormat": "american"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    found = set()
    for bookmaker in data.get("bookmakers", []):
        if not isinstance(bookmaker, dict):
            continue
        markets = bookmaker.get("markets", [])
        if any(isinstance(m, dict) and m.get("key") == market for m in markets):
            key = bookmaker.get("key")
            if key:
                found.add(key)
    return found


def append_log_row(row: dict) -> None:
    is_new = not LOG_PATH.exists()
    with LOG_PATH.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_HEADER)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def main(market: str, sport: str) -> None:
    api_key = _load_env_key()
    now = datetime.now(timezone.utc)

    events = todays_events(api_key, sport)
    print(f"{now.date()} {now.strftime('%H:%M UTC')} — sport={sport!r} market={market!r} — "
          f"{len(events)} of today's events checked (regions={REGIONS})\n")

    if not events:
        print(f"No live {sport!r} events found for today's UTC date right now — nothing to check.")
        print("(Not necessarily a problem: could just be before today's events are posted yet, "
              "or -- for a weekly sport like NFL -- today simply isn't a game day this week.)")
        events_seen_all: set[str] = set()
    else:
        events_seen_all = set()
        for event in events:
            seen = bookmakers_for_market(api_key, event["id"], market, sport)
            events_seen_all |= seen
            label = f"{event.get('away_team')} @ {event.get('home_team')}"
            print(f"  {label}: {sorted(seen) if seen else '(none)'}")
        print()

    row = {
        "date_utc": str(now.date()),
        "time_utc": now.strftime("%H:%M:%S"),
        "sport": sport,
        "market": market,
        "events_checked": len(events),
    }
    print("Bookmaker coverage for this market today:")
    for book in TRACKED_BOOKMAKERS:
        present = book in events_seen_all
        row[book] = "yes" if present else "no"
        print(f"  {book:<12} {'YES' if present else 'no'}")

    other = sorted(events_seen_all - set(TRACKED_BOOKMAKERS))
    row["other_bookmakers_seen"] = ";".join(other)
    if other:
        print(f"  other bookmakers seen: {', '.join(other)}")
    else:
        print("  other bookmakers seen: (none)")

    append_log_row(row)
    print(f"\nAppended to {LOG_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--market", default="batter_home_runs", help="The Odds API market key to check (default: batter_home_runs)")
    parser.add_argument(
        "--sport", default=DEFAULT_SPORT,
        help=f"The Odds API sport key to check (default: {DEFAULT_SPORT!r} -- the original MLB "
             "investigation's own behavior, unchanged when this flag is omitted)",
    )
    args = parser.parse_args()
    main(args.market, args.sport)
