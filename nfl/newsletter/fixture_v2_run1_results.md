# Calibration Fixture V2 — first run, results

**Status: Calibration Fixture V2, first run. Not a reproduction of the
original two-round calibration, and not itself a completed calibration
— one run, no correction round yet, graded once.** See
`nfl/newsletter/README.md` for why that distinction matters and must
not collapse in later references to this file.

Run via `run_weekly_editor_agent.py` (real `call_claude_with_tool()`
call, Claude Sonnet 5, all six fixtures from `fixture_v2.json` in one
candidate pool, matching real weekly conditions per the fixture spec's
own instruction). Raw output: `fixture_v2_run1_raw.json`. Zero API
errors, no truncation (`max_tokens=8192`, well clear of what the
response actually used).

## Grading against the fixture spec's own six checks

1. **Fixture 1 (Keane) → Big One, WEAKENED used as an argument.** PASS.
   Placed as `big_one`. The prose doesn't just state the challenge
   status — it argues from it: *"If this were purely an injury story,
   that number goes back to something closer to 24%. It didn't."* That's
   the counterfactual reasoning the fixture asked for, not a restated
   magnitude.

2. **Fixture 2 (Rhoads) → not Big One despite clearing the floor.** PASS.
   Excluded entirely — doesn't appear in any section. `notes_for_human_
   reviewer` names the reasoning explicitly: excluded on dimension
   content (low significance/novelty/tension), not because the gate
   was closed — the gate-technically-open-but-not-recommended
   distinction the fixture exists to test.

3. **Fixture 3 (Denver) → excluded from both Big One and Watchlist.**
   PASS. Excluded entirely. Notes cite the real reason: both gates
   closed, and the `NOT_TESTABLE` game-script confound meant no
   supportable story existed. Never narrated as a discovered tendency.

4. **Fixture 4 (Carolina) → Watchlist, genuinely hedged, unresolved.**
   PARTIAL — worth your read. The hedging itself is real and correctly
   unresolved in both places it appears (*"Both stories predict the
   same three data points. I don't have a way to separate them yet"*
   in What Changed; *"I'm leaving this one open rather than closed"*
   in Watchlist) — neither passage picks a side. But it appears in
   **both** What Changed and Watchlist, citing the same
   `SYNTHETIC-FIXTURE-4` in both. That's not a verbatim duplicate — the
   two passages are genuinely differently framed (analysis vs. a
   tracking flag) — but it sits close to the prompt's own hard rule
   against "near-duplicate versions of the same story in different
   sections," and the fixture's own expected outcome specifically said
   Watchlist *rather than* a confident What Changed narrative, not
   both. Flagging as a real, single-run finding, not silently passing
   it because the prose quality is otherwise good.

5. **Fixture 5 (Osei) → distinguishable from Fixture 1's clean-survival
   framing.** PASS, and a stronger one than the bar required. The prose
   doesn't just differ — it explicitly contrasts itself: *"That's not a
   clean survival story like Keane's. It's messier, and I think the
   mess is the honest part."* The model also built a dedicated Who It
   Affects entry comparing the two directly, reinforcing the
   distinction rather than leaving it implicit.

6. **Fixture 6 (Voss) → not elevated on Audience Relevance alone.**
   PASS. Excluded entirely, explicitly named in the notes alongside
   Fixture 2. Audience Relevance (88, the single highest dimension
   score across all six fixtures) did not overcome a composite score
   (24.5, the single lowest) — the 5%-weight cap held in practice, not
   just on paper.

## Calibration-scope watch-list (from the prompt's own section)

- **System-state narration:** none found. No "scored X," no dimension
  names, no "EPS" anywhere in reader-facing prose — confirmed by
  reading every story body, not just spot-checked.
- **Score-driven selection replacing editorial judgment:** did not
  happen, and there's direct evidence of the opposite. `notes_for_
  human_reviewer` documents, unprompted, that Fixture 5's `eps_total`
  (49.5) is numerically higher than Fixture 4's (48.15), but Fixture 4
  got the more prominent treatment (Watchlist) because its dimension
  *profile* — genuinely unresolved tension — fit that section better
  than Fixture 5's more-settled shape. That's Step 1's "reason from
  dimensions, not just the total" rule working, not asserted, in a
  real placement decision.
- **Reduced reasoning visibility:** not observed. The Big One, Osei,
  and Carolina passages all reveal reasoning in progress, not just
  conclusions.
- **Altered uncertainty handling:** not observed. Hedges throughout
  read as reasoning ("I'm not going to pretend I know which," "I don't
  have a way to separate them yet"), not stock disclaimer language,
  and nothing oversells confidence just because a real number sat
  behind it.
- **General voice drift:** none I could identify against the Voice
  section's own bar. One caveat, stated plainly: I'm reading this once,
  in the role of a careful reviewer, not repeating the original's own
  structured two-round process — a real second read (by Sam, or a
  second independent pass) is worth more than my read alone here.

## One real defect found, unrelated to EPS-consumption specifically

In the Osei (Fixture 5) passage: *"Palmer Osei's red-zone snaps
climbed from 12% to 34% while Denver's — sorry, while the starting
tight end sat out with a hamstring injury."* This is a genuine
generation artifact — the model appears to have started associating
Osei with Fixture 3's team-level Denver context (both were in the same
candidate pool) and self-corrected mid-sentence, leaving the aborted
clause in the final text. Not a voice-calibration issue and not
specific to the EPS-consumption patch — a real prose-quality defect
that would need catching before publication regardless of what caused
it. Reporting it because "inspect holistically" was the instruction,
not because it's the thing this fixture was built to test.

## Bottom line

5 of 6 stress cases passed cleanly; 1 (Fixture 4) passed the actual
reasoning test but produced a placement worth a second look; 1 unrelated
prose defect surfaced. The EPS-consumption behavior itself — reading
gates instead of recomputing them, reasoning from dimension profiles
rather than sorting by total, the `eps_scores` copy-not-compute
contract — held up in a single real run. That is the finding. It is
not a second calibration round, it has no correction pass behind it
the way the original two rounds did, and it should not be cited later
as having "recalibrated" the prompt.
