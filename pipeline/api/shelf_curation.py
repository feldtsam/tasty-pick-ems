"""
Decides which scored candidates (the same flat dicts scored_picks.py
already produces — see its `_build_scored_pick`) populate each of the
app's six shelves, plus the Tasty Six.

THREE ODDS-TIER SHELVES ("+300-499", "+500-699", "Going Nuclear" +700+):
straightforward filter-by-odds-range, ranked by `final_score` — the core
model's output is exactly the right signal here, no new data needed.

THREE THEMED SHELVES — and the real distinction that drove this module's
design:
  - "Weather Factors": ranked by the Environment pillar being the
    DOMINANT factor in the score (the single highest of the four pillar
    scores for that candidate). This one needed no new data — weather is
    inherently about TODAY's specific game, not a historical trend, so
    there's no recency mismatch between what the shelf promises and what
    the core model already measures.
  - "Hot Hitters" and "Cold Pitchers to Attack" BOTH imply recent form (a
    hot streak / a recent slump), which the core model's Skill and
    Matchup pillars structurally cannot provide — those are built
    entirely from season-long aggregates. Populating these two shelves
    from season-long pillar dominance would be a real mismatch between
    the shelf's name and what it actually shows. Both are instead driven
    by live_data/recent_form.py's genuine recent-window stats (see that
    module's docstring for the data source and window-size reasoning):
      * "Hot Hitters" ranks by the CANDIDATE's own recent OPS.
      * "Cold Pitchers to Attack" ranks by the candidate's OPPOSING
        PITCHER's recent ERA — this shelf still lists BATTER picks (every
        candidate here is a batter HR prop), themed around "this matchup
        is extra good right now because the pitcher they're facing has
        been getting hit hard lately," not a shelf of pitchers themselves.

SAMPLE-SIZE GATING: a 15-game or 5-start recent window is inherently
noisy. Neither themed shelf trusts a "hot"/"cold" read below a minimum
real sample (MIN_HITTER_RECENT_SAMPLE / MIN_PITCHER_RECENT_SAMPLE) — a
candidate whose recent-form sample is too thin (a rookie a few games into
their debut, a pitcher on a short IL-return workload) is simply not
eligible for that shelf, rather than ranked on a 2-game "hot streak" that's
really just noise.

CROSS-SLATE PLAYER DEDUPLICATION: a player appears on at most ONE shelf
per slate run. This reverses an earlier design decision — multiple shelf
membership used to be considered expected, not a bug (a real elite
hitter in a great park with a cold opposing starter could legitimately
be an odds-tier pick, a Hot Hitter, AND a Cold-Pitcher-matchup pick
simultaneously) — but real production screenshots showed this reads
badly to users (the same player's name/photo repeated across shelves on
the same slate).

SCORE-BASED ASSIGNMENT, not build-order priority (a real, deliberate
revision of THIS module's own first version of the fix): a contested
player is kept on whichever shelf they score HIGHEST in, not whichever
shelf happens to build first. "Highest" is NOT the raw shelf_score
compared directly across shelves — confirmed directly (real 2026-08-19
data) that this would be meaningless: Shohei Ohtani's shelf_score was
81.2 on `+300-499` (final_score, ~0-100 scale), 0.978 on Hot Hitters
(recent OPS, ~0.5-1.5 scale), and 3.56 on Cold Pitchers to Attack
(opposing pitcher's recent ERA, ~0-20+ scale) — comparing those raw
numbers would make odds-tier shelves win almost every contested case
purely from unit/scale differences, not genuine fit. Instead, every
eligible candidate is converted to a PERCENTILE RANK (0-100) within
that shelf's own full eligible pool first (_percentile_rank) — "how
exceptional is this player FOR this shelf, relative to who else could
fill it" — and percentiles are compared across shelves, an apples-to-
apples comparison the raw metrics never were. See _resolve_player_
conflicts for the full mechanism; a percentile TIE (two shelves,
identical relative standing — realistic on thin pools, e.g. rank 1 of 1
in both) falls back to the fixed build order below as a deterministic
tertiary tiebreak, not the primary mechanism anymore.

THIS REQUIRES A DIFFERENT SHAPE than the build-order version: shelves
can no longer be built one at a time with a running exclusion set,
since a player's assignment can't be decided until every shelf's FULL
eligible pool (and every OTHER shelf they might also qualify for) is
known. assign_shelves() now runs three distinct phases: (1) compute
every shelf's full eligible pool + scores, across all shelves, before
assigning anyone (the *_eligible functions); (2) resolve every multi-
shelf player to their single highest-percentile shelf, removing them
from every other shelf's eligible pool (_resolve_player_conflicts);
(3) only then run the existing per-shelf assembly — sort, size limit,
DEFAULT_MAX_PER_GAME cap, skip-and-backfill from the next-best
REMAINING eligible candidate (_rank_eligible / _ranked, unchanged from
before either fix). Fixed build order (the three odds tiers, then Hot
Hitters, then Cold Pitchers to Attack, then Weather Factors) is
preserved throughout, both for the tiebreak above and for compute_
tasty_six()'s own downstream ordering dependency — see test_shelf_
curation.py for the real-data validation.

WITHIN-SHELF PLAYER DEDUPLICATION (_dedupe_by_player), a separate,
LAYERED-ON-TOP fix, not a replacement for the cross-shelf one above:
confirmed live (2026-08-20) that a player can have TWO real candidate
ROWS in the SAME shelf's own eligible pool at once — e.g. Cam Smith
duplicated in Hot Hitters, one row from game_pk 824155 created
2026-08-19 (a stale prior-day row), one from game_pk 824153 created
2026-08-20 (today's real one) — root cause under separate investigation
(see the shelf_curation README section), but this defensive layer
doesn't wait on that: assign_shelves() collapses each shelf's own
eligible pool to one row per player, keeping the higher-shelf_score
row, BEFORE _resolve_player_conflicts ever runs — which matters because
that function's own percentile math assumes at most one row per
(player, shelf) going in.

SHELF SIZE: DEFAULT_SHELF_SIZE=8, proposed from real pool sizes observed
while building this (150 real scored picks across 9 real games) — see the
conversation/README for the actual per-shelf counts that informed this
number. Kept as a plain parameter, not hardcoded, since the "right" size
is a product call more than an engineering one.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "live_data"))

from recent_form import fetch_batters_recent_form, fetch_pitchers_recent_form  # noqa: E402

MIN_HITTER_RECENT_SAMPLE = 8    # of the 15-game window recent_form.py uses
MIN_PITCHER_RECENT_SAMPLE = 3   # of the 5-start window recent_form.py uses

ODDS_TIERS = [
    ("+300-499", 300, 499),
    ("+500-699", 500, 699),
    ("Going Nuclear", 700, None),
]

DEFAULT_SHELF_SIZE = 8

# A shelf can never be dominated by one unusually strong/cold/hot real
# game — CONFIRMED against real live data (2026-08-09), not hypothetical:
# Cold Pitchers to Attack came back 8 of 8 picks from a single real game
# (every batter facing the same real cold opposing pitcher, who
# structurally share that pitcher's exact recent_era as their shelf_score
# — see _cold_pitchers_shelf). Weather Factors showed the same pattern at
# smaller scale (8 picks from only 2 real games) since environment_score
# is heavily game/park-level, not purely per-batter. Applied uniformly via
# _ranked() below to all four shelf types, since the underlying gap (no
# per-game cap anywhere) is the same in each — only the SEVERITY differs
# by how much a shelf's ranking metric is shared across batters in the
# same real game. 3 (not 2) chosen as the default: tight enough to
# guarantee real diversity, loose enough that a genuinely great real game
# doesn't need to be arbitrarily thinned to a single pick.
DEFAULT_MAX_PER_GAME = 3


def _ranked(entries: list, size: int, max_per_game: int = DEFAULT_MAX_PER_GAME) -> list:
    """
    Attach a 1-based rank and truncate to `size` — shared by every
    shelf-builder below so rank numbering and the per-game cap are
    applied identically everywhere, not reimplemented per shelf.

    `entries` must already be sorted best-first by the caller's own
    ranking metric, and already conflict-resolved (see _resolve_player_
    conflicts — a player_id appearing twice is no longer this function's
    concern at all; that's decided upstream now, before this ever runs).
    Walks that real order and SKIPS (never discards outright — just
    doesn't count toward this shelf) any entry that would push its real
    game_pk over `max_per_game`, letting the next-best real candidate
    from a DIFFERENT game backfill the slot instead of leaving it empty
    or letting one real game crowd out the rest of the slate. A game_pk
    that never reaches the cap is completely unaffected — this only
    activates once a specific real game's own candidates would
    otherwise dominate.
    """
    game_counts = {}
    kept = []
    for e in entries:
        game_pk = e["candidate"]["game_pk"]
        if game_counts.get(game_pk, 0) >= max_per_game:
            continue
        kept.append({**e, "rank": len(kept) + 1})
        game_counts[game_pk] = game_counts.get(game_pk, 0) + 1
        if len(kept) >= size:
            break
    return kept


def _rank_eligible(entries: list, size: int, max_per_game: int = DEFAULT_MAX_PER_GAME, sort_key=None) -> list:
    """
    Sorts an already-eligible, already conflict-resolved entries list
    best-first (by shelf_score descending, unless a shelf needs its own
    secondary tiebreak — see _cold_pitchers_eligible) and applies
    _ranked()'s size/max_per_game truncation. The thin "assemble" half
    of what used to be one combined "_xxx_shelf" function per shelf,
    now separated from eligibility (the *_eligible functions below) so
    _resolve_player_conflicts can run in between on each shelf's FULL
    pool, before any size/max_per_game truncation happens.
    """
    if sort_key is None:
        def sort_key(e):
            return -e["shelf_score"]
    return _ranked(sorted(entries, key=sort_key), size, max_per_game)


def _odds_tier_eligible(candidates: list, lo: int, hi) -> list:
    pool = [c for c in candidates if c["odds"] >= lo and (hi is None or c["odds"] <= hi)]
    return [{"candidate": c, "shelf_score": c["final_score"]} for c in pool]


def _hot_hitters_eligible(candidates: list, batter_form: dict) -> list:
    eligible = []
    for c in candidates:
        form = batter_form.get(c["mlbam_id"])
        if not form or form["recent_games_sampled"] < MIN_HITTER_RECENT_SAMPLE or form["recent_ops"] is None:
            continue
        eligible.append({"candidate": c, "shelf_score": form["recent_ops"], "recent_form": form})
    return eligible


def _cold_pitchers_eligible(candidates: list, pitcher_form: dict) -> list:
    eligible = []
    for c in candidates:
        opp_id = c.get("opp_pitcher_mlbam_id")
        if opp_id is None:
            continue
        form = pitcher_form.get(opp_id)
        if not form or form["recent_starts_sampled"] < MIN_PITCHER_RECENT_SAMPLE or form["recent_era"] is None:
            continue
        eligible.append({"candidate": c, "shelf_score": form["recent_era"], "opposing_pitcher_recent_form": form})
    return eligible


def _cold_pitchers_sort_key(e: dict):
    # Highest recent ERA first (coldest pitcher = best to attack); the
    # batter's own final_score breaks ties when the same cold pitcher
    # produces multiple eligible batters (expected — see module docstring).
    # This is the real mechanism that once let one real game take over
    # the whole shelf: every batter facing the same real cold pitcher
    # shares that pitcher's identical recent_era, so they all sort
    # together at the top — _ranked()'s max_per_game cap is what actually
    # prevents that, not anything in this sort itself.
    return (-e["shelf_score"], -e["candidate"]["final_score"])


def _weather_factors_eligible(candidates: list) -> list:
    eligible = []
    for c in candidates:
        pillars = {
            "skill": c["skill_score"], "matchup": c["matchup_score"],
            "environment": c["environment_score"], "opportunity": c["opportunity_score"],
        }
        if max(pillars, key=pillars.get) == "environment":
            eligible.append({"candidate": c, "shelf_score": c["environment_score"]})
    return eligible


def _dedupe_by_player(entries: list) -> list:
    """
    Collapses multiple candidate ROWS for the same player WITHIN a
    single shelf's own eligible pool down to one — keeps the higher-
    shelf_score row, discards the rest. A different axis than
    _resolve_player_conflicts below (which handles ONE candidate row
    being independently eligible for MULTIPLE DIFFERENT shelves, not
    multiple rows for one player landing in the SAME shelf) — layered
    on top of it, not a replacement: this runs first, so _resolve_
    player_conflicts always sees at most one row per (player, shelf)
    pair, which is the assumption its own percentile math depends on.

    Real production case that motivated this (confirmed live,
    2026-08-20, reproduced against real data): a player can have two
    real scored_picks rows on the same day if scored-picks-read returns
    a stale prior-day row alongside today's real one — Cam Smith had a
    row from game_pk 824155 (created 2026-08-19) alongside game_pk
    824153 (created 2026-08-20), both independently eligible for Hot
    Hitters, both surviving into that shelf's output uncaught by either
    the per-game cap (different real games, so max_per_game never
    triggers) or cross-shelf conflict resolution (both rows are in the
    SAME shelf, so there's no "other" shelf to lose to). This is a
    defensive layer regardless of why the duplicate row existed
    upstream — see the shelf_curation README section on the
    scored-picks-read root cause for that separate investigation.
    """
    best_by_player = {}
    for e in entries:
        mlbam_id = e["candidate"]["mlbam_id"]
        if mlbam_id not in best_by_player or e["shelf_score"] > best_by_player[mlbam_id]["shelf_score"]:
            best_by_player[mlbam_id] = e
    return list(best_by_player.values())


def _percentile_rank(entries: list) -> list:
    """
    Attaches _percentile (0-100, higher = better) to a COPY of each
    entry, based on that entry's rank position within THIS list's own
    shelf_score ordering — 1-indexed, best gets 100, worst gets
    100/n. This is what makes "highest score" comparable ACROSS shelves
    whose shelf_score metrics are on completely different scales (OPS
    ~0.5-1.5, ERA ~0-20+, final_score/environment_score ~0-100) — see
    the module docstring's real Shohei Ohtani numbers. Computed once,
    upfront, against each shelf's FULL eligible pool (before any
    conflict resolution or truncation) — an order-independent snapshot,
    not a value that could shift depending on what order conflicts
    happen to get resolved in.
    """
    ranked = sorted(entries, key=lambda e: -e["shelf_score"])
    n = len(ranked)
    return [{**e, "_percentile": 100.0 * (n - i) / n} for i, e in enumerate(ranked)]


def _resolve_player_conflicts(eligible_by_shelf: dict) -> dict:
    """
    eligible_by_shelf: {shelf_name: [entry, ...]}, each shelf's FULL
    eligible pool (unranked, untruncated) — the *_eligible functions'
    own output, in shelf_curation's fixed build order (three odds
    tiers, Hot Hitters, Cold Pitchers to Attack, Weather Factors).

    For every player_id (mlbam_id) eligible for 2+ shelves, keeps them
    ONLY in the shelf where their _percentile_rank is highest, removing
    them as a candidate from every other eligible shelf entirely — not
    just from that shelf's final ranked output, but from the pool
    _rank_eligible sorts and truncates, so a shelf that loses a
    contested player still has its own next-best REMAINING real
    candidate available to backfill the freed slot (the same skip-and-
    backfill _ranked() already does for the per-game cap).

    Ties (identical percentile in 2+ shelves — realistic on thin pools,
    e.g. a player who's rank 1 of 1 in one shelf and rank 1 of 1 in
    another) are broken by this dict's own iteration order — the fixed
    build order above — via strict `>` when updating each player's best
    shelf, so the FIRST shelf encountered wins a true tie. A secondary,
    tertiary tiebreak, not the primary mechanism.

    Returns a new {shelf_name: [entry, ...]} dict, same shelf keys and
    order as the input, with every player_id appearing in at most one
    shelf's list, and _percentile stripped back off (it was only ever
    needed for this comparison, not the final shelf output shape).
    """
    percentiled = {shelf_name: _percentile_rank(entries) for shelf_name, entries in eligible_by_shelf.items()}

    best_shelf = {}
    for shelf_name, entries in percentiled.items():
        for e in entries:
            mlbam_id = e["candidate"]["mlbam_id"]
            pct = e["_percentile"]
            if mlbam_id not in best_shelf or pct > best_shelf[mlbam_id][1]:
                best_shelf[mlbam_id] = (shelf_name, pct)

    resolved = {}
    for shelf_name, entries in percentiled.items():
        kept = [e for e in entries if best_shelf[e["candidate"]["mlbam_id"]][0] == shelf_name]
        resolved[shelf_name] = [{k: v for k, v in e.items() if k != "_percentile"} for e in kept]
    return resolved


def assign_shelves(
    candidates: list, season: int, shelf_size: int = DEFAULT_SHELF_SIZE, max_per_game: int = DEFAULT_MAX_PER_GAME,
) -> dict:
    """
    candidates: a list of scored-pick dicts (scored_picks.py's output
    shape — needs mlbam_id, opp_pitcher_mlbam_id, odds, final_score, the
    four *_score pillar fields). Typically the pooled scored_picks from
    every game in a day's slate, gathered by whatever caller already has
    read access to them (see README — this pipeline itself doesn't read
    scored_picks back from Lovable, same constraint as everywhere else).

    Fetches recent-form data live, scoped to only the players actually
    present in `candidates` — not the whole league.

    `max_per_game` caps how many of a shelf's `shelf_size` slots one
    single real game_pk can occupy — see _ranked()'s docstring for the
    real production case (Cold Pitchers to Attack, 8 of 8 from one game)
    that motivated this. Kept as a plain parameter, same "product call,
    not an engineering one" treatment as shelf_size itself.

    CROSS-SLATE PLAYER DEDUPLICATION, SCORE-BASED: every shelf's full
    eligible pool is computed first (the *_eligible functions, still in
    this function's own fixed build order — three odds tiers, then Hot
    Hitters, then Cold Pitchers to Attack, then Weather Factors), THEN
    _resolve_player_conflicts keeps each multi-shelf player only in
    their single highest-percentile shelf, THEN each shelf's remaining
    (conflict-resolved) pool is sorted and truncated via _rank_eligible
    — see the module docstring and _resolve_player_conflicts for the
    full reasoning (why raw shelf_score isn't comparable across shelves,
    why percentile rank is, and how build order still serves as a
    tertiary tiebreak on true percentile ties). Real production case
    that originally motivated cross-slate dedup at all (live
    screenshots, not hypothetical): the same player appearing twice in
    the same shelf's OWN list is a different bug _ranked()'s max_per_
    game cap doesn't touch (that only caps one game_pk's share of a
    SINGLE shelf) — this caps one PLAYER's presence across the WHOLE
    slate's six shelves. See shelf_curation's test suite for the
    real-data validation of this.

    Returns {shelf_name: [{"candidate": {...}, "rank": int,
    "shelf_score": float, ...extra}, ...]}, one list per shelf name.
    """
    batter_ids = list({c["mlbam_id"] for c in candidates})
    pitcher_ids = list({c["opp_pitcher_mlbam_id"] for c in candidates if c.get("opp_pitcher_mlbam_id")})

    batter_form = fetch_batters_recent_form(batter_ids, season)
    pitcher_form = fetch_pitchers_recent_form(pitcher_ids, season)

    eligible_by_shelf = {}
    for label, lo, hi in ODDS_TIERS:
        eligible_by_shelf[label] = _odds_tier_eligible(candidates, lo, hi)
    eligible_by_shelf["Hot Hitters"] = _hot_hitters_eligible(candidates, batter_form)
    eligible_by_shelf["Cold Pitchers to Attack"] = _cold_pitchers_eligible(candidates, pitcher_form)
    eligible_by_shelf["Weather Factors"] = _weather_factors_eligible(candidates)

    eligible_by_shelf = {name: _dedupe_by_player(entries) for name, entries in eligible_by_shelf.items()}
    resolved = _resolve_player_conflicts(eligible_by_shelf)

    shelves = {}
    for shelf_name, entries in resolved.items():
        sort_key = _cold_pitchers_sort_key if shelf_name == "Cold Pitchers to Attack" else None
        shelves[shelf_name] = _rank_eligible(entries, shelf_size, max_per_game, sort_key)
    return shelves


def compute_tasty_six(shelves: dict) -> dict:
    """
    One pick per shelf, six distinct players by default — with a
    deterministic fallback for a real case this needs to handle:
    confirmed against real data that the SAME player can legitimately be
    the #1 pick on two different shelves at once (e.g. a real elite
    hitter who's independently both the best `Going Nuclear` price and
    facing the coldest real pitcher on the slate). Six shelves each
    independently reporting their own #1 could then mean the "Tasty Six"
    is really only five distinct players.

    Processes shelves in `shelves`' own iteration order (the fixed order
    assign_shelves() builds them in: the three odds tiers, then Hot
    Hitters, then Cold Pitchers to Attack, then Weather Factors) — same
    order every time, so which shelf "wins" a contested player is
    deterministic, not incidental to whatever order happened to run.
    For each shelf, walks down its OWN ranked list (capped at
    `shelf_size`, not re-fetching beyond what's already computed) and
    picks the first candidate not already claimed by an earlier shelf in
    this pass.

    If a shelf's entire ranked list is already claimed by earlier shelves
    — a real edge case on a thin slate with few eligible candidates —
    falls back to that shelf's own #1 as a last resort rather than
    leaving the slot empty. This is flagged, not silent: the shelf name
    shows up in the returned `repeats` list.

    A shelf with zero eligible candidates at all still contributes `None`,
    not a fabricated pick.

    Returns {"picks": {shelf_name: entry_or_None, ...}, "repeats": [shelf_name, ...]}.
    """
    used_keys = set()
    picks = {}
    repeats = []

    for shelf_name, entries in shelves.items():
        if not entries:
            picks[shelf_name] = None
            continue

        chosen = next(
            (e for e in entries if (e["candidate"]["mlbam_id"], e["candidate"]["game_pk"]) not in used_keys),
            None,
        )
        if chosen is None:
            chosen = entries[0]
            repeats.append(shelf_name)

        picks[shelf_name] = chosen
        used_keys.add((chosen["candidate"]["mlbam_id"], chosen["candidate"]["game_pk"]))

    return {"picks": picks, "repeats": repeats}
