# HR Prop Flattening Pipeline

Flattens The Odds API's nested `batter_home_runs` response into a flat
list Make.com can consume directly, filtered to the "at least 1 HR" line
(`point: 0.5`, `name: "Over"` only).

## Live endpoints

### `/api/flatten` — flatten and filter only

`https://pipeline-coral.vercel.app/api/flatten`

- **POST** with a JSON body: a single event object (has a `bookmakers`
  key), a list of event objects, or `{"events": [...]}`.
- Returns a flat JSON array — one row per (player, bookmaker):
  `player_name`, `odds`, `bookmaker`, `game_id`, `home_team`, `away_team`,
  `commence_time`.
- **GET** the same URL for a health check.

### `/api/flatten-and-forward` — flatten, sign, and push to Lovable

`https://pipeline-coral.vercel.app/api/flatten-and-forward`

- Same accepted input as `/api/flatten`.
- Internally: flattens/filters, then HMAC-SHA256 signs the exact JSON
  string being sent (see `lovable_forward.py` for the signing details) and
  POSTs it to the Lovable backend webhook.
- Returns `{"success": bool, "rows_sent": int, "lovable_status_code":
  int|None, "error": str|None, "diagnostics": dict}` — Make.com only needs
  to check `success`. See "Diagnostics" below for what the `diagnostics`
  field is for.
- **Confirmed working at the HTTP layer** against the live Lovable
  endpoint (`sha256=<hex>` header format, signed over the sorted-keys JSON
  string — both accepted as implemented, both 200 responses). Actual row
  presence in `hr_props_raw` isn't observable from this codebase (no DB
  access into that backend, by design) — confirm on the Lovable side if
  full assurance is needed.
- Requires `LOVABLE_WEBHOOK_SECRET` to be set (Vercel env var, Production +
  Preview, stored as Sensitive — write-only, not readable back). Optional
  `LOVABLE_WEBHOOK_URL` env var overrides the default target URL if the
  Lovable project's URL ever changes (e.g. on publish).

## Diagnostics — "zero rows" isn't always "zero props today"

Real incident: 22 real Make.com calls each returned `"success": true,
"rows_sent": 0`, indistinguishable from "no HR props were available for
any of the 22 games" (implausible, but not something the old response
could rule out). Root cause was a caller sending a nested field
(`bookmakers`, `markets`, or `outcomes` — or occasionally the whole body)
as a JSON-encoded *string* instead of a real array, which `flatten_hr_props`
would previously treat as empty (`.get("bookmakers", [])` on a dict just
returns `[]` if the key's value doesn't behave like a list) rather than
erroring — the exact kind of silent failure that's indistinguishable from
genuinely-empty from the outside.

Two changes as a result:

1. **Defensive string recovery** at every nesting level (`flatten_hr_props.py`)
   — if `bookmakers`/`markets`/`outcomes` (or the whole request body, or the
   `events` list) arrives as a JSON string instead of a real array/object,
   it's now parsed and recovered rather than silently treated as empty.
   Covered by `test_malformed_input.py`.
2. **A `diagnostics` object**, both in the `/api/flatten-and-forward`
   response and in server-side logs (`vercel logs`, look for
   `[flatten-and-forward]` / `[flatten]` lines — logging uses
   `print(..., flush=True)` specifically because a short-lived serverless
   invocation can exit before buffered output ever reaches the log stream,
   which silently ate the first attempt at this logging locally before the
   flush was added), showing exactly how many bookmakers/markets/outcomes
   were actually found at each level, and whether any string-recovery
   kicked in. `{"bookmakers_seen": 22, "hr_markets_seen": 22,
   "outcomes_matching_filter": 0}` means real data arrived and genuinely had
   no 0.5-Over lines today. `{"bookmakers_recovered_from_string": 22}` means
   the fix above just saved the request. `{"bookmakers_seen": 0}` with no
   recovery flags means the caller's request didn't have bookmaker data at
   all — a caller-side problem, not a parsing one.

## Deployment

Auto-deploys on every push to `main` via Vercel's GitHub integration
(Root Directory: `pipeline`). No manual deploy step needed — just push.
Environment variable changes need a new deployment to take effect for
already-running functions — push something (even trivial) after adding or
changing one.

## Local development

```bash
cd pipeline
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd api
python3 test_flatten_hr_props.py     # flatten/filter test suite
python3 test_lovable_forward.py      # signing + forwarding test suite
FLASK_APP=index.py python3 -m flask run --port 5099   # run locally
```
