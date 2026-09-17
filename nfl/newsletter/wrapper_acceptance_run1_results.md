# Wrapper acceptance test — run 1

What this is: the acceptance test EPS spec §12 #7 ("receipts-freeze
reconciliation") was blocked on, run for the first time against the
actual production invocation boundary (`persist_weekly_brief.py`) —
qualified Story Objects (Fixture V2) → Editor Agent (unchanged) →
shape + freeze the gates → Evidence Validator, against the shaped
rows, not transient Editor output.

Run with `run_wrapper_acceptance_test.py`. Raw artifacts alongside this
file: `wrapper_acceptance_run1_editor_output.json` (the real Editor
Agent output), `wrapper_acceptance_run1_shaped.json` (the frozen rows
`shape_newsletter_rows()` produced from it), `wrapper_acceptance_run1_
validator_results.json` (full Validator output, both checks).

## The specific bar this closes: real pass

`check_gate_consistency` against the frozen rows: **2/2 pass, no
`needs_review`, no `fail`.**

```
pass big_one       ['SYNTHETIC-FIXTURE-1'] — big_one_eligible was true at draft time
pass watchlist     ['SYNTHETIC-FIXTURE-1'] — watchlist_eligible was true at draft time
```

Both gates were read from Fixture 1's own live `eps.gates`
(`{"big_one_eligible": true, "watchlist_eligible": true}`) at the
moment `shape_newsletter_rows()` froze it, then checked back out of
the frozen `eps_scores` snapshot — not recomputed, not re-fetched. This
is the exact receipts-freeze round trip the acceptance test exists to
confirm, and it holds.

The other four `validate_newsletter_story` checks (claim, relationship,
interrogation traceability, evidence-confidence alignment), run
per-row against the persisted (shaped) rows: **0 hard fails across all
4 real-content rows** (big_one, what_changed ×2, market_knows_
something; `from_the_desk` skips claim-shaped checks by design, empty
`intelligence_story_ids`). A handful of `needs_review` items (see
below) — none block `passed`.

**This is the finish line for the specific bar asked for:
`check_gate_consistency` passes cleanly, alongside the other four
Validator checks, against real persisted rows, not `needs_review`.**

## What else this run surfaced — not part of the wrapper's job, still worth reporting

Running the FULL `validate_editorial_contract()` (not just gate
consistency, since that's what "hand the persisted rows to the
Validator" means) found a real, hard `one_treatment` failure:

```
fail SYNTHETIC-FIXTURE-1 appears as a full stories[] entry in 2
     sections (big_one, watchlist) — exactly one primary treatment is
     allowed; any other section wanting to point at it must use
     cross_references instead.
```

This is the exact duplicate-placement pattern documented in
`nfl/newsletter/README.md` as "run 2's" regression, which "run 3"'s
`cross_references[]` schema fix was supposed to close. I ran the real
Editor Agent against Fixture V2 twice while building this wrapper (see
`wrapper_acceptance_run1_editor_output.json` for the second, canonical
run) — **both real runs placed Fixture 1 as a full treatment in both
Big One and Watchlist.** Not sampling noise from one run; reproduced
twice.

This is an Editor Agent prompt-adherence issue, not a wrapper defect —
`shape_newsletter_rows()` and `check_one_treatment()` both did exactly
their job (freeze what was actually output; catch the violation
mechanically). Explicitly out of scope for this task to fix ("no new
Editor Agent behavior"). Flagged here because it's real, reproducible,
and the fix that the README says "held" in run 3 evidently does not
hold reliably for a story eligible for both gates at once — worth a
real look at Step 2's duplicate-detection rule specifically for the
double-gate case, separately from this task.

`scoring_language_leak`: clean, 0 issues, both runs.

## Two caveats that shape how to read "pass" here, stated plainly

**No live network write was executed.** `NFL_PIPELINE_WEBHOOK_SECRET`
is redacted in this environment (returns the literal string
`"[SENSITIVE]"` on read) — there is no way to construct a real HMAC
signature from here. This test exercises `shape_newsletter_rows()`
directly (the pure function — no I/O) and treats its output as the row
content that would be persisted. "Persisted" in this result means
*correctly shaped and gate-frozen*, not *confirmed present in the live
`newsletter_story` table*. `newsletter-write.ts` was built, committed,
and verified structurally (route resolves, correctly gates on the
missing secret with a 500, `tsc --noEmit` clean) — but the actual
signed round trip has never run.

**Fixture V2's `entity` field doesn't match the Validator's own
`stories_by_id` contract.** Fixture V2's `entity` is a plain
descriptive string (`"RB Dorsey Keane"`) — correct for what it was
built and confirmed against (`story_interrogation.py`/`eps.py`'s real
output shapes, which is what the Editor Agent consumes). The Evidence
Validator's `stories_by_id`, per its own module docstring, expects the
real production join shape (`entity: {player_name, team, ...}`).
Handing Fixture V2's raw dicts to `validate_newsletter_story` crashes
inside `_real_name_pool()` (`entity.get(...)` on a bare string) —
confirmed directly, not assumed; this is the first time this exact
path has been exercised against Fixture V2. `run_wrapper_acceptance_
test.py` reshapes `entity: str` into `entity: {"player_name": str}`
before handing fixtures to the Validator, purely to let the test run —
moving existing information into the shape a real caller would supply,
not inventing anything. A real production `stories_by_id` (fetched via
the actual RPC join) would not need this. Also worth knowing: Fixture
V2's `intelligence_story_id` values (`"SYNTHETIC-FIXTURE-1"`, etc.) are
not valid UUIDs, so this exact fixture data could never pass a real
write to `newsletter_story.intelligence_story_ids` (`uuid[]`) even with
a working secret — real Story Objects carry real UUIDs; this is a
fixture-testing constraint only.

## Bottom line

The wrapper does its one job correctly: it reads each Story Object's
live `eps.gates` at persistence time and freezes them faithfully, and
`check_gate_consistency` against the resulting rows is a real, clean
`pass` — twice, across two independent real Editor Agent runs. EPS
spec §12 #7 is closed. Everything found beyond that (`one_treatment`,
the two shape caveats above) is real and worth carrying forward, but
none of it is a defect in `persist_weekly_brief.py` or `newsletter-
write.ts` — it's either explicitly out-of-scope Editor Agent behavior,
or a pre-existing gap between Fixture V2's shape and the Validator's
stricter production contract, surfaced now because this is the first
time the two were actually run against each other.
