"""
Searched Card writer — the evidence-vs-price classifier (Deliverable 1).

STAGE 1 DELIVERABLE, design-and-tests only. See searched_card_flags.py —
nothing here is wired into any real pipeline path yet.

WHAT THIS FUNCTION DOES, per the approved product rules: computes one of
three labels for a player BEFORE any writer runs, using ONLY football
evidence (td_opportunity, role_momentum, situation, evidence_quality) to
describe the relationship between that evidence and the price — never a
fair-value or mispricing judgment, and never by reading market_value_score
or tpe_score (see test_circularity.py, Deliverable 5, for the proof this
is true by construction, not just by convention):

  "supports_expectation"   — the player's football-evidence rank and the
                              price's own rank (both computed fresh, within
                              this week's pool) are close together.
  "raises_questions"       — they are far apart, either direction (the
                              evidence outranks the price, or the price
                              outranks the evidence).
  "insufficient_evidence"  — the comparison can't be trusted at all; this
                              gates BEFORE the rank comparison is even
                              consulted. As of Stage 1g, three independent
                              reasons can fire this: too few priced players
                              in the whole pool, this row's own completeness
                              fields showing its td_opportunity/role_momentum
                              are themselves mostly fallback readings, or
                              this row's own evidence_quality being too low.

ZERO IMPORTS FROM THE REST OF nfl/, ON PURPOSE. This module could import
nfl/normalize.py's build_reference_scale/percentile_lookup and nfl/
market_value.py's implied_probability — they are pure, already-reviewed
functions and importing them would be less code. Deliberately not done:
the whole point of Deliverable 5 (see test_circularity.py) is to prove
this classifier never reads market_value_score/tpe_score. A reviewer
should be able to confirm that by reading this one file top to bottom,
without having to also audit everything two import-hops away in
scoring.py/market_value.py for a buried reference. _implied_probability
and _percentile_rank below are small, deliberate duplicates of logic that
already exists elsewhere (market_value.implied_probability,
normalize.percentile_lookup) — the same "duplicate rather than
cross-import" call this codebase already makes elsewhere for trivial
pieces (see nfl_writer_common.py's own _as_list, or ShelfPersonality in
nfl_shelf_personalities.py) extended here for a non-trivial reason: an
air-gapped dependency graph IS the evidence this classifier is decoupled
from the market-value/overall-score pipeline, not merely a claim about it.

THE STARTING HYPOTHESIS THIS WAS BUILT TO TEST (not assumed as true):
compare the player's rank on the three football-evidence pillars within
the week's pool against the rank of the price (implied probability) in
that SAME pool. A large gap suggests the price and the on-field evidence
are telling different stories ("raises_questions"); a close match
suggests they agree ("supports_expectation"). See classification_rubric.md
in this directory for the real numbers (a real NFL week, 745 players)
this was tested against, where the hypothesis held up reasonably and
where it didn't.

NUMERIC THRESHOLDS — both stated plainly, with their real justification,
not invented precision:

  FOOTBALL_EVIDENCE_WEIGHTS (30/20/20 renormalized to sum to 1): NOT a new
  number. These are scoring.CONFIG["universal_tpe"]["core_weights"]'s own
  real td_opportunity/role_momentum/situation weights, taken as given and
  renormalized over just the three football pillars the exact same way
  scoring.score_universal_tpe already renormalizes core_score over
  whichever pillars are present on a row missing market_value_score (see
  that function's own docstring). This is the one number in this module
  that is NOT arbitrary — it's the existing, reviewed pillar weighting,
  simply evaluated without the market component this classifier is
  forbidden from reading.

  GAP_THRESHOLD = 30.0 (percentile points): ARBITRARY, stated plainly.
  Chosen by looking at the real rank-gap distribution on a real NFL week
  (2025, week 10; n=745; see classification_rubric.md) — the median
  absolute gap in that population is ~28.3 points, and a 30-point cutoff
  classifies roughly half the real population into each of
  supports_expectation/raises_questions before the evidence_quality gate
  is even applied (52.5% / 47.5% in that week). That is a property of
  THIS fixture's own distribution, not a validated signal — see this
  module's own "WHERE THIS IS ARBITRARY" note below and classification_
  rubric.md's own discussion for why a percentile-gap threshold will
  always classify roughly a fixed share of any population regardless of
  whether the gap means anything real, and what outcome data would be
  needed before trusting it.

  EVIDENCE_QUALITY_FLOOR = 50.0: grounded in scoring.py's own convention,
  not picked fresh. 50.0 is the exact fill_neutral() fallback value used
  everywhere else in scoring.py (e.g. score_signal_breach's own
  fallback_sentinel) to mean "a missing reading, not a genuinely weak
  one." evidence_quality = sqrt(completeness * convergence) degenerates
  to EXACTLY 50.0 when both axes are themselves fully neutral-fallback
  (completeness=50, convergence=50 — the "nothing real to measure on
  either axis" case). A row scoring below that is worse than the fully-
  degenerate case: real signal that actively disagrees with itself, or
  real incompleteness bad enough to pull the geometric mean under the
  floor a total absence of data would produce. That is why BELOW 50, not
  AT OR below some other round number, is the gate.

WHERE THIS IS ARBITRARY, STATED PLAINLY (see classification_rubric.md for
the fuller discussion):
  - GAP_THRESHOLD's specific value (30, not 25 or 35) was chosen to land
    near an even split on one real week's data, not against any real
    anytime-TD OUTCOME data. A percentile-rank gap is, by construction,
    roughly uniformly distributed when the two ranked quantities are
    largely independent — ANY threshold will classify close to a fixed
    fraction of any population as "far apart," which proves nothing about
    whether that fraction is the right one to flag editorially.
  - Equal-ish treatment of "evidence ranks higher than price" and "price
    ranks higher than evidence" as the same "raises_questions" direction
    is a simplifying choice — the two could plausibly deserve different
    handling (a player the market likes MORE than the football evidence
    does is a different kind of question than one the market likes LESS),
    and this rubric does not distinguish them.
  - This has never been checked against which of these labels actually
    predicted anytime-TD outcomes. See classification_rubric.md's own
    "what real outcome data would be needed" section.

STAGE 1b ADDITIONS (this revision) — two separate changes, each its own
reasoning, neither touching the three labels above in meaning:

  `evidence_vs_price` (NEW FIELD, not a new label): the direction that
  `label`'s own "raises_questions" was collapsing on purpose (see
  "Direction-blindness is a real simplification" above). Three values:
  "evidence_ranks_higher" / "evidence_ranks_lower" / "aligned". THIS FIELD
  DESCRIBES A RANK POSITION, NOTHING ELSE — never fair value, never
  mispricing, never a verdict on whether the price is correct. It reuses
  GAP_THRESHOLD as its own aligned/not-aligned boundary, not a new
  constant, for a specific reason stated plainly: `label`'s own
  supports_expectation/raises_questions split already uses this exact gap
  comparison, and giving the direction field a DIFFERENT threshold would
  let a card describe the same underlying gap as "supports_expectation"
  (via label) and "evidence_ranks_higher" (via evidence_vs_price, if its
  own threshold were narrower) in the same breath — two fields built from
  the same number, disagreeing with each other, for no reason grounded in
  anything real. One arbitrary number this module hasn't validated
  against outcomes is already an honestly-flagged weakness; a second,
  differently-arbitrary number that could additionally make the two
  fields contradict each other would be worse, not more precise. See
  classification_rubric.md for the fuller discussion.

  IS evidence_vs_price COMPUTED WHEN label IS "insufficient_evidence"
  (from any gate)? Stage 1b's answer here was YES, always — see that
  reasoning preserved (and then overridden) in "STAGE 1c REVERSAL" below.
  Short version: that answer has been reversed, and the reversal still
  holds under Stage 1g's new gates too (see "STAGE 1g CHANGES" below).

  MIN_POOL_SIZE (NEW GATE, another reason insufficient_evidence can fire):
  an UNMEASURED proposal, same honesty standard as pool_size_report.md's
  own floor, which this directly implements — not a second,
  independently-derived number. See "STAGE 1c UPDATE" below for the
  current value (150) and the real evidence behind it; this gate's
  mechanics are unchanged from Stage 1b. Gates on the PRICE population
  specifically (len of the rows in `pool` with a real consensus_price_
  american), not the evidence population, because pool_size_report.md's
  own instability concern is about the market/price percentile
  specifically (echoing market_value.py's own score_market_value
  docstring: "a percentile computed against 20 players... is a much
  noisier, more easily-skewed number") — td_opportunity/role_momentum/
  situation are computed for essentially every rostered player every
  week (confirmed: every row in both local fixtures has these three
  populated; see classification_rubric.md), so an equivalent floor on the
  evidence population would almost never fire and isn't the real risk
  this gate exists to bound. Checked BEFORE every other gate (see
  classify_evidence_vs_price below) — a pool too thin to trust ANY
  percentile computed against it undermines every other comparison just
  as much as the gap comparison, so there's no reason to let a clean
  completeness/quality reading run interference for a gate that is
  really about population size, not about this one row's own data.
  DEFENSIVE NOTE (Stage 1c, Task 4, read-only finding — see pool_size_
  report.md for the full citation): a real future caller must filter
  `week IS NOT NULL` (and not rely on an exact `week = :week` equality
  alone) before building `pool` from `nfl_price_history` — rows with a
  real player_id and a real price can still have week=NULL when the
  game-to-schedule lookup fails independently of player matching, and
  this gate has no way to detect a pool silently contaminated with
  rows from the wrong week.
  STAGE 1g, Task 3: this gate is checked against the count of priced
  players in the week's pool BEFORE the completeness gate below is
  applied — see "STAGE 1g CHANGES" for why that specific ordering is
  the one that actually matches what the pool_size_report.md evidence
  measured.

STAGE 1c UPDATE — two separate changes:

  1. MIN_POOL_SIZE: 50 -> 150, on NEW real evidence, reported directly by
  the user (not independently queried or verified by this package — no
  DB access exists in this environment; see pool_size_report.md for the
  full honest framing of that distinction). The user reports running the
  real read-only SQL pool_size_report.md itself proposed, against live
  2026 NFL `nfl_price_history` data, and finding a clear two-cluster
  pattern in per-day distinct-player-with-a-quote counts: every day lands
  either at 56-or-fewer (the low cluster — tail days belonging to the
  NEXT week's slate, 18-56 players) or at 192-or-more (the high
  cluster — every normal day; every week's first day was above 280
  except Week 1, at 192). The gap between the two clusters (roughly
  60-190) contains no observed data point at all. 150 is a round number
  chosen to sit inside that real gap — STILL a proposal, not a derived
  threshold (no outcome/stability data backs the exact value 150 over,
  say, 100 or 180), but a materially stronger justification than Stage
  1b's "~2.5x one game's ~20-player pool" arithmetic: that number was
  extrapolated from a single confirmed anecdote with no day-level
  distribution behind it at all, where this one is picked inside a real,
  reported two-cluster gap in actual day-level data. See pool_size_
  report.md for the full write-up, including an explicit caveat this
  module's own gate does NOT measure the identical thing the user's
  reported numbers measure (a daily snapshot of ANY quote that day, vs.
  this function's own `price_pool_size`, scoped by the caller to each
  player's latest quote as of search time) — the 150 threshold is
  carried over as a reasonable proxy between two related-but-different
  metrics, not as a measurement of the same one.

  2. The "STAGE 1b ADDITIONS" reasoning above, arguing FOR always
  computing `evidence_vs_price` even under insufficient_evidence, is
  SUPERSEDED — see "STAGE 1c REVERSAL" immediately below. Left visible
  above (not deleted) so a reader can see what changed and why, rather
  than finding contradictory code next to reasoning that reads as if it
  still applies.

STAGE 1c REVERSAL — evidence_vs_price now hidden under insufficient_evidence:

  Stage 1b argued, at length, that `evidence_vs_price` should ALWAYS be
  computed and returned, even when `label` is `insufficient_evidence`
  from any gate — the reasoning above this note is that original
  argument, left intact rather than quietly rewritten, specifically so
  the reversal has something concrete to be a reversal OF. The core of
  that argument: hiding a derived field while leaving the raw numbers
  (`evidence_rank_pct`/`price_rank_pct`/`gap`) visible would be an
  "inconsistent half-measure," and the real safety gate belonged at the
  PROMPT layer (searched_card_prompt.py's hard rules telling the model
  not to trust the comparison), not by withholding the field itself.

  This revision (Stage 1c) reverses that call, on review, for a reason
  Stage 1b did not weigh: `evidence_vs_price` is not just a number on a
  diagnostic dict, it is a value that gets handed DIRECTLY to the writer
  model as one of its GIVENS (searched_card_prompt.py's dynamic prompt)
  and carried onto the assembled card as a display field (searched_card_
  schema.build_searched_card). A prompt-layer instruction is a request
  that the model behave a certain way; it is not a guarantee, and a
  content-generation pipeline whose explicit premise is "never let the
  model infer trustworthiness from data it shouldn't have" is weaker, not
  equally safe, when it hands the model the exact fact ("evidence_ranks_
  higher" / "evidence_ranks_lower" / "aligned") the instruction is asking
  it not to lean on. Removing the field from what reaches the writer
  entirely closes that gap at the DATA layer instead of relying solely on
  instruction-following — the same "scoping removes the temptation, the
  prompt backs it up" pattern this package's own prompt module docstring
  already names as its preferred discipline for product rule 2
  (tpe_score/market_value_score), now applied consistently to this field
  too.

  What changes concretely: when `label == INSUFFICIENT_EVIDENCE` (from
  ANY gate — originally just two, now four as of Stage 1g), this
  function returns `evidence_vs_price = None` instead of the real
  ranking value. `evidence_rank_pct`/`price_rank_pct`/`gap` are NOT
  affected by this change and remain the real computed numbers in every
  case — this reversal is scoped to the one field that reaches the
  writer-facing prompt and the assembled card's display fields, not to
  the diagnostic numbers a reviewer would use to audit a gated row (see
  classification_rubric.md's own Laquon Treadwell example, which remains
  valid: his real gap is still visible in the returned dict, only
  `evidence_vs_price` itself is None for that row). searched_card_
  prompt.py's dynamic prompt omits the "Evidence-vs-price rank position"
  block entirely when it is None, rather than printing the string "None"
  to the model.

STAGE 1g CHANGES (this revision) — a real, user-supplied completeness
signal replaces part of the Stage 1f heuristic, and the two rank
populations are unified:

  1. THE COMPLETENESS GATE (NEW, a fourth reason insufficient_evidence can
  fire). Stage 1f diagnosed, using a heuristic (td_opportunity==57.1 AND
  role_momentum==50.0, since no completeness data was available in that
  stage's export), that roughly half of a real week's pool consists of
  "fill" rows — players with no real trailing signal at all, whose
  pillar scores are themselves just the neutral-fallback arithmetic
  working itself out (see scoring.py's score_td_opportunity/
  score_role_momentum and their own completeness columns). This stage
  replaces that heuristic with the REAL mechanism scoring.py already
  computes for exactly this purpose: td_opportunity_completeness and
  role_momentum_completeness, each `(1 - mean(fallback_flags)) * 100` —
  0 means every single input to that pillar was a neutral-50 fallback,
  100 means every input was real.

  THE RULE: a row is insufficient_evidence if role_momentum_completeness
  == 0 OR td_opportunity_completeness < TD_OPPORTUNITY_COMPLETENESS_FLOOR
  (50.0, see that constant's own comment for the real distribution this
  was picked against). BOTH FIELDS ARE OPTIONAL ON `row`. If a field is
  absent (None/missing), that half of the OR is simply never evaluated —
  if BOTH are absent, this gate never fires for that row at all, and
  classify_evidence_vs_price behaves exactly as it did before this
  stage (Stage 1f and earlier). This is a deliberate backward-
  compatibility design, not an oversight: nothing before Stage 1g ever
  had this data, and every caller that still doesn't shouldn't see any
  behavior change.

  WHY role_momentum_completeness == 0 but td_opportunity_completeness <
  50 (not also == 0, and not the same threshold for both): these are
  two different real distributions, not one threshold applied twice.
  Per the real week-7-2026 data the user reported running against
  nfl_stub_weeks: role_momentum_completeness clusters at exactly 0
  (423 of 734 real rows) or at 20-or-above (24+107+21+143+16 = 311 rows,
  the lowest of which is 20) — there is no row between 0 and 20, so
  `== 0` cleanly separates "fully fallback" from "has at least some real
  signal," with nothing arbitrary about exactly where the line falls.
  td_opportunity_completeness clusters at exactly 30 (399 of 734 rows)
  or at 60-or-above (65+47+1+222 = 335 rows) — again nothing observed
  between 30 and 60, so any threshold in that gap (not just 50) would
  draw an identical real split; 50 is the round number chosen inside it,
  the same "pick a round number inside a real observed gap" logic
  MIN_POOL_SIZE already uses for its own 150. Both thresholds are
  PROPOSALS against this one real week's distribution, not validated
  against outcome data — stated as plainly as every other arbitrary
  constant in this module.

  2. SAME POPULATION FOR BOTH RANKS (Task 2). Before this stage,
  evidence_rank_pct was computed over every row in `pool` with
  td_opportunity present (in practice, nearly the WHOLE pool, since
  td_opportunity is populated — even if only as a fallback — for
  essentially every rostered player), while price_rank_pct was computed
  over only the rows with a real price — two DIFFERENT populations,
  confirmed directly in Stage 1f's own investigation of this exact
  file. This stage unifies them: both ranks are now computed over the
  SAME population — rows in `pool` that (a) have a real
  consensus_price_american AND (b) do not themselves fail the
  completeness gate above (or have no completeness data at all, in
  which case they're treated as passing, per the backward-compatibility
  rule above). A row that fails its OWN completeness gate is excluded
  from this comparison population too, not just labeled
  insufficient_evidence directly — Stage 1f's own diagnosis was that
  letting hundreds of fallback-score rows sit in the comparison pool
  measurably distorted real players' ranks (3 of 16 real favorites
  flipped label once fill rows were removed from the pool); this
  population change is the structural fix for that, not just a label
  override on the fill rows themselves.

  3. THE POOL-SIZE GATE'S OWN POPULATION IS UNCHANGED (Task 3,
  explicitly). `price_pool_size` (what MIN_POOL_SIZE is checked against)
  is still the RAW count of priced rows in `pool`, computed BEFORE the
  completeness gate is applied to build the ranking population above —
  not the smaller, post-gate count. This is deliberate, not an
  oversight: the real evidence behind MIN_POOL_SIZE=150 (the Stage 1c
  write-up above) measured "distinct players with a quote that day,"
  which has nothing to do with completeness at all — a day's board can
  have 300 real live quotes and still be a valid, well-populated board
  even if half of those 300 players individually have thin trailing
  data. Gating pool size on the SMALLER, post-completeness count would
  silently make the floor harder to clear for reasons unrelated to what
  the floor was actually measuring.

  ORDERING (restated plainly, now four gates deep): (1) pool-size gate
  on the raw priced count, (2) this row's own completeness gate, (3)
  this row's own evidence_quality floor, (4) the gap comparison. Each
  gate is checked only if every gate before it passed — the `reason`
  string always names whichever gate actually fired first, never a
  later one that would also have fired.
"""
import bisect

FOOTBALL_EVIDENCE_WEIGHTS = {
    "td_opportunity": 30 / 70,
    "role_momentum": 20 / 70,
    "situation": 20 / 70,
}

GAP_THRESHOLD = 30.0
EVIDENCE_QUALITY_FLOOR = 50.0

# UNMEASURED PROPOSAL, value updated Stage 1c -- implements pool_size_
# report.md's own floor, not a separately-derived number. Originally 50
# (Stage 1b), picked as "~2.5x one game's ~20-player pool," a single
# anecdote with no day-level distribution behind it. Now 150 (Stage 1c),
# picked to sit inside a real reported gap in live 2026 day-level data --
# distinct-player-with-a-quote counts cluster at <=56 (tail days) or
# >=192 (normal days), with nothing observed in between -- per the user-
# reported query results in pool_size_report.md. Still a proposal, not a
# validated floor: 150 is a round number chosen to sit inside that real
# gap, not a number derived from any variance/stability measurement, and
# the user's reported metric (ANY quote that day) is related to but not
# identical to this gate's own price_pool_size (the caller's as-of-
# search-time pool) -- see pool_size_report.md's own explicit caveat on
# that distinction before treating 150 as validated for this exact use.
# STAGE 1g: checked against the RAW priced count, before the completeness
# gate below -- see module docstring's "STAGE 1g CHANGES", point 3.
MIN_POOL_SIZE = 150

# STAGE 1g, Task 1 -- UNMEASURED PROPOSALS, against one real week's
# distribution (season 2026, week 7; n=734; completeness values reported
# by the user from a real nfl_stub_weeks query, not independently queried
# by this package -- no DB access exists in this environment). See the
# module docstring's "STAGE 1g CHANGES" section for the full real
# distribution each of these two thresholds was picked against:
#   td_opportunity_completeness: 399 rows at exactly 30, 335 rows at 60+,
#     nothing observed in between -- 50 is a round number inside that gap.
#   role_momentum_completeness: 423 rows at exactly 0, 311 rows at 20+,
#     nothing observed in between -- 0 is not a round-number choice at
#     all, it is the literal boundary of the low cluster itself, which is
#     why ROLE_MOMENTUM_COMPLETENESS_ZERO below is an exact-equality
#     check, not a "< some threshold" one.
TD_OPPORTUNITY_COMPLETENESS_FLOOR = 50.0
ROLE_MOMENTUM_COMPLETENESS_ZERO = 0.0

SUPPORTS_EXPECTATION = "supports_expectation"
RAISES_QUESTIONS = "raises_questions"
INSUFFICIENT_EVIDENCE = "insufficient_evidence"

# The new field (Stage 1b) -- a relative-ranking fact ONLY. See module
# docstring's "STAGE 1b ADDITIONS" section for why this is a separate
# field from `label`, not a fourth label, and for why it reuses
# GAP_THRESHOLD rather than its own threshold.
EVIDENCE_RANKS_HIGHER = "evidence_ranks_higher"
EVIDENCE_RANKS_LOWER = "evidence_ranks_lower"
ALIGNED = "aligned"
EVIDENCE_VS_PRICE_VALUES = (EVIDENCE_RANKS_HIGHER, EVIDENCE_RANKS_LOWER, ALIGNED)

REQUIRED_ROW_FIELDS = ("td_opportunity", "role_momentum", "situation", "evidence_quality", "consensus_price_american")

# STAGE 1g, Task 1 -- OPTIONAL. Absent on a row (or on every row in a
# pool built before this stage existed) means "no completeness data for
# this row" -- the completeness gate simply never fires for it, matching
# this classifier's behavior from every stage before Stage 1g. See
# _fails_completeness_gate and the module docstring's "STAGE 1g CHANGES".
OPTIONAL_COMPLETENESS_FIELDS = ("td_opportunity_completeness", "role_momentum_completeness")


def _implied_probability(american_odds: float) -> float:
    """
    Standard American-odds -> implied probability, 0-1. A deliberate
    duplicate of nfl/market_value.py's implied_probability() — same two
    formulas, not imported. See this module's own docstring for why this
    classifier has zero imports from the rest of nfl/ on purpose.
    """
    price = float(american_odds)
    if price > 0:
        return 100.0 / (price + 100.0)
    return -price / (-price + 100.0)


def _percentile_rank(value: float, population: list) -> float:
    """
    % of `population` at or below `value`, 0-100 — the same side='left'
    semantics as normalize.percentile_lookup, reimplemented here (not
    imported; see module docstring) with plain Python instead of pandas/
    numpy, so this file has zero third-party or intra-repo dependencies.
    `population` should include `value` itself when the row being
    classified passes the completeness gate (it does, in that case — the
    row is a real member of the eligible population it's being ranked
    against). STAGE 1g: a row that itself FAILS the completeness gate is
    excluded from `population` even for its own rank computation — see
    classify_evidence_vs_price and the module docstring's "STAGE 1g
    CHANGES", point 2; this function itself is unaware of that and simply
    ranks whatever `value`/`population` it's given, same as every stage
    before this one.
    An empty population returns 50.0 (neutral), the same fallback
    convention normalize.fill_neutral uses elsewhere in this codebase.
    """
    n = len(population)
    if n == 0:
        return 50.0
    idx = bisect.bisect_left(sorted(population), value)
    return idx / n * 100.0


def _football_evidence_composite(row: dict) -> float:
    return sum(float(row[field]) * weight for field, weight in FOOTBALL_EVIDENCE_WEIGHTS.items())


def _fails_completeness_gate(row: dict, td_opportunity_completeness_floor: float) -> bool:
    """
    STAGE 1g, Task 1. True if `row`'s OWN completeness fields show its
    td_opportunity/role_momentum are themselves mostly fallback readings:
    role_momentum_completeness == 0, OR td_opportunity_completeness <
    td_opportunity_completeness_floor. Either field missing from `row`
    (None, or the key absent entirely) simply skips that half of the OR —
    a row with neither field present always returns False here (the gate
    never fires), matching this classifier's pre-Stage-1g behavior
    exactly. See module docstring's "STAGE 1g CHANGES" for the real
    distribution each threshold was picked against.
    """
    role_completeness = row.get("role_momentum_completeness")
    if role_completeness is not None and float(role_completeness) == ROLE_MOMENTUM_COMPLETENESS_ZERO:
        return True
    td_completeness = row.get("td_opportunity_completeness")
    if td_completeness is not None and float(td_completeness) < td_opportunity_completeness_floor:
        return True
    return False


def classify_evidence_vs_price(
    row: dict,
    pool: list,
    gap_threshold: float = GAP_THRESHOLD,
    evidence_quality_floor: float = EVIDENCE_QUALITY_FLOOR,
    min_pool_size: int = MIN_POOL_SIZE,
    td_opportunity_completeness_floor: float = TD_OPPORTUNITY_COMPLETENESS_FLOOR,
) -> dict:
    """
    Classify one player's row against its week's `pool` (a list of dicts,
    each with at least td_opportunity/role_momentum/situation; `row`
    itself must be a member of `pool`, or at least drawn from the same
    real population — the rank comparison means nothing against a
    different pool). See REQUIRED_ROW_FIELDS for what `row` itself must
    have; OPTIONAL_COMPLETENESS_FIELDS for what it may additionally carry.

    STAGE 1g: evidence_rank_pct and price_rank_pct are now computed over
    the SAME population — every row in `pool` that has a real
    consensus_price_american AND does not itself fail the completeness
    gate (see _fails_completeness_gate). Before this stage, the evidence
    ranking population was effectively the whole pool (anything with
    td_opportunity present) while the price ranking population was only
    the priced rows — two different populations. See module docstring's
    "STAGE 1g CHANGES", point 2, for why that mismatch mattered and what
    changed.

    `min_pool_size` (default MIN_POOL_SIZE=150, an UNMEASURED proposal —
    see module docstring and pool_size_report.md) gates on the RAW priced
    population size — every row in `pool` with a real
    consensus_price_american, counted BEFORE the completeness gate is
    applied (see module docstring's "STAGE 1g CHANGES", point 3, for why
    that specific ordering was kept) — when fewer than `min_pool_size`
    rows qualify, this function returns "insufficient_evidence"
    regardless of every other gate or the gap, no matter how clean the
    rest of the row looks.

    `td_opportunity_completeness_floor` (default
    TD_OPPORTUNITY_COMPLETENESS_FLOOR=50.0) is the one completeness
    threshold exposed as a parameter, for the same configurability
    discipline gap_threshold/evidence_quality_floor/min_pool_size already
    have. ROLE_MOMENTUM_COMPLETENESS_ZERO (an exact-equality check, not a
    floor) is not exposed as a parameter — see that constant's own
    comment for why 0 is the real boundary of the low cluster itself,
    not a round number chosen to sit near one.

    Returns:
      {
        "label": "supports_expectation" | "raises_questions" | "insufficient_evidence",
        "evidence_vs_price": "evidence_ranks_higher" | "evidence_ranks_lower" | "aligned" | None,
                                  # A RELATIVE-RANKING FACT ONLY — never a
                                  # fair-value or mispricing claim. None
                                  # whenever label is "insufficient_evidence"
                                  # (from ANY of the four gates) — see module
                                  # docstring's "STAGE 1c REVERSAL" section.
                                  # evidence_rank_pct/price_rank_pct/gap below
                                  # are UNAFFECTED by this and remain the real
                                  # numbers in every case.
        "evidence_rank_pct": float,      # 0-100, this row's football-evidence rank in the eligible population
        "price_rank_pct": float,         # 0-100, this row's price rank in the SAME eligible population
        "gap": float,                    # evidence_rank_pct - price_rank_pct, signed
        "implied_probability_pct": float,# 0-100, this row's own price as implied probability
        "evidence_quality": float,       # pass-through, the value the quality gate was checked against
        "price_pool_size": int,          # the RAW priced-pool size the pool-size gate was checked against (Task 3: before the completeness gate)
        "eligible_population_size": int, # STAGE 1g. the size of the SAME population both ranks were computed over (priced AND completeness-passing)
        "td_opportunity_completeness": float | None,  # STAGE 1g. pass-through, None if absent from `row`
        "role_momentum_completeness": float | None,   # STAGE 1g. pass-through, None if absent from `row`
        "reason": str,                   # one-line human explanation of why this label
      }

    Raises ValueError if `row` is missing any of REQUIRED_ROW_FIELDS —
    fail loud, same convention as personality_for_shelf()/
    intensity_for_band() elsewhere in this codebase: a row with no price
    or no evidence_quality has no business reaching this function at all,
    and a silent fallback here would quietly manufacture a label from
    data that was never real.
    """
    missing = [f for f in REQUIRED_ROW_FIELDS if row.get(f) is None]
    if missing:
        raise ValueError(f"classify_evidence_vs_price: row is missing required field(s) {missing}")

    # Task 3: the RAW priced-pool size, before the completeness gate --
    # see module docstring's "STAGE 1g CHANGES", point 3, for why this
    # ordering is deliberate.
    priced_pool = [r for r in pool if r.get("consensus_price_american") is not None]
    price_pool_size = len(priced_pool)

    # Task 2: the SAME eligible population for both ranks -- priced AND
    # not itself failing the completeness gate (or completeness simply
    # absent, which _fails_completeness_gate already treats as passing).
    eligible_population = [r for r in priced_pool if not _fails_completeness_gate(r, td_opportunity_completeness_floor)]

    composite = _football_evidence_composite(row)
    evidence_population = [_football_evidence_composite(r) for r in eligible_population]
    evidence_rank_pct = round(_percentile_rank(composite, evidence_population), 1)

    row_ip = _implied_probability(row["consensus_price_american"])
    price_population = [_implied_probability(r["consensus_price_american"]) for r in eligible_population]
    price_rank_pct = round(_percentile_rank(row_ip, price_population), 1)

    gap = round(evidence_rank_pct - price_rank_pct, 1)
    evidence_quality = float(row["evidence_quality"])

    td_completeness = row.get("td_opportunity_completeness")
    role_completeness = row.get("role_momentum_completeness")
    row_fails_completeness = _fails_completeness_gate(row, td_opportunity_completeness_floor)

    # evidence_vs_price: a pure relative-ranking fact, computed from the
    # real ranks the same way regardless of which label ultimately
    # applies -- see module docstring's "STAGE 1c REVERSAL" section for
    # why the VALUE RETURNED TO THE CALLER is nonetheless suppressed to
    # None below when label ends up INSUFFICIENT_EVIDENCE. gap itself
    # (and therefore this computation) still reuses gap_threshold, not a
    # separate constant, for the reason the module docstring's "STAGE 1b
    # ADDITIONS" section already gives.
    if abs(gap) <= gap_threshold:
        evidence_vs_price = ALIGNED
    elif gap > 0:
        evidence_vs_price = EVIDENCE_RANKS_HIGHER
    else:
        evidence_vs_price = EVIDENCE_RANKS_LOWER

    if price_pool_size < min_pool_size:
        label = INSUFFICIENT_EVIDENCE
        reason = (
            f"only {price_pool_size} players in this week's pool have a live price (below the "
            f"min_pool_size floor of {min_pool_size}) — a percentile computed against this few live quotes "
            "is not trustworthy enough to support any of the other labels, regardless of completeness, "
            "evidence_quality, or how close the ranks happen to look. UNMEASURED proposal, not a validated "
            "floor -- see pool_size_report.md."
        )
    elif row_fails_completeness:
        label = INSUFFICIENT_EVIDENCE
        reason = (
            f"this row's own completeness fields (role_momentum_completeness={role_completeness}, "
            f"td_opportunity_completeness={td_completeness}) show its td_opportunity/role_momentum are "
            "themselves mostly neutral-fallback readings, not real trailing signal — a rank comparison "
            "built from a fallback score isn't measuring anything real, regardless of evidence_quality or "
            "how the ranks happen to compare. UNMEASURED proposal thresholds -- see module docstring."
        )
    elif evidence_quality < evidence_quality_floor:
        label = INSUFFICIENT_EVIDENCE
        reason = (
            f"evidence_quality={evidence_quality} is below the floor ({evidence_quality_floor}) — "
            "the football-evidence pillars are too thin or too contradictory to trust any rank comparison, "
            "regardless of how close or far the price rank sits."
        )
    elif abs(gap) <= gap_threshold:
        label = SUPPORTS_EXPECTATION
        reason = (
            f"football-evidence rank ({evidence_rank_pct}) and price rank ({price_rank_pct}) are within "
            f"{gap_threshold} points of each other (gap={gap}) — the price and the on-field evidence are "
            "telling a similar story this week."
        )
    else:
        label = RAISES_QUESTIONS
        direction = "the evidence ranks well above the price" if gap > 0 else "the price ranks well above the evidence"
        reason = (
            f"football-evidence rank ({evidence_rank_pct}) and price rank ({price_rank_pct}) differ by "
            f"{abs(gap)} points, past the {gap_threshold}-point threshold ({direction}) — this does not say "
            "which one is \"right,\" only that they disagree enough to be worth a reader's attention."
        )

    # STAGE 1c REVERSAL (now covering all four gates, Stage 1g included):
    # evidence_vs_price is suppressed to None whenever the label is gated
    # to insufficient_evidence, from ANY gate above -- see module
    # docstring's "STAGE 1c REVERSAL" section for the full reasoning.
    # evidence_rank_pct/price_rank_pct/gap themselves are NOT touched by
    # this -- they remain the real computed numbers, available on this
    # return dict for diagnostic/review purposes, even on a gated row.
    if label == INSUFFICIENT_EVIDENCE:
        evidence_vs_price = None

    return {
        "label": label,
        "evidence_vs_price": evidence_vs_price,
        "evidence_rank_pct": evidence_rank_pct,
        "price_rank_pct": price_rank_pct,
        "gap": gap,
        "implied_probability_pct": round(row_ip * 100, 1),
        "evidence_quality": evidence_quality,
        "price_pool_size": price_pool_size,
        "eligible_population_size": len(eligible_population),
        "td_opportunity_completeness": float(td_completeness) if td_completeness is not None else None,
        "role_momentum_completeness": float(role_completeness) if role_completeness is not None else None,
        "reason": reason,
    }
