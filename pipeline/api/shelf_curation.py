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

MULTIPLE SHELF MEMBERSHIP IS EXPECTED, not a bug: shelves are computed
completely independently over the full candidate pool — there is
deliberately no cross-shelf deduplication. A real elite hitter in a great
park with a cold opposing starter can legitimately be an odds-tier pick,
a Hot Hitter, AND a Cold-Pitcher-matchup pick simultaneously. Confirmed
this doesn't break anything (see test_shelf_curation.py).

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
    shelf-builder below so rank numbering (and now the per-game cap) is
    applied identically everywhere, not reimplemented per shelf.

    `entries` must already be sorted best-first by the caller's own
    ranking metric. Walks that real order and SKIPS (never discards
    outright — just doesn't count toward this shelf) any entry that would
    push its real game_pk over `max_per_game`, letting the next-best real
    candidate from a DIFFERENT game backfill the slot instead of leaving
    it empty or letting one real game crowd out the rest of the slate. A
    game_pk that never reaches the cap is completely unaffected — this
    only activates once a specific real game's own candidates would
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


def _odds_tier_shelf(candidates: list, lo: int, hi, size: int, max_per_game: int = DEFAULT_MAX_PER_GAME) -> list:
    pool = [c for c in candidates if c["odds"] >= lo and (hi is None or c["odds"] <= hi)]
    pool.sort(key=lambda c: -c["final_score"])
    return _ranked([{"candidate": c, "shelf_score": c["final_score"]} for c in pool], size, max_per_game)


def _hot_hitters_shelf(candidates: list, batter_form: dict, size: int, max_per_game: int = DEFAULT_MAX_PER_GAME) -> list:
    eligible = []
    for c in candidates:
        form = batter_form.get(c["mlbam_id"])
        if not form or form["recent_games_sampled"] < MIN_HITTER_RECENT_SAMPLE or form["recent_ops"] is None:
            continue
        eligible.append((c, form))
    eligible.sort(key=lambda pair: -pair[1]["recent_ops"])
    return _ranked(
        [{"candidate": c, "shelf_score": form["recent_ops"], "recent_form": form} for c, form in eligible],
        size, max_per_game,
    )


def _cold_pitchers_shelf(candidates: list, pitcher_form: dict, size: int, max_per_game: int = DEFAULT_MAX_PER_GAME) -> list:
    eligible = []
    for c in candidates:
        opp_id = c.get("opp_pitcher_mlbam_id")
        if opp_id is None:
            continue
        form = pitcher_form.get(opp_id)
        if not form or form["recent_starts_sampled"] < MIN_PITCHER_RECENT_SAMPLE or form["recent_era"] is None:
            continue
        eligible.append((c, form))
    # Highest recent ERA first (coldest pitcher = best to attack); the
    # batter's own final_score breaks ties when the same cold pitcher
    # produces multiple eligible batters (expected — see module docstring).
    # THIS is exactly the real mechanism that let one real game take over
    # the whole shelf: every batter facing the same real cold pitcher
    # shares that pitcher's identical recent_era, so they all sort
    # together at the top — _ranked()'s max_per_game cap is what actually
    # prevents that now, not anything in this sort itself.
    eligible.sort(key=lambda pair: (-pair[1]["recent_era"], -pair[0]["final_score"]))
    return _ranked(
        [{"candidate": c, "shelf_score": form["recent_era"], "opposing_pitcher_recent_form": form} for c, form in eligible],
        size, max_per_game,
    )


def _weather_factors_shelf(candidates: list, size: int, max_per_game: int = DEFAULT_MAX_PER_GAME) -> list:
    eligible = []
    for c in candidates:
        pillars = {
            "skill": c["skill_score"], "matchup": c["matchup_score"],
            "environment": c["environment_score"], "opportunity": c["opportunity_score"],
        }
        if max(pillars, key=pillars.get) == "environment":
            eligible.append(c)
    eligible.sort(key=lambda c: -c["environment_score"])
    return _ranked([{"candidate": c, "shelf_score": c["environment_score"]} for c in eligible], size, max_per_game)


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

    Returns {shelf_name: [{"candidate": {...}, "rank": int,
    "shelf_score": float, ...extra}, ...]}, one list per shelf name.
    """
    batter_ids = list({c["mlbam_id"] for c in candidates})
    pitcher_ids = list({c["opp_pitcher_mlbam_id"] for c in candidates if c.get("opp_pitcher_mlbam_id")})

    batter_form = fetch_batters_recent_form(batter_ids, season)
    pitcher_form = fetch_pitchers_recent_form(pitcher_ids, season)

    shelves = {}
    for label, lo, hi in ODDS_TIERS:
        shelves[label] = _odds_tier_shelf(candidates, lo, hi, shelf_size, max_per_game)
    shelves["Hot Hitters"] = _hot_hitters_shelf(candidates, batter_form, shelf_size, max_per_game)
    shelves["Cold Pitchers to Attack"] = _cold_pitchers_shelf(candidates, pitcher_form, shelf_size, max_per_game)
    shelves["Weather Factors"] = _weather_factors_shelf(candidates, shelf_size, max_per_game)
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
