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
- **`weekly_editor_agent_prompt_v2.md`** — the real, calibrated Weekly
  Editor Agent system prompt. First commit of this asset to the repo —
  see its own provenance note at the top for how it got here (recovered
  from an earlier session's transcript + character-bible/scoring-rubric
  docs, not freshly drafted). No calling code wired to it yet (see gap
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
wrapper, no Flask endpoint, no Make.com wiring — it is the calibrated
prompt text only. Building that caller is separate, larger work, not
implied by committing this file.

**The EPS-consumption edit (prompt §"Output format", the bracketed
2026-09 note) has not been made.** The prompt still describes Step 1 as
the agent *generating* `eps_scores` itself. Per Editorial Priority Score
V1's own §11, once EPS (`nfl/eps.py`) is wired into a real caller, this
prompt needs a real edit: remove EPS generation, add EPS/gates as input
read from `nfl_intelligence_stories.eps`. That scope is already fully
specified there — do not re-derive it here. The voice-calibration
question above applies again once that edit lands: re-run it after,
not before, since the edit changes prompt *structure* even though the
voice content itself shouldn't need new calibration — verify, don't
assume.
