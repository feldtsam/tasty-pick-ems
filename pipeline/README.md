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
  string being sent (see `lovable_forward.py` for the signing details and
  the assumptions about Lovable's expected header format — those are
  genuinely unverified until a real round-trip test confirms them) and
  POSTs it to the Lovable backend webhook.
- Returns `{"success": bool, "rows_sent": int, "lovable_status_code":
  int|None, "error": str|None}` — Make.com only needs to check `success`.
- Requires `LOVABLE_WEBHOOK_SECRET` to be set (Vercel env var, Production +
  Preview, stored as Sensitive — write-only, not readable back). Optional
  `LOVABLE_WEBHOOK_URL` env var overrides the default target URL if the
  Lovable project's URL ever changes (e.g. on publish).

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
