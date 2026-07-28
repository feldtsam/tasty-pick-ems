"""
Orchestrates the full per-game pipeline: raw Odds API event data + a
game_pk in, real scored HR-prop picks out. Combines three already-tested,
independent pieces without reimplementing any of them:

  1. flatten_hr_props.flatten_any() — flattens the raw odds event(s) into
     flat (player_name, odds, bookmaker) rows. Same accepted input shapes
     as /api/flatten-and-forward already uses (single event, list, or
     {"events": [...]}), so Make.com's existing odds-fetch step needs zero
     changes to feed this.
  2. live_data.build_candidates_for_game() — this game's real lineup,
     matchup, and environment context, per player, keyed by MLBAM ID.
  3. live_scoring.score_candidate() — the validated five-pillar scorer.

PLAYER MATCHING is the one genuinely new piece: odds data identifies
players by free-text name (The Odds API's `description` field), while
live-data candidates are keyed by MLBAM ID with a name from the MLB Stats
API. There's no shared ID between the two sources, so this has to match on
name — and name mismatches are a REAL failure mode, confirmed against real
data pulled for this exact module: for the 2026-07-28 BAL @ DET game, The
Odds API's own feed spells a real player "Javier Baez" while the MLB Stats
API spells the same real person "Javier Báez" — same game, same player,
different bytes. See normalize_name() and match_players() below for how
this is handled — always exact-match first, a bounded fuzzy fallback
second, and every odds entry that still can't be matched is reported, not
silently dropped.
"""
import difflib
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

_API_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_API_DIR / "live_data"))
sys.path.insert(0, str(_API_DIR / "live_scoring"))

from build_game_candidates import build_candidates_for_game, clean_for_score_candidate  # noqa: E402
from flatten_hr_props import flatten_any  # noqa: E402
from score_candidate import score_candidate  # noqa: E402

# Suffixes stripped from both sides before comparing. Real bookmaker feeds
# are inconsistent about including these — confirmed against real data:
# The Odds API's feed for this project just drops them ("Vladimir Guerrero"
# for a player MLB's own API lists with "Jr." attached) rather than
# spelling them differently. Stripping from both sides is safe in
# practice: a real Jr./Sr. pair both active in MLB on the same game slate,
# sharing a market, is not a realistic collision to guard against here.
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

# difflib.SequenceMatcher ratio cutoff for the fuzzy fallback — deliberately
# high. This only ever runs against the small (~18-name), single-game pool
# of that game's own lineup, not the whole league, which keeps false-positive
# risk low even at this cutoff.
FUZZY_MATCH_CUTOFF = 0.85
# How much better the best fuzzy candidate must be than the runner-up to be
# accepted without human review — otherwise it's ambiguous, not guessed at.
FUZZY_MARGIN_OVER_RUNNER_UP = 0.05


def normalize_name(name: str) -> str:
    """
    Case/accent/punctuation/suffix-insensitive normalization, applied
    identically to both odds and live-data names so exact comparison after
    normalization is the primary (non-fuzzy) matching path:
      - Unicode NFKD decompose + drop combining marks: "Báez" -> "Baez",
        "Peña" -> "Pena" — confirmed necessary against real data (see
        module docstring).
      - Lowercase; strip periods/apostrophes/commas (keeps letters, digits,
        spaces, hyphens — "O'Hoppe" and "Jean Segura" both survive intact).
      - Drop a trailing generational suffix (Jr/Sr/II/III/IV/V) if present.
      - Collapse whitespace.
    """
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", name)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = without_accents.lower()
    no_punct = re.sub(r"[.,'’]", "", lowered)
    letters_only = re.sub(r"[^a-z0-9\- ]", " ", no_punct)
    tokens = letters_only.split()
    if tokens and tokens[-1] in _SUFFIXES:
        tokens = tokens[:-1]
    return " ".join(tokens)


def _fuzzy_match(name: str, candidate_keys: list):
    """Returns (matched_key, ratio) for a confident single fuzzy match
    against candidate_keys, or (None, 0.0) if there's no match clearing
    FUZZY_MATCH_CUTOFF, or if the top two candidates are too close to call
    (ambiguous, not guessed)."""
    close = difflib.get_close_matches(name, candidate_keys, n=2, cutoff=FUZZY_MATCH_CUTOFF)
    if not close:
        return None, 0.0
    top_ratio = difflib.SequenceMatcher(None, name, close[0]).ratio()
    if len(close) == 1:
        return close[0], top_ratio
    runner_up_ratio = difflib.SequenceMatcher(None, name, close[1]).ratio()
    if top_ratio - runner_up_ratio >= FUZZY_MARGIN_OVER_RUNNER_UP:
        return close[0], top_ratio
    return None, 0.0  # too close to call — treated as unmatched, not guessed


def match_players(odds_rows: list, candidates: list) -> dict:
    """
    Matches flattened odds rows (each with a `player_name`) to this game's
    live-data candidates (each with `player_name` and `mlbam_id`), by name.

    Matching order, per distinct normalized odds player name:
      1. EXACT — normalize_name() on both sides, direct dict lookup. The
         primary path; confirmed to cover the large majority of real
         players (17 of 18 for the real BAL@DET test case below).
      2. FUZZY — only if step 1 finds nothing: difflib against this game's
         own (small, ~18-name) candidate pool, cutoff=0.85, accepted only
         if exactly one candidate is a confident, unambiguous best match
         (see _fuzzy_match). Every fuzzy match is tagged
         match_type="fuzzy" in the output — never silently treated the
         same as an exact match, so a caller can choose to trust it or
         flag it for human review.
      Anything left over is reported in `unmatched_odds` with the raw
      name and its odds rows preserved — never silently dropped.

    A name that normalizes to more than one live-data candidate (a genuine
    same-name collision within this one game — rare, but the odds data has
    no team field to disambiguate with) is also reported as unmatched
    rather than guessed.

    Returns {
      "matched": [{"candidate": {...}, "odds_rows": [...], "best_odds": int,
                   "best_odds_bookmaker": str, "num_bookmakers": int,
                   "match_type": "exact"|"fuzzy", "matched_name_raw": str}],
      "unmatched_odds": [{"player_name": str, "reason": str, "odds_rows": [...]}],
      "unmatched_candidates": [candidate, ...],  # this game's lineup players with no odds offered at all — normal, informational only
    }
    """
    candidate_pool = {}
    for c in candidates:
        candidate_pool.setdefault(normalize_name(c["player_name"]), []).append(c)

    odds_groups = {}
    for row in odds_rows:
        odds_groups.setdefault(normalize_name(row["player_name"]), []).append(row)

    matched = []
    unmatched_odds = []
    used_mlbam_ids = set()

    for norm_name, rows in odds_groups.items():
        pool_hit = candidate_pool.get(norm_name)
        match_type = "exact"

        if pool_hit is None:
            fuzzy_key, _ratio = _fuzzy_match(norm_name, list(candidate_pool.keys()))
            if fuzzy_key is not None:
                pool_hit = candidate_pool[fuzzy_key]
                match_type = "fuzzy"

        if pool_hit is None:
            unmatched_odds.append({
                "player_name": rows[0]["player_name"],
                "reason": "no exact or confident fuzzy match against this game's confirmed lineup",
                "odds_rows": rows,
            })
            continue

        if len(pool_hit) > 1:
            unmatched_odds.append({
                "player_name": rows[0]["player_name"],
                "reason": f"ambiguous — {len(pool_hit)} live-data players in this game normalize to the same "
                          f"name and odds data has no team field to disambiguate",
                "odds_rows": rows,
            })
            continue

        candidate = pool_hit[0]
        best_row = max(rows, key=lambda r: r["odds"])  # American odds: higher raw value is always the better price, positive or negative
        used_mlbam_ids.add(candidate["mlbam_id"])
        matched.append({
            "candidate": candidate,
            "odds_rows": rows,
            "best_odds": best_row["odds"],
            "best_odds_bookmaker": best_row["bookmaker"],
            "num_bookmakers": len(rows),
            "match_type": match_type,
            "matched_name_raw": rows[0]["player_name"],
        })

    unmatched_candidates = [c for c in candidates if c["mlbam_id"] not in used_mlbam_ids]

    return {"matched": matched, "unmatched_odds": unmatched_odds, "unmatched_candidates": unmatched_candidates}


def _build_scored_pick(match: dict, game: dict, score_result: dict) -> dict:
    """One row for the scored_picks table/webhook — see README for the
    full schema this is meant to match. Flat, queryable columns for
    everything a caller would filter/sort on, plus one nested
    `pillar_detail` blob (components + notes) for drill-down/debugging."""
    c = match["candidate"]
    pillars = score_result["pillars"]
    return {
        "player_name": c["player_name"],
        "mlbam_id": c["mlbam_id"],
        "team": c["team"],
        "batting_order_slot": c["batting_order_slot"],
        "opp_pitcher_name": c.get("opp_pitcher_name"),
        "opp_pitcher_mlbam_id": c.get("opp_pitcher_mlbam_id"),
        "game_pk": game["game_pk"],
        "home_team": game["home_team"],
        "away_team": game["away_team"],
        "game_date_utc": game["game_date_utc"],
        "venue_name": (game.get("venue") or {}).get("name"),
        "odds": match["best_odds"],
        "bookmaker": match["best_odds_bookmaker"],
        "num_bookmakers": match["num_bookmakers"],
        "match_type": match["match_type"],
        "skill_score": pillars["skill"]["score"],
        "matchup_score": pillars["matchup"]["score"],
        "environment_score": pillars["environment"]["score"],
        "opportunity_score": pillars["opportunity"]["score"],
        "final_score": score_result["final_score"],
        "star_rating": score_result["star_rating"],
        "score_tier": score_result["score_tier"],
        "passes_odds_filter": score_result["passes_odds_filter"],
        "pillar_detail": pillars,
        "notes": score_result["notes"],
        "scored_at": datetime.now(timezone.utc).isoformat(),
    }


def build_scored_picks_for_game(game_pk: int, raw_odds_event) -> dict:
    """
    The orchestrator. `raw_odds_event` accepts the exact same shapes
    flatten_any() does (single event object, list of events, or
    {"events": [...]}). PURE — no network call to Lovable happens here;
    that's the Flask route's job (see index.py), matching the existing
    separation between flatten_hr_props.py (pure) and lovable_forward.py
    (network) so this stays fully testable offline.

    Returns:
      {
        "game_pk": int,
        "matchup": {"away_team": str, "home_team": str, "lineup_status": str},
        "scored_picks": [ ...one dict per matched, successfully-scored player... ],
        "match_summary": {
          "odds_entries_total": int, "matched": int,
          "unmatched_odds": [...], "unmatched_odds_count": int,
          "unmatched_candidates_count": int,
        },
        "errors": [ {"player_name": str, "error": str}, ... ],  # per-player scoring failures — never silently swallowed
      }
    """
    odds_rows = flatten_any(raw_odds_event)
    if odds_rows is None:
        return {
            "game_pk": game_pk,
            "matchup": None,
            "scored_picks": [],
            "match_summary": {"odds_entries_total": 0, "matched": 0, "unmatched_odds": [],
                               "unmatched_odds_count": 0, "unmatched_candidates_count": 0},
            "errors": [{"player_name": None, "error": "raw_odds_event was not a recognized shape "
                                                        "(expected a single event object, a list of events, or {'events': [...]})"}],
        }

    game_result = build_candidates_for_game(game_pk)
    game = game_result["game"]

    matchup = {"away_team": game["away_team"], "home_team": game["home_team"], "lineup_status": game["lineup_status"]}

    if game["lineup_status"] != "confirmed":
        return {
            "game_pk": game_pk,
            "matchup": matchup,
            "scored_picks": [],
            "match_summary": {
                "odds_entries_total": len(odds_rows), "matched": 0,
                "unmatched_odds": [{"player_name": r["player_name"], "reason": f"game lineup_status is "
                                    f"{game['lineup_status']!r}, not 'confirmed' — no live-data candidates exist yet", "odds_rows": [r]}
                                   for r in odds_rows],
                "unmatched_odds_count": len(odds_rows),
                "unmatched_candidates_count": 0,
            },
            "errors": [],
        }

    match_result = match_players(odds_rows, game["candidates"])

    scored_picks = []
    errors = []
    for match in match_result["matched"]:
        clean = clean_for_score_candidate(match["candidate"])
        clean["odds"] = match["best_odds"]
        try:
            score_result = score_candidate(clean)
        except Exception as e:  # noqa: BLE001 — one bad candidate must not sink the whole game's batch
            errors.append({"player_name": match["candidate"]["player_name"], "error": f"{type(e).__name__}: {e}"})
            continue
        scored_picks.append(_build_scored_pick(match, game, score_result))

    return {
        "game_pk": game_pk,
        "matchup": matchup,
        "scored_picks": scored_picks,
        "match_summary": {
            "odds_entries_total": len(odds_rows),
            "matched": len(match_result["matched"]),
            "unmatched_odds": match_result["unmatched_odds"],
            "unmatched_odds_count": len(match_result["unmatched_odds"]),
            "unmatched_candidates_count": len(match_result["unmatched_candidates"]),
        },
        "errors": errors,
    }
