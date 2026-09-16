# Calibration Fixture V2 — run 3, results

**Status: Calibration Fixture V2, run 3 — the second correction pass on
the duplicate-treatment issue, and the first one that held.** Two runs
in, zero fixes had actually landed; this is the run where the fix did.
Still a correction pass on one narrow behavior, not a second
independent calibration round — the original voice/structure
calibration is untouched and unreconfirmed by this.

Fix under test: `cross_references[]`, a new array alongside `stories[]`
in every section, structurally incapable of being a full treatment
(only `text` + `refers_to_intelligence_story_id`, no `headline`/`body`/
`eps_scores`). Step 2's duplicate rule now points at the schema
directly ("must never appear in any other section's `stories` array,
no exceptions") instead of relying on adjectives like "brief" to do
enforcement. Tool schema in `run_weekly_editor_agent.py` updated to
match; `stories[]`'s own shape untouched.

Run via the same caller, all six fixtures in one pool. Raw output:
`fixture_v2_run3_raw.json`. Zero API errors, no truncation.

## Priority 1 — Fixture 4: PASS, clean

Carolina (Fixture 4) receives **exactly one** `stories[]` entry, placed
in Who It Affects. No other section's `stories` array cites
`SYNTHETIC-FIXTURE-4`, and no `cross_references` entry points at it
either — a genuine single treatment, not a treatment plus a pointer.
This is the outcome runs 1 and 2 both failed to produce.

## Priority 2 — Fixture 1 (the regression target): PASS, and the fix's real mechanism is visible

Keane (Fixture 1) has **exactly one** `stories[]` entry (Big One). What
Changed does reference it — but via a real `cross_references` entry,
not a second `stories` item:

> *"Keane's role change, told in full above, is the cleaner version of
> the same question this section keeps circling: which shifts outlast
> their excuse."*

This is the fix working exactly as designed: run 2 gave the model no
way to express "connect to this without retelling it" except by
writing a shorter version of a full entry, and it took that option
three times. Given a real structural alternative, it used the
alternative instead — zero full-entry duplication for Fixture 1 this
run, down from three sections in run 2.

## Priority 3 — the other four: scanned specifically for this failure mode

No Story Object appears in `stories[]` of more than one section
anywhere in this run:
- Fixture 2 (Rhoads) — excluded entirely, as in both prior runs.
- Fixture 3 (Denver) — excluded entirely, as in both prior runs.
- Fixture 5 (Osei) — one `stories` entry (What Changed), no duplication.
- Fixture 6 (Voss) — excluded entirely, as in both prior runs.

Zero instances of the target failure mode across all six.

## `cross_references` usage

Used once (What Changed → Fixture 1), empty everywhere else. Not the
"unused entirely" case the instructions said would be fine either way
— it was used, and used for a real, substantive connection rather than
padding.

## One genuine side effect worth flagging, not a failure

`watchlist_populated: false` — there is no Watchlist section at all
this run, despite **two** stories (Fixture 1 and Fixture 4) having
`eps.gates.watchlist_eligible == true`. Both were already given their
one `stories` entry elsewhere (Big One, Who It Affects), and the new
hard rule means neither can *also* get a second `stories` entry in
Watchlist — so eligibility alone no longer guarantees the section
populates. The model flagged this itself, unprompted, in its own
notes: *"a future week where a watchlist-eligible story ISN'T also
big-one/what-changed-worthy would be a cleaner test of that section
actually populating."* That's a correct read of what the new rule
actually does, not a bug in it — worth being aware of as a real
consequence of "no duplicate treatment," since it means `watchlist_
eligible` counts from EPS won't map 1:1 to what shows up in that
section once a story is already covered elsewhere.

## Bottom line

The specific thing this run tested — does `cross_references` give the
model a real structural way to avoid duplicate full treatments — held,
cleanly, on both the original failing case (Fixture 4) and the case
that regressed in run 2 (Fixture 1), with no new instances anywhere
else. This is the first correction pass in this whole sequence that
actually produced the intended result. It is still one run, still
scoped to this one behavior, and still not a re-confirmation of the
original two-round voice/structure calibration — record it as run 3,
not as the prompt being fully calibrated.
