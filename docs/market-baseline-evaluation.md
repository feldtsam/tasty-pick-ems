# Market-as-Baseline Philosophy — Evaluation

Read-only evaluation. No code, schema, migration, Make.com, or prompt changes were made.
Date: 2026-09-25. Repo: `tasty-pick-ems` @ `sutton-v1`, plus `tastypickems` (Lovable) for migrations.

---

## 0. Blocking gap: the spec and five of its six reference documents are not on disk

The spec itself was not attached to the request, and I could not find it or most of the
documents the amendments cite:

| Document | Status |
|---|---|
| "TPE Market-as-Baseline Philosophy" (the spec) | **NOT FOUND** — not attached, not on disk |
| `market-trends-v1-spec.md` | **NOT FOUND** |
| `TPE_Editorial_Voice_Spec.md` | **NOT FOUND** |
| Intelligence Signal Patterns investigation | **NOT FOUND** |
| Spec section 10, section 19 (A–F) | unavailable (inside the missing spec) |
| `migration 20260910022730_add_book_odds_to_nfl_tables.sql` | **FOUND**, and applied — see §1.5 |

Searched: both repo trees, `~/Claude Code`, and a content grep for the spec's own vocabulary
("Market Lags", "Insufficient Market Signal", "Market Disagrees", "market-as-baseline") across
every `.md`, `.py`, `.ts`, and `.txt` outside `node_modules`/`.venv`. Zero hits.

**What this blocks.** Amendments 1, 2, 3, and 8 all turn on comparing the spec's exact wording
against another document's exact wording. I cannot do that honestly from memory of either. The
A–F structure below is my own reconstruction; I could not check it against section 19.

**What it does not block.** Amendments 4, 5, 6, and 7 are questions about this codebase and this
database. Those are answered in full below, and they are the part that decides what is possible.
Three of the premises they were built on have changed since they were written.

---

## 1. DATA READINESS AUDIT

Run first, per instruction. Every answer is a query result or a file reference.

Live queries used the `anon` publishable key from `tastypickems/.env` against
`get_nfl_price_history_window`, `get_published_nfl_shelf_picks`, and `get_nfl_week_windows` —
all `SECURITY DEFINER` RPCs explicitly `GRANT EXECUTE ... TO anon`
(`supabase/migrations/20260920150000_get_nfl_price_history_window.sql`). Read-only, no writes.

### 1.1 Is a price recorded at approval, at publication, and before lock? — **PARTIAL**

| Checkpoint | Recorded? | Where |
|---|---|---|
| Continuous polling | **YES** | `nfl_price_history`, ~15-minute cadence |
| Previous curation cycle | **YES** | `nfl_content_drafts.previous_odds` / `previous_odds_at` |
| Card approval | **PARTIAL** | `nfl_content_drafts.odds` at `generated_at`; approval time itself is `reviewed_at`, and the price is not re-snapshotted then |
| Publication | **PARTIAL** | same row; publication is a `review_status` transition, not a priced event |
| Before lock | **YES, derivable** | `nfl_price_history` keeps polling; `commence_time` / `kickoff_utc` gives the lock boundary |

`previous_odds` is **not** closing-line value. `src/lib/nfl/published-nfl-picks.ts:112` defines it
as "the previous cycle's odds for this player/market", written from the prior draft row
(`src/routes/api/public/nfl-content-drafts-write.ts:361-362`). It measures curation cycle to
curation cycle, which is the *pre*-publication leg — the opposite side of the window from CLV.

The publication-to-lock leg is not stamped anywhere, but it is **reconstructable** from
`nfl_price_history` alone, because that table polls continuously through kickoff. See §7.

### 1.2 Is sportsbook, market, event, player, and timestamp on every price row? — **PARTIAL**

`PRICE_HISTORY_COLUMNS`, `nfl/market_value.py:303-323`:

| Field | Present | Note |
|---|---|---|
| timestamp | **YES** | `poll_timestamp`, UTC ISO 8601 |
| event | **YES** | `event_id`, plus `commence_time`, `home_team`, `away_team` |
| player | **PARTIAL** | `player_id` (gsis), **nullable when unmatched** — see §1.3 |
| sportsbook | **YES** | `best_book` (single) and `book_odds` (full per-book array) |
| market | **NO** | not a column. Implicit: `ATTD_MARKET` (`player_anytime_td`) is the only market polled (`market_value.py:88`). Fine today, a landmine the first time a second market is added. |

### 1.3 `n_books` distribution — **PREMISE OVERTURNED**

The premise was "1 on every row earlier this season." That is no longer true, and has not been
for some time. Exact counts from `get_nfl_price_history_window`, season 2026:

| Week | Total rows | `player_id` NULL | n_books=1 | n_books≥3 | Modal n_books |
|---|---|---|---|---|---|
| 1 | 30,145 | 14,037 | 2,439 | 12,763 | 8 |
| 2 | 77,282 | 37,539 | 1,237 | 37,623 | 8 (23,078 rows) |
| 3 | 62,745 | 25,489 | 6,309 | 24,883 | 8 (9,267 rows) |

Multi-book consensus is healthy. A live week-3 sample shows real spread across DraftKings,
Caesars, FanDuel, BetRivers, BetOnline.ag, BetMGM, and Bovada.

**Two things to know about those totals.** First, `player_id IS NULL` and `n_books IS NULL`
co-occur *exactly* — 0 rows in either mismatch bucket, all three weeks. These are the
deliberate unmatched-capture rows: `new_price_history_rows()` (`market_value.py:326-380`) writes
matching failures with `matched=False` and every pricing column explicitly `None`, "not silently
dropped from the record." Working as designed.

Second, that is still a **40–49% unmatched rate**, and any denominator that does not filter on
`matched = true` is inflated roughly 2×. Worth watching on its own merits.

### 1.4 Is Market Trends still at `fresh_row_count: 0`? — **PREMISE OVERTURNED**

The blocker described in `nfl/market_intelligence.py:35-45` — "nfl_price_history has never had a
row written to it as of this task", "the same currently-empty table Movement is blocked on" — is
**stale**. That table now holds 170,172 rows across weeks 1–3.

Freshness right now: the most recent real priced row is `2026-09-25T12:14:35Z`, **1.0 hours old**
against a `freshness_max_age_hours` of 2.0 (`market_intelligence.py:101`). At this moment the
gate passes and Deviation stories are possible. The module docstring should no longer be read as
describing current reality.

Caveat I could not close: freshness is evaluated at generation time, not now. A 2.0-hour window
against a ~15-minute poll cadence is satisfiable, but only while the poller is actually running.
I did not verify the Make.com schedule (out of scope).

### 1.5 Is book-level odds capture live? — **PREMISE OVERTURNED**

Migration `20260910022730_add_book_odds_to_nfl_tables.sql` is **applied, not pending**.

Evidence: `book_odds` comes back populated on **67/67** week-2 and **118/118** week-3 published
picks via `get_published_nfl_shelf_picks(2026, w)`, carrying real per-book arrays. The
`get_published_nfl_shelf_picks()` no-arg form 404s (`PGRST202`) only because
`20260916011620` later re-signatured it to take `(p_season, p_week)` — not because the migration
is unapplied.

**Still open, and I could not close it read-only:** whether `nfl_price_history.book_odds` is
populated, as distinct from `nfl_content_drafts.book_odds`. The write is gated by
`NFL_PRICE_HISTORY_INCLUDE_BOOK_ODDS` (`nfl/api/index.py:128`, `:416`), which strips the key
entirely when off. The column is in `PRICE_HISTORY_COLUMNS` and the value is computed either way,
so flipping the gate is the whole change. The anon RPC does not expose `book_odds`, and
confirming the gate's live value would need a POST to `/api/poll-market-value` — a write path,
so I did not. **This one question gates Disagreement (cross-book spread) as a stored signal.**

### 1.6 The two evidence-side timing limits

- **Role Changes detects expanding roles only — CONFIRMED.** `nfl/role_changes.py:34-38` and
  `_signal_direction_for_row()` at `:342-364`: "Role Changes is explicitly, by design,
  unidirectional", with no path to a "his role is shrinking" story. Bidirectional is filed as
  Valuable-not-built.
- **RB/WR/TE trend candidates before Week 5 — NOT CONFIRMED.** I could not find a week-5 gate in
  `shelves.py` or elsewhere. What I did observe: the RB/WR/TE Trends shelves appear in the
  blueprint (`shelves.py:2-3`) but publish **zero** picks in weeks 2 and 3; division shelves
  (AFC/NFC East–West) carry those slots instead. Consistent with the limit, but I did not find
  the mechanism, so treat the Week-5 number as unverified.

### 1.7 Audit summary

| Question | Verdict |
|---|---|
| Price at approval / publication / lock | **PARTIAL** — lock leg derivable, the other two not stamped |
| Sportsbook / market / event / player / timestamp | **PARTIAL** — market absent, player nullable |
| `n_books` distribution | **EXISTS** — modal 8, premise overturned |
| Market Trends freshness | **EXISTS** — premise overturned, table has 170k rows and is fresh |
| Book-level capture | **PARTIAL** — applied and live on drafts; price-history gate unverified |

---

## A. The one-sentence principle

**EXISTS — already implemented, more carefully than the sentence states.**

The principle ("the market is the baseline, not the enemy; disagreement is a reason to
investigate, not proof of an edge") is not a new idea in this codebase. It is already load-bearing
production logic in `nfl/story_interrogation.py`, and it got there through measurement.

`story_interrogation.py:91-115` records the finding: a live 36-candidate sample across three
measurement rounds found `signal_verdict` gating out 92–100% of conclusive cases, **28 of 31 (90%)
driven by genuine market-vs-internal disagreement**. Root cause, in the module's own words: "the
prompt was treating market disagreement as uniform evidence against survival regardless of what
the detected signal was actually claiming."

The fix (`:468-530`) is more precise than the spec sentence. It distinguishes three claim shapes
and gives market evidence a different role in each:

- **Market-vs-internal** — market data is **CONSTITUTIVE**. "Do NOT treat the market disagreeing
  with the internal read as evidence against the signal for this shape of claim — that
  disagreement is the substance being tested, not a threat to it."
- **Cross-pillar internal split** — market data is **INCIDENTAL**. "A market that hasn't reacted
  is consistent with 'nobody has noticed this internal tension yet,' not evidence the tension
  isn't real."
- **Same-signal temporal drift** — market data is a genuine independent check.

**Recommendation: promote to canon, with the amendment that the canon sentence is the summary and
`story_interrogation.py:468-530` is the actual rule.** Your read is right, and the code is ahead of
it. The risk of canonizing only the sentence is that it reads as uniform guidance, which is the
exact failure mode the 36-candidate sample already diagnosed and fixed.

## B. DETECT > VALIDATE > COMPARE > INTERROGATE > EXPLAIN

**PARTIAL — the method is already the shape of the pipeline; naming it changes nothing.**

Your read (EXPERIMENTAL, belongs in Interrogation not detectors) is correct, and I would go
further: the sequence already exists as built structure, so adopting it as a *label* is
UNNECESSARY, while adopting it as a *detector* requirement would be actively harmful.

Mapped against real code:

| Stage | Where it lives |
|---|---|
| DETECT | `shelves.py` pools, `role_changes.py`, `redzone.py`, `market_intelligence.build_deviation_stories()` |
| VALIDATE | `_freshness_gate()` (`market_intelligence.py:246`), `_evidence_state_for_row()` (`:265`) |
| COMPARE | `_peer_tier_core_score()`, and `_market_data_for_candidate()` (`curate_home_shelves.py:1029`) |
| INTERROGATE | `story_interrogation.interrogate_story()`, Passes 3–4 in `curate_home_shelves.py` |
| EXPLAIN | `nfl_tension.find_tension()`, `generate_nfl_shelf_card_content.py` |

Detectors are deliberately kept market-free. `market_intelligence.py:47-60` rules out reading any
persisted `core_score`/`tpe_score` because those "may have market_value_score already folded in,
and reusing that value here would silently reintroduce the exact circularity §5 rules out." Pushing
COMPARE into detectors would undo that on purpose.

**Recommendation: EXPERIMENTAL, Interrogation only. Do not make it a detector contract.**

## C. Market-relationship states (Confirms / Lags / Disagrees / Insufficient Signal)

**CONFLICT on naming; PARTIAL on concept. DEFER, and route to the Signal Patterns investigation.**

I cannot do the full four-state mapping you asked for in amendment 1 — that needs both missing
specs. What I can establish from code:

| Spec state | Nearest existing thing | Label |
|---|---|---|
| Market Lags | Deviation (`build_deviation_stories()`), which compares TD Opportunity / Role Momentum / Situation against price and **excludes** Market Value | **PARTIAL** — same concept, and the exclusion you described is real and deliberate (`market_intelligence.py:47-60`) |
| Market Confirms | no dedicated signal; `nfl_tension.py` convergence is the closest | **PARTIAL** |
| Market Disagrees | the MARKET-VS-INTERNAL claim shape (`story_interrogation.py:477-497`) | **EXISTS** under different framing |
| Insufficient Market Signal | `evidence_state = "thin"` (`_evidence_state_for_row()`, `:265-271`) | **EXISTS** |

**On amendment 2 (the word "disagreement"): the collision is real and worse than described.**
`market_intelligence.py:26-33` reserves Disagreement for "multiple books, concurrent spread", and
it is **not built**, blocked on per-book persistence that §1.5 shows is now nearly unblocked. The
name belongs to a signal that is about to become buildable, so using it for evidence-vs-price
would collide head-on within a release or two. Keep the evidence-vs-price concept internal and
unnamed.

**On amendment 3: agreed, and the code agrees.** `market_intelligence.py:26-33` shows V1 shipped
Deviation only, with Movement and Disagreement specified but deliberately unbuilt. A fourth
vocabulary layered on top of a taxonomy that is one-third implemented would be predefining exactly
what the Signal Patterns investigation decided to derive from real Story Objects.

**Recommendation: DEFER. File Confirms/Lags/Disagrees as candidate patterns for that
investigation. Do not name them now.**

## D. Card-detail proposal

**DEFER — but the stated dependency does not exist. Amendment 4's premise is wrong.**

The premise was: "`story_interrogation.py` feeds only `eps.py` and the newsletter pipeline. Picks
shelf cards run on `content_writer/nfl_tension.py`, and the two pipelines share no code."

I built an AST import graph over every non-test `.py` under `nfl/`. All three clauses fail:

1. **`story_interrogation.py` has exactly one non-test importer, and it is the Picks path.**
   `api/curate_home_shelves.py:130` — `from story_interrogation import interrogate_story`. That
   module is NFL Shelf Curation itself; it assigns home shelves and shapes the
   `nfl_content_drafts` rows that become published picks.
2. **`eps.py` has zero non-test importers.** It does not consume `story_interrogation` by import
   at all.
3. **The two paths share code and share a module.** `card_writer_common.py` is imported by both
   `story_interrogation.py:304` and `generate_nfl_shelf_card_content.py` (the `nfl_tension`
   consumer). More directly, `curate_home_shelves.py` imports **both** — `interrogate_story` at
   `:130` and `generate_nfl_shelf_card_draft` at `:134` — and runs them in the same curation pass
   (Pass 3 selection, Pass 4 concurrent interrogation, then the writer loop).

So there is no pipeline split to unify. Interrogation already runs inside Picks curation.

**This makes the card-detail proposal cheaper than assumed, and I am still recommending DEFER**,
for a different reason: the data the card would display is not being produced yet. See §E.

Per instruction, I did not design the card.

## E. `market_reaction` — can it carry this philosophy without new fields?

**EXISTS, and YES — no new fields. It is starved, not missing.**

**What it is.** `market_reaction` is a required string on the `confirmation` object of
Interrogation's tool-call schema (`story_interrogation.py:625-627`), persisted in the output record
(`:914-917`), and swept by the confidence-escalation scan (`:709`).

**What it holds.** Per the prompt at `:396-400`: "describe what market_data (if provided) shows
about whether the relevant price/line has moved. Describe the movement itself — **do not interpret
what the movement means for betting relevance**. If market_data is not provided, say it isn't
available; do not guess."

That instruction is the spec's philosophy already written down: observe the market, refuse the
inference to edge. **The field can carry this philosophy with zero schema change.**

**Whether it is populated on real rows — this is the finding that matters most in this report.**

`_market_data_for_candidate()` (`curate_home_shelves.py:1029`) reduces real `nfl_price_history`
rows into the `{current_attd_odds, odds_history}` shape Interrogation expects, with 24-hour
checkpoint de-duplication. It is built, tested, and correct. It is fed by the
`price_history_by_player` parameter of `shape_content_draft_rows()`
(`curate_home_shelves.py:1694`), which **defaults to `None`**.

**Nothing passes it.** I checked every call site: no non-test caller anywhere supplies
`price_history_by_player`. The module says so itself at `:1466-1469`:

> "NOT YET wired to a live per-request fetch inside api/index.py — that's the one real remaining
> integration step; this function is ready for it, correctly no-ops without it."

So on every production row today, `market_data` is `None`, and `market_reaction` says the market
data is unavailable. The 36/36-UNRESOLVED result recorded at `:1455-1462` is diagnosed there as
exactly this: "a real input-starvation bug", not a gate-tuning problem.

**And the data is already in the same function.** `api/index.py` calls
`market_value_snapshot_for_curation()` during curation (`:924-925`, `:1272`, `:1543` reports
`price_history_rows`). The price rows are fetched, used to refresh the 4th pillar, and then not
handed to Interrogation.

**Recommendation: this is the highest-value, lowest-cost item in the whole evaluation.** One
parameter, already designed for, from data already in scope in the same request. Everything else
here depends on it: every market-relationship state, and the card detail in §D, describes output
that cannot exist until `market_data` is fed. I did not make the change (read-only). It deserves
its own scoped task with a before/after measurement against the same 36-candidate sample that
found the problem.

## F. Brand voice / discouraged language

**PARTIAL — cannot complete. `TPE_Editorial_Voice_Spec.md` is not on disk.**

I will not guess at the overlap between two lists when I can read neither, and I will not propose
"at most two added lines" against a rule set I have not seen. That part of amendment 8 stays open.

What exists in code, for whenever the spec resurfaces:

- `CONFIDENCE_ESCALATING_LANGUAGE` (`nfl/newsletter/evidence_validator.py:496-500`) — 14 phrases:
  *confirmed, clearly, definitively, certainly, settled, proven, undeniably, without question, no
  doubt, obviously, is a fact, already know, for certain, conclusively*.
- `check_evidence_confidence_alignment()` (`:508`) fires them **conditionally**, only when a
  referenced story is classified `limited`. Missing classification is treated as UNKNOWN and the
  check is skipped with an explicit note rather than passing silently.
- `story_interrogation.py:305` imports the same list, so the scan also runs inside Interrogation.

The conditional design is the notable part: it targets confidence that outruns evidence rather
than banning words outright. Any addition should match that shape.

---

## 7. THE MINIMUM VIABLE EXPERIMENT — closing-line value

Prioritized per amendment 7. Split between outcome quality and information quality, measured as:
**did the price move toward the pick between publication and lock?**

### 7.1 Can an existing table already capture this? — **YES, with one caveat**

No new table. No new column. The two legs are already stored:

| Leg | Source |
|---|---|
| Price at publication | `nfl_price_history` row nearest `nfl_content_drafts.generated_at` for that `player_id` |
| Last price before lock | `nfl_price_history` row with max `poll_timestamp` < `commence_time` |
| Book identity | `best_book` on both rows (`PRICE_HISTORY_COLUMNS`, `market_value.py:319`) |
| Shelf | `nfl_content_drafts.shelf` |
| Intelligence family | `intelligence_family` on the Interrogation record |

`get_nfl_price_history_for_player(p_player_id)`
(`migrations/20260920160000_...sql`) already returns a player's full unreduced poll history with
timestamps, and is granted to `anon`. The experiment is a read and a join, not a build.

**The caveat, and it is the one real gap:** `best_book` is not guaranteed stable between the two
polls. It is whichever book was best at that moment, so comparing publication `best_price` to lock
`best_price` measures "best available moved", not "this book moved", and best-of-8 is a biased
estimator that drifts with book count. Resolving §1.5 (is `nfl_price_history.book_odds`
populated?) fixes this completely: with the per-book array on both rows, same-book comparison is
a dict lookup.

### 7.2 Smallest change that would capture it cleanly

In order of increasing cost. Stop at the first that suffices.

1. **Nothing** — if `nfl_price_history.book_odds` turns out to be populated. Same-book CLV is
   already fully computable. **Check this first; it may be a zero-change experiment.**
2. **Flip `NFL_PRICE_HISTORY_INCLUDE_BOOK_ODDS` to true.** No migration — the column exists and
   the value is already computed; the gate only strips it from the forwarded payload
   (`api/index.py:416`). This is an env var, and it is yours to set.
3. **Only if both fail:** stamp `published_price` / `published_price_at` / `published_book` on
   `nfl_content_drafts` at the approval transition. Three nullable columns, same "honest null"
   convention the table already uses. I am not recommending this yet — 1 and 2 almost certainly
   make it unnecessary.

### 7.3 Caveats to state alongside any result

- **Single-book noise.** Now partly obsolete, since modal `n_books` is 8 (§1.3), but 6,309 week-3
  rows are still single-book and must be reported separately, not pooled.
- **Hold on +300 props is large.** A CLV edge smaller than the hold is not an edge. State the
  vig-adjusted number or state neither.
- **Causation.** A move triggered by an injury announcement is not evidence TPE read the situation
  correctly. Any pick whose move coincides with a `role_changes` injury event should be flagged,
  not silently counted as a win.
- **Unmatched rows.** Filter `matched = true` / `player_id IS NOT NULL` or the denominator is
  inflated ~2× (§1.3).
- **Survivorship.** Picks are drawn from the +300 floor, so the population is already
  price-selected. CLV within it does not generalize outside it.

### 7.4 Sample size

**Not proposing a number, per instruction.**

What would let us set one: the standard deviation of per-pick CLV in implied-probability points.
That is computable today from weeks 1–3 without any new collection — 170,172 price rows against
185 approved week-2/week-3 picks. Measure the spread first, then size the experiment to the effect
you would act on. Anything I named before that measurement would be arbitrary.

**Breakdown to produce:** by `shelf` (12 live values in weeks 2–3), and by Intelligence family.
Note that RB/WR/TE Trends publish zero picks in weeks 2–3 (§1.6), so those cells will be empty
until at least Week 5 — the breakdown is structurally incomplete this early regardless of sample.

---

## 8. Out of scope — one Sutton note

Per amendment 9, nothing here proposes changes to TPE Score, the +300 floor, or the Weekly Editor
Agent.

**One item fits Sutton's data-watchdog remit specifically.** `market_intelligence.py:35-45` asserts
in a module docstring that `nfl_price_history` "has never had a row written to it" and is
"currently-empty". That table now holds 170,172 rows. The claim went stale silently, and it is
load-bearing — it is the stated reason Movement and Disagreement were not built.

This is the shape of thing Sutton watches: an upstream state change that no downstream consumer
noticed. It is not a stale-*price* failure, since the prices are 1.0 hours fresh, so it fits his
remit less cleanly than a true price-staleness signal would. I mention it because a "table
asserted empty is now 170k rows" check is cheap and would have caught it. Whether that belongs in
Sutton V1 is your call, not a recommendation I am making here.

---

## 9. Verdicts against your current read

| Your read | Verdict | Why |
|---|---|---|
| One-sentence principle ready for canon | **CONFIRMED, sharpened** | Already production logic. Canonize the sentence *and* `story_interrogation.py:468-530`, or the summary reads as uniform guidance — the exact failure the 36-candidate sample fixed. |
| DETECT>VALIDATE>COMPARE>INTERROGATE>EXPLAIN is EXPERIMENTAL, Interrogation not detectors | **CONFIRMED** | The sequence already exists structurally. Detectors exclude market data deliberately to avoid circularity (`market_intelligence.py:47-60`). |
| Market-relationship states DEFERRED | **CONFIRMED** | Name collision with an unbuilt-but-nearly-buildable Disagreement signal; taxonomy belongs to the Signal Patterns investigation. |
| Card details DEFERRED | **CONFIRMED, wrong reason** | Not blocked by a pipeline split — there isn't one (§D). Blocked because `market_reaction` is starved (§E). |
| Score changes DEFERRED | **CONFIRMED** | Out of scope, and unsupported by anything in this audit. |

### If only one thing happens

Wire `price_history_by_player` into `shape_content_draft_rows()` (§E). One parameter, data already
fetched in the same request, the integration step the module already names as the last one
remaining. Everything else in this evaluation describes output that cannot exist until it is done.
