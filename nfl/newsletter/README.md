# nfl/newsletter/

Weekly Brief pipeline pieces, committed as each is ready — not a working
end-to-end agent yet. See the two spec docs this is built against (Story
Interrogation V1, Editorial Priority Score V1) for the full architecture;
this file just tracks what's actually landed here vs. what's still open.

## What's here

- **`evidence_validator.py`** — deterministic claim/relationship-
  traceability checker for narrated newsletter copy. Built, tested
  (24/24 synthetic checks), not yet merged to `main` (still on
  `story-interrogation-v1-schema-slot`).
- **`weekly_editor_agent_prompt_v2.md`** — the Weekly Editor Agent system
  prompt. Two layers, not one uniform state — see its own provenance note
  at the top: the Voice/Step 3/most of Hard Rules/most of Output Format
  is the original, recovered-and-twice-calibrated content (not freshly
  drafted). Step 1, part of Step 2, one Hard Rules line, and the
  `eps_scores` output-field instructions are a **second, later patch** —
  genuinely new prompt text implementing the EPS-consumption contract
  (EPS spec §11), landed in this repo and now run once against Fixture
  V2 (see gap below for the real, honest status of that run).
- **`fixture_v2.json`** — six synthetic stress cases built to test
  discrimination (not just recognition) once real interrogation/EPS
  content exists — see the fixture spec for the full design. Real
  `interrogation`/`eps` field shapes, confirmed matching `story_
  interrogation.py`/`eps.py`'s own actual output before this was run.
- **`run_weekly_editor_agent.py`** — minimal caller built specifically
  to run Fixture V2 against the prompt. Not production calling code —
  no Flask endpoint, no Make.com wiring, no retry/language-scan
  infrastructure the way `story_interrogation.py`/`eps.py` have.
- **`fixture_v2_run1_raw.json`** / **`fixture_v2_run1_results.md`** —
  the real first run's output and its grading against the fixture
  spec's own checklist. See gap below for what this run does and
  doesn't establish.
- **`fixture_v2_run2_raw.json`** / **`fixture_v2_run2_results.md`** —
  run 2, after a surgical clarification to Step 2's duplicate-detection
  rule (two cases: consolidate different Story Objects covering the
  same situation; give one Story Object relevant to multiple sections
  exactly one full treatment, with cross-references elsewhere allowed
  only as a sentence, never a second full entry with its own
  `eps_scores`). Graded primarily against the fixture that clarification
  targeted (Fixture 4) — see gap below for the real, honest result.
- **`fixture_v2_run3_raw.json`** / **`fixture_v2_run3_results.md`** —
  run 3, after adding a real `cross_references[]` array to the output
  schema (a pointer, structurally incapable of being a full treatment —
  no `headline`/`body`/`eps_scores`) and rewriting Step 2's rule to
  point at the schema directly. This is the fix that held — see gap
  below.
- **`persist_weekly_brief.py`** — the production invocation boundary:
  qualified Story Objects → `run_weekly_editor_agent()` (unchanged) →
  shape + freeze the gates into `newsletter_issue`/`newsletter_story`
  row shape → hand the persisted rows to the Evidence Validator.
  `shape_newsletter_rows()`/`freeze_eps_scores()` are pure functions —
  no I/O; only `persist_weekly_brief()`/`run_and_persist()` make the
  real signed write, via `newsletter-write.ts` (tastypickems `main`).
- **`run_wrapper_acceptance_test.py`** / **`wrapper_acceptance_run1_*`**
  — the first pass at EPS spec §12 #7, run against real *shaped/frozen*
  rows rather than transient Editor output — but explicitly NOT a live
  write at the time: `NFL_PIPELINE_WEBHOOK_SECRET` was inaccessible from
  that environment, so `check_gate_consistency`'s clean pass there was
  against correctly-shaped data, not data confirmed present in the live
  table (see that file's own honest caveat). Superseded by the real live
  round trip below — kept as the first, still-real confirmation that the
  shaping/freezing logic itself was correct before the live write was
  ever possible. A separate, reproducible `one_treatment` regression in
  the Editor Agent's own output was also found here, out of scope for
  this wrapper to fix.
- **`run_double_gate_fix_test.py`** / **`fixture_v2_run{4,5,6}_raw.json`**
  / **`double_gate_fix_test_results.md`** — a real, attempted fix for the
  `one_treatment` regression above (Step 2 of `weekly_editor_agent_
  prompt_v2.md`, "Gate eligibility and one-treatment are independent
  constraints"), tested against Fixture 1 (the real double-gate case)
  three real times. **Result: 2/3 clean — a real improvement, not a
  fix.** See gap below.
- **`write_newsletter_round_trip_results.md`** — the real live round
  trip: `/api/write-newsletter` (tasty-pick-ems `main`) → real signed
  write → real `newsletter_issue`/`newsletter_story` rows, confirmed
  persisted by reading them back directly in the Supabase table editor
  (not the API response echo). **EPS spec §12 #7 is genuinely closed as
  of this round trip** — see that file and the gap note below for the
  real Validator output this closure rests on, plus the one real, non-
  infra finding it surfaced (`intelligence_story_ids` is `uuid[]`;
  Fixture V2's synthetic tags aren't valid UUIDs and were remapped to
  real ones, with the mapping kept for traceability).

## Real, open gaps — flagged, not silently worked around

**The voice-calibration fixture itself was not recovered, only its test
results.** The prompt was tested twice against a synthetic 6-Story-Object
fixture before this commit — which stories, which placements, which
outcomes were judged correct is known (from the recovered session
transcript), but the fixture's actual Story Object *content* is not
sitting anywhere in this repo, and a repo-wide search turned up nothing.

This matters for anyone reading "the voice-calibration test passed twice"
later and assuming there's something to re-run: there isn't, yet. A new
6-object fixture built now — even one designed to look similar — would be
a **new test against new data**, not a repeat of the one that already
passed. Don't report a future run against a reconstructed fixture as
"re-confirming" the original calibration; it would be confirming
something related, not the same thing. If the original fixture turns up
later (a different session-search pass, a file Sam finds separately),
swap it in before trusting any "re-run" language.

**No production calling code exists yet.** `run_weekly_editor_agent.py`
is real, but it's a minimal, single-purpose script built to run Fixture
V2 — no Flask endpoint, no Make.com wiring, no retry/language-scan
infrastructure. Building the real Thursday caller is separate, larger
work, not implied by this script existing.

**The EPS-consumption edit has landed (Step 1, part of Step 2, one Hard
Rules line, the `eps_scores` output-field instructions — per Editorial
Priority Score V1's own §11) but is NOT voice-calibrated.** This is a
real, separate status from the original content around it: the original
passed two real test rounds; this patch has passed zero. Do not record
or report this patch as "recalibrated," "re-confirmed," or otherwise
validated against the original two-round result — it hasn't been run
against anything yet, only reviewed for scope (the diff against the
prior committed version touches exactly the sections named above and
nothing else — confirmed directly, not assumed).

The prompt's own new "Calibration scope for this edit" section names
what a real calibration run should watch for (system-state narration,
score-driven selection replacing editorial judgment, reasoning
visibility eroding now that there's a number to lean on, uncertainty
handling drifting, general voice drift) — use that list when the time
comes rather than re-deriving it.

**Fixture V2 now exists and has been run once.** `fixture_v2.json` (six
synthetic stress cases, each with real `interrogation`/`eps` content
matching `story_interrogation.py`/`eps.py`'s actual output shapes) and
`run_weekly_editor_agent.py` (minimal caller, built specifically for
this run — not production calling code) are both committed. Results:
`fixture_v2_run1_raw.json` (the real model output) and `fixture_v2_
run1_results.md` (graded against the fixture spec's own six checks
plus the prompt's own "Calibration scope for this edit" watch-list).

**Run 1 status:** 5 of 6 stress cases passed cleanly; Fixture 4 passed
the reasoning test but produced a placement worth a second look
(appeared in both What Changed and Watchlist, each a full entry); one
unrelated prose-generation defect surfaced (an aborted mid-sentence
entity mix-up — see `task_a793e35a`, filed separately, not blocking).

**Run 2 status — the duplicate-detection clarification did not fix
what it targeted.** After adding the prompt's two-case duplicate rule
and updating Fixture 4's expected outcome to match, Fixture 4 *still*
received two full entries (What Changed + Watchlist), each carrying
its own complete `eps_scores`/provenance block — structurally the same
shape as run 1, not the "one primary treatment + a brief cross-
reference" the clarification asked for. Worse, the same pattern spread
to Fixture 1 (Keane) this run, which now appears in three sections
(Big One, Who It Affects, Watchlist) each with a full entry — a new
occurrence, not present in run 1. The other five fixtures showed no
regression. Full detail, including a real hypothesis about why (the
output tool schema has no lightweight structural option for "a bare
cross-reference," only full `stories[]` entries with required
`eps_scores`), in `fixture_v2_run2_results.md`.

**Run 3 status — the fix held.** Run 2's own hypothesis (schema gap,
not phrasing gap) was right: with `cross_references[]` giving the model
a real structural way to point at a story without retelling it,
Fixture 4 got exactly one `stories` entry (Who It Affects, zero
duplication — the original failing case, now clean), and Fixture 1 —
which regressed to three full entries in run 2 — dropped back to
exactly one `stories` entry (Big One) plus one real `cross_references`
pointer from What Changed. Zero instances of the failure mode anywhere
else across all six fixtures. Full detail in `fixture_v2_run3_
results.md`, including one genuine, non-bug side effect worth knowing:
a story that's `watchlist_eligible` but already got its one treatment
elsewhere now correctly can't *also* land in Watchlist, so eligibility
alone no longer guarantees that section populates — the model flagged
this itself, correctly, in its own notes.

**Do not cite run 3 as having "recalibrated" the prompt.** It confirms
one specific behavior (the duplicate-treatment fix) held on one real
run. The original two-round voice/structure calibration is still
untouched and unreconfirmed by any of runs 1–3 — that status hasn't
changed, and shouldn't be conflated with "this one narrow thing now
works."

**Run 3's "the fix held" did not generalize to the double-gate case,
confirmed by directly re-running it.** Fixture 1 (Keane) is the same
fixture that regressed in run 2 (three full entries: Big One, Who It
Affects, Watchlist) and read clean in run 3 (one entry, Big One, plus a
real cross-reference). It is also the ONLY fixture with both
`big_one_eligible` and `watchlist_eligible` true at once. When this was
later exercised for real in the Weekly Brief wrapper's own acceptance
runs (`wrapper_acceptance_run1_results.md`), it broke again on both
runs. A Step 2 prompt fix was written specifically for this case
("gate eligibility and one-treatment are independent constraints" —
build the Watchlist candidate list after the Big One is chosen, from
whatever remains) and tested three real times
(`double_gate_fix_test_results.md`): **2 of 3 runs placed Fixture 1
cleanly; 1 of 3 duplicated it again (Big One + Who It Affects this
time, not Big One + Watchlist).** `check_one_treatment` correctly
caught the failure on all 3/3 runs, including the one the prompt
missed. Conclusion, stated plainly rather than rounded up: the prompt
fix is a real, meaningful improvement over the pre-fix state, but is
not sufficient on its own to guarantee correctness — production
correctness for this specific failure mode rests on the Evidence
Validator's `check_one_treatment` catching it before publish, not on
the Editor never producing it. Do not report this as "fixed."

**EPS spec §12 #7 (receipts-freeze reconciliation) — CLOSED, via a real
live round trip, not the wrapper's mere existence.** `/api/write-
newsletter` (tasty-pick-ems `main`) made a real signed write to
`newsletter-write.ts`; the resulting `newsletter_issue`/`newsletter_
story` rows were confirmed persisted by reading them back directly in
the Supabase table editor — not the write endpoint's own response echo
— with every value, including the load-bearing `eps_scores.big_one_
eligible`/`watchlist_eligible` booleans, matching exactly what the
Editor Agent produced. `check_gate_consistency` was then run against
the exact persisted-row values (reconstructed via `shape_newsletter_
rows()`, the same deterministic pure function that produced what was
actually sent — not a fresh invention) and returned a real, clean
`pass` on the one placement this run produced (`big_one`), with zero
`needs_review`. Full `validate_editorial_contract` also passed clean:
0 hard fails, `one_treatment` clean across all 3 real stories,
`scoring_language_leak` clean. See `write_newsletter_round_trip_
results.md` for the full writeup, including the one real, non-infra
finding this round trip surfaced: `newsletter_story.intelligence_
story_ids` is `uuid[]` in the live schema, and Fixture V2's synthetic
tags (`SYNTHETIC-FIXTURE-1`, etc.) aren't valid UUID literals — the
first attempt was correctly rejected by Postgres, not a wrapper bug;
the retry remapped them to real UUIDs (mapping kept in that file for
traceability) while leaving `player_ids` (genuinely `text[]`, confirmed
directly) as human-readable synthetic tags. The orphaned issue row from
the first attempt, and the successful second attempt's row, were both
cleanly deleted afterward (cascade-checked) — nothing real or synthetic
was left behind in either table.
