# cfb/ — CFB v1 shared red-zone ingestion layer

Feeds the **TD Opportunity** (§2) and **Situation / Defensive Matchup**
(§3) pillars of the CFB Universal TPE Score. See
`../docs/CFB_v1_Scoring_Design_Spec.md` and the approved Step 1 schema
proposal.

Mirrors `nfl/` in shape and rules ("duplicate rather than cross-import" —
this package imports nothing from `nfl/`).

## What it does

One manually-triggered run per `(season, week)`:

1. `GET /games?year=&week=&classification=fbs` — 1 call.
2. `GET /plays?year=&week=&classification=fbs&playType=TD` — 1 call, the
   week's touchdown plays (all types; offensive subset filtered in memory).
3. `GET /roster?year=&classification=fbs` — 1 call, season-cached.
4. For each **completed** game: `GET /plays/stats?gameId=` — ~1 call/game,
   ~150–350 rows, **fetched concurrently** (thread pool, 8 workers).
5. Aggregate red-zone / inside-10 / goal-line band **raw** touch + TD
   counts:
   - **A** → `cfb_player_redzone_weekly` — per `(player_id, season, week)`
   - **B** → `cfb_defense_redzone_allowed_weekly` — per
     `(defense team_id, position_group, season, week)`
6. One HMAC-signed POST per aggregation to its Lovable write route.

~94 CFBD calls and ~15s wall-clock for a full ~90-game week. See spec
§8b; the response carries `timing` + `cost_estimate` blocks.

Touch = `Rush` + `Target` + `Reception` stat rows (spec §8a — CFBD's
`Target` is incomplete-only). TD attribution: a touch on a `/plays`
offensive-TD `playId` that also appears in `/plays/stats`. Rolling
windows / percentiles / scoring are **not** here — the later scoring task
derives them over the stored rows (NFL parity).

## Run it

```
POST /api/ingest-and-write-redzone
Header: X-Pipeline-Secret: <PIPELINE_INCOMING_SECRET>
Body:   {"season": 2025, "week": 3, "preview_only": true}
```

`{"dry_run": true}` returns only the projected CFBD call count + wall-clock
for that week (no ingest).

`preview_only: true` runs the whole ingest + aggregation and returns the
full diagnostics bundle **without** writing to Supabase — use it for the
first live smoke test. Drop `preview_only` to write.

`GET /api/ingest-and-write-redzone` is a health check / usage string.

## Local

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.local.example .env.local   # fill in CFBD_API_KEY etc.
.venv/bin/python test_redzone.py   # fixture unit tests, no network
```

## Env vars

See `.env.local.example`. `CFBD_API_KEY`, `PIPELINE_INCOMING_SECRET`,
`CFB_PIPELINE_WEBHOOK_SECRET` are required; the two `LOVABLE_CFB_*_WRITE_URL`
vars are optional overrides.
