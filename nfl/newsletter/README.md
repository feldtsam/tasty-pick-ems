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

**Do not cite either run as having "recalibrated" the prompt.** Two
runs in, zero correction passes have actually landed a fix — the
original two-round calibration had a real fix applied and confirmed
between rounds; these two rounds still have an open, unresolved
finding. The natural next step is a decision on how to fix the
duplicate-entry behavior (prompt wording again, vs. a schema change
giving the model a real lightweight cross-reference option) before
attempting a third run — not layering another unprompted iteration on
top of this one.
