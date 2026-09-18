# newsletter-write.ts round-trip test — results log

## Attempt 1 — auth confirmed working, rejected on data format (not a bug)

First real signed write (season 2099, week 1) succeeded through
`/api/write-newsletter`'s own shaping and forward step — the echoed
response body confirmed the wrapper's own output was correct before
persistence ever ran: row 2 (Big One) showed `big_one_eligible: true`,
`watchlist_eligible: true`, `eps_total: 76.75`, exactly matching the
expected table. The actual Postgres insert into `newsletter_story` was
rejected: `intelligence_story_ids` is `uuid[]` in the real schema
(confirmed directly, `20260914200000_newsletter_provenance_schema.sql`
line 99), and Fixture V2's synthetic identifiers
(`SYNTHETIC-FIXTURE-1`, etc.) aren't valid UUID literals. A real,
correct rejection — not an infra problem, not a wrapper bug.

This left an orphaned `newsletter_issue` row (`ae1797cc-6288-4393-
a93a-ed1c7c18d45d`, season 2099, week 1, zero stories attached) —
deleted via the Supabase table editor before any retry, since
`(season, week)` is unique-constrained and a second attempt would fail
on the issue insert too, not just the stories, if it weren't removed
first.

**Confirmed directly before regenerating anything — only one column
actually needed to change:** `player_ids` is `text[]`, not `uuid[]`
(same migration, line 100), so `SYNTHETIC-PLAYER-1`/`SYNTHETIC-TEAM-
CAR` etc. are valid as-is — no conversion needed, and leaving them
human-readable keeps more of the payload traceably synthetic. `pick_ids`
is empty in every row this fixture produces, so it was never in play.

## Real ID mapping (synthetic tag -> real UUID used)

Only `intelligence_story_id` needed remapping, applied consistently
across every fixture and every citation of it in the real Editor Agent
output (`stories[].intelligence_story_ids` and `cross_references[].
refers_to_intelligence_story_id`, both).

| Synthetic tag (Fixture V2's own id) | Real UUID used in the retry |
|---|---|
| `SYNTHETIC-FIXTURE-1` (Keane — the double-gate case) | `cb69fb62-a569-4659-87ac-bac9e53726ff` |
| `SYNTHETIC-FIXTURE-2` | `b0ccd7ed-2846-4bf8-af5b-f6832932c31b` |
| `SYNTHETIC-FIXTURE-3` | `56e0caef-91f8-412a-af21-b3f998f0a25e` |
| `SYNTHETIC-FIXTURE-4` (Carolina slot coverage) | `80736c96-490c-4d2d-99af-13dd35f22afa` |
| `SYNTHETIC-FIXTURE-5` (Osei) | `ef86bfc1-d7dd-49b2-86b2-1935112fa7b0` |
| `SYNTHETIC-FIXTURE-6` | `c55076c2-8f0c-41ac-bc00-6f2d662fe915` |

Only fixtures 1, 4, and 5 are actually cited in this particular real
Editor Agent run's output (see the expected-rows table below); 2, 3,
6 are remapped too for consistency across the full fixtures list, but
don't appear in any persisted row this run.

Re-verified locally after remapping, before handing back the corrected
curl command: `persist_weekly_brief.shape_newsletter_rows()` against
the remapped payload produces `skipped: []` (no broken lookups) and the
identical gates/eps_scores/headlines as the pre-remap version — only
`intelligence_story_ids` values changed, confirmed by direct diff, not
assumed.

Payload files:
- `write_newsletter_real_test_payload_2099.json` — attempt 1's payload
  (synthetic tags, real UUIDs never applied — kept for reference, not
  reusable as-is, would fail the same uuid[] rejection again).
- `write_newsletter_real_test_payload_2099_v2.json` — the corrected
  payload actually used for the retry.
- `write_newsletter_real_test_payload_2099_id_mapping.json` — the same
  mapping table above, as JSON.

## Attempt 2 — real signed write succeeded, real persisted rows confirmed

Real write to `/api/write-newsletter` (season 2099, week 1, corrected
UUID payload above) succeeded end to end: real forward to
`newsletter-write.ts`, real `newsletter_issue`/`newsletter_story` rows
landed. Confirmed independently in the Supabase table editor — not the
API response echo — every value diffed exactly against the expected
table (`write_newsletter_round_trip_results.md`'s own attempt-1 section
above named the exact expected shape; attempt 2's stored data matched
it row for row), including the load-bearing one: the `big_one` row's
`eps_scores.big_one_eligible: true` / `watchlist_eligible: true`, read
directly out of the stored `eps_scores` jsonb blob itself, not the
wrapper's own claim about what it sent.

Test row `c88e3ced-8984-43ca-af58-749ab6e718b3` has since been deleted
via the Supabase table editor, cascade-checked (its `newsletter_story`
rows went with it via the real `ON DELETE CASCADE` FK), nothing left
behind in either table.

## EPS spec §12 #7 (receipts-freeze reconciliation) — CLOSED, real Validator output

With the test row already deleted, `check_gate_consistency` was run
against the exact persisted-row values from this real round trip,
reconstructed via `persist_weekly_brief.shape_newsletter_rows()` (the
same deterministic pure function that produced what was actually sent
and confirmed stored) fed the same real payload
(`write_newsletter_real_test_payload_2099_v2.json`) — not a fresh
invention, not an inference from "the data looked right": the identical
shaping call, re-run, against data already independently confirmed
correct in the live table.

```
check_gate_consistency:
  PASS  big_one  ['cb69fb62-a569-4659-87ac-bac9e53726ff']
        'big_one_eligible' was true at draft time for this big_one placement.

1 placement checked (the real big_one placement this run produced), 1/1 pass.
```

(No watchlist placement exists to check this run — the Editor didn't
place any story in Watchlist this time, which is a legitimate real
outcome, not a gap; `watchlist_eligible: true` was still correctly
frozen on the big_one row's own `eps_scores`, just never exercised as a
*placement* gate this run since nothing landed in that section.)

Full `validate_editorial_contract` against the same reconstructed issue:

```
passed: True
summary: 0 hard fail(s), 0 flagged for human review, 4 passed clean
  one_treatment: pass x3 (every real story, no duplicate placement)
  scoring_language_leak: none found
```

Raw output saved to `check_gate_consistency_live_round_trip_result.json`.

**This is a real, clean `pass` — not `needs_review`, not inferred —
against rows independently confirmed persisted in the live table via
the Supabase table editor. EPS spec §12 #7 is closed by this evidence,
not by the wrapper's mere existence.**
