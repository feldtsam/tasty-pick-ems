# Evidence-vs-Price Classification Rubric (Deliverable 1)

STAGE 1 DESIGN DOC — nothing described here is wired anywhere. See
`searched_card_flags.py`. The pure function implementing this rubric is
`evidence_price_classifier.classify_evidence_vs_price()`, and every
number in this document was produced by actually running it (see
`test_evidence_price_classifier.py`, which reproduces this table under
pytest) — nothing below is hand-typed arithmetic.

## The hypothesis this rubric tests, not assumes

Compare a player's rank on the three football-evidence pillars
(`td_opportunity`, `role_momentum`, `situation`) within the week's pool
against the rank of the price (implied probability) in that **same**
pool. A large gap between the two ranks suggests the on-field evidence
and the price are telling different stories this week
(`raises_questions`); a close match suggests they agree
(`supports_expectation`). `evidence_quality` below a floor gates
`insufficient_evidence` regardless of the rank comparison — a thin or
self-contradictory evidence base can't license any claim about agreement
or disagreement with the price.

This is a hypothesis, not a validated signal. See "What this rubric does
NOT prove" below.

## Stage 1b additions: `evidence_vs_price` (new field) and the pool-size gate

Two separate revisions in this pass, neither changing the three labels'
meaning:

1. **`evidence_vs_price`** — a new field on the classifier's return
   dict, not a fourth label. The three labels above (especially
   `raises_questions`) were, on purpose, direction-blind (see "Where
   this rubric feels arbitrary" below, kept from Stage 1): a player the
   market likes *more* than the football evidence does, and a player
   the market likes *less*, both fire `raises_questions` identically.
   `evidence_vs_price` exposes that direction, as its own, separate,
   ONLY-a-ranking-fact value:
   - `"evidence_ranks_higher"` — the football-evidence rank sits above
     the price rank, by more than `GAP_THRESHOLD`.
   - `"evidence_ranks_lower"` — the reverse.
   - `"aligned"` — the two ranks are within `GAP_THRESHOLD` of each
     other.

   **Why this reuses `GAP_THRESHOLD` rather than its own threshold**:
   `label`'s own supports_expectation/raises_questions split already
   uses this exact gap comparison. Giving `evidence_vs_price` a
   different, narrower boundary would let the two fields describe the
   same real gap in contradictory ways on the same card (e.g. `label`
   says `supports_expectation` while `evidence_vs_price` says
   `evidence_ranks_higher`, for the identical gap) — two numbers this
   module has never validated against outcomes disagreeing with each
   other is a worse failure mode than the field simply inheriting the
   one threshold already in use. This is a judgment call, stated
   plainly, not a finding — a reasonable reviewer could ask for a
   dedicated, independently-chosen threshold instead; the case against
   that threshold existing nowhere is the self-contradiction risk above,
   not that a second number couldn't ever be justified.

   **`evidence_vs_price` MUST NEVER be read as mispricing or fair
   value** — same hard constraint as `label`. It states only where two
   ranks sit relative to each other in this week's pool.

   **Is it computed when `label` is `insufficient_evidence`? STAGE 1c
   REVERSAL — the answer below is from Stage 1b and is now WRONG; it is
   kept here, struck through in spirit, so the reversal has something
   concrete to point at.** Stage 1b's original answer was: *"Yes,
   always — from either gate (the pre-existing `evidence_quality` floor,
   or the new pool-size gate below). `evidence_rank_pct`/`price_rank_pct`
   /`gap` were already computed and returned unconditionally in Stage 1
   ... making the derived `evidence_vs_price` field disappear only under
   one specific label would be an inconsistent half-measure, not a real
   safety gate ... the actual gate is enforced at the prompt layer
   instead."*

   **As of Stage 1c, that call is reversed: `evidence_vs_price` is now
   `None` whenever `label` is `insufficient_evidence`, from either
   gate.** `evidence_rank_pct`/`price_rank_pct`/`gap` are UNCHANGED by
   this — they remain real, computed, visible numbers on a gated row
   (the Laquon Treadwell row below still shows a real gap). Only the
   derived `evidence_vs_price` value is withheld. Why the reversal: Stage
   1b's argument treated the prompt-layer instruction (telling the model
   not to trust the comparison under `insufficient_evidence`) as an
   adequate safety mechanism on its own. On review, it isn't a
   *guarantee* the way withholding the data is — a pipeline built around
   "never hand the model a fact it shouldn't lean on" is weaker when it
   hands the model the fact anyway and simply asks it not to use it.
   Removing `evidence_vs_price` from what reaches `searched_card_
   prompt.py` and the assembled card entirely closes that gap at the data
   layer, consistent with how `tpe_score`/`market_value_score` are
   already handled (never reaching the citable-fields set at all, rather
   than reaching the model with an instruction not to use them). See
   `evidence_price_classifier.py`'s own module docstring, "STAGE 1c
   REVERSAL" section, for the full reasoning.

2. **The pool-size gate** — `classify_evidence_vs_price` now takes a
   `min_pool_size` parameter (default `MIN_POOL_SIZE`, **150 as of Stage
   1c** — was `50` under Stage 1b; see "Stage 1c: the pool floor, 50 ->
   150" below for the new evidence behind that change — directly
   implementing `pool_size_report.md`'s own proposed floor, not a
   separately-derived number). When fewer than `min_pool_size` rows in
   the pool have a real `consensus_price_american`, the classifier
   returns `insufficient_evidence` regardless of `evidence_quality` or
   the gap. **UNMEASURED, stated plainly, same honesty standard as
   `pool_size_report.md`'s own framing**: 150 is a better-grounded number
   than 50 was (see below), but it is still a round number chosen to sit
   inside a real gap, not a validated one — see that document for why.
   Checked BEFORE the `evidence_quality` floor (a pool too thin to trust
   undermines the quality comparison just as much as the gap
   comparison, so there's no reason to let a clean `evidence_quality`
   value override a gate that's really about population size).

## Stage 1c: the pool floor, 50 -> 150

`MIN_POOL_SIZE` changed from `50` (Stage 1b) to `150` (Stage 1c), on new
evidence reported directly by the user, not independently queried or
confirmed by this package — this environment has no DB access (see
`pool_size_report.md`'s own "Confirming the DB-access question" section,
unchanged by this revision: still true, still no live query was run from
inside this task). The user reports running the exact read-only SQL
`pool_size_report.md` itself proposed, against live 2026 NFL
`nfl_price_history` data, and finding:

> Live 2026 NFL data shows distinct players with a quote per day
> clusters at 56 or fewer (tail days belonging to the next week's slate:
> 18-56 players) and 192 or more (every normal day; every week's first
> day was above 280 except Week 1 at 192). Anything from about 60 to 190
> separates the clusters; 150 is a round number inside that gap and is
> still a proposal, not a derived threshold.

**Honest framing of what this is and isn't**: 150 is NOT a validated or
derived threshold — it is a round number picked to sit inside a real
observed gap between two real clusters in real day-level data. That is a
materially stronger justification than Stage 1b's original "roughly
2.5× one game's ~20-player pool" arithmetic (a single confirmed anecdote
with no day-level distribution behind it at all — see this document's
original framing below, kept for the record), but it is still a
proposal, not a measurement: no outcome or percentile-stability data
backs 150 specifically over, say, 100 or 180 — only that both numbers
sit inside the same observed gap.

**A caveat this document will not gloss over**: the user's reported
data and this classifier's own `price_pool_size` are RELATED but NOT
IDENTICAL metrics. The user's numbers count distinct players with *any*
quote on a given calendar day — a daily snapshot. `price_pool_size` (the
value `classify_evidence_vs_price` actually gates on) counts rows in
whatever `pool` list the *caller* hands it, which this package's own
design assumes has already been scoped by the caller to "each player's
latest quote as of search time" (see `pool_size_report.md`'s second SQL
query, "using ONLY each player's latest poll as of end-of-day") — an
as-of-search-time count, not a calendar-day snapshot count. A pool built
at 2pm on a Wednesday and a count of "everyone with a quote at some point
on Wednesday" are related (the second is an upper bound on candidates for
the first) but can genuinely differ in size. Carrying the 150 threshold
over from one metric to gate the other is a reasonable proxy, not a
claim that the two numbers measure the same thing — stated plainly here
rather than left implicit.

## The three labels

- **`supports_expectation`** — football-evidence rank and price rank are
  within `GAP_THRESHOLD` (30 percentile points) of each other.
- **`raises_questions`** — they differ by more than that, in either
  direction. This does **not** say the price is wrong, or that the
  evidence is wrong — only that they disagree enough to be worth a
  reader's attention. See product rule 4: this label must never be
  worded as a fair-value or mispricing claim.
- **`insufficient_evidence`** — `evidence_quality` is below the floor
  (50.0). Checked **before** the rank comparison; a row can have a
  tiny rank gap and still be `insufficient_evidence` if the underlying
  evidence can't be trusted (see the Laquon Treadwell example below).

## Every numeric threshold, stated plainly

| Constant | Value | Justification | Honest status |
|---|---|---|---|
| `FOOTBALL_EVIDENCE_WEIGHTS` | td_opportunity 30/70, role_momentum 20/70, situation 20/70 | **Not a new number.** These are `scoring.CONFIG["universal_tpe"]["core_weights"]`'s own real weights (30/20/20/20, market_value_score excluded), renormalized over the three football pillars the exact same way `score_universal_tpe` already renormalizes `core_score` for any row missing `market_value_score`. | Grounded in existing, reviewed code — the one number here that is not arbitrary. |
| `GAP_THRESHOLD` | 30.0 percentile points | Chosen by inspecting the real rank-gap distribution on 2025 week 10 (n=745, see below): median absolute gap ≈28.3, and 30.0 lands close to an even split (52.5% / 47.5%) before the evidence_quality gate is applied. | **Arbitrary.** Picked to produce a non-degenerate split of *this fixture's own distribution*, not validated against any real anytime-TD outcome. See below for why this is a real limitation, not a formality. |
| `EVIDENCE_QUALITY_FLOOR` | 50.0 | Reuses `scoring.py`'s own `fill_neutral`/`fallback_sentinel` convention (50.0 is the exact value `score_signal_breach`'s `fallback_sentinel` already uses to mean "a missing reading, not a genuinely weak one"). `evidence_quality = sqrt(completeness * convergence)` degenerates to exactly 50.0 when both axes are themselves fully neutral-fallback — below that is *worse* than having no real data at all. | Grounded in an existing codebase convention, not invented fresh — but the choice of exactly this degenerate point as "the" floor (rather than, say, the 25th percentile of the real distribution) is still a judgment call. |

## Real numbers: the rank-gap distribution (2025, week 10; n=745)

Source: `nfl/data/stub_weeks/2025_wk10.csv` — the only local fixture with
real, populated `consensus_price_american` across a FULL week (745 of
745 rows). **CORRECTED (Stage 1b)**: `2026_wk1.csv` does NOT have zero
populated odds, as this document originally (and wrongly) stated —
re-checked directly with code: it has 20 of 772 rows populated, the
same 20-row count as `synthetic_smoke_test.csv` (see "Why the short-odds
band is (mostly) synthetic" below for the full correction, including a
real negative-odds row this error also obscured). `2025_wk10.csv` is
still the right fixture for the numbers in this section specifically,
because 745/745 is a full week's coverage and 20/772 is not.

```
abs(gap) describe():  mean=32.1  std=23.1  min=0.0  25%=12.8  50%=28.3  75%=48.2  max=95.6

fraction of population with abs(gap) <= threshold:
  15 -> 29.7%    20 -> 37.3%    25 -> 43.2%    30 -> 52.5%    35 -> 60.8%    40 -> 67.0%

evidence_quality < 50.0:  35 / 745 rows (4.7%)
```

Resulting label distribution on this real week, at the chosen thresholds:

```
supports_expectation:    379  (50.9%)
raises_questions:        331  (44.4%)
insufficient_evidence:    35  ( 4.7%)
```

## Real rows, real ranks, real labels (≥10 rows, short/mid/long odds)

Produced by `test_evidence_price_classifier.py::
test_classify_real_fixture_rows_and_print_rubric_table` and
`::test_synthetic_short_odds_rows_for_the_band_the_real_fixture_lacks`
— reproduced verbatim from the actual pytest `-s` output (see the final
report for the full captured run).

**Real fixture rows** (2025 week 10 — mid/long odds only; this fixture's
minimum price is +250, see "where this rubric is already showing its
seams" below):

| player | pos | price | implied% | td_opp | role_mom | situation | ev_quality | ev_rank | px_rank | gap | **label** |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|---|
| Tutu Atwell | WR | +250 | 28.6 | 37.9 | 43.9 | 61.6 | 87.3 | 16.5 | 99.7 | -83.2 | **raises_questions** |
| Jaylen Wright | RB | +317 | 24.0 | 57.2 | 56.0 | 60.4 | 64.4 | 68.6 | 93.3 | -24.7 | **supports_expectation** |
| Braelon Allen | RB | +396 | 20.2 | 43.2 | 44.5 | 28.2 | 91.5 | 5.5 | 86.6 | -81.1 | **raises_questions** |
| Nico Collins | WR | +459 | 17.9 | 37.0 | 31.8 | 72.7 | 74.3 | 15.6 | 80.1 | -64.5 | **raises_questions** |
| Juwan Johnson | TE | +555 | 15.3 | 48.9 | 31.9 | 43.1 | 88.0 | 9.4 | 73.4 | -64.0 | **raises_questions** |
| Devin Singletary | RB | +636 | 13.6 | 36.2 | 36.4 | 42.1 | 93.7 | 4.0 | 67.0 | -63.0 | **raises_questions** |
| Jake Bobo | WR | +714 | 12.3 | 57.2 | 74.5 | 62.6 | 59.9 | 88.7 | 60.4 | 28.3 | **supports_expectation** |
| John FitzPatrick | TE | +988 | 9.2 | 80.7 | 32.6 | 29.4 | 69.8 | 38.3 | 40.7 | -2.4 | **supports_expectation** |
| Justin Shorter | WR | +1228 | 7.5 | 57.2 | 50.0 | 23.6 | 53.6 | 13.8 | 20.9 | -7.1 | **supports_expectation** |
| Grant Calcaterra | TE | +1390 | 6.7 | 57.2 | 50.0 | 53.8 | 63.4 | 48.3 | 7.8 | 40.5 | **raises_questions** |
| Marcedes Lewis | TE | +417 | 19.3 | 57.2 | 100.0 | 45.1 | **44.2** | 91.3 | 84.2 | 7.1 | **insufficient_evidence** |
| Laquon Treadwell | WR | +422 | — | — | — | — | **49.8** | — | — | 13.9 | **insufficient_evidence** |

Laquon Treadwell is the clearest illustration of the floor-before-gap
ordering: his rank gap (13.9) is well inside the supports_expectation
band on its own, but `evidence_quality=49.8` sits just under the 50.0
floor, so the row is gated to `insufficient_evidence` regardless (see
`test_low_evidence_quality_yields_insufficient_evidence_regardless_of_gap`).

**Synthetic short-odds rows** (−300 to −110; see below for why these are
synthetic, run against the real 745-player pool):

| player (synthetic) | price | evidence_quality | gap | **label** |
|---|--:|--:|--:|---|
| strong goal-line back | −250 | 80.0 | −0.7 | **supports_expectation** |
| short price, mediocre evidence | −180 | 70.0 | −92.5 | **raises_questions** |
| short price, thin sample | −120 | 42.0 | −43.1 | **insufficient_evidence** |

## Why the short-odds band is (mostly) synthetic, stated plainly

**CORRECTED (Stage 1b)**: the original version of this section claimed
"none of the three local fixtures contain a single negative/favorite
consensus_price_american value." That was wrong, and the error survived
into `pool_size_report.md`'s own fixture-coverage note too (also
corrected there). Checked directly this time, with code, not re-asserted
from memory: `2026_wk1.csv` has 20 of 772 rows with a populated
`consensus_price_american` (not zero, as Stage 1 claimed), and one of
those 20 **is** negative: Jaxon Smith-Njigba, −105.0, with
`td_opportunity=57.2, role_momentum=50.0, situation=49.1,
evidence_quality=34.9` all real and populated alongside it.
`synthetic_smoke_test.csv` carries the identical 20-row price pattern
(including the same −105.0 row, there under a placeholder name), which
is presumably why the two were conflated in the original pass.

Run through the real classifier, against `2026_wk1.csv`'s own full
772-row pool (20 of which carry a price — the resulting pool gate
output and 745-row-pool comparison are both reproduced in Task 5 of the
final report, not just asserted here): this row's `evidence_quality`
(34.9) sits below `EVIDENCE_QUALITY_FLOOR` (50.0), so it classifies
`insufficient_evidence` regardless of its rank gap — a real negative-
odds row, but one that (by coincidence, not by selection) doesn't
exercise the `supports_expectation`/`raises_questions` split on the
short-price band either. So the corrected, honest statement is: a real
negative-odds row with a full real evidence record **does** exist
locally (contrary to the original claim), but it does not, by itself,
give this rubric a real demonstration of the short-price band hitting
`supports_expectation` or `raises_questions` — only `2025_wk10.csv`
(745/745 populated, minimum price +250, confirmed with no negative rows
in THAT specific fixture) gives a full week's worth of real short-to-
long coverage, and it has no favorites in it. The three synthetic rows
below remain the only local demonstration of the −300/−110 band
actually reaching `supports_expectation`/`raises_questions`, and should
still not be read as evidence the rubric behaves well on real
short-odds data — only that the function doesn't error or do anything
obviously broken on that input shape.

## Where this rubric feels arbitrary, where it might misclassify, what would validate it

- **The gap threshold is a property of this population, not of truth.**
  A percentile-rank gap between two largely-independent ranked
  quantities is roughly uniformly distributed by construction — *any*
  cutoff will classify close to a fixed fraction of *any* population as
  "far apart," regardless of whether that fraction means anything
  editorially. Picking 30 because it lands near an even split on one
  real week is a defensible way to avoid a degenerate rubric (one label
  swallowing everything), but it is not evidence the threshold is
  *correct* in the sense of separating "worth a reader's attention" from
  "not."
- **Direction-blindness is a real simplification.** `raises_questions`
  fires identically whether the evidence outranks the price or the price
  outranks the evidence. Those are plausibly different editorial
  situations (a player the market likes *more* than the on-field
  evidence does is a different kind of story than one the market likes
  *less*), and this rubric collapses them into one label on purpose, for
  simplicity, not because they're interchangeable.
- **The three-pillar composite's internal weighting is inherited, not
  re-derived for this purpose.** It's the right call to reuse
  `core_weights` rather than invent new ones, but those weights were
  tuned (loosely — see `scoring.py`'s own "hypothesis to tune later"
  framing) for a different end: predicting `tpe_score`'s own ranking of
  opportunity, not for this rank-vs-price comparison specifically.
- **What real outcome data would be needed before trusting this**:
  real anytime-TD results joined to the week's classification, checked
  for whether `raises_questions` rows (the evidence/price disagreement
  case) actually scored at a different rate than `supports_expectation`
  rows of similar `tpe_score` — and separately, whether the *direction*
  of disagreement (evidence > price vs. price > evidence) predicted
  anything differently. None of that exists locally; this package has
  never been run against real outcomes, and says so rather than implying
  otherwise.

## Stage 1g — the completeness gate and the same-population fix

Two real, user-supplied findings drove this revision, both against one
real week (season 2026, week 7; n=734 real `nfl_stub_weeks` rows):

**The completeness gate.** `td_opportunity_completeness` and
`role_momentum_completeness` (scoring.py's own real fallback-tracking
columns — 0 means every input to that pillar was a neutral-50 fallback,
100 means every input was real) were joined in for the first time. Their
real distribution on this week: `td_opportunity_completeness` clusters
at exactly 30 (399 of 734 rows) or at 60-and-above (335 rows), nothing
observed in between; `role_momentum_completeness` clusters at exactly 0
(423 of 734 rows) or at 20-and-above (311 rows), nothing observed in
between. `TD_OPPORTUNITY_COMPLETENESS_FLOOR = 50.0` and
`ROLE_MOMENTUM_COMPLETENESS_ZERO = 0.0` (in `evidence_price_
classifier.py`) are both picked inside/at those real observed gaps — the
same "round number inside a real cluster boundary" logic `MIN_POOL_SIZE`
already used for 150, now applied twice more. **Still proposals, not
validated thresholds** — nothing here has been checked against real
anytime-TD outcomes either.

**Why this replaces, and improves on, Stage 1f's heuristic.** Stage 1f
could only identify "fill" rows by `td_opportunity == 57.1 AND
role_momentum == 50.0` — a real but imperfect proxy (392 of 734 rows
matched it, but the true completeness-based count differs slightly,
since a row can have SOME real signal in one pillar and none in the
other). The real completeness columns are the authoritative signal this
heuristic was always standing in for.

**Same population (Task 2).** Before this stage, `evidence_rank_pct` was
computed over essentially the WHOLE pool (anything with `td_opportunity`
present — in practice nearly every row, fallback rows included), while
`price_rank_pct` was computed over only the priced rows — two different
populations. Stage 1f's own recomputation (see the final report for that
stage) proved this mismatch was not academic: removing fill rows from the
comparison pool flipped 3 of 16 real favorites' labels. This stage fixes
it structurally — both ranks are now computed over the SAME population:
priced AND completeness-gate-passing. A row that fails its own
completeness gate is excluded from everyone else's ranking population
too, not just labeled directly.

**The pool-size gate's own count is deliberately unchanged (Task 3).**
`MIN_POOL_SIZE` is still checked against the RAW priced count, before
the completeness gate shrinks the ranking population — because the real
evidence behind 150 (the two-cluster day-level pattern reported in
`pool_size_report.md`) measured "distinct players with ANY live quote
that day," which has nothing to do with completeness. Gating pool size
on the smaller, post-completeness count would make the floor harder to
clear for a reason it was never designed to measure.
