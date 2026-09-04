"""
The Odds API `americanfootball_ncaaf` player-prop coverage sweep.

WHY THIS EXISTS: CFB books post player props on a compressed
Thu/Fri/gameday cadence, not NFL's long lead time. Two 2026-08-30 sweeps
at 4-6 days out found 0/90 games with any prop; the Market Value pillar
was deferred out of CFB v1 behind a "re-poll inside 2 days of kickoff"
gate (see the CFB Market Value Findings report). This script is that
re-poll, made repeatable: same 11 prop market keys, run it again any
week and the result is appended to ncaaf_prop_coverage_log.jsonl (next
to this file) so the picture accumulates instead of each run replacing
the last. Seeded with the two 2026-08-30 sweeps + the 2026-09-04
decisive sweep (44/91), reconstructed from each run's saved raw.

It answers one question per run: of this week's FBS games, how many have
`player_anytime_td` (TPE's actual market) and the wider prop suite
populated -- broken out by days-to-kickoff and by matchup tier
(P4/marquee vs G5 vs FCS-involved), because that split is what informs
whether Market Value can be reused for CFB and at what scope.

No writes to any pipeline table, no Lovable calls. Read-only against The
Odds API. Empty prop responses are unbilled by the API.

Usage:
    ODDS_API_KEY=<key>  python3 cfb/scripts/poll_ncaaf_prop_coverage.py
    # options:
    #   --days-ahead N   include games kicking off within N days (default 8)
    #   --no-log         print the report but don't append to the .jsonl log
    #   --json PATH      also write the full per-game detail as one JSON blob

The 11 prop market keys are frozen to match the 2026-08-30 sweeps exactly
-- do not reorder or swap without also noting it in the findings doc, or
run-to-run comparison breaks.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG_PATH = HERE / "ncaaf_prop_coverage_log.jsonl"
FBS_REF_PATH = HERE / "fbs_teams_2026.json"

ODDS_BASE = "https://api.the-odds-api.com/v4/sports/americanfootball_ncaaf"

# Frozen: the exact 11 keys the 2026-08-30 sweeps used. player_anytime_td
# is the one TPE actually needs; the rest gauge how deep a game's board is.
PROP_MARKET_KEYS = [
    "player_pass_yds",
    "player_pass_tds",
    "player_pass_completions",
    "player_rush_yds",
    "player_rush_attempts",
    "player_reception_yds",
    "player_receptions",
    "player_anytime_td",
    "player_1st_td",
    "player_pass_rush_reception_yds",
    "player_kicking_points",
]

# days-to-kickoff buckets, low edge inclusive; "live" is anything already
# started (lead < 0). Order is the report/log order.
BUCKETS = ["live", "<1d", "1-2d", "2-3d", "3-4d", "4d+"]


def bucket_for(lead_days: float) -> str:
    if lead_days < 0:
        return "live"
    if lead_days < 1:
        return "<1d"
    if lead_days < 2:
        return "1-2d"
    if lead_days < 3:
        return "2-3d"
    if lead_days < 4:
        return "3-4d"
    return "4d+"


# ---------------------------------------------------------------------------
# Matchup tier. FBS membership + P4/G5 come from fbs_teams_2026.json, which
# is CFBD's own /teams/fbs?year=2026 response reduced to
# {school: {conf, mascot, tier}}. Refresh it from CFBD if realignment
# happens (the rebuilt Pac-12 already landed here as G5-tier, correctly).
# A team The Odds API names that isn't in that file is treated as FCS.
# ---------------------------------------------------------------------------
def _load_fbs_ref() -> dict:
    ref = json.loads(FBS_REF_PATH.read_text())
    # {"<school> <mascot>": school} for exact matching against Odds API's
    # "<school> <mascot>" team strings, plus the raw ref for fallbacks.
    combos = {f"{s} {v['mascot']}".strip(): s for s, v in ref.items()}
    return ref, combos


def _match_school(odds_team: str, ref: dict, combos: dict) -> str | None:
    if odds_team in combos:
        return combos[odds_team]
    # CFBD uses short school forms ("Massachusetts", not "UMass") -- fall
    # back to a distinctive-mascot match, then a fuzzy match.
    toks = odds_team.split()
    for n in (2, 1):
        mascot = " ".join(toks[-n:])
        hits = [s for s, v in ref.items() if v["mascot"] == mascot]
        if len(hits) == 1:
            return hits[0]
    import difflib

    close = difflib.get_close_matches(odds_team, list(combos), n=1, cutoff=0.6)
    return combos[close[0]] if close else None


def tier_for(away: str, home: str, ref: dict, combos: dict) -> str:
    """P4 if either side is a power-conf/Notre Dame program; FCS if either
    side isn't FBS at all; G5 otherwise. FCS wins over P4 because a
    P4-vs-FCS blowout is a no-prop game, not a marquee one."""
    schools = [_match_school(away, ref, combos), _match_school(home, ref, combos)]
    if any(s is None for s in schools):
        return "FCS"
    tiers = {ref[s]["tier"] for s in schools}
    return "P4" if "P4" in tiers else "G5"


# ---------------------------------------------------------------------------
# The Odds API
# ---------------------------------------------------------------------------
def _get(url: str) -> tuple[dict, object]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return headers, json.load(resp)
    except urllib.error.HTTPError as e:
        return {"_status": str(e.code)}, json.loads(e.read() or b"{}")


def fetch_events(api_key: str) -> list[dict]:
    hdr, data = _get(f"{ODDS_BASE}/events?apiKey={api_key}")
    if not isinstance(data, list):
        raise SystemExit(f"events fetch failed: {hdr.get('_status')} {data}")
    return data


def fetch_event_props(api_key: str, event_id: str) -> tuple[dict, str | None]:
    url = (
        f"{ODDS_BASE}/events/{event_id}/odds?apiKey={api_key}"
        f"&regions=us&oddsFormat=american&markets={','.join(PROP_MARKET_KEYS)}"
    )
    hdr, data = _get(url)
    rem = hdr.get("x-requests-remaining")
    markets: dict[str, list[str]] = {}
    if isinstance(data, dict):
        for book in data.get("bookmakers", []):
            for m in book.get("markets", []):
                markets.setdefault(m["key"], []).append(book["key"])
    return {k: sorted(set(v)) for k, v in markets.items()}, rem


# ---------------------------------------------------------------------------
# Sweep + report
# ---------------------------------------------------------------------------
def run_sweep(api_key: str, days_ahead: int) -> dict:
    now = datetime.now(timezone.utc)
    ref, combos = _load_fbs_ref()

    events = fetch_events(api_key)
    horizon = now.timestamp() + days_ahead * 86400
    week = []
    for e in events:
        ct = datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00"))
        lead = (ct.timestamp() - now.timestamp()) / 86400
        # include anything up to `days_ahead` out, and anything <12h stale
        # (just-kicked games still return a useful board snapshot).
        if -0.5 <= lead <= days_ahead:
            week.append((e, ct, lead))
    week.sort(key=lambda t: t[2])

    games = []
    rem = None
    for i, (e, ct, lead) in enumerate(week):
        markets, rem = fetch_event_props(api_key, e["id"])
        games.append({
            "away": e["away_team"],
            "home": e["home_team"],
            "kickoff_utc": e["commence_time"],
            "lead_days": round(lead, 2),
            "bucket": bucket_for(lead),
            "tier": tier_for(e["away_team"], e["home_team"], ref, combos),
            "markets": markets,
            "anytime_td_books": len(markets.get("player_anytime_td", [])),
        })
        tag = ",".join(sorted(markets)) if markets else "-"
        print(f"  [{i + 1:>2}/{len(week)}] lead={lead:5.2f}d {games[-1]['tier']:3} "
              f"{e['away_team'][:24]:24} @ {e['home_team'][:24]:24}  {tag}")
        time.sleep(0.12)

    return _summarize(now, api_key, days_ahead, games, rem)


def _summarize(now, api_key, days_ahead, games, quota_remaining) -> dict:
    def blank():
        return {"games": 0, "any_prop": 0, "anytime_td": 0, "full_suite": 0}

    by_bucket = {b: blank() for b in BUCKETS}
    by_tier = {t: blank() for t in ("P4", "G5", "FCS")}
    by_market = {k: 0 for k in PROP_MARKET_KEYS}

    for g in games:
        has_any = bool(g["markets"])
        has_att = "player_anytime_td" in g["markets"]
        full = "player_pass_yds" in g["markets"] and has_att
        for d in (by_bucket[g["bucket"]], by_tier[g["tier"]]):
            d["games"] += 1
            d["any_prop"] += has_any
            d["anytime_td"] += has_att
            d["full_suite"] += full
        for k in g["markets"]:
            if k in by_market:
                by_market[k] += 1

    n = len(games)
    return {
        "polled_at": now.isoformat(),
        "odds_api_key_tail": api_key[-4:],
        "days_ahead": days_ahead,
        "n_games": n,
        "n_with_any_prop": sum(1 for g in games if g["markets"]),
        "n_with_anytime_td": sum(1 for g in games if "player_anytime_td" in g["markets"]),
        "n_full_suite": sum(1 for g in games if "player_pass_yds" in g["markets"]
                            and "player_anytime_td" in g["markets"]),
        "quota_remaining": quota_remaining,
        "by_bucket": {b: v for b, v in by_bucket.items() if v["games"]},
        "by_tier": by_tier,
        "by_market": by_market,
        "games": games,
    }


def print_report(s: dict) -> None:
    print(f"\n{'=' * 72}")
    print(f"CFB player-prop coverage  |  polled {s['polled_at']}  |  key …{s['odds_api_key_tail']}")
    print(f"{s['n_games']} games within {s['days_ahead']}d  "
          f"|  {s['n_with_any_prop']} with any prop  "
          f"|  {s['n_with_anytime_td']} with player_anytime_td  "
          f"|  {s['n_full_suite']} full suite")
    print(f"{'=' * 72}")

    print(f"\n  by days-to-kickoff       games   any   anytime_td   full")
    for b in BUCKETS:
        v = s["by_bucket"].get(b)
        if v:
            print(f"  {b:<22} {v['games']:6d} {v['any_prop']:5d} {v['anytime_td']:11d} {v['full_suite']:6d}")

    print(f"\n  by matchup tier          games   any   anytime_td   full")
    for t in ("P4", "G5", "FCS"):
        v = s["by_tier"][t]
        print(f"  {t:<22} {v['games']:6d} {v['any_prop']:5d} {v['anytime_td']:11d} {v['full_suite']:6d}")

    print(f"\n  per market key (games populated):")
    for k in PROP_MARKET_KEYS:
        print(f"    {k:<32} {s['by_market'][k]:>3d}")

    thin = [g for g in s["games"] if g["markets"] and "player_pass_yds" not in g["markets"]]
    none = [g for g in s["games"] if not g["markets"]]
    if thin:
        print(f"\n  thin games (no full suite, anytime_td/1st_td only) -- {len(thin)}:")
        for g in thin:
            print(f"    {g['tier']:3} lead={g['lead_days']:5.2f}d  {g['away'][:22]:22} @ {g['home'][:22]:22}  {sorted(g['markets'])}")
    if none:
        print(f"\n  no props at all -- {len(none)} (tier counts: "
              + ", ".join(f"{t}={sum(1 for g in none if g['tier'] == t)}" for t in ('P4', 'G5', 'FCS')) + ")")

    print(f"\n  quota remaining: {s['quota_remaining']}")


def append_log(summary: dict) -> None:
    # The log keeps the decision-relevant shape only: the bucket/tier/market
    # summary in full, plus a per-game row for every game that HAS a prop
    # (so a later run can see which games flipped). Empty games are counted
    # by tier, not listed -- they're the bulk and the count is enough. Full
    # per-game board detail (which books, per market) is --json only.
    record = {k: v for k, v in summary.items() if k != "games"}
    propped = [g for g in summary["games"] if g["markets"]]
    empty = [g for g in summary["games"] if not g["markets"]]
    record["propped_games"] = [
        {"away": g["away"], "home": g["home"], "kickoff_utc": g["kickoff_utc"],
         "lead_days": g["lead_days"], "bucket": g["bucket"], "tier": g["tier"],
         "markets": sorted(g["markets"]), "anytime_td_books": g["anytime_td_books"]}
        for g in propped
    ]
    record["empty_games_by_tier"] = {
        t: sum(1 for g in empty if g["tier"] == t) for t in ("P4", "G5", "FCS")
    }
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")
    print(f"\n  appended run to {LOG_PATH.relative_to(HERE.parent.parent)} "
          f"({LOG_PATH.stat().st_size} bytes, {sum(1 for _ in LOG_PATH.open())} runs logged)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days-ahead", type=int, default=8)
    ap.add_argument("--no-log", action="store_true")
    ap.add_argument("--json", metavar="PATH")
    args = ap.parse_args()

    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        sys.exit("ODDS_API_KEY not set")

    summary = run_sweep(api_key, args.days_ahead)
    print_report(summary)

    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=1))
        print(f"\n  wrote full detail to {args.json}")
    if not args.no_log:
        append_log(summary)


if __name__ == "__main__":
    main()
