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
  (EPS spec §11), landed in this repo but **not yet voice-calibrated**
  (see gap below). No calling code wired to either layer yet (see gap
  below).

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

**No calling code exists yet.** This prompt has no `call_claude_with_tool()`
wrapper, no Flask endpoint, no Make.com wiring — it is prompt text only.
Building that caller is separate, larger work, not implied by committing
this file.

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

**That calibration run cannot happen yet — there is no fixture.** The
original two-round calibration ran against a synthetic 6-Story-Object
fixture whose actual content was never recovered (see above); the run
this EPS-consumption patch needs is "Fixture V2" — a new fixture,
covering EPS-consumption behavior specifically, not yet designed. When
Fixture V2 exists and gets run, that is a new calibration test, full
stop — not a reproduction of the original, and not something that
retroactively validates this patch by association with the original's
passing result.
