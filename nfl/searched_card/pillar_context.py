"""
Searched Card writer — per-player pillar context (Stage 1h, Task 2).

STAGE 1 DELIVERABLE, design-and-tests only. See searched_card_flags.py —
nothing here is wired into any real pipeline path yet.

WHAT THIS IS: a deterministic, pure function (`build_pillar_context`) that
turns one player's row + the week's pool into a structured, plain-language
description of where each football-evidence pillar sits relative to the
SAME comparison population `evidence_price_classifier.classify_evidence_
vs_price` ranks against — priced players that pass the completeness gate.
This is deliberately NOT a new scoring signal: it adds no new number, no
new threshold that changes any label. It exists so the writer has
something richer than a single rank/gap to build a why_reasons claim
from — "td_opportunity sits below this week's median, at full
completeness, among 260 comparable players" is a real, citable fact a
why_reason can state plainly; a bare percentile rank is not football
language a reader would recognize.

ZERO DEPENDENCE ON market_value_score/tpe_score, same discipline as
evidence_price_classifier.py and for the identical reason — see
test_circularity.py's pillar-context section for the proof. This module
DOES import from evidence_price_classifier.py (the completeness-gate
helper and the two threshold constants), which is a deliberate departure
from that module's own "zero imports from the rest of nfl/" rule: that
rule exists specifically to make the market_value_score/tpe_score
independence inspectable by reading one file; reusing the SAME
completeness-gate logic here (rather than forking a second copy that
could silently drift) serves that same goal better than duplicating it
would, and the circularity test for this module checks its own source
directly, the same way, so the "one file to audit" property still holds
per-module, just for a slightly larger file set than one.

WHAT EACH PILLAR MEASURES, taken from the real scoring code (nfl/
scoring.py, read during the Stage 1f/1g investigations of this same
repo), not invented fresh:
  td_opportunity  — recent red-zone touchdown production and conversion
                     (Proven Heat), weighted toward the most recent game,
                     plus usage trend (Emerging Heat).
  role_momentum   — CHANGE in role (touch share, snap share, depth-chart
                     movement) -- not role size. A long-stable, high-
                     volume role scores LOW here by design; this pillar
                     cannot and must not be read as "how big is the
                     role."
  situation       — matchup and environment (defensive matchup
                     allowed-rate, weather/roof).
  evidence_quality — how complete and consistent the underlying data is
                     (completeness × convergence across the other three
                     pillars) -- a data-trust signal, not a football
                     signal itself.

THE "ABOVE"/"BELOW"/"NEAR" TOLERANCE (NEAR_MEDIAN_TOLERANCE, 5.0 score
points): an UNMEASURED PROPOSAL, same honesty standard as every other
threshold in this package. Picked as a small, round band around the
median so a value one or two points off the median (noise-level, on a
0-100 score) doesn't get over-stated as a real "above"/"below" claim —
not validated against any real variance measurement of these specific
pillars.

THE POPULATION-SIZE "LIMITS" CHECK (MIN_COMPARISON_POPULATION, reusing
evidence_price_classifier.MIN_POOL_SIZE's value, not a second
independently-derived number): included for defense-in-depth and
documentation completeness. In practice it should rarely fire — any row
this function is called for has, by construction of the pipeline this
would eventually plug into, already cleared classify_evidence_vs_price's
own pool-size gate (checked against the identical raw-priced-pool
quantity) before a caller would bother building pillar context for it at
all. It is still checked here, independently, rather than assumed,
because build_pillar_context has no way to know HOW a future caller
invokes it, and a function that silently trusts an un-enforced
precondition is a worse design than one that checks it again cheaply.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evidence_price_classifier import (  # noqa: E402
    MIN_POOL_SIZE,
    TD_OPPORTUNITY_COMPLETENESS_FLOOR,
    _fails_completeness_gate,
)

PILLAR_NAMES = ("td_opportunity", "role_momentum", "situation", "evidence_quality")

PILLAR_DESCRIPTIONS = {
    "td_opportunity": "recent red-zone touchdown production and conversion, weighted toward the most recent game, plus usage trend",
    "role_momentum": "change in role -- touch share, snap share, depth-chart movement -- not role size",
    "situation": "matchup and environment",
    "evidence_quality": "how complete and consistent the underlying data is",
}

# Only two pillars have a real completeness column at all (confirmed
# directly from nfl/scoring.py during Stage 1f/1g: score_td_opportunity
# writes td_opportunity_completeness, score_role_momentum writes role_
# momentum_completeness; score_situation and score_evidence_quality
# write no equivalent column). situation/evidence_quality simply have no
# entry here -- their "completeness" in the returned context is always
# None, not a fabricated 100.
PILLAR_COMPLETENESS_FIELDS = {
    "td_opportunity": "td_opportunity_completeness",
    "role_momentum": "role_momentum_completeness",
}

# UNMEASURED PROPOSAL -- see module docstring.
NEAR_MEDIAN_TOLERANCE = 5.0

# Reuses MIN_POOL_SIZE's real value, not a second independently-derived
# number -- see module docstring's "THE POPULATION-SIZE 'LIMITS' CHECK".
MIN_COMPARISON_POPULATION = MIN_POOL_SIZE


def _median(values: list):
    """Plain-Python median, no third-party dependency (same zero-
    dependency discipline evidence_price_classifier.py's own
    _percentile_rank already uses). None for an empty list."""
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2.0


def _comparison_population(pool: list, td_opportunity_completeness_floor: float) -> list:
    """
    The SAME population evidence_price_classifier.classify_evidence_vs_
    price ranks both evidence and price against: priced rows that do not
    themselves fail the completeness gate. Deliberately calls the SAME
    _fails_completeness_gate helper that module uses -- not a
    reimplementation -- so this can never silently drift from the
    classifier's own definition of "comparable."
    """
    priced = [r for r in pool if r.get("consensus_price_american") is not None]
    return [r for r in priced if not _fails_completeness_gate(r, td_opportunity_completeness_floor)]


def build_pillar_context(
    row: dict,
    pool: list,
    td_opportunity_completeness_floor: float = TD_OPPORTUNITY_COMPLETENESS_FLOOR,
    near_median_tolerance: float = NEAR_MEDIAN_TOLERANCE,
) -> dict:
    """
    Pure function. Returns:
      {
        "comparison_population_size": int,
        "pillars": {
          "td_opportunity": {
            "value": float,
            "median": float | None,
            "position": "above" | "below" | "near" | "unknown",
            "comparison_population_size": int,   # same number, repeated per pillar for convenience
            "completeness": float | None,         # None for situation/evidence_quality -- no such column exists
            "what_it_measures": str,
          },
          "role_momentum": {...}, "situation": {...}, "evidence_quality": {...},
        },
        "limits": [str, ...],   # names + reason for any pillar with completeness < 100, or population < MIN_COMPARISON_POPULATION
      }

    `row` must have td_opportunity/role_momentum/situation/evidence_quality
    (the same REQUIRED_ROW_FIELDS subset evidence_price_classifier.py
    requires, minus consensus_price_american -- this function doesn't
    need the row's own price, only its pillars).

    "position" is "unknown" only when the comparison population is empty
    for that pillar (e.g. every comparable row is somehow missing that
    field) -- distinct from "near", which means a real median was
    computed and this row's value sits within `near_median_tolerance` of
    it.
    """
    comparison_population = _comparison_population(pool, td_opportunity_completeness_floor)
    population_size = len(comparison_population)

    pillars = {}
    limits = []
    for name in PILLAR_NAMES:
        value = float(row[name])
        comp_values = [float(r[name]) for r in comparison_population if r.get(name) is not None]
        median = _median(comp_values)

        if median is None:
            position = "unknown"
        else:
            diff = value - median
            if abs(diff) <= near_median_tolerance:
                position = "near"
            elif diff > 0:
                position = "above"
            else:
                position = "below"

        completeness_field = PILLAR_COMPLETENESS_FIELDS.get(name)
        completeness = (
            float(row[completeness_field])
            if completeness_field and row.get(completeness_field) is not None
            else None
        )

        pillars[name] = {
            "value": round(value, 1),
            "median": round(median, 1) if median is not None else None,
            "position": position,
            "comparison_population_size": population_size,
            "completeness": completeness,
            "what_it_measures": PILLAR_DESCRIPTIONS[name],
        }

        if completeness is not None and completeness < 100.0:
            limits.append(f"{name}: completeness {completeness:.0f} (below 100 -- partly a neutral-fallback reading, not entirely real)")
        if population_size < MIN_COMPARISON_POPULATION:
            limits.append(f"{name}: comparison population only {population_size} players (below {MIN_COMPARISON_POPULATION})")

    return {
        "comparison_population_size": population_size,
        "pillars": pillars,
        "limits": limits,
    }


def writer_facing_pillar_context(full_context: dict, label: str) -> dict:
    """
    STAGE 1h, Task 2's own gating rule: "When the label is
    insufficient_evidence, pillar_context must NOT be given to the
    writer except the 'limits' list and the reason the label fired."
    The REASON itself is already carried separately on the classifier's
    own return dict (classification['reason']) and already reaches the
    writer via searched_card_prompt.py's existing "Why: {reason}" line
    -- this function's own job is narrower: decide how much of
    build_pillar_context's OUTPUT reaches the writer, not to re-surface
    the reason a second time.

    Not gated (label != "insufficient_evidence"): the full context,
    unmodified.
    Gated (label == "insufficient_evidence"): only {"limits": [...]} --
    no per-pillar values, medians, or positions at all. Same "scope it
    out at the data layer, don't just instruct the model not to use it"
    discipline evidence_price_classifier.py's own evidence_vs_price ->
    None suppression already uses (see that module's "STAGE 1c
    REVERSAL").
    """
    if label == "insufficient_evidence":
        return {"limits": full_context["limits"]}
    return full_context
