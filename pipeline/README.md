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
  Preview, stored as Sensitive — write-only, not readable back).
- Target URL comes from `LOVABLE_WEBHOOK_URL` (same env var setup as the
  secret), currently `https://tastypickems.lovable.app/api/public/pipeline-write`.
  This is env-var-driven specifically so a future Lovable URL change (it's
  already changed once, when the project was published/renamed) is a
  config update, not a code change — update the env var, push a trivial
  commit to trigger a redeploy (env var changes need a fresh deployment to
  reach already-running functions), done. `DEFAULT_LOVABLE_URL` in
  `index.py` is a hardcoded fallback for defense-in-depth only — keep it in
  sync when the env var changes, but the env var is the one that actually
  matters.

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

## Live scoring (`api/live_scoring/`) — NOT deployed or wired to live data yet

Ports the five-pillar model validated in `backtest/` (hand-set weights,
the completed Player Skill pillar with Pull%/FB%, no red-flag penalties —
per the decision documented there) into a function that scores one
player's HR prop for one game at a time, instead of a historical
batch/dataframe. `backtest/` itself is untouched — read-only source for
generating a snapshot, never modified.

- **`score_candidate(candidate: dict) -> dict`** — the scoring function.
  Pure Python, no pandas, no runtime dependency on `backtest/`. See the
  full input schema and known-gap fields in its module docstring.
- **`reference_data/reference_snapshot_2025.json`** (~110KB, bundled;
  `reference_snapshot_2022.json` also still present, superseded, kept for
  the year-over-year comparison below) — everything the scorer needs to
  reproduce backtest's exact percentile normalization and calibrated
  constants (batter/pitcher/bullpen reference scales, park factors by
  team, platoon bonus, batting-order curve) without needing pandas or
  backtest's multi-hundred-MB raw data at runtime. Generated by
  `scripts/build_reference_snapshot.py <season> <month_label>`, which
  imports backtest/scoring's *actual* validated code (not a
  reimplementation) to guarantee zero drift — regenerate by running that
  script from backtest/'s own venv if the reference season ever changes
  again. That script also handles the games+context merge for calibration
  locally rather than via backtest's `build_calibration_games()`, so
  `backtest/scoring/dataset.py` never needs editing just to add a new
  season to its lookup table — `backtest/` stays at zero file changes, not
  just zero behavioral changes.
- **Star-rating boundaries and temperature min/max scaling stay pinned to
  the validated 2023 full-season backtest distribution**, deliberately
  independent of whichever season powers the percentile scales. Both come
  from actually running the scoring model at scale and observing its real
  output — recalibrating them for a new reference year would mean
  re-running the full out-of-sample validation loop with that year as the
  baseline, a separate, larger task than "refresh the reference
  population." Temperature scaling was also the one real adaptation
  needed to go from batch to single-candidate scoring in the first place
  (backtest computes it *relative to the batch being scored*, meaningless
  for one candidate) — using fixed absolute bounds sidesteps that either way.
- **Star rating**: 1-5 stars from quintile boundaries of the real,
  validated 2023 full-season score distribution — evidence-based cut
  points, not arbitrary round numbers. `score_tier` (Elite/Strong/
  Moderate/Weak/Poor, matching the existing deployed `hr_score.py`'s
  label convention) is derived from the *same* star count, not an
  independent fixed-threshold scale — an earlier version used separate
  scales and a real test candidate landed "5 stars" and "Moderate"
  simultaneously, which a later AI-writing step couldn't have said
  coherently in the same breath.
- **Tested** against 6 hand-made candidates in `test_score_candidate.py` —
  4 core scenarios plus a missing-data case and a known-gap-fields case,
  using **real 2022 stat lines** for real players (Aaron Judge, Myles
  Straw, Bryan Reynolds, Aaron Nola, Yusei Kikuchi — pulled directly from
  backtest/'s validated data, not invented), scored against the **2025
  reference population** (see below). Checks directional sanity (elite
  scores highest, weak scores lowest, a tough matchup meaningfully drags
  down even an elite hitter, missing data lands near neutral rather than
  being silently skewed) rather than exact target numbers, since there's
  no single "correct" score for one hypothetical game. All 17 checks pass.
- **Known gaps — not realistically sourceable for live scoring yet**:
  - **Projected PA** isn't a real pre-game input (same gap documented in
    backtest/README.md) — the model scores opportunity via
    `batting_order_slot` through an empirically-calibrated curve, not a
    PA projection directly.
  - **Opposing bullpen quality** is *computable* with the exact
    methodology validated in backtest (`scripts/fetch_bullpen_quality.py`)
    and now has a 2025 table (see below) — the live-sourcing gap now is
    getting a live-game opposing-bullpen figure into the request at
    scoring time, not the reference data itself.
  - **Pitch-type tendencies** and three pitcher metrics (`opp_fb_pct_allowed`,
    `opp_avg_exit_velo_allowed`, `opp_xera`) are accepted in the input
    schema for forward-compatibility but were never part of the validated
    matchup pillar — providing them has zero effect on the score, and
    `score_candidate()` says so explicitly in its `notes` output every time.

### Reference population refreshed: 2022 → 2025 (resolved gap)

The reference population (percentile scales, park factors, platoon bonus,
batting-order curve) now comes from the **2025 season** — the most
recently completed full season, not partial in-progress 2026 data (too
small a sample this early in the year for a stable percentile
distribution). Star-rating boundaries and temperature scaling stay pinned
to the validated 2023 backtest distribution regardless (see above) — that
wasn't part of what needed refreshing.

Pulled with backtest's own scripts, exactly as before, adding new
2025-suffixed files to `backtest/data/` — zero existing file touched,
zero behavior of the validated 2021/2022/2023 results changed:
`fetch_park_factors.py`, `fetch_month_statcast.py`, `fetch_bullpen_quality.py`,
`fetch_season_baselines.py`, `fetch_batted_ball_profile.py` (HR pull-side
sanity check passed again: 66.6%/70.6% pulled for RHB/LHB, consistent with
2022's 64.6%/68.0%), `fetch_game_context.py` (43,668 rows, 2 of 2,428 games
timed out — negligible).

**This was a real, consequential change, not a small refinement** — exactly
what was worth checking rather than assuming. Re-running the same 6 test
candidates against both snapshots side by side
(`scripts/compare_snapshot_years.py`):

| Candidate | 2022 score | 2022 stars | 2025 score | 2025 stars | Δ |
|---|---|---|---|---|---|
| Judge/weak-pitcher/good-park | 86.7 | 5 | 81.5 | 5 | −5.2 |
| Straw/elite-pitcher/bad-park | 16.1 | 1 | 21.9 | 1 | +5.8 |
| Reynolds/avg-everything | 61.6 | 5 | 55.0 | **4** | −6.6 |
| Judge/elite-pitcher | 65.0 | 5 | 59.8 | **4** | −5.2 |
| Straw/great-context | 55.4 | 4 | 46.6 | **2** | −8.8 |
| missing-data | 50.0 | 3 | 50.0 | 3 | 0.0 |

**Star rating changed for 3 of 6 candidates** — one by two full stars.
Directional sanity held throughout (elite still scores highest, weak still
scores lowest, middling still lands strictly between), but the magnitude
shift is real and explainable by two concrete, independently-verified
drivers, not vague drift:

1. **The league genuinely got more offense-heavy.** Every skill metric's
   median rose from 2022 to 2025 in the qualified reference population
   (barrel% 6.9%→8.0%, hard-hit% 38.0%→40.7%, avg exit velo 88.3→89.1,
   xSLG .383→.392, xwOBA .303→.308, HR/PA .0268→.0274), and league-wide HR
   rate on batted balls rose ~7.8% relative (0.0420→0.0452). A frozen 2022
   stat line naturally ranks a bit lower against a 2025 league where the
   bar moved up — the percentile system responding correctly to a real
   shift, not a bug.
2. **Individual park factors can swing hard year to year.** Milwaukee went
   from the single best HR park in 2022 (132.0) to *below* league average
   in 2025 (91.7) — confirmed directly from both park_factors files, not
   inferred. That's the specific reason "Straw/great-context" (built
   leaning on Milwaukee being a great park) dropped far more than the
   others — a real instance of the single-season/unadjusted park factor
   noise already documented as a known limitation in backtest/README.md.
3. Also verified independently from raw 2025 pitch data (not just trusted
   from the snapshot output): the **platoon effect roughly doubled**
   (relative lift +0.051 in 2022 → +0.117 in 2025 — same-hand HR rate
   0.0278→0.0289, opposite-hand 0.0293→0.0325, both up slightly, but the
   *gap* between them widened notably).

Practical takeaway: the reference year is not a cosmetic setting. A player
evaluated as elite under one season's baseline can genuinely read as
merely strong under another's, and a park-dependent scenario can swing
multiple stars purely from park-factor year-to-year noise. Worth keeping
in mind whenever this snapshot gets refreshed again in the future.

## Live data (`api/live_data/`) — deployed, NOT wired to Make.com yet

Pulls one real MLB game's lineup, weather, and current-season player
stats, and assembles flat JSON with one `score_candidate()`-ready dict per
hitter — field names matching that function's input schema exactly, so
its output plugs straight in. Same sequencing as live scoring: built and
validated against real live data first. The endpoint itself is now
deployed (see below), but nothing has been configured in Make.com to call
it yet — that connection is still a separate step.

### `/api/live-data/game/<game_pk>` — one game, GET, no request body

`https://pipeline-coral.vercel.app/api/live-data/game/<game_pk>`

**Deliberately per-game, not per-day** — mirrors `/api/flatten-and-forward`'s
one-call-per-event shape exactly, so Make.com's existing Iterator-over-games
pattern (already built and proven for the odds pipeline) can call this the
same way once it's wired in, with no new Make.com pattern to design.

This wasn't the original design — an earlier version fetched a whole
day's games in one batched call and took ~19-20s for a 12-game day, over
Vercel's Hobby-tier 10s serverless timeout. Profiling (not guessing) found
the whole-slate per-player MLB Stats API calls were the dominant cost
(8.7s alone for 198 batters), not the Savant bulk CSVs (~0.6s flat,
independent of player count) or the game-context call. Restructured to
fetch one game's ~18-20 players per call instead:

- **`build_candidates_for_game(game_pk: int) -> dict`** in
  `build_game_candidates.py` is the production entry point; run directly
  with `python3 build_game_candidates.py <game_pk>`.
- **`game_data.py`** — ONE `feed/live` call per game_pk gets everything:
  status/teams/venue, the AUTHORITATIVE probable pitchers, the
  batting-order lineup, and weather/roof. This replaced the earlier
  two-call design (a day-level schedule hydrate for lineups + a separate
  per-game `feed/live` call for weather) once it was confirmed that
  `feed/live` alone carries all of it — including the lineup, extracted
  from `liveData.boxscore.teams.*.players`: confirmed the *starting* nine
  are exactly the players whose `battingOrder` value ends in `"00"`
  (`"100".."900"`), producing the identical 9-player order the old
  schedule-hydrate approach did. Because every call fetches fresh at
  request time, there's no separate "schedule pull" to drift from a
  scratch by construction — unlike the old two-call design, which needed
  an explicit cross-check between the two calls to catch that.
  Confirmed real weather format: `{"condition": "Clear", "temp": "83",
  "wind": "7 mph, Out To CF"}`, including the `"Roof Closed"` case for
  retractable roofs.
  Confirmed an invalid/unknown `game_pk` does **NOT** 404 — `feed/live`
  returns HTTP 200 with a near-empty placeholder body (`gamePk: 0`,
  `status.detailedState: "Unknown"`) — so `game_data.py` explicitly checks
  the returned `gamePk` matches the request and raises `ValueError`,
  which the Flask route turns into a real `404` instead of a confusing
  downstream crash.
- **`mlb_schedule.py`** — now day-level game *discovery* only (which
  game_pks exist today), used by `test_live_data.py` and local dev, not by
  the production per-game path. Still confirmed and documented: a game
  more than ~2 days out (status `"Scheduled"`) has empty lineups; they
  reliably fill in starting around `"Pre-Game"` status (roughly 1-1.5
  hours before first pitch) and stay populated through `"In
  Progress"`/`"Final"`; `probablePitcher` is available far earlier,
  independent of lineup posting. Games are tagged `lineup_status`:
  `"confirmed"` (candidates built), `"not_yet_posted"` (returned with an
  empty `candidates` list, not silently dropped), or `"not_happening"`
  (postponed/cancelled).
- **`savant_stats.py`** — bulk current-season Statcast quality metrics
  (avg exit velo, hard-hit%, barrel%, sweet-spot%, xSLG, xwOBA, and the
  pitcher "allowed" equivalents) from Baseball Savant's CSV leaderboards,
  fetched with `min=0` (confirmed via a real request) so small-sample
  players are included too — not pandas, a small in-memory `csv.DictReader`
  parse, since these endpoints return ~600-800 rows, not backtest's
  multi-hundred-MB files. **Keyed by `player_id`, confirmed to be the same
  MLBAM ID used everywhere else** (lineup, probable-pitcher, `/people`
  endpoints) — no name-matching needed, unlike scraping Baseball-Reference
  (investigated and rejected for this reason; see below).
- **`player_season_stats.py`** — per-player official counting stats
  (PA/HR for hitters; IP/K/HR-allowed/BF for pitchers, with a custom
  parser for MLB's ".1"/".2" = thirds-of-an-inning notation) from
  `/people/{id}/stats`, fetched concurrently (`ThreadPoolExecutor`, same
  pattern as `backtest/scripts/fetch_game_context.py`) for just the
  players in today's lineups. **Deliberately per-player, not the bulk
  `/api/v1/stats?stats=season&group=hitting...` leaderboard** — that bulk
  endpoint was tried first and found to have a hidden qualification
  filter (a `limit=2000` request returned only 147 "leader" players, every
  one with 322+ PA, no error or signal that anyone below that was
  excluded) — confirmed via a real request before it became a silent bug,
  not assumed to work. Also fetches batter/pitcher handedness via the
  `/people?personIds=...` batch endpoint (confirmed to accept a
  comma-joined ID list in one call).
- **Stat-source decision — recommendation given explicitly, not
  defaulted silently** (see `stat_selection.py`'s module docstring for the
  full reasoning): **sample-size-gated fallback to the player's own real
  2025 season stats**, not a weighted blend of current-season and 2025
  numbers. A player's current season is used once their PA/IP clears the
  *same* qualification thresholds already validated in
  `backtest/scoring/config.py` (`min_pa=100`, `min_ip=20`); below that,
  falls back to that specific player's real 2025 full-season stat line
  (bundled in the live-scoring reference snapshot's `batter_lookup_by_id`/
  `pitcher_lookup_by_id`); if the player has no 2025 MLB record either
  (true rookies), falls back further to the small current-season sample
  rather than leaving the field blank. A weighted blend was considered and
  rejected for now — it would invent a new weighting function with no
  backtested evidence for what the weight-by-sample-size curve should be,
  which is real, separate validation work, not a one-line choice; every
  number this way stays a real stat a real player actually posted, never
  an interpolation. Can be revisited into an actual blend later if the
  straight-fallback version looks unstable in practice, the same
  ship-then-evaluate precedent as red-flag penalties in `backtest/`. Each
  candidate's `_stat_source` field
  (`current_season`/`prior_season_2025_fallback`/
  `current_season_small_sample_no_fallback`/`unavailable`) and
  `_stat_source_note` make the decision visible per player, not hidden.
- **Known gaps — confirmed absent, not assumed**:
  - **Pull%/FB%** (batted-ball profile): no Savant CSV leaderboard exposes
    this. The historical backtest computes it from raw pitch-by-pitch
    Statcast data, impractical live in a serverless function. Omitted;
    `score_candidate()` already tolerates missing `pull_pct`/`fb_pct` as
    neutral within the contact-quality component.
  - **Opposing bullpen quality**: same gap already documented for live
    scoring — the validated methodology only has reference tables through
    2023.
  - **Odds**: this module has no odds source — that's `flatten_hr_props.py`'s
    job from a different upstream API (The Odds API). A caller combining
    both pipelines needs to join them.
- **Tested against the real live 2026-07-27 slate, per-game** (`test_live_data.py`,
  no mocks) — calls `build_candidates_for_game()` once per game_pk
  individually (exactly what the deployed route does per request), not a
  batch wrapper. 12 games, 11 with confirmed lineups (198 real candidates),
  1 not yet posted (correctly zero candidates). All 17 checks passed,
  including feeding every one of the 198 real candidates through the
  actual `score_candidate()` and confirming zero crashes and a
  non-degenerate score spread. 9 candidates hit the 2025 fallback path
  (real players with real small 2026 samples, e.g. a 47-PA or 27-PA start
  to the season); none hit "no source at all."
- **Runtime — the actual thing the restructure was for**: individual
  `build_candidates_for_game()` calls measured **min=0.28s, avg=2.15s,
  max=2.43s** across the real slate (`test_live_data.py` times each call
  and asserts every one stays under 8s, leaving real margin below Vercel
  Hobby's actual 10s limit). Also confirmed at the HTTP layer against a
  locally running Flask server: `GET /api/live-data/game/824001` → 200 in
  ~1.9-2.7s. Down from the old whole-slate design's ~19-20s total for a
  12-game day — the restructure fixed exactly the problem it was meant to.
- **Error handling confirmed against real requests, not assumed**: an
  invalid `game_pk` (e.g. `/api/live-data/game/1`) returns a clean `404`
  with a real error message rather than a raw 500 — this needed an
  explicit fix once testing revealed `feed/live` doesn't 404 on its own
  for a bad ID (see `game_data.py` above).

## Deployment

Auto-deploys on every push to `main` via Vercel's GitHub integration
(Root Directory: `pipeline`). No manual deploy step needed — just push.
Environment variable changes need a new deployment to take effect for
already-running functions — push something (even trivial) after adding or
changing one. `api/live_scoring/` has no route wired up yet (it's a
function other endpoints call into, not deployed standalone) — see the
section above. `api/live_data/` DOES have a live route
(`/api/live-data/game/<game_pk>`, see above) once this is pushed, but it
isn't called from anywhere yet — no Make.com scenario is configured to hit
it.

## Local development

```bash
cd pipeline
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd api
python3 test_flatten_hr_props.py     # flatten/filter test suite
python3 test_malformed_input.py      # defensive-parsing test suite
python3 test_lovable_forward.py      # signing + forwarding test suite
cd live_scoring
python3 test_score_candidate.py      # live single-candidate scoring test suite
cd ../live_data
python3 test_live_data.py            # live-data test suite — hits real APIs, no mocks
cd ..
FLASK_APP=index.py python3 -m flask run --port 5099   # run locally
```
