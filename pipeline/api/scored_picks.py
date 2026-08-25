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

ODDS FILTER — a genuine HARD PRE-SCORING GATE, not just an informational
flag. The product spec (Tasty Pick Ems Master Product Blueprint §6) is
explicit: "Every candidate bet must clear +300 or higher before it enters
scoring at all... anything under +300 is discarded before the AI or the
rules engine ever touches it." An earlier version of this module didn't
actually do that — it matched and scored every candidate regardless of
odds, and only recorded a `passes_odds_filter` boolean on the output
(score_candidate.py's own field, still present and still useful as a
self-descriptive flag on anything scored directly). That let real
sub-+300 candidates (confirmed: Willson Contreras at +245) reach
`scored_picks`. Fixed here: any matched candidate whose best odds fall
below `MIN_ODDS_FOR_FILTER` (imported from score_candidate.py, so there's
one source of truth for the threshold, not two) is filtered out BEFORE
score_candidate() is ever called on it — never scored, never stored, and
reported by name in `match_summary.excluded_below_odds_filter` rather than
silently vanishing.
"""
import difflib
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

_API_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_API_DIR / "live_data"))
sys.path.insert(0, str(_API_DIR / "live_scoring"))

import requests

from build_game_candidates import build_candidates_for_game, clean_for_score_candidate  # noqa: E402
from flatten_hr_props import flatten_any  # noqa: E402
from lovable_forward import compute_signature, serialize_payload  # noqa: E402
from recent_form import fetch_batters_recent_form, fetch_pitchers_recent_form  # noqa: E402
from score_candidate import MIN_ODDS_FOR_FILTER, score_candidate  # noqa: E402

RECENT_STATCAST_FORM_REQUEST_TIMEOUT_SECONDS = 20

# Empty-shaped fallbacks so a player with no window sample (or no opposing
# pitcher on record yet) still gets real, honest zero/None values on the
# scored_picks row rather than a missing key downstream.
_EMPTY_BATTER_FORM = {"recent_games_sampled": 0, "recent_ops": None, "recent_hr_per_pa": None,
                       "recent_home_runs": 0, "recent_hits": 0, "recent_xbh": 0}
_EMPTY_PITCHER_FORM = {"recent_starts_sampled": 0, "recent_innings_pitched": 0.0, "recent_era": None,
                        "recent_hr_per_9": None, "recent_k_per_9": None, "recent_bb_per_9": None,
                        "recent_home_runs": 0, "recent_hits": 0}

# A batter with no row in recent_statcast_form yet (the daily batch job
# hasn't run, or genuinely has zero real batted-ball events in its
# trailing window) gets real, honest nulls here rather than a missing key
# downstream — same "empty-shaped fallback" treatment as the two above.
_EMPTY_STATCAST_FORM = {"recent_barrel_pct": None, "recent_fb_pct": None,
                         "recent_avg_launch_angle": None, "recent_batted_ball_events": None}


def fetch_recent_statcast_form(secret: str, read_url: str) -> dict:
    """
    Calls Lovable's signed recent-statcast-form-read endpoint — the daily-
    refreshed table pipeline/scripts/recent_statcast_form.py populates
    (real Barrel %/Fly-Ball %/avg Launch Angle over a trailing calendar-
    day window, bulk-pulled once a day; see that script's own docstring
    for why this can't be a live per-request Statcast call). Returns
    {mlbam_id (int): {...row...}}, one entry per real batter with at
    least one batted-ball event in the window. Same signed-POST pattern
    as curate_shelves.py's fetch_todays_scored_picks, reusing
    compute_signature()/serialize_payload() directly rather than
    reimplementing the signing.
    """
    payload_str = serialize_payload({})
    signature = compute_signature(secret, payload_str)
    response = requests.post(
        read_url,
        data=payload_str.encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Signature": signature},
        timeout=RECENT_STATCAST_FORM_REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    rows = response.json().get("recent_statcast_form", [])
    return {int(r["mlbam_id"]): r for r in rows}

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


def _implied_probability(price) -> float:
    """
    Standard American-odds -> implied probability conversion. Same real
    formula as NFL's market_value.py's implied_probability(), scalar
    here rather than pandas-vectorized since nothing else in this file
    uses pandas — deliberately not introducing it as a new dependency
    for one calculation. Positive price (underdog-style payout):
    100 / (price + 100). Negative price (favorite-style payout):
    -price / (-price + 100).
    """
    price = float(price)
    return 100.0 / (price + 100.0) if price > 0 else -price / (-price + 100.0)


def _book_odds_for_match(rows: list) -> list:
    """
    Reshapes match_players()'s own real per-book rows (match["odds_rows"]
    — already computed, already in memory; nothing re-fetched) into the
    real book_odds column shape: [{bookmaker, odds, implied_prob}, ...].
    implied_prob is stored as a raw 0-1 fraction (rounded to 4 decimal
    places for real JSON-payload cleanliness — not pre-rounded to a
    display precision; that stays the reader's job, same "store raw,
    round for display" convention NFL's own consensus_implied_
    probability already follows). Order matches the order odds_rows
    arrived in (bookmaker order from the raw Odds API response) — not
    re-sorted by price, so a caller wanting "best price first" sorts it
    themselves; this column's job is to carry the full real picture, not
    pre-opine on presentation order.
    """
    return [
        {"bookmaker": r["bookmaker"], "odds": r["odds"], "implied_prob": round(_implied_probability(r["odds"]), 4)}
        for r in rows
    ]


# Real safety gate, not a placeholder -- confirmed directly (see the
# conversation this was added in) that _build_scored_pick()'s full
# returned dict reaches forward_to_lovable() completely unmodified:
# index.py's write route does `scored_picks = result["scored_picks"]`
# then `forward_to_lovable(scored_picks, secret, url)` with NO
# intermediate allowlist/mapping step in between. That means whatever
# keys this function puts in its return dict are exactly what gets
# POSTed -- there's no separate place to "wire book_odds into the
# write" the way there might be if a shaping layer existed here.
#
# Given that, adding book_odds unconditionally would ship it into every
# real write starting immediately, before the real book_odds column
# exists on Lovable's scored_picks table -- and this codebase has no
# visibility into whether that endpoint's real implementation silently
# ignores an unrecognized field or hard-rejects the whole request (a
# strict Zod/schema check would fail EVERY scored-picks-write, not just
# silently drop book_odds -- a real regression risk for the already-
# working odds/bookmaker/num_bookmakers fields too, not contained to
# this one new field). Not something to guess at from this side alone
# -- see the report this was flagged in.
#
# SCORED_PICKS_INCLUDE_BOOK_ODDS, unset/false by default: book_odds is
# built by _book_odds_for_match() either way (cheap, pure, already
# tested) but only actually added to the real write payload when this
# is explicitly turned on -- flip it once Lovable confirms the column
# exists, no other code change needed at that point.
_INCLUDE_BOOK_ODDS_IN_WRITE = os.environ.get("SCORED_PICKS_INCLUDE_BOOK_ODDS", "").strip().lower() in ("1", "true", "yes")


def _build_scored_pick(match: dict, game: dict, score_result: dict,
                        batter_form: dict, pitcher_form: dict, statcast_form: dict) -> dict:
    """One row for the scored_picks table/webhook — see README for the
    full schema this is meant to match. Flat, queryable columns for
    everything a caller would filter/sort on, plus one nested
    `pillar_detail` blob (components + notes) for drill-down/debugging.

    batter_form/pitcher_form are the {mlbam_id: {...}} dicts from
    recent_form.py, fetched once per game for every eligible candidate (see
    build_scored_picks_for_game) — NOT re-fetched per-shelf. This is what
    replaces StoryDetail.tsx's previous seeded-random placeholder numbers
    with the candidate's own real last-15-games form and the real recent
    form of the specific opposing pitcher they're facing.

    statcast_form is the {mlbam_id: {...}} dict from
    recent_statcast_form.py's daily batch job (see fetch_recent_
    statcast_form) — HITTERS ONLY, keyed on the candidate's OWN mlbam_id,
    never the opposing pitcher's. Pitcher-allowed Statcast metrics
    (Hard-Hit % Allowed, Exit Velo, xERA) are out of scope for this.

    book_odds is gated by _INCLUDE_BOOK_ODDS_IN_WRITE (see its own
    comment right above this function) — omitted from the returned dict
    entirely when the gate is off, not set to null/empty. Omitting the
    key outright, rather than sending an explicit null, means a caller
    with the gate off produces byte-identical output to before book_odds
    existed at all."""
    c = match["candidate"]
    pillars = score_result["pillars"]
    own_form = batter_form.get(c["mlbam_id"], _EMPTY_BATTER_FORM)
    opp_id = c.get("opp_pitcher_mlbam_id")
    opp_form = pitcher_form.get(opp_id, _EMPTY_PITCHER_FORM) if opp_id else _EMPTY_PITCHER_FORM
    own_statcast_form = statcast_form.get(c["mlbam_id"], _EMPTY_STATCAST_FORM)
    row = {
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
        # Raw environmental inputs, not just the normalized 0-100 environment
        # sub-scores already in pillar_detail. These already exist on the
        # live_data candidate dict (build_game_candidates.py sets them from
        # the game's real feed/live weather) but were being discarded here
        # rather than persisted — score_candidate() consumes them to compute
        # environment_score and never returns them. A caller writing
        # specific, real weather copy (e.g. "wind blowing out at 7 mph")
        # needs the actual units, not just the percentile.
        "temp_f": c.get("temp_f"),
        "wind_speed_mph": c.get("wind_speed_mph"),
        "wind_description": c.get("wind_description"),
        "roof_status": c.get("roof_status"),
        # The candidate's OWN real recent form (last 15 games played).
        "recent_games_sampled": own_form.get("recent_games_sampled"),
        "recent_ops": own_form.get("recent_ops"),
        "recent_home_runs": own_form.get("recent_home_runs"),
        "recent_hits": own_form.get("recent_hits"),
        "recent_xbh": own_form.get("recent_xbh"),
        # The OPPOSING PITCHER's real recent form (last 5 real starts) —
        # what "Cold Pitchers to Attack" cards actually mean by "recent
        # form": how the pitcher this batter is facing has looked lately,
        # not the batter's own numbers a second time.
        "opp_pitcher_recent_starts_sampled": opp_form.get("recent_starts_sampled"),
        "opp_pitcher_recent_era": opp_form.get("recent_era"),
        "opp_pitcher_recent_hr_per_9": opp_form.get("recent_hr_per_9"),
        "opp_pitcher_recent_home_runs": opp_form.get("recent_home_runs"),
        "opp_pitcher_recent_hits": opp_form.get("recent_hits"),
        # Real recent-window Statcast metrics (trailing calendar-day
        # window, a separate daily batch job — see recent_statcast_form.py)
        # for the candidate's OWN hitting only. Null when the daily job
        # hasn't run yet, or the player has too few real batted-ball
        # events to clear its sample-size gate — never fabricated.
        "recent_barrel_pct": own_statcast_form.get("recent_barrel_pct"),
        "recent_fb_pct": own_statcast_form.get("recent_fb_pct"),
        "recent_avg_launch_angle": own_statcast_form.get("recent_avg_launch_angle"),
        "recent_batted_ball_events": own_statcast_form.get("recent_batted_ball_events"),
        "notes": score_result["notes"],
        "scored_at": datetime.now(timezone.utc).isoformat(),
    }
    if _INCLUDE_BOOK_ODDS_IN_WRITE:
        # Additive, not a replacement -- odds/bookmaker/num_bookmakers
        # above (the single best price) still drive scoring/shelf logic
        # unchanged; book_odds carries the full real per-book picture
        # match_players() already computed (match["odds_rows"]) but this
        # function used to discard.
        row["book_odds"] = _book_odds_for_match(match["odds_rows"])
    return row


def build_scored_picks_for_game(game_pk: int, raw_odds_event, recent_statcast_form: dict | None = None) -> dict:
    """
    The orchestrator. `raw_odds_event` accepts the exact same shapes
    flatten_any() does (single event object, list of events, or
    {"events": [...]}). PURE — no network call to Lovable happens here;
    that's the Flask route's job (see index.py), matching the existing
    separation between flatten_hr_props.py (pure) and lovable_forward.py
    (network) so this stays fully testable offline.

    `recent_statcast_form` is the pre-fetched {mlbam_id: {...}} lookup
    from fetch_recent_statcast_form() — deliberately a PARAMETER here, not
    fetched internally the way batter_form/pitcher_form are below (those
    call the free MLB Stats API directly; this one is a signed call to
    Lovable, and keeping any Lovable network call out of this function is
    the one invariant this docstring has always promised). The Flask
    route fetches it once and passes it in; omitted (None) by any other
    real or test caller, in which case every candidate gets the real,
    honest empty-shaped Statcast form (see _EMPTY_STATCAST_FORM) rather
    than an error.

    Returns:
      {
        "game_pk": int,
        "matchup": {"away_team": str, "home_team": str, "lineup_status": str},
        "scored_picks": [ ...one dict per matched, odds-eligible, successfully-scored player... ],
        "match_summary": {
          "odds_entries_total": int,
          "matched": int,                          # matched by NAME — before the odds gate
          "unmatched_odds": [...], "unmatched_odds_count": int,
          "unmatched_candidates_count": int,
          "excluded_below_odds_filter": [...],      # matched by name, but odds < MIN_ODDS_FOR_FILTER — never scored
          "excluded_below_odds_filter_count": int,
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
                               "unmatched_odds_count": 0, "unmatched_candidates_count": 0,
                               "excluded_below_odds_filter": [], "excluded_below_odds_filter_count": 0},
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
                "excluded_below_odds_filter": [], "excluded_below_odds_filter_count": 0,
            },
            "errors": [],
        }

    match_result = match_players(odds_rows, game["candidates"])

    # Hard pre-scoring odds gate (see module docstring) — anything below
    # +300 never reaches score_candidate() at all, matching the product
    # spec's "discarded before... scoring... ever touches it".
    below_odds_filter = [m for m in match_result["matched"] if m["best_odds"] < MIN_ODDS_FOR_FILTER]
    eligible_matches = [m for m in match_result["matched"] if m["best_odds"] >= MIN_ODDS_FOR_FILTER]

    # Recent-form fetched ONCE per game, for every odds-eligible candidate
    # (and every distinct opposing pitcher they face) — not per-shelf. This
    # is what gives every published shelf (not just Hot Hitters/Cold
    # Pitchers, which previously called recent_form.py themselves) real
    # last-N-games data on the scored_picks row, with a single batch of API
    # calls shared across shelves instead of duplicated per shelf.
    current_season = game_result.get("current_season") or int(game["game_date_utc"][:4])
    batter_ids = [m["candidate"]["mlbam_id"] for m in eligible_matches]
    opp_pitcher_ids = list({
        m["candidate"]["opp_pitcher_mlbam_id"] for m in eligible_matches
        if m["candidate"].get("opp_pitcher_mlbam_id")
    })
    batter_form = fetch_batters_recent_form(batter_ids, current_season) if batter_ids else {}
    pitcher_form = fetch_pitchers_recent_form(opp_pitcher_ids, current_season) if opp_pitcher_ids else {}
    statcast_form = recent_statcast_form if recent_statcast_form is not None else {}

    scored_picks = []
    errors = []
    for match in eligible_matches:
        clean = clean_for_score_candidate(match["candidate"])
        clean["odds"] = match["best_odds"]
        try:
            score_result = score_candidate(clean)
        except Exception as e:  # noqa: BLE001 — one bad candidate must not sink the whole game's batch
            errors.append({"player_name": match["candidate"]["player_name"], "error": f"{type(e).__name__}: {e}"})
            continue
        scored_picks.append(_build_scored_pick(match, game, score_result, batter_form, pitcher_form, statcast_form))

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
            "excluded_below_odds_filter": [
                {"player_name": m["candidate"]["player_name"], "odds": m["best_odds"]} for m in below_odds_filter
            ],
            "excluded_below_odds_filter_count": len(below_odds_filter),
        },
        "errors": errors,
    }
