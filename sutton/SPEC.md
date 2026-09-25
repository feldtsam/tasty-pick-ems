# Sutton V1 — Implementation Spec

Status: v1.4, Sep 24 2026. This version amends v1.0 after Claude Code's first look at the real Make data. The changes are listed in the "v1.x changes" sections at the bottom. Charter: *SUTTON — TPE UPSTREAM AGENT, Master Blueprint*. This spec is the V1 slice of that charter and nothing more.

Save location: `sutton/SPEC.md` on branch `sutton-v1`. This keeps Sutton self-contained and avoids creating a new top-level `docs/` tree.

## The V1 hypothesis

Can Sutton reliably notice a meaningful TPE pipeline problem before Sam does, without creating more work than he saves?

## Approved decisions

1. **Make collects its own execution history and pushes it to Sutton.** The Sutton Vercel project never holds a Make API token.
2. **GREEN days send a one-line daily email** during the trust-building phase. The daily email also works as a dead-man's switch: if no email arrives, Sutton is down.
3. **The watched set is four scenarios.**
4. **Sutton's own silence is classified as `HARNESS_HEALTH`**, not as a TPE learning signal.
5. **Duration rule revised after the Sep 24 check.** Make's execution API returns total run duration only, with no per-module timing. The rule compares each scenario against its own recent history (see L1).

## Watched scenarios (Make team 2604332)

| Scenario | ID | Cadence |
|---|---|---|
| NFL Picks Daily Generation | 6186710 | daily |
| NFL Grade Bookmarks Live | 6241867 | daily |
| NFL ATTD Price History Poller | 6152892 | hourly |
| NFL Split 2 (weekly reconcile) | 6079077 | weekly, Tuesday |

Every scenario gets its own baseline. There are no global thresholds.

L1 (duration drift) applies only to Picks and Grade Bookmarks Live. Those are the two scenarios where run time measures health against a hard timeout:
- The poller's run time tracks its workload (1 operation versus 542), not its health.
- NFL Split 2 runs weekly and will never build a baseline.

Both remain fully covered by the inspection rules and L2.

## Flow

```
Make "Sutton Collector" scenario (every 2 hours)
  1. For each watched scenario: Make app "List scenarios" (isActive/isPaused)
     + "List scenario logs" for records since (last Sutton collector run − 3h overlap)
     This is incremental. Make's history API returns at most 50 rows per request,
     so large pulls are not reliable.
  2. Normalize → POST /api/sutton-run on the `sutton` Vercel project
         │
         ▼
  sutton project
    store.py     upsert new observations (idempotent)
    state        GET sutton-state-read → last 8 days of observations for the watched
                 scenarios + last_collection_at + escalations emailed in last 24h.
                 Baselines are computed from Sutton's own stored copy, not from the Make payload.
    checks.py    deterministic rules → signals with class + tier     (no network, no LLM)
    status       tier → GREEN / YELLOW / RED                          (code)
    packet.py    evidence packet from signals                         (code)
    interpret.py ONE LLM call, only if status ≠ GREEN                 (LLM)
    validate     output contract check; fallback text on any failure  (code)
    store.py     write observations + incidents via Lovable HMAC routes
    respond      { status, deliver_escalation, deliver_radar, subject, body, shadow }
         │
         ▼
  Make routes on the response → email tastypickems@gmail.com
```

- Production never calls Sutton. Sutton never calls production.
- Sutton's only outbound calls are to the Anthropic API and the three `sutton-*` Lovable routes.

## Normalized input (Make → Sutton)

```json
{
  "collected_at": "ISO-8601",
  "mode": "collect | daily_radar",
  "scenarios": [
    {
      "scenario_id": 6186710,
      "name": "NFL Picks Daily Generation",
      "is_active": true,
      "is_paused": false,
      "executions": [
        { "execution_id": "hex32", "started_at": "ISO", "ended_at": "ISO|null",
          "duration_ms": 320859, "status": 1, "run_type": "auto|manual",
          "error_name": null, "error_message": null, "cause_module": null }
      ],
      "events": [
        { "event_id": "string", "at": "ISO",
          "event_type": "warning|modify|schedule|start|stop|backoff",
          "detail": "All 9 attempts to reconnect the process failed" }
      ]
    }
  ]
}
```

State (last collection time, recent escalations, and 8 days of stored observations) is fetched by Sutton from `sutton-state-read`. It is not part of the Make payload.

### Raw input (v1.5)

`/api/sutton-run` also accepts the Make API's own responses, so the collector
scenario can forward them without an HTTP module doing field-by-field mapping:

```json
{
  "collected_at": "ISO-8601",
  "mode": "collect | daily_radar",
  "raw_scenarios": "GET /api/v2/scenarios response",
  "scenarios": [
    { "scenario_id": 6186710, "raw_logs": "GET /api/v2/scenarios/<id>/logs response" }
  ]
}
```

Both fields take either a bare array or Make's wrapper object (`{"scenarios": […]}`,
`{"scenarioLogs": […]}`). The shape is detected structurally: `raw_scenarios` at the
top level, or `raw_logs` on any scenario entry. The normalized shape above still
works unchanged.

Three rules govern the raw path:

- **Redaction runs first and sweeps everything.** `redact_all()` walks the whole
  tree, because the secret-bearing field is `error.message`, not a top-level
  `error_message`. Values under ID-named keys are spared; a Make execution id is a
  32-hex run and redacting it would destroy the idempotency key.
- **`raw_scenarios` is read for three fields only**: `name`, `isActive`,
  `isPaused`. Nothing else in that response is evidence, and it carries a
  blueprint per scenario.
- **A watched id absent from `raw_scenarios` is inactive**, which routes it to I1.
  A scenario Make no longer lists is a scenario that is not running.

A scenario whose `raw_logs` is malformed or empty is skipped, recorded as a
HARNESS_HEALTH LOG, and the other scenarios still run. One bad Make response must
not cost a whole collection.

## Data realities (confirmed against real Make history, Sep 24)

1. **The history API returns at most 50 rows per request, newest first, with no truncation signal.**
   - The Step A fixture builder must page by walking the `to` bound backwards.
   - It must write each page to disk as it goes.
   - It must assert completeness: no gap between one page's oldest row and the next page's newest.
   - The live collector is incremental for the same reason.
2. **Executions and timeline events come back in one array with different shapes.**
   - Executions have `eventType: "EXECUTION_END"`, and their `type` means run type (`auto`/`manual`).
   - Timeline events have no `eventType`, and their `type` means the event kind.
   - Normalize these into the separate `executions` and `events` lists. Never read `type` without checking which shape the record is.
3. **Timeline events have no `scenarioId`.** Attach it from the query context.
4. **`endedAt` is sometimes missing.** Use `timestamp` as `started_at` and `duration` as the duration. Derive `ended_at` when it's absent. No rule depends on `ended_at`.
5. **The same failure appears with different error names.**
   - `ModuleTimeoutError` with two different messages
   - `DataError` "timeout of 40000ms exceeded"

   V1 rules never group by error name. Failure classification is deferred. Error text is stored only after redaction (item 6).
6. **Error messages can contain secrets.** A real Sep 11 record on 6241867 carries the full `X-Pipeline-Secret` value inside `error.message`. `error_message` and `detail` are untrusted free text. See "Redaction" below.
7. **Status 2 exists.** It means Make's "finished with warnings," and it can come with a populated `error` object. See the status definitions below.
8. **Scenario names change over time.** 6241867 was named "Integration HTTP" before Sep 11 18:37. Key everything on `scenario_id`. Display names come from the current `scenarios_list`, never from historical execution rows.

## Redaction (mandatory, before anything is stored, logged, or sent)

`sutton/redact.py` runs on every free-text field (`error_message`, `detail`, and anything placed in `extra`). It runs:
- in the fixture builder, before any fixture is written to disk
- as the first step of `/api/sutton-run`, before the payload is stored, logged, or used

The endpoint never prints or logs the raw request body.

**Patterns replaced with `[REDACTED:<first 8 hex of sha256>]`:**
- any run of 32 or more hex characters, except when the whole field is exactly a Make execution ID in an ID field (ID fields are never free text)
- `Bearer <token>`
- `apikey=…`, `api_key=…`, `token=…`, `secret=…`, `key=…` (query-string style)
- any header-style `X-*-Secret`, `X-*-Key`, or `Authorization` followed by a value
- any quoted value of 20 or more characters that follows `header` and `value` wording, like the Make message shape `Invalid value for header 'X-…': '…'`

The fingerprint lets identical failures group later without carrying the value.

**Other rules:**
- The evidence packet never includes `error_message` or `detail` text, redacted or not. Facts are structured fields only: counts, durations, statuses, `error_name`, `cause_module`, and timestamps. Error text never goes to the Anthropic API.
- If redaction raises an exception, the field is replaced with `[REDACTED:unparsed]` and the run continues.

## Deterministic rules

**Execution status.** Make returns 1 = success, 2 = finished with warnings, 3 = failed.
- **Success** means status 1 only. It is the only status that counts toward the L1 baseline and the only one that "recovers" a failure streak.
- **Failure** means status 3, or status 2 with a populated `error` object. Both count for I2 and I2b.
- Status 2 with no `error` object is neither. It is ignored by every rule.

**Definitions.**
- A **config-change event** is a `modify` or `schedule` event. `start` and `stop` are not config changes. They are restarts and pauses, and I1 covers pause state.
- A **testing window** is the 2 hours after a config-change event. **All** executions inside it are ignored by every rule, both `auto` and `manual`.
- The **post-edit scope** of a scenario is its executions after the most recent config-change event, excluding the testing window. I1, I2, I2b and L1 evaluate only this scope. An edit starts a fresh record: failures from before the edit (for example, setup failures) don't carry forward, and failures after the testing window still count.
- The **post-edit baseline** is the successful `auto` runs in post-edit scope.

| ID | Class | Condition | Tier |
|---|---|---|---|
| I1 `SCENARIO_DISABLED` | INSPECTION | Watched scenario `is_active=false` or `is_paused=true`, **or** a "reconnect … failed" warning event in post-edit scope with no successful execution after it | ESCALATE |
| I2 `CONSECUTIVE_FAILURES` | INSPECTION | 2 or more consecutive failed executions (status 3) in post-edit scope, with **no successful execution since** (the failure is unrecovered) | ESCALATE |
| I2b `UNRECOVERED_FAILURE` | INSPECTION | The most recent post-edit execution failed, and it is a single failure | RADAR |
| — | INSPECTION | Any failure or failure streak that was followed by a success | LOG ("recovered") |
| L1 `DURATION_DRIFT` | LEARNING | Applies only to scenarios listed in `CONFIG.l1_scenarios`. An **evaluable run** is a successful `auto` run in post-edit scope that has 5 prior post-edit successful `auto` runs behind it. An evaluable run is **flagged** when both hold: its duration is more than 1.5× the median of those 5 prior runs, **and** it exceeds that median by at least 30s. RADAR fires when 2 of the last 5 evaluable runs are flagged. Until 5 evaluable runs exist (about 10 successful post-edit automatic runs), the tier is LOG with reason `INSUFFICIENT_BASELINE`. | RADAR, never ESCALATE |
| L2 `HUMAN_COMPENSATION` | LEARNING | Counts **rescued failures**, not manual runs. A rescue run is a `manual` execution, outside any testing window, whose most recent prior `auto` execution of the same scenario failed. A **rescued failure** is a distinct failed `auto` execution that received at least one rescue run. Several rescue runs aimed at the same failure count once. RADAR fires when there are 3 or more rescued failures in the trailing 7 days, timed by each failure's first rescue, and it stays until they age out. This rule is **not** reset by edits. | RADAR |
| H1 `COLLECTION_GAP` | HARNESS_HEALTH | `collected_at − last_collection_at` is more than 26h | RADAR |
| H2 `INTERPRETATION_FAILED` | HARNESS_HEALTH | LLM error, timeout, invalid JSON, or validator rejection | LOG |

**Status color.**
- Any ESCALATE → RED
- else any RADAR → YELLOW
- else GREEN

HARNESS_HEALTH signals are reported under their own heading and never mixed into the TPE status line.

**Escalation delivery.** The ESCALATE email fires only when no escalation for the same `signal_id + scenario_id` is listed in `state.escalations_last_24h`.

**Why the 30s floor:** 30s is 10% of Vercel's 300s function budget. A slower run below that size isn't operationally meaningful, whatever the ratio. Without the floor, a 0.6s run against a 0.2s median would count as drift.

**Thresholds live in one `CONFIG` dict:**
- `l1_scenarios`: 6186710, 6241867
- 1.5× multiplier
- 30s absolute floor
- 5-run baseline
- 5 evaluable runs required
- 2-of-5 trigger
- 3 rescued failures in 7 days
- 2h testing window
- 26h gap

Thresholds change only in a deliberate batch review (charter §16), never in response to a single alert.

## Sutton contract

**Input:** the evidence packet.

```json
{
  "date": "2026-09-16",
  "status": "YELLOW",
  "watched_scenarios": 4,
  "signals": [
    { "signal_id": "L1_DURATION_DRIFT", "class": "LEARNING", "tier": "RADAR",
      "scenario": "NFL Picks Daily Generation",
      "facts": { "latest_duration_s": 320, "baseline_median_s": 173,
                 "flagged_runs_of_last_5": 2, "threshold_multiplier": 1.5 },
      "recent_edits": ["2026-09-07T22:32:05Z"] }
  ]
}
```

**L1 facts describe the flagged runs, not the latest run.** For L1, `facts` must contain:
- `flagged_runs`: a list of `{at, duration_s, baseline_median_s, excess_s}` for each flagged run among the last 5 evaluable runs
- `flagged_runs_of_last_5`
- `threshold_multiplier`
- `absolute_floor_s`

The latest evaluable run appears only if it is flagged, or else as `latest_run: {at, duration_s, flagged: false}`. That way nothing in the packet lets an unflagged run read as the evidence.

**Output:** JSON only.

```json
{ "headline": "one sentence",
  "interpretation": "at most two sentences; separates observed facts from hypothesis",
  "recommended_investigation": "one sentence, or 'Continue observing.'",
  "confidence": "low | medium | high" }
```

**The validator rejects the output, falls back to deterministic text, and logs H2 if any of these hold:**
- The output is not valid JSON, or has missing or extra keys.
- There are more than 80 words across all fields.
- Any number in the output does not appear in the packet's `facts`. This is the anti-fabrication check.
- There is any mention of time or effort cost (hours, minutes spent) unless it is in `facts`.

**Additional contract rules:**
- Tier, status, and color are never in the output schema. The email header is rendered by code from the packet.
- There is no LLM call on GREEN.
- The model is set by the `SUTTON_MODEL` env var. The volume is at most a few calls per day.

**System prompt essentials.** Sutton is TPE's upstream agent. He interprets only the supplied evidence and never invents facts, causes, or costs. He treats correlation as hypothesis, and says "Continue observing." when uncertain. His tone is calm, plain, and brief.

## Email formats

```
SUTTON — GREEN
No meaningful emerging risks detected across 4 watched scenarios.
```

```
SUTTON — YELLOW
NFL Picks Daily Generation: <headline>
<interpretation>
Watching: <signal>. <recommended_investigation>
```

```
SUTTON — RED (escalation)
<scenario>: <headline>
<interpretation>
Recommendation: <recommended_investigation>
```

- Daily radar goes out at 7:00am CT. It reports the **worst status, and every signal that fired, across all collection ticks since the previous daily radar**, not just the state at 7:00am. Signals that fired and cleared are listed as "cleared."
- Known limitation: a rolling median absorbs a sustained slowdown after about 5 runs, so L1 detects change, not a permanently high level. That's acceptable for V1.
- ESCALATE emails go out on the collection run that detects them.
- A HARNESS_HEALTH line is appended only when present.
- If the LLM fails, the email carries the deterministic facts plus "Interpretation unavailable."

## Storage (existing Supabase project; pipeline-internal, service_role only, RLS on, zero anon/authenticated policies)

**`sutton_observations`** is the durable mirror, because Make keeps history for only about 30–40 days.
- Columns: `id` uuid, `scenario_id` int, `kind` (execution|event), `source_id` text (execution_id or event_id), `occurred_at`, `ended_at`, `duration_ms`, `status`, `run_type`, `event_type`, `error_name`, `error_message`, `cause_module`, `detail`, `collected_at`, `extra` jsonb.
- Unique on `(scenario_id, kind, source_id)`. Re-sends are ignored.

**`sutton_incidents`** is append-only for Sutton.
- Columns: `id`, `detected_at`, `record_type` (signal|radar), `signal_id`, `signal_class` (INSPECTION|LEARNING|HARNESS_HEALTH), `scenario_id`, `tier`, `status_color`, `evidence` jsonb, `interpretation` jsonb null, `model_name`, `llm_ok` bool, `shadow` bool, `emailed` bool.
- Sam fills in the outcome columns by hand later: `outcome_note`, `was_real` (yes|no|unknown). Sutton's route cannot update any row.

**Routes (Lovable, HMAC with a new `SUTTON_WRITE_SECRET`):**
- `sutton-observations-write`: upsert, ignoring duplicates
- `sutton-incidents-write`: insert only
**Signing contract (as built):**
- Every route uses HMAC-SHA256 with `SUTTON_WRITE_SECRET`, hex, in the `X-Signature` header. A `sha256=` prefix is optional.
- **POST routes** sign the exact raw request body bytes. Both POST routes accept a JSON array only.
- **`sutton-state-read`** requires a `ts` query value in unix seconds and signs the string `ts=<n>`. It returns 401 when the signature fails, when `ts` is missing, or when `ts` is more than 300s from server time.
- The migration is `drizzle/migrations/0011_create_sutton_tables.sql`. That's Lovable Cloud's current migration folder.

- `sutton-state-read`: signed GET, returns:
  - `last_collection_at`
  - escalations emailed in the last 24h
  - all `sutton_observations` rows for the watched scenarios with `occurred_at` in the last 8 days

  That's roughly 200 rows per day at most, mostly from the hourly poller.

## Permission boundary

**The `sutton` project env holds exactly:**
- `ANTHROPIC_API_KEY`
- `SUTTON_MODEL`
- `SUTTON_INCOMING_SECRET` (Make → Sutton)
- `SUTTON_WRITE_SECRET` (Sutton → Lovable routes)
- `SUTTON_SHADOW`

**It never holds:**
- a Supabase key
- a Make token
- any pipeline or Odds secret

**Other boundaries:**
- The Lovable routes are hardcoded to the `sutton_*` tables.
- The Make app connection that collects history lives only in Make and uses the narrowest read scope Make offers.
- The collector scenario uses list modules only.
- Sam generates all secrets himself. Claude Code never writes credential values.

## Shadow mode (first ~5 days)

With `SUTTON_SHADOW=true`:
- There are no ESCALATE emails.
- One daily email goes out with the subject prefix `[SHADOW]`.
- All incidents are stored with `shadow=true`.

Sam flips the flag after reviewing about 5 days of output.

## Acceptance tests

1. **Historical replay (the main test).** Evaluate the rules at each 2-hour tick from Sep 8 to Sep 24 using real Make execution history.
   - Must-haves (v1.3):
     - Sep 8–11: GREEN on every tick
     - Grade Bookmarks Live: ESCALATE on Sep 12
     - Picks: L1 RADAR fires before the first Picks ESCALATE on Sep 21. (v1.0 said "by Sep 16." That date was a guess, not a requirement. The requirement is learning before inspection.)
     - Picks: ESCALATE on Sep 21
     - Grade Bookmarks: no ESCALATE after its successful restart on Sep 15 at 23:25, and no L1 at all. The L2 RADAR from the three outage restarts is expected, and it clears when they age out.
     - Split 2 and the poller are not continuously YELLOW from Sep 16 onward.
   - Report the full day-by-day table.
   - If a must-have fails, report it. Do not tune thresholds to fit.
2. **Green stays quiet.** Seven normal days produce GREEN and zero LLM calls.
3. **Insufficient baseline stays silent.** Fewer than 5 post-edit runs gives L1 = LOG.
4. **Learning before inspection.** On the replay date where Picks L1 first fires, there is no Picks ESCALATE.
5. **Sutton cannot choose tier or status.** LLM output containing `tier`, `status`, or `color` keys is rejected, and the header is unchanged.
6. **Sutton cannot manufacture evidence.** Output containing a number not in `facts` is rejected and fallback text is used.
7. **LLM failure is contained.** A timeout, exception, or invalid JSON still returns 200 with a deterministic radar, and H2 is logged.
8. **Sutton cannot modify production.**
   - The `sutton` env contains only the listed vars.
   - A grep of `sutton/` finds no outbound hosts other than the Anthropic API and the three `sutton-*` routes.
9. **Output stays concise.** Anything over 80 words is rejected.
10. **Idempotency.** Sending the same payload twice creates no duplicate observations and no second ESCALATE email.
11. **Harness health is separate.** A 30h gap produces H1 RADAR under HARNESS_HEALTH, and the TPE status is unaffected.
12. **Secrets never leave redaction.**
    - The real Sep 11 6241867 record, and synthetic cases for each pattern, come out of `redact.py` with no 32+ hex run or secret-shaped value left.
    - A grep of the committed fixture for `[0-9a-f]{32,}` outside ID fields finds nothing.
    - The evidence packet built from a failure contains no `error_message` or `detail` key.
13. **Status 2 handling.** A status-2 run with an error counts toward I2. A status-2 run without an error changes nothing. A status-2 run never enters the L1 baseline.

## Explicitly deferred

- Product and Business watchdogs, and cross-domain reasoning
- Improvement Proposals
- Statistical baselines (MAD, adaptive thresholds), incident similarity, and organizational memory retrieval
- Master Bible Reality-to-Canon gap detection (`STATUS_REVIEW_RECOMMENDED`)
- Missed-schedule detection (a scheduled run that never fired)
- Deploy awareness (code commits as regime changes)
- Supabase-derived signals:
  - `nfl_price_history` poll cadence and coverage
  - Postgres `RAISE WARNING`s from review-status triggers
- Endpoint-side run logging inside production
- Watching the other five scenarios
- Moving secrets out of Make scenario definitions into Make connections
- Evaluation metrics (false-positive rate, lead time) until 10–15 meaningful alerts exist

## Success checkpoints

- **~30 days:** Sam trusts GREEN without re-checking.
- **~60 days:** Sutton catches something first.
- **~90 days:** a Sutton-originated investigation removes recurring friction.
- If Sutton costs more attention than he saves, simplify or pause him.

## v1.2 changes (after Claude Code found a secret in execution data)

- **Redaction section added.** It is mandatory at the fixture builder and at the endpoint's first step. Error text never enters the evidence packet or the LLM call. This supersedes the v1.1 "store raw values unchanged" wording.
- **Status definitions added.** Status 1 is success. Status 3, or status 2 with an error, is failure. Status 2 without an error is ignored.
- **Scenario identity is `scenario_id`.** Names come from the current `scenarios_list` only.
- **Acceptance tests 12 and 13 added.**

## v1.3 changes (after the first historical replay)

The replay found three rule-semantics problems, and all were fixed as one batch before launch:
- **L1 had no size floor.** Hourly poller jitter (0.6s against a 0.2s median) produced YELLOW on Sep 8–11. L1 now needs a 30s absolute increase, and it only watches Picks and Grade Bookmarks Live.
- **L1 fired on 2 evaluable runs.** It now needs 5 evaluable runs before the 2-of-5 test can fire.
- **L2 counted development runs.** Manual runs during building kept Split 2 YELLOW all week and made all four scenarios YELLOW on Sep 19–20. L2 now counts only rescue runs: a manual run after a failed automatic run.
- **The daily radar sampled one tick,** and L1 flickered within a day. The radar now reports everything since the last radar.
- **Must-haves 3 and 5 restated** to match the charter's intent; must-have 6 added. Must-have 3 is looser than before, and that's deliberate.

## v1.4 changes (after the second replay)

- **L2 now counts distinct rescued failures, not rescue runs.** On the weekly Split 2, one Sep 15 failure produced 8 rescue runs over four days of fixing, which held L2 YELLOW for the whole replay. Repetition means automation needing a human again and again, not one fix taking several tries.
- **L1 packet facts are built from the flagged runs.** The latest run is labeled unflagged when it isn't flagged, so the LLM can't present it as the evidence.

## v1.5 changes (raw Make input)

- **The endpoint accepts raw Make API responses** as well as the normalized shape. The collector scenario forwards what Make returns; Sutton does the mapping. See "Raw input (v1.5)".
- **`normalize.py` is the single source of truth for that mapping**, imported by both `build_fixture.py` and `api/index.py`. The two used to hold separate copies, which meant the rules could be validated against one normalization and run against another.
- **`redact_all()` sweeps the whole payload tree**, since raw Make nests the secret at `error.message`. ID fields are exempt.
