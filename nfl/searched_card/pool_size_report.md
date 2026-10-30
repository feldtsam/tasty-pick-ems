# Market-Value Pool-Size Investigation (Deliverable 2)

STAGE 1 DESIGN DOC. No implementation beyond what Deliverable 1 already
has (the `evidence_quality` floor, which is a separate, existing-signal
gate, not a pool-size gate). This document is read-only investigation of
code and local fixtures — no database access was available or used (see
"Confirming the DB-access question" below).

## What a "pool size" even means here

`market_value_score` (`nfl/scoring.py:score_market_value`) and this
package's own price-rank (`evidence_price_classifier.classify_
evidence_vs_price`) both percentile-rank `consensus_implied_probability`
within one week's live-poll population. The "pool" is: how many distinct
players have a posted `player_anytime_td` market from at least one
sportsbook, for that week, at the moment the percentile is computed. A
percentile computed against a pool of 20 is a much noisier number than
one computed against a pool of 500 — this is already a named,
acknowledged caveat in the existing code (`market_value.py:862-874`,
`score_market_value`'s own docstring: "a percentile computed against 20
players in one game is a much noisier, more easily-skewed number than a
percentile computed against a multi-season population").

## Pool size by day of week — every claim labeled

**CONFIRMED** (direct code comments, not inferred):

- `nfl/market_value.py:792` (`market_value_snapshot_for_curation`'s own
  docstring) and `nfl/api/index.py:1404` (`curate-and-write-drafts`'s own
  comment) both independently state: *"Thin/empty coverage (the normal
  state until Wed–Thu of a game week) is not a failure."* Two
  independent real comments in two different files agreeing on the same
  fact is as close to confirmed as code comments get without a live
  query.
- `nfl/api/curate_home_shelves.py:1073-1120` (`_MARKET_DATA_MIN_
  CHECKPOINT_GAP_HOURS` docstring): *"the real polling cadence in this data is
  itself sub-daily (multiple real polls per day, confirmed directly
  against real cached data)."* Confirms polling happens more than once a
  day once a market exists — relevant to pool-size *stability* within a
  day, not to the day-over-day growth question directly.
- `nfl/market_value.py:866` (`score_market_value`'s own docstring):
  *"right now, a single game (~20 players)."* A real, specific number
  from a real early investigation — the smallest unit of pool growth is
  roughly "~20 players per game that has a posted market," not a
  per-week constant.
- `nfl/market_value.py:701` (`market_intelligence_snapshot_for_
  generation`'s own docstring): a real, specific prior investigation
  found `nfl_price_history` held **"17,876+ real rows"** for
  season=2026, week=1. This is **total poll rows for the week**, not
  distinct players (the same player can appear in many rows across
  many intra-day polls — see the sub-daily cadence point above) — it
  upper-bounds nothing about per-day distinct-player pool size on its
  own, but it does confirm that a real, substantial polling volume
  exists for at least one real week once the Make.com scenario is
  running.

**INFERRED** (reasonable, not directly stated anywhere):

- Pool size very likely grows monotonically-ish across the week:
  Thursday Night Football's market typically posts earliest (often
  days ahead), Sunday's full slate (the bulk of games) posts
  progressively through the week and fills in most through Thu/Fri/Sat,
  and Monday Night Football's own market is sometimes last to firm up
  for props specifically. This is standard sportsbook behavior
  (consistent with "thin/empty until Wed–Thu" above), not something this
  codebase's comments state as a day-by-day curve.
- Early in the week (Mon/Tue, before any market has posted), the pool
  for market-value purposes is effectively **zero or near-zero** for
  most of the slate — consistent with the "thin/empty... until Wed–Thu"
  comment, and consistent with `score_universal_tpe`'s own
  renormalization path existing specifically to handle an all-NaN
  market column gracefully.
- By Sunday (the day of the bulk of games, and the day closest to
  kickoff for most of the slate), the pool is very likely at or near its
  real weekly maximum, since sportsbooks converge on firmer numbers as
  games approach and most books post anytime-TD markets for most
  rostered skill players by then.

**UNKNOWN** (no local way to check):

- The actual distinct-player pool size, by day, for any real week. Not
  derivable from `~20 players/game` without knowing how many games have
  a posted market on each day, which is not recorded anywhere local.
- Whether pool size is meaningfully different week-to-week (e.g. bye
  weeks reducing the slate, weather-driven line delays, etc.).
- Whether the 17,876-row week-1-2026 figure corresponds to a thin or a
  rich distinct-player pool — row count and distinct-player count are
  different questions this codebase's local artifacts cannot resolve.

## Confirming the DB-access question, directly

`nfl_price_history` (the table that would answer this with a real query)
is **not local** — confirmed by searching this repository for its
schema: no `CREATE TABLE`, no `schema.sql`, no ORM model, anywhere in
`nfl/` (the only hits are docstring/comment mentions of the table name
in `market_intelligence.py`/`market_value.py`/`api/index.py`, never a
definition). `nfl/market_value.py:396-415`'s own module comment confirms
this directly: the project has "no connection strings, no schema.sql, no
ORM usage" for anything in `nfl/` — the real data lives on a separate
Lovable/Supabase-style backend, reachable only through a signed HTTP
forward (`nfl/api/lovable_forward.py`), which this task's hard
constraints forbid calling (no network calls of any kind) even if
credentials were available in this environment (they are not — no
`NFL_PIPELINE_WEBHOOK_SECRET` or equivalent was read from this
sandbox). **Confirmed: no live DB access was available or used for this
investigation**, and none was attempted.

## A defensible minimum pool size, and why it's a proposal, not a measurement

Given the real `~20 players/game` figure and a typical ~13-16 game NFL
week, a plausible ballpark for "most of the slate has posted" is
somewhere in the 150-300+ distinct-player range by Thursday/Friday —
but this is arithmetic on one confirmed anecdote, not a measured
distribution, and should be labeled as such to anyone reading this.

**STAGE 1b's original proposed floor** (superseded — see "STAGE 1c
UPDATE" below, kept here for the record rather than silently deleted):
*a minimum of 50 distinct players with a live quote, for that week,
before showing the market-confidence percentile at all.* Reasoning at
the time, stated plainly: 50 is roughly 2.5× the one confirmed
single-game pool size (~20 players) — i.e., "at least a few games'
worth of markets have posted," not just one early game (Thursday night)
carrying the entire week's percentile. Below 50, a single outlier quote
can move a percentile rank by several points in either direction — the
kind of instability the existing `score_market_value` docstring already
flags as a known, accepted weakness for small pools. This was explicitly
**not** derived from any real variance measurement — a round,
defensible-sounding number chosen the same way `GAP_THRESHOLD` in
`classification_rubric.md` was, extrapolated from a single confirmed
anecdote (`~20 players/game`) with no real day-level distribution behind
it at all.

Per product rule 5: below this floor, the design is to **hide the
percentile/market-confidence number but keep showing the observed odds
and the raw implied probability** — this document proposes the number;
no code in this package implements the hide/show UI toggle itself (Stage
1 is design-and-tests; see `classification_rubric.md`'s own classifier,
which already computes `implied_probability_pct` independently of pool
size and would remain displayable even when the percentile is hidden).

**STAGE 1b UPDATE**: this document's proposed floor was also
implemented as a GATE inside the classifier itself (`evidence_price_
classifier.classify_evidence_vs_price`'s new `min_pool_size` parameter).
This is a different, narrower thing than the UI hide/show toggle
described above (that toggle is about whether a reader ever sees a
market-confidence percentile at all; the classifier gate is about
whether this package's own `supports_expectation`/`raises_questions`/
`insufficient_evidence` label should be trusted) — both reuse the exact
same proposed number for the exact same reason, but neither implements
the other. The UI toggle itself remains unimplemented, as stated above.

## STAGE 1c UPDATE: the floor, 50 -> 150, on new real evidence

`MIN_POOL_SIZE` (and the `min_pool_size` gate parameter above) is now
**150**, not 50. This is reported here as **user-supplied real query
results**, not as something this package independently confirmed — this
task's environment has no DB access (see "Confirming the DB-access
question, directly" above, unchanged: still true, no live query was run
from inside this revision either). The user reports running the exact
read-only SQL this document already proposed (see "The exact read-only
SQL someone with real DB access should run" below, unchanged by this
revision), against live 2026 NFL `nfl_price_history` data, and reports
the following finding:

> Live 2026 NFL data shows distinct players with a quote per day
> clusters at 56 or fewer (tail days belonging to the next week's slate:
> 18-56 players) and 192 or more (every normal day; every week's first
> day was above 280 except Week 1 at 192). Anything from about 60 to 190
> separates the clusters; 150 is a round number inside that gap and is
> still a proposal, not a derived threshold.

**What this is, stated plainly**: a real two-cluster pattern in real
day-level data — every observed day falls into one of two clumps (≤56 or
≥192), with nothing reported in the roughly 60-190 range between them.
150 is a round number picked to sit inside that real gap. This is a
materially **stronger** justification than Stage 1b's original 50 —
that number was arithmetic on a single confirmed anecdote with no
day-level distribution behind it at all, where 150 is chosen inside an
actual reported gap in actual day-level counts — but it is still **not**
a validated or derived threshold. No percentile-stability or outcome
data backs 150 specifically over, say, 100 or 180; it is still a
proposal, same honesty standard this document has used throughout, just
a better-grounded proposal than before.

**An explicit caveat this document will not gloss over**: the user's
reported metric and this classifier's own `price_pool_size` are
RELATED but NOT THE SAME THING, and treating them as interchangeable
would overstate how well-grounded 150 actually is for THIS gate
specifically. The user's reported counts are a **daily snapshot** — how
many distinct players have *any* quote posted on a given calendar day.
`price_pool_size` (what `classify_evidence_vs_price`'s `min_pool_size`
gate actually checks) counts rows in whatever `pool` list the *caller*
hands the classifier, which this package's own design assumes has
already been scoped by the caller to "each player's latest quote **as
of search time**" (see the second SQL query below, "using ONLY each
player's latest poll as of end-of-day" — an as-of-a-specific-moment
count, not a full-calendar-day snapshot count). These two numbers are
related — a daily snapshot is an upper bound on what an as-of-search-time
pool built that same day could contain — but they are not guaranteed to
be the same size, and no measurement locally confirms how closely they
track each other in practice. Carrying the 150 threshold over from the
(daily-snapshot) metric the user measured to gate the (as-of-search-time)
metric this classifier actually uses is a reasonable proxy, given the
two are clearly related and no better-targeted number exists — not a
claim that the threshold has been validated against the exact metric it
is now gating.

## The exact read-only SQL someone with real DB access should run

Column names below follow `market_value.PRICE_HISTORY_COLUMNS` — the
intended schema documented in that module (not a live `CREATE TABLE`
statement this investigation had access to; verify real column names
against the actual table before running).

```sql
-- Distinct-player pool size per day of a target week, using the LATEST
-- poll per player per day (mirrors market_value.latest_price_history_
-- per_player's own "last poll on file" semantic, scoped to one day
-- instead of the whole week).
SELECT
    date_trunc('day', poll_timestamp) AS poll_day,
    extract(isodow FROM poll_timestamp) AS iso_day_of_week,  -- 1=Mon .. 7=Sun
    count(DISTINCT player_id) AS distinct_players_with_a_live_quote
FROM nfl_price_history
WHERE season = :season
  AND week = :week
  AND matched = true
  AND player_id IS NOT NULL
GROUP BY 1, 2
ORDER BY 1;

-- The same, but using ONLY each player's latest poll as of end-of-day
-- (closer to "how big was the real board at that point"), for a single
-- target week, run once per day if a point-in-time cut is needed:
SELECT count(DISTINCT player_id)
FROM (
    SELECT player_id, max(poll_timestamp) AS latest_poll
    FROM nfl_price_history
    WHERE season = :season AND week = :week
      AND matched = true AND player_id IS NOT NULL
      AND poll_timestamp <= :as_of_timestamp
    GROUP BY player_id
) t;

-- Sanity check against the one real anecdote this investigation found
-- (17,876+ raw rows for season=2026, week=1) -- total POLL rows, not
-- distinct players, for comparison against the distinct-player counts
-- above:
SELECT count(*) AS total_poll_rows
FROM nfl_price_history
WHERE season = 2026 AND week = 1;
```

## STAGE 1c, Task 4: a read-only finding on NULL `week` rows

Read-only investigation (grep/read only, no code written, per this
task's scope) of how `nfl_price_history` rows can end up with
`week = NULL` — relevant here because this package's whole pool-
construction premise (a `pool` list the caller has already scoped to
one real `season`/`week`) assumes `week` is real on every row the caller
hands in. Full citations and reasoning are in the final report (Task 4);
the one-sentence takeaway worth recording here: `week` is resolved
per-row, independently of player matching, via a schedule lookup
(`nfl/api/index.py:_week_lookup_for_season`) that returns `None` — not a
guess — for any event whose `(home_team, away_team)` pair the lookup
can't resolve against that season's schedule, so a row can carry a real
`player_id` and a real price while still having `week = NULL`.
**Defensive note for a future real caller**: this package's own gates
(`MIN_POOL_SIZE`/`min_pool_size` above included) have no way to detect a
`pool` silently contaminated with such rows — a real caller must filter
`week IS NOT NULL` explicitly (not just match `player_id IS NOT NULL` or
`matched = true`, and not rely on an exact `week = :week` equality
alone, which happens to exclude NULLs incidentally in the two queries
above but would not in a differently-shaped future query) before
building `pool` from `nfl_price_history`.

## Stage 1g — MIN_POOL_SIZE's own population, restated precisely

`evidence_price_classifier.py`'s `MIN_POOL_SIZE=150` gate is checked
against the RAW count of priced rows in the week's pool — deliberately
BEFORE the new completeness gate (see `classification_rubric.md`'s own
Stage 1g section) narrows the ranking population down further. This is
the correct match to what this document's own real evidence actually
measured: "distinct players with ANY live quote that day" says nothing
about whether those players' `td_opportunity`/`role_momentum` pillars
are themselves fully populated or mostly fallback — a day's board with
300 real quotes is still a well-populated board by the metric this
threshold was picked against, even if half of those 300 players
individually have thin trailing data. Gating pool size on the smaller,
post-completeness count would conflate two different questions
("is the board well-populated" vs. "is this particular row's own data
real") that this revision deliberately keeps separate, as two distinct
gates checked in sequence, not one.
