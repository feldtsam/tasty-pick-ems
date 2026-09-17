# Double-gate duplicate-treatment fix — test results

What this tests: the Step 2 prompt fix (`weekly_editor_agent_prompt_v2.md`
— "Gate eligibility and one-treatment are independent constraints" +
"One Story Object, one primary treatment") against the real double-gate
stress case, Fixture 1 (SYNTHETIC-FIXTURE-1, "Keane") — the only fixture
with `big_one_eligible: true` AND `watchlist_eligible: true`
simultaneously, and the fixture that broke on both real wrapper
acceptance runs after passing clean in calibration run 3.

Per the test plan: this bug is probabilistic, not deterministic (same
fixture, same prompt intent, different real outcomes across real runs
already on record), so a single clean rerun is not evidence. Three real
runs (real `ANTHROPIC_API_KEY` calls, real model sampling each time),
grading Fixture 1's actual placement every time.

## Distribution: 2/3 clean — NOT a clean pass

| Run | Manual grade | Sections with a full `stories[]` entry | `check_one_treatment` |
|---|---|---|---|
| 4 | **FAIL** | `big_one`, `who_it_affects` | `fail` — "appears as a full stories[] entry in 2 sections" |
| 5 | pass | `big_one` only | `pass` |
| 6 | pass | `big_one` only | `pass` |

Raw output for each run: `fixture_v2_run4_raw.json`, `fixture_v2_run5_raw.json`,
`fixture_v2_run6_raw.json`. Harness: `run_double_gate_fix_test.py`.

**Run 4, in detail:** Fixture 1 got a full treatment as the Big One
(correct — `big_one_eligible: true`) *and* a second full treatment in
Who It Affects, with its own headline/body/`eps_scores`, not a
`cross_references` pointer. `what_changed` in that same run correctly
used a `cross_references` pointer back to Fixture 1 instead of a second
entry — so the fix's mechanism visibly worked in one place in the same
run and failed in another. This is not the exact big_one+watchlist
pairing from the original bug reports (this run had no Watchlist
section at all), but it is the same underlying violation the fix's
second bullet targets — one Story Object, more than one primary
treatment — just surfacing in a different section pair.

**Per the test plan's own stated bar:** this is not 3/3, so the honest
conclusion is that the prompt-level fix is a real, meaningful
improvement (2/3 clean, one section catches a case correctly right next
to a section that doesn't) but is **not sufficient on its own**.
`check_one_treatment` in the Evidence Validator is the actual backstop
this needs to rely on in production — and it worked correctly on all
3/3 runs, including the one the prompt itself missed. That's a real,
different reliability story than "the Editor never produces this" — it
means a real production run can still emit a double-treatment, and the
system's actual correctness guarantee rests on the Validator catching
it before publish, not on the prompt alone.

## A separate, real finding surfaced while running this test

2 of the 3 runs (4 and 6) had `editor_output["sections"]` come back from
the real tool-call as a **JSON-encoded string**, not a parsed array —
confirmed directly (`type(sections) == str`, and parsing the string
produces exactly the expected list-of-section-dicts shape). Run 4's
string additionally had one stray trailing character (a `,` past the
array's own closing `]`) that a plain `json.loads()` rejects outright.

This never happened in any of the original three pre-fix calibration
runs (checked directly — `fixture_v2_run{1,2,3}_raw.json` all have
`sections` as a real list) and is unrelated to the Step 2 prompt content
itself — the string, once parsed, contained coherent, well-formed story
data both times. `run_double_gate_fix_test.py`'s own `_normalize_sections()`
handles this defensively (parse the string, falling back to stripping a
trailing comma) so real run content isn't lost or a crash isn't the
result, but this is a real, apparently not-rare failure mode (2/3 in
this small sample) in `run_weekly_editor_agent.py`/`card_writer_common.
call_claude_with_tool`'s handling of a complex nested array tool-call
parameter, worth a look on its own — separately from and not blocking
this specific double-gate test.

## Bottom line

- Step 2 prompt fix: applied, real improvement, not sufficient alone (2/3).
- `check_one_treatment`: the real backstop, 3/3 correct across all runs
  including the one the prompt missed — this is what production
  correctness should be understood to rest on for this specific failure
  mode, not the prompt.
- New, separate finding: an intermittent (2/3 in this sample) tool-call
  parsing anomaly on the `sections` field, unrelated to Step 2, flagged
  for its own follow-up.
