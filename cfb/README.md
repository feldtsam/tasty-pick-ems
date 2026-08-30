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
2. For each **completed** game: `GET /plays/stats?gameId=` — ~1 call/game,
   ~150–350 rows, never near the 2,000-row cap.
3. Resolve `athleteId → position` via `/roster` (season-cached).
4. Aggregate red-zone / inside-10 / goal-line band **raw** touch + TD
   counts:
   - **A** → `cfb_player_redzone_weekly` — per `(player_id, season, week)`
   - **B** → `cfb_defense_redzone_allowed_weekly` — per
     `(defense team_id, position_group, season, week)`
5. One HMAC-signed POST per aggregation to its Lovable write route.

Touch = `Rush` + `Target` stat rows (not `Reception`). TD attribution =
`Touchdown` stat row on the same `playId` **and** `athleteId`. Rolling
windows / percentiles / scoring are **not** here — the later scoring task
derives them over the stored rows (NFL parity).

## Run it

```
POST /api/ingest-and-write-redzone
Header: X-Pipeline-Secret: <PIPELINE_INCOMING_SECRET>
Body:   {"season": 2025, "week": 3, "preview_only": true}
```

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
