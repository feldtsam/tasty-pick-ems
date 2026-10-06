"""
CFB anytime-TD odds poller — the processing layer behind
POST /api/poll-cfb-attd-odds (cfb/api/index.py).

Make.com fetches raw Odds API event JSON (americanfootball_ncaaf,
market player_anytime_td, oddsFormat american) and POSTs it here; this
module never calls The Odds API. Per event it:

  1. parses the player_anytime_td market with poll_ncaaf_prop_coverage.
     parse_event_odds (every book present, name/description verbatim),
  2. detects the outcome schema and matches players to the CFBD roster
     constrained to the event's two schools (attd_match.
     match_cfb_attd_players, unchanged -- its four unmatched reasons are
     carried through to the response),
  3. resolves game_id / team_id / opponent from that week's CFBD /games,
  4. shapes one row per matched player (attd_match.shape_cfb_attd_odds_
     rows, unchanged) and adds the consensus fields using NFL's exact
     rule (see consensus_from_book_odds), event_id, commence_time and a
     single poll_timestamp for the whole request.

Rows land in cfb_player_attd_odds_weekly (unique on player_id/season/
week, latest poll wins) through the same signed forward every other CFB
write uses. Unmatched outcomes are counted, never written -- the table is
keyed by a real CFBD athleteId.

CFBD COST: two calls per request at most, both cached in-process --
/games for the week (game ids, team ids, opponents) and the season
/roster (~15k rows) -- and the roster is fetched lazily, only when at
least one event actually carries the market. A warm Vercel instance
reuses both across the batches of one poll; a cold one pays them once.
"""
from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from attd_match import implied_probability, match_cfb_attd_players, shape_cfb_attd_odds_rows  # noqa: E402
from ids import CFBDError, team_id_map_from_games  # noqa: E402
from lovable_forward import forward_to_lovable, resolve_url_env  # noqa: E402
from plays_stats import fetch_games  # noqa: E402
from poll_ncaaf_prop_coverage import load_fbs_ref, parse_event_odds  # noqa: E402
from roster import fetch_fbs_roster  # noqa: E402

ATTD_ODDS_WRITE_URL_ENV = "LOVABLE_CFB_PLAYER_ATTD_ODDS_WEEKLY_WRITE_URL"
DEFAULT_ATTD_ODDS_WRITE_URL = "https://tastypickems.lovable.app/api/public/cfb-player-attd-odds-weekly-write"

# Rows per signed POST. ~1,100 rows is a full priced Saturday (45 games x
# ~25 players); 250 keeps each body well under 1 MB and makes a partial
# failure attributable to a chunk, not the whole poll.
FORWARD_CHUNK_SIZE = 250

EXPECTED_INPUT_ERROR = (
    "Expected {\"season\": int, \"week\": int, \"events\": [...]} where events is a list of Odds API "
    "event objects; a single event object (with a 'bookmakers' key) or a bare list is also accepted."
)

UNMATCHED_REASONS = ("rookie_or_new", "position_out_of_scope", "team_mismatch", "name_collision")

# In-process caches -- a warm Vercel instance serves one poll's batches
# without re-fetching; a cold instance fetches once per request.
_GAMES_CACHE: dict = {}
_ROSTER_CACHE: dict = {}
_FBS_REF_CACHE: dict = {}


def normalize_events_input(data):
    """Same tolerant shapes as NFL's /api/poll-market-value
    (_normalize_events_input): {"events": [...]}, a single event object
    (has "bookmakers"), or a bare list. None when unrecognizable."""
    if isinstance(data, dict) and "events" in data:
        return data["events"] if isinstance(data["events"], list) else None
    if isinstance(data, dict) and "bookmakers" in data:
        return [data]
    if isinstance(data, list):
        return data
    return None


def probability_to_american(p: float) -> int:
    """NFL's _probability_to_american, scalar: p <= 0.5 -> positive
    (underdog) price, p > 0.5 -> negative (favorite) price, rounded."""
    p = float(p)
    if p <= 0.5:
        return int(round((1 - p) / p * 100))
    return int(round(-(p / (1 - p)) * 100))


def consensus_from_book_odds(book_odds: list) -> dict:
    """
    NFL's exact rule (nfl/market_value.py snapshot_scoring_inputs),
    applied to one player's deduped per-book list:
      * consensus_implied_probability = MEDIAN of each book's own implied
        probability (never an average of raw American prices -- they are
        not linear in probability space),
      * best_price / best_book = the book with the LOWEST implied
        probability (the longest payout; a numeric max on raw prices
        gets the sign mix wrong),
      * n_books = distinct bookmakers,
      * consensus_price_american = the consensus probability converted
        back to an American price.
    One book degenerates to that book's own price everywhere.
    """
    books = [b for b in book_odds if b.get("odds") is not None]
    if not books:
        return {"n_books": 0, "best_price": None, "best_book": None,
                "consensus_implied_probability": None, "consensus_price_american": None}
    probs = [float(b.get("implied_prob")) if b.get("implied_prob") is not None else implied_probability(b["odds"]) for b in books]
    best_i = min(range(len(books)), key=lambda i: probs[i])
    consensus_p = statistics.median(probs)
    return {
        "n_books": len({b["bookmaker"] for b in books}),
        "best_price": int(books[best_i]["odds"]),
        "best_book": books[best_i]["bookmaker"],
        "consensus_implied_probability": round(consensus_p, 4),
        "consensus_price_american": probability_to_american(consensus_p),
    }


def resolve_event_game(home_school, away_school, games: list):
    """The CFBD /games entry whose two schools are exactly this event's
    resolved pair, either orientation (neutral-site listings can flip
    home/away relative to the sportsbook). None when not found."""
    if not home_school or not away_school:
        return None
    pair = {home_school, away_school}
    for g in games:
        if {g.get("homeTeam"), g.get("awayTeam")} == pair:
            return g
    return None


def _games_for(season: int, week: int, calls: list) -> list:
    key = (int(season), int(week))
    if key not in _GAMES_CACHE:
        _GAMES_CACHE[key] = fetch_games(season, week)
        calls.append("/games")
    return _GAMES_CACHE[key]


def _roster_for(season: int, calls: list) -> list:
    key = int(season)
    if key not in _ROSTER_CACHE:
        _ROSTER_CACHE[key] = fetch_fbs_roster(season)
        calls.append("/roster")
    return _ROSTER_CACHE[key]


def _fbs_ref() -> tuple:
    if "ref" not in _FBS_REF_CACHE:
        _FBS_REF_CACHE["ref"] = load_fbs_ref()
    return _FBS_REF_CACHE["ref"]


def clear_caches() -> None:
    """Test hook."""
    _GAMES_CACHE.clear()
    _ROSTER_CACHE.clear()
    _FBS_REF_CACHE.clear()


def process_events(
    events: list,
    season: int,
    week: int,
    *,
    poll_timestamp: str,
    games: list | None = None,
    roster: list | None = None,
    fbs_ref: tuple | None = None,
    cfbd_calls: list | None = None,
) -> dict:
    """
    Parse, match, resolve and shape every event. Pure apart from the
    lazily-cached CFBD fetches (injectable for tests via games= /
    roster= / fbs_ref=). Returns {"rows": [...], "diagnostics": {...}};
    rows are already in cfb_player_attd_odds_weekly's column shape.
    """
    calls = cfbd_calls if cfbd_calls is not None else []
    ref, combos = fbs_ref if fbs_ref is not None else _fbs_ref()

    diag = {
        "events_received": len(events),
        "events_with_no_market": [],
        "events_with_parse_errors": [],
        "events_processed": 0,
        "events_game_unresolved": [],
        "schema_seen": {},
        "matched": 0,
        "unmatched_by_reason": {reason: 0 for reason in UNMATCHED_REASONS},
        "unmatched_sample": [],
    }
    rows: list = []
    games_list = games
    roster_list = roster

    for event in events:
        event_id = event.get("id") if isinstance(event, dict) else None
        try:
            _, outcomes = parse_event_odds(event)
            if not outcomes:
                diag["events_with_no_market"].append(event_id)
                continue
            if roster_list is None:
                roster_list = _roster_for(season, calls)
            if games_list is None:
                games_list = _games_for(season, week, calls)

            match = match_cfb_attd_players(
                outcomes, event.get("home_team"), event.get("away_team"), roster_list, ref, combos,
            )
            diag["schema_seen"][match["schema"]] = diag["schema_seen"].get(match["schema"], 0) + 1
            diag["matched"] += len(match["matched"])
            for u in match["unmatched"]:
                reason = u.get("match_issue_type")
                diag["unmatched_by_reason"][reason] = diag["unmatched_by_reason"].get(reason, 0) + 1
                if len(diag["unmatched_sample"]) < 25:
                    diag["unmatched_sample"].append({"event_id": event_id, "name": u.get("player_name_raw"), "reason": reason})

            name_map = team_id_map_from_games(games_list)
            game = resolve_event_game(match["home_school"], match["away_school"], games_list)
            if game is None:
                diag["events_game_unresolved"].append(event_id)
            game_id = int(game["id"]) if game and game.get("id") is not None else None

            for row in shape_cfb_attd_odds_rows(match["matched"], season, week):
                team = row.get("team")
                opponent = match["away_school"] if team == match["home_school"] else match["home_school"]
                row.update({
                    "team_id": name_map.get(team),
                    "opponent": opponent,
                    "opponent_team_id": name_map.get(opponent),
                    "game_id": game_id,
                    "event_id": event_id,
                    "commence_time": event.get("commence_time"),
                    "poll_timestamp": poll_timestamp,
                    **consensus_from_book_odds(row["book_odds"]),
                    "extra": {"schema": match["schema"], "home_team_raw": event.get("home_team"), "away_team_raw": event.get("away_team")},
                })
                rows.append(row)
            diag["events_processed"] += 1
        except CFBDError:
            # A CFBD outage is a request-level failure (the endpoint turns
            # it into a 502), never a per-event "parse error" that would
            # let the batch report 200 with nothing priced.
            raise
        except Exception as e:  # noqa: BLE001 -- one malformed event never sinks the batch (same as NFL's poller)
            diag["events_with_parse_errors"].append({"event_id": event_id, "error": f"{type(e).__name__}: {e}"})
            print(f"[poll-cfb-attd-odds] event_id={event_id!r} parse_error={e!r} -- skipping this event, batch continues", flush=True)

    diag["players_priced"] = len(rows)
    diag["cfbd_calls"] = list(calls)
    return {"rows": rows, "diagnostics": diag}


def forward_rows_in_chunks(rows: list, secret: str, url: str | None = None, chunk_size: int = FORWARD_CHUNK_SIZE) -> dict:
    """
    Signed POSTs of {"rows": [...]} in chunks of chunk_size to the odds
    write route (resolve_url_env fallback, same as every other CFB
    forward). Stops at the first failed chunk and reports it -- a partial
    write is visible, never a silent success. Returns {success, status_
    code, error, response_body, chunks_sent, chunks_total, rows_sent}.
    """
    if not rows:
        return {"success": None, "status_code": None, "error": None, "response_body": None,
                "chunks_sent": 0, "chunks_total": 0, "rows_sent": 0}
    resolved_url = url or resolve_url_env(ATTD_ODDS_WRITE_URL_ENV, DEFAULT_ATTD_ODDS_WRITE_URL)
    chunks = [rows[i:i + chunk_size] for i in range(0, len(rows), chunk_size)]
    last = None
    sent = 0
    for i, chunk in enumerate(chunks):
        payload = json.loads(json.dumps({"rows": chunk}, default=str))
        last = forward_to_lovable(payload, secret, resolved_url)
        if not last["success"]:
            return {**last, "chunks_sent": i, "chunks_total": len(chunks), "rows_sent": sent, "failed_chunk_index": i}
        sent += len(chunk)
    return {**last, "chunks_sent": len(chunks), "chunks_total": len(chunks), "rows_sent": sent}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
