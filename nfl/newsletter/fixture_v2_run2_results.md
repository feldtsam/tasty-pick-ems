# Calibration Fixture V2 — run 2, results

**Status: Calibration Fixture V2, run 2 — a surgical prompt clarification
(the duplicate-detection two-case rule in Step 2) plus a matching
update to Fixture 4's expected outcome, then a full rerun. Still one
prompt clarification into a single fixture design, not a second
independent calibration round.** Graded primarily against Fixture 4,
the specific thing the clarification targeted; the other five checked
only for regression, per instruction — not re-scrutinized fresh.

Run via the same `run_weekly_editor_agent.py`, all six fixtures in one
pool. Raw output: `fixture_v2_run2_raw.json`. Zero API errors, no
truncation.

## Primary grade: Fixture 4 — the fix did not hold

**Fixture 4 (Carolina) still receives two full entries, each with its
own complete `eps_scores`/provenance block** — one in What Changed, one
in Watchlist. Both are real `stories[]` array items with `headline`,
`body`, `intelligence_story_ids: ["SYNTHETIC-FIXTURE-4"]`,
`player_ids`, and identical `eps_scores` (48/44/51/55/57/30/48.15 in
both). The Watchlist entry's body text is short (two sentences), but
brevity of prose isn't what the rule specified — the rule specified no
second entry carrying its own `eps_scores`/provenance block, full
stop. This is structurally the same shape as run 1, not the "one
primary treatment + a cross-reference sentence inside another story's
prose" the clarification asked for. **This is a genuine miss, not a
partial credit — reporting it as a clean fail against the specific
thing this run tested, not softening it because the prose quality is
otherwise fine.**

Worth noting for whoever iterates on this next: the tool schema itself
may be part of why this keeps happening. Every section's `stories[]`
item is required to carry its own `eps_scores` — there's no schema slot
for "a bare cross-reference sentence with no full story object." The
model has to actively choose *not* to create a second `stories[]` entry
at all (folding the cross-reference into another section's existing
story body) rather than being offered a lighter-weight structural
option for it. That's a real hypothesis, not a confirmed cause — worth
checking before assuming this is purely a prompt-wording problem that
a stronger sentence would fix.

## A new instance of the same underlying issue, on a different fixture

**Fixture 1 (Keane) now appears in three sections — Big One, Who It
Affects, and Watchlist — each with its own full `eps_scores`/provenance
block**, all three carrying the identical dimension scores. This is a
new occurrence, not present in run 1, and it's the same structural
pattern as Fixture 4's failure: multiple full entries for one Story
Object rather than one primary treatment plus real cross-references.

The model's own `notes_for_human_reviewer` self-flagged this
unprompted: *"three appearances of one Story Object is on the high end
and worth a second look for whether Who It Affects added enough new
information to justify inclusion rather than a cross-reference sentence
in the Big One instead."* That's an honest, correct self-assessment —
but the behavior itself is exactly what the new rule was meant to
prevent, now showing up on the fixture that passed cleanly last run.
Net effect of this clarification, in this one run: the target case
didn't improve, and the same failure mode spread to a second fixture.

## Regression check on the other five (not re-scrutinized beyond this)

- **Fixture 1 (Keane) — Big One placement and WEAKENED-as-argument
  reasoning both hold.** *"An injury explains a temporary blip. It
  doesn't explain a back holding the role after the guy he was
  supposedly filling in for is standing on the practice field
  again."* No regression on the original test. (Its new triple-
  appearance is a separate, new finding — see above — not a regression
  of what Fixture 1 itself was built to check.)
- **Fixture 2 (Rhoads) — still excluded entirely**, with the same
  reasoning named in the notes. No regression.
- **Fixture 3 (Denver) — still excluded entirely**, both gates
  correctly failed. No regression.
- **Fixture 5 (Osei) — still distinguishable from Keane's clean
  framing** (*"Part of this reverted. Part of it didn't"* vs. Keane's
  confident survival narrative), though this run doesn't explicitly
  name-check Keane the way run 1 did. The original test was
  distinguishability, not an explicit cross-reference — still holds.
  No regression.
- **Fixture 6 (Voss) — still excluded entirely**, named alongside
  Rhoads in the notes for the same "no real story" reasoning. No
  regression.

## Osei mid-sentence artifact — logged separately, as instructed

The "while Denver's — sorry, while the starting tight end..." aborted
conflation from run 1 **did not recur** in this run's Osei passage. One
non-recurrence isn't proof it's fixed — LLM output is stochastic, and
this could easily reappear on a future run. Filed as a background task
(`task_a793e35a`) to investigate properly (recurrence rate across
multiple runs, whether candidate-pool composition/size correlates with
it) — unrelated to the EPS-consumption work, not blocking, not
something this results file resolves.

## Bottom line

The specific thing this run tested — did the duplicate-detection
clarification give Fixture 4 exactly one full treatment — **did not
pass**. The clarification also surfaced the same pattern on a fixture
that previously passed. This is real information the prompt clarification
didn't yet produce the intended effect and needs another look, most
likely at the tool schema's own structural options for a lightweight
cross-reference, not just the prompt wording. Recording this as run 2,
not as a second independent calibration round, and not layering a third
unprompted fix-and-rerun cycle on top of it — flagging back for a
decision on how to proceed.
