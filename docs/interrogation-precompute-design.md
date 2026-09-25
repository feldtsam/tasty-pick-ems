# Interrogation Precompute — Design

Design only. Nothing here is built. Written 2026-09-25.

## Why

Curation runs daily at 10:00 CT and calls `/api/curate-and-write-drafts`, a Vercel
function with `maxDuration: 300` (`nfl/vercel.json`). Interrogation runs inside that
request and is the dominant term:

- 286 ATTD-eligible candidates in week 3; Pass 3's 65%-per-band gate selects **186**
- at `interrogation_max_concurrency: 15` that is **13 sequential waves**, each one
  Claude call deep — 104s at 8s/call, 208s at 16s
- the bounded retry path can spend up to `max_retries + 1` calls per candidate, so the
  wave count is a floor, not a ceiling

Runs were 140–190s before candidate-level Interrogation landed on 2026-09-20
(`4309b75`). They are 300–430s now, and eight consecutive scheduled runs failed with
`ModuleTimeoutError` on Sep 21–23. Interrogation accounts for the difference.

Everything else in the request is small by comparison: the writer loop is 32 cards in
3 waves (~32–42s at the measured 10.66s mean), and Around the League, scoring and the
reads are seconds each. Moving Interrogation out is the only change that reclaims
enough to matter.

## What gets stored

A new table, `nfl_story_interrogations`. Not a column on `nfl_content_drafts`: a draft
row is per placement, Interrogation is per candidate, and one candidate can reach
several placements. Storing it on the draft would duplicate the record and reintroduce
the divergence problem described below.

| column | type | notes |
|---|---|---|
| `id` | uuid pk | |
| `season`, `week` | int | |
| `player_id` | text | gsis id |
| `event_id` | text | together with `player_id`, the candidate key `_interrogate_unique_candidates` already uses |
| `interrogation_version` | text | `story_interrogation.INTERROGATION_VERSION`, today `v2_relationship_established` |
| `signal_verdict` | text | SURVIVES / FAILS / UNRESOLVED — what the STOP gate reads |
| `relationship_established` | bool | only meaningful on UNRESOLVED; nullable |
| `market_reaction` | text | `confirmation.market_reaction`, the field that has never been persisted anywhere |
| `supporting_signals` | text | `confirmation.supporting_signals` |
| `contradicting_signals` | text | `confirmation.contradicting_signals` |
| `challenge` | jsonb | `challenge.alternate_explanations[]` verbatim |
| `judgment` | jsonb | `what_we_know` / `what_we_dont_know` / `evidence_significance` |
| `market_data` | jsonb | the `current_attd_odds` + `odds_history` actually fed to the call, nullable |
| `interrogation_status` | text | complete / failed — Pass 4's three-state contract minus `not_selected`, which is an absence, not a row |
| `computed_at` | timestamptz | drives the staleness rule |

Unique on `(season, week, player_id, event_id, interrogation_version)`. The version in
the key means a prompt change produces new rows rather than silently overwriting
records generated under different rules, and curation can require the version it
expects.

Three flat text columns (`market_reaction`, `supporting_signals`,
`contradicting_signals`) rather than one `confirmation` jsonb: `market_reaction` is the
field this whole line of work exists to make observable, and a flat column is queryable
without json extraction. The rest stays jsonb because nothing reads inside it.

RLS on, service_role only, zero anon/authenticated policies — same posture as
`nfl_price_history` and `nfl_content_drafts`. A narrow `SECURITY DEFINER` read RPC can
be added later if a diagnostic needs one; it is not required for this design.

## Lovable changes: the migration and two routes

`nfl_story_interrogations` is a **Lovable migration**, not something this repo creates.
Schema lives in `tastypickems/supabase/migrations/`, and applying it goes through
Lovable's own apply step — a pushed migration and an applied one are not the same thing
for this project, which is what left `book_odds` looking unapplied for weeks.

Three pieces, in this order:

**1. Migration** — `<timestamp>_create_nfl_story_interrogations.sql`

- the table as specced above
- `UNIQUE (season, week, player_id, event_id, interrogation_version)`
- index on `(season, week)`, the access pattern both routes use
- `ALTER TABLE ... ENABLE ROW LEVEL SECURITY`, `GRANT ALL ... TO service_role`, and
  **zero anon/authenticated policies** — same posture as `nfl_price_history` and
  `nfl_content_drafts`

**2. Write route** — `src/routes/api/public/nfl-story-interrogations-write.ts`

- HMAC-signed POST, `NFL_PIPELINE_WEBHOOK_SECRET`, same signature verification as
  `nfl-price-history-write.ts`
- body: `{season, week, interrogations: [...]}`
- **upsert on the unique key**, not insert. A precompute re-run after a partial failure
  must correct rows rather than add competing ones — that upsert is what makes divergent
  verdicts unrepresentable rather than merely unlikely
- returns received / upserted counts, matching the existing write routes' convention

**3. Read route** — `src/routes/api/public/nfl-story-interrogations-read.ts`

- HMAC-signed POST `{season, week}`, returns the week's rows
- **cursor pagination from day one**: optional `before_timestamp` + `before_id`
  together, one alone a 400, ordered `computed_at DESC, id DESC`. The same contract
  `nfl-price-history-read.ts` now has.

That last point is not speculative caution. `nfl-price-history-read.ts` shipped without
paging, Supabase's `db-max-rows` silently capped it at 1,000, and every consumer spent
weeks reducing ~1.6% of a week to latest-per-player while believing it had the whole
thing. A week of interrogations is far smaller — 186 rows against 64,134 — so the cap
will not bite for a long time. It will bite eventually, and it will be silent when it
does. Build the contract now, while it costs nothing.

## When it runs, and what curation does without it

A new endpoint, `POST /api/precompute-interrogations {season, week}`, on the same
Vercel function. It does eligibility → shelf assignment → cap → Pass 3 selection →
Pass 4 execution, then writes the results. It writes no draft rows and generates no
cards.

Schedule: **08:15 CT**, 1h45m before curation. Not 08:00 — the NFL scenario, the Sutton
Collector and Tuesday's Split 2 all start on the hour, and the precompute shares a
Vercel function with curation, so starting it into that contention is asking for the
failure mode this design exists to remove.

The 15-minute offset is enough because of how Make actually retries. A failure is not a
doubled wait inside one execution; it is a fresh execution on a backoff ladder of 1, 2,
5, 10, 60, 180, 720 and 1440 minutes, stopping after 9 attempts. From an 08:15 start,
with each attempt capped at ~300s:

| attempt | starts | done by |
|---|---|---|
| 1 | 08:15 | 08:20 |
| 2 | 08:21 | 08:26 |
| 3 | 08:28 | 08:33 |
| 4 | 08:38 | 08:43 |
| 5 | 08:53 | 08:58 |
| 6 | 09:58 | 10:03 — **first one that collides with curation** |

Five full attempts complete by ~08:58, an hour clear of curation. The 60-minute rung is
the boundary, and it is the reason the start time matters more than the gap: any start
after ~09:00 puts the fifth attempt inside curation's window.

If you want a sixth attempt before curation, start at **07:30** instead — that moves the
60-minute rung to 09:08 and still lands clear. I would not go earlier than that without
a reason; the only cost of 08:15 is the sixth attempt, and a candidate set that has
failed five times in 45 minutes is not usually one that a sixth attempt fixes.

Curation then reads instead of generating, and every candidate resolves to one of four
states:

| state | condition | curation's behaviour |
|---|---|---|
| fresh | row exists, matching `interrogation_version`, `computed_at` within 12h | use it, exactly as if Pass 4 had just produced it |
| stale | row exists but older than 12h | treat as `not_selected` |
| version mismatch | row exists under a different `interrogation_version` | treat as `not_selected` |
| missing | no row | treat as `not_selected` |

`not_selected` is the right fallback because it already exists and already means the
correct thing: *no attempt was made*. `find_tension()` does not gate on it, the
candidate keeps its legacy gap-based classification, and the card still gets written.
Pass 3's own governing principle — "resource allocation must never masquerade as
evidence evaluation" — applies unchanged. A precompute that did not run degrades
curation to pre-Interrogation behaviour, which is a known-good state, not a broken one.

It must never fall back to `failed`. That state means scrutiny ran and produced nothing
usable, which is an epistemic claim; a missing precompute justifies no such claim.

12h, not 24h: the precompute runs 1h45m before curation, so a fresh row is under two
hours old in the normal case, and about three hours old if it only succeeded on the
fifth retry rung. A 12h window absorbs both, tolerates one skipped precompute plus a
late curation, and still refuses a row from the previous day.

### Reporting the fallback count

Curation's response reports these four counts alongside the stage timings added in
`fc0f680`, so a run that quietly degraded is visible without reading logs:

```json
"interrogation_source": {
  "candidates": 186,
  "fresh": 181,
  "fallback_missing": 3,
  "fallback_stale": 2,
  "fallback_version_mismatch": 0,
  "precompute_computed_at": "2026-09-26T13:17:04Z"
}
```

Three separate fallback counts rather than one total, because they have different
causes and different fixes. `missing` means the precompute never produced that
candidate — it did not run, or it ran against a different candidate set. `stale` means
it ran too long ago, which points at the schedule or a retry ladder that outran the 12h
window. `version_mismatch` means a prompt or schema change shipped without a precompute
re-run, which is a deploy-ordering problem, not an availability one. Collapsing them
would hide which of the three is happening.

`precompute_computed_at` is the newest `computed_at` across the rows curation actually
used, so the age of the inputs is readable directly off the run.

**As a Sutton signal.** This is a clean fit for his remit: an upstream state change that
no downstream consumer notices, on a pipeline he already watches. Curation keeps
succeeding and keeps writing cards when the precompute is missing — that is the design,
and it is exactly why the degradation is invisible without being reported.

The facts a signal would carry are the six counts above plus `precompute_computed_at`.
Deterministic code decides the tier; the interpretation is the only part that is
LLM-shaped. Two observations that should shape the rule, both from this design rather
than from a guess:

- a **rising fallback fraction across consecutive runs** is the meaningful shape, not a
  single run's absolute number. One run with fallbacks is a missed precompute; three in
  a row is a schedule or a deploy that has silently stopped working.
- `version_mismatch` above zero deserves attention at *any* count, because it cannot
  happen by chance. It means code and stored results disagree.

**Do not set the threshold from this document.** There is no run history for a field
that does not exist yet. Report the counts first, watch a couple of weeks of real runs,
then set it from the observed distribution — the same discipline applied to the
closing-line experiment's sample size.

## The duplication and divergence problem

`curate_home_shelves.py:1738` records the hazard: if one logical curation is split
across two HTTP invocations with no shared state, each call re-runs Pass 3 selection
and every Interrogation call over the same candidate population, and the same player
can get two independently generated, potentially divergent `signal_verdict`s.

Precompute removes the hazard rather than managing it. Interrogation runs **once**, in
its own request, and writes a row keyed on `(season, week, player_id, event_id,
interrogation_version)`. Any number of later readers — one curation call, two, a retry,
a manual rerun — read the same row and see the same verdict. Divergence is not
mitigated; it becomes unrepresentable, because there is only ever one record per
candidate per version.

The unique constraint is what enforces this. A precompute re-run upserts on that key,
so a retry corrects a row rather than adding a competing one.

## Should `shelves_to_process` be switched on?

Not as part of this. Possibly not at all.

It exists to split curation across two invocations because one invocation exceeded
300s. Once Interrogation is out of the request, the remaining work is roughly the
writer loop (32–42s), Around the League, scoring and the reads — comfortably inside the
budget. A split would add the cross-call coordination problem back for no gain.

Keep it dormant. Revisit only if the post-precompute curation runs show the writer loop
alone approaching the ceiling, in which case the precompute step is the precedent for
how to split safely: give the second stage its own request and its own stored input.

## What this means for the paged read

The paged read (`price-history-pagination`, `c2ae5e4`, unmerged) costs 42.7s for a full
week-3 read and ~85s at twice the volume. Against a request already over budget that is
unaffordable, which is why it is on hold.

After precompute, the two reads separate cleanly:

- **precompute** needs the full week, because Interrogation's `market_data` needs
  several days of checkpoints to show movement. It has its own 300s budget and spends
  ~43s of it on the read. That fits.
- **curation** needs only latest-per-player for the Market Value pillar. A time-bounded
  read — the 24h bound measured at 13.8s, recovering 23 of the 27 players the cap was
  hiding — is enough, and the 4 it excludes all have newest rows more than a day old.

So the option A/B/D question resolves itself: precompute takes the expensive read,
curation takes the bounded one. Neither competes with the other, and the paged read
stops being a merge risk.

## What this means for the market-reaction branch

`market-reaction-wiring` (`50a1622`, unmerged) feeds real price history into
`market_data` so `market_reaction` describes actual movement instead of reporting the
data unavailable. That work moves to the precompute step unchanged — same
`_market_data_for_candidate`, same matched-book `move_from_previous`, same freshness
gate. It is the same code in a different request.

Two things get better. The stage that needs the full week is the one that now has room
for it. And `market_reaction` gets a column, so the before/after measurement that has
been blocked on it — no schema change was in scope, and the field lives only in memory
today — becomes a query.

Sequencing: precompute first, then rebase the market-reaction commits onto it. Merging
market-reaction into today's curation request would add the full paged read to a
request that is already failing.

## Make changes, in plain steps

Steps 1–3 are the new scenario. Steps 4–5 adjust the existing one. Nothing here touches
module internals beyond what is listed.

1. **Create a scenario** named something like *NFL Interrogation Precompute*.
2. **Add one HTTP module** to it:
   - method POST
   - URL: the same base as the curation module, path `/api/precompute-interrogations`
   - headers: the same pipeline secret header the curation module already sends
   - body: `{"season": <season>, "week": <week>}`, resolved the same way the curation
     module resolves them today
   - **timeout: 300 seconds** — matching the function's own ceiling, so Make does not
     keep waiting on a request Vercel has already killed
3. **Schedule it for 08:15 CT daily** (see the schedule section for why not 08:00).
4. **On the existing curation scenario**, leave the schedule at 10:00 CT and change
   nothing else on the first run after this ships. The point of the first run is to see
   the new `timing.stages` block with `interrogation` at or near zero.
5. **After one clean run**, consider lowering the curation module's timeout to match
   whatever the new total actually is, so a genuine hang surfaces quickly instead of
   occupying the full 300s.

### What the run history already answered

Both open questions are closed, from real execution history:

- **Module timeouts.** Failed runs die at ~320s on the third HTTP module, so that
  module's effective timeout is ~300s and matches `maxDuration`. The function and the
  module give up at effectively the same moment, which is the configuration you want:
  neither sits waiting on the other. One earlier module is set to **40s**, and two
  Sep 21 failures hit it ("timed out after 40 seconds"). That module is not curation,
  but it is worth knowing a 40s ceiling exists on the same scenario.
- **Retries.** Each failure retries as its own execution on a 1 / 2 / 5 / 10 / 60 /
  180 / 720 / 1440-minute ladder, stopping after 9 attempts. Not a doubled wait inside
  one run. The eight consecutive Sep 21–23 failures are that ladder, not eight
  independent breakages, and the warning that ended it is the 9th attempt giving up.

The practical consequence is in the schedule section above: the ladder's first five
rungs all fit before curation from an 08:15 start, and the 60-minute rung is the first
that does not.

## What this does not address

The precompute request has the same 300s ceiling. At 186 calls and 13 waves it fits
today with room, but the same growth that broke curation would eventually break it. The
levers, in the order I would reach for them, are `interrogation_max_concurrency` (more
parallelism, fewer waves), then `interrogation_top_pct_per_price_band` (fewer
candidates, less coverage). Neither is needed now, and the stage timings from `fc0f680`
are what should decide when either becomes necessary.
