#!/usr/bin/env bash
#
# Sutton V1 smoke test. Run by hand, once, after the Vercel env vars are set.
#
#   export SUTTON_URL=https://sutton-three.vercel.app
#   export SUTTON_INCOMING_SECRET=...          # the value you set in Vercel
#   bash sutton/smoke.sh
#
# What it does
#   1. GET  /api/sutton-run                 unauthenticated -> expect 401
#   2. GET  /api/sutton-run?probe=state     authenticated   -> signed state read
#   3. POST /api/sutton-run                 one synthetic payload, mode=collect
#   4. prints the exact rows to delete afterwards
#
# This script never needs SUTTON_WRITE_SECRET. The signed state read happens
# inside the deployed function using the project's own env var, so the write
# secret stays in Vercel and never reaches a shell.
#
# The payload is deliberately fake: scenario_id 99999 is not one of the four
# watched scenarios, so it cannot disturb a real baseline. It carries two
# successes and one unrecovered failure, which produces I2b RADAR -> YELLOW ->
# one real LLM call, exercising interpret -> validate -> render end to end.
#
# Nothing here is deleted automatically. There is no delete route, by design.

set -uo pipefail

: "${SUTTON_URL:?set SUTTON_URL, e.g. https://sutton-three.vercel.app}"
: "${SUTTON_INCOMING_SECRET:?set SUTTON_INCOMING_SECRET to the value you put in Vercel}"

URL="${SUTTON_URL%/}"
SCENARIO_ID=99999

have_jq() { command -v jq >/dev/null 2>&1; }
show() { if have_jq; then jq .; else cat; fi; }
rule() { printf '\n%s\n' "------------------------------------------------------------"; }

iso() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
iso_offset() {  # $1 = minutes ago
  if date -u -v-1M +%s >/dev/null 2>&1; then
    date -u -v-"$1"M +"%Y-%m-%dT%H:%M:%SZ"          # BSD/macOS
  else
    date -u -d "$1 minutes ago" +"%Y-%m-%dT%H:%M:%SZ"  # GNU
  fi
}

COLLECTED_AT="$(iso)"
T_MINUS_30="$(iso_offset 30)"
T_MINUS_20="$(iso_offset 20)"
T_MINUS_10="$(iso_offset 10)"
STARTED_AT="$(iso)"

rule
echo "STEP 1  GET /api/sutton-run with no secret  (expect 401 from the Flask app,"
echo "        not Vercel's 404 — that is what proves the rewrite works)"
rule
code=$(curl -s -o /tmp/sutton_smoke_1.json -w '%{http_code}' -m 30 "$URL/api/sutton-run")
echo "HTTP $code"
cat /tmp/sutton_smoke_1.json | show
[ "$code" = "401" ] || echo "!! expected 401, got $code"

rule
echo "STEP 2  GET /api/sutton-run?probe=state  (signed state read, server-side)"
rule
code=$(curl -s -o /tmp/sutton_smoke_2.json -w '%{http_code}' -m 40 \
  -H "X-Sutton-Secret: $SUTTON_INCOMING_SECRET" \
  "$URL/api/sutton-run?probe=state")
echo "HTTP $code"
cat /tmp/sutton_smoke_2.json | show
if have_jq; then
  ok=$(jq -r '.state_probe.ok // "missing"' /tmp/sutton_smoke_2.json)
  [ "$ok" = "true" ] || echo "!! state read did not return 200 — check SUTTON_WRITE_SECRET"
fi

rule
echo "STEP 3  POST /api/sutton-run  (synthetic scenario $SCENARIO_ID, mode=collect)"
rule
cat > /tmp/sutton_smoke_payload.json <<JSON
{
  "collected_at": "$COLLECTED_AT",
  "mode": "collect",
  "scenarios": [
    {
      "scenario_id": $SCENARIO_ID,
      "name": "SYNTHETIC SMOKE TEST — delete me",
      "is_active": true,
      "is_paused": false,
      "executions": [
        { "execution_id": "smoke-ok-1", "started_at": "$T_MINUS_30", "ended_at": null,
          "duration_ms": 120000, "status": 1, "run_type": "auto",
          "error_name": null, "error_message": null, "cause_module": null },
        { "execution_id": "smoke-ok-2", "started_at": "$T_MINUS_20", "ended_at": null,
          "duration_ms": 125000, "status": 1, "run_type": "auto",
          "error_name": null, "error_message": null, "cause_module": null },
        { "execution_id": "smoke-fail-1", "started_at": "$T_MINUS_10", "ended_at": null,
          "duration_ms": 130000, "status": 3, "run_type": "auto",
          "error_name": "SyntheticSmokeError", "error_message": "synthetic failure, not real",
          "cause_module": "MakeRequest" }
      ],
      "events": []
    }
  ]
}
JSON
echo "payload:"; cat /tmp/sutton_smoke_payload.json | show

code=$(curl -s -o /tmp/sutton_smoke_3.json -w '%{http_code}' -m 90 \
  -X POST "$URL/api/sutton-run" \
  -H "X-Sutton-Secret: $SUTTON_INCOMING_SECRET" \
  -H 'Content-Type: application/json' \
  --data-binary @/tmp/sutton_smoke_payload.json)
FINISHED_AT="$(iso)"
echo; echo "HTTP $code"; cat /tmp/sutton_smoke_3.json | show

rule
echo "EXPECTATIONS"
rule
cat <<'EXPECT'
  status               YELLOW        one unrecovered failure -> I2b RADAR
  deliver_escalation   false         I2b is RADAR, not ESCALATE
  deliver_radar        false         mode=collect
  shadow               true          while SUTTON_SHADOW=true
  storage_ok           true
  llm_ok               true          a real Anthropic call was made and passed
                                     the validator. false means it was rejected
                                     or failed; the body then carries the facts
                                     plus "Interpretation unavailable." and an
                                     H2_INTERPRETATION_FAILED signal, which is
                                     contained behaviour, not a broken run.
  storage_detail.observations.inserted   3 on a first run, 0 on a re-run
EXPECT

if have_jq; then
  rule
  echo "ACTUAL (key fields)"
  rule
  jq '{status, shadow, deliver_escalation, deliver_radar, storage_ok, llm_ok,
       observations_written, incidents_written, storage_detail, storage_errors,
       signals: [.signals[] | {signal_id, tier}]}' /tmp/sutton_smoke_3.json
  rule
  echo "EMAIL THAT WOULD BE SENT"
  rule
  jq -r '"SUBJECT: " + .subject + "\n\n" + .body' /tmp/sutton_smoke_3.json
fi

rule
echo "CLEANUP — nothing was deleted. Run this in Supabase when you are done."
rule
cat <<SQL
-- Synthetic smoke-test rows. scenario_id $SCENARIO_ID is not a watched scenario.
delete from sutton_observations
 where scenario_id = $SCENARIO_ID
   and source_id in ('smoke-ok-1', 'smoke-ok-2', 'smoke-fail-1');

delete from sutton_incidents
 where scenario_id = $SCENARIO_ID;

-- The run may also have written HARNESS_HEALTH incidents, which carry
-- scenario_id = null and so cannot be selected by scenario. Scope them by the
-- window this script ran in, and eyeball the rows before deleting:
select id, detected_at, signal_id, signal_class, tier, llm_ok, shadow
  from sutton_incidents
 where scenario_id is null
   and detected_at between '$STARTED_AT' and '$FINISHED_AT';
SQL

rule
echo "Raw responses kept at:"
echo "  /tmp/sutton_smoke_1.json   unauthenticated GET"
echo "  /tmp/sutton_smoke_2.json   state probe"
echo "  /tmp/sutton_smoke_3.json   POST response"
echo "  /tmp/sutton_smoke_payload.json"
