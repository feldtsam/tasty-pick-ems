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

## Scored picks (`api/scored_picks.py`) — deployed, NOT wired to Make.com yet

Ties together everything above into the final orchestration piece: raw
Odds API event data + a game_pk in, real scored HR-prop picks out,
forwarded to a new Lovable webhook. Combines three already-tested,
independent pieces with zero reimplementation — `flatten_hr_props.py`
(odds flattening), `live_data`'s `build_candidates_for_game()` (lineup/
matchup/environment), and `live_scoring`'s `score_candidate()` (the
five-pillar scorer) — plus one genuinely new piece: matching a player
between the two data sources.

### `/api/score-game-props/game/<game_pk>` — one game, POST, same odds body as `/api/flatten-and-forward`

`https://pipeline-coral.vercel.app/api/score-game-props/game/<game_pk>`

Deliberately reuses two existing shapes rather than inventing a new
contract: the URL path parameter matches `/api/live-data/game/<game_pk>`,
and the POST body is the *exact* same raw-odds shape
`/api/flatten-and-forward` already accepts (single event object, list of
events, or `{"events": [...]}`) — Make.com's existing odds-fetch step
needs zero changes to feed this once it's wired in.

**Architecture decision, made deliberately (per explicit direction)**: this
endpoint does NOT read back from `hr_props_raw` to get the odds it needs —
that would require a new read-access pathway into Lovable that doesn't
exist yet. Instead, Make.com passes the raw odds data it already fetched
earlier in its existing loop straight into this endpoint alongside the
game_pk. Reuses a working pathway instead of solving a new permissions
problem.

### Player matching — the one genuinely new piece

Odds data identifies players by free-text name (The Odds API's
`description` field); live-data candidates are keyed by MLBAM ID with a
name from the MLB Stats API. There's no shared ID between the two
sources, so this has to match on name — confirmed to be a REAL failure
mode, not a theoretical one: pulling real data for this exact module (BAL
@ DET, 2026-07-28, game_pk 824243), **The Odds API's own feed spells a
real player "Javier Baez" while the MLB Stats API spells the same person
"Javier Báez"** — same game, same player, different bytes.

`scored_picks.py`'s `match_players()` handles this in two ordered steps,
never silently guessing:

1. **Exact, after normalization** (`normalize_name()`) — the primary
   path. Normalization strips accents (Unicode NFKD decompose + drop
   combining marks: `Báez` → `baez`), lowercases, strips punctuation, and
   drops a trailing generational suffix (`Jr`/`Sr`/`II`/`III`/`IV`/`V`) —
   confirmed necessary because real bookmaker feeds are inconsistent about
   including these (a real player's odds-feed name dropped a suffix the
   MLB Stats API includes). This one step was enough to resolve the real
   Báez/Baez case above via the exact path, not the fuzzy fallback below —
   a better outcome than needing fuzzy logic for it.
2. **Bounded fuzzy fallback**, only if step 1 finds nothing:
   `difflib.SequenceMatcher`, cutoff 0.85, run only against that one
   game's own small (~18-name) lineup pool — never the whole league, which
   keeps false-positive risk low even at a fairly loose cutoff. Accepted
   ONLY if exactly one candidate is a confident, unambiguous best match
   (the top match must beat the runner-up by ≥0.05); otherwise treated as
   unmatched rather than guessed. Every fuzzy match is tagged
   `match_type: "fuzzy"` in the output — never silently treated the same
   as an exact match, so a caller can flag it for human review.

A name that normalizes to **more than one** live-data candidate in the
same game (a genuine collision — odds data has no team field to
disambiguate with) is also reported as unmatched rather than guessed.
Anything left unmatched after both steps is preserved in the response's
`match_summary.unmatched_odds`, with the raw name and its odds rows intact
— never silently dropped. A lineup player with no odds offered at all
(normal — not every bench bat gets a market) is separately reported in
`unmatched_candidates`, informational only, not treated as an error.

**Tested** (`test_scored_picks.py`) at two levels: synthetic edge cases
hand-crafted to exercise every path (exact w/ accent+suffix, the fuzzy
path via a real near-miss spelling `"Bobby Wit Jr"` → `"Bobby Witt Jr."`,
an ambiguous same-name collision, a fully unmatched name, an unmatched
lineup player) — needed because a real slate's odds proved the *exact*
path alone was enough for that one game, which would leave fuzzy/ambiguous
logic unverified without dedicated fixtures — plus a full **real,
no-mocks** end-to-end run: real Odds API `batter_home_runs` data for BAL @
DET joined against that game's real live-data candidates. All 18 real
odds entries matched (17 direct, 1 — Báez — via the accent-stripping exact
path), zero unmatched, zero per-player scoring errors, scores directionally
sane (non-degenerate spread, star ratings monotonic with `final_score`).
22/22 checks pass.

Multiple bookmakers offering the same player: `score_candidate()` takes a
single scalar `odds` value, so the best (numerically highest, which for
American odds is always the more favorable price for the bettor
regardless of sign) price across bookmakers is used, with `bookmaker` and
`num_bookmakers` recorded on the output so which book it came from is
never hidden.

Each returned "scored pick" is: player identity (`player_name`,
`mlbam_id`, `team`, `batting_order_slot`), opposing pitcher, game info
(`game_pk`, teams, `game_date_utc`, `venue_name`), the odds actually used
(`odds`, `bookmaker`, `num_bookmakers`, `match_type`), the four pillar
scores (`skill_score`/`matchup_score`/`environment_score`/`opportunity_score`),
`final_score`, `star_rating`, `score_tier`, `passes_odds_filter`, the full
nested pillar detail (components + notes, for drill-down) in
`pillar_detail`, and `scored_at` (UTC timestamp of when this was computed —
distinct from the game's own time).

The endpoint's own response to the CALLER (Make.com) is separate from what
gets forwarded to Lovable — it's a summary: `scored_count`,
`match_summary` (`odds_entries_total`, `matched`, `unmatched_odds`,
`unmatched_odds_count`, `unmatched_candidates_count`,
`excluded_below_odds_filter`, `excluded_below_odds_filter_count` — matched
by name but below the hard +300 gate, never scored), `errors` (per-player
scoring failures — one bad candidate never sinks the whole batch),
`forwarded` (bool), `lovable_status_code`, `forward_error`. Returns `400`
for an unrecognized odds payload shape (matching `/api/flatten-and-forward`'s
existing convention), `404` for an unknown game_pk, `502` if forwarding to
Lovable fails or `LOVABLE_WEBHOOK_SECRET` isn't configured.

**Confirmed working end-to-end against the real, live Lovable endpoint**
(`https://tastypickems.lovable.app/api/public/scored-picks-write`).
`LOVABLE_SCORED_PICKS_WEBHOOK_URL` is set in Vercel (Production + Preview,
same pattern as `LOVABLE_WEBHOOK_URL`); it reuses the existing
`LOVABLE_WEBHOOK_SECRET` (no new secret) — confirmed by the forward itself
succeeding, since a wrong secret would fail HMAC verification on Lovable's
side. Real test against BAL @ DET (game_pk 824243), sent twice in a row to
check upsert behavior on a duplicate submission (same discipline that
caught the real `rows_sent: 0` bug in the `hr_props_raw` pipeline):

| Send | HTTP | Lovable's response |
|---|---|---|
| 1st | 200 | `{"ok":true,"received":18,"upserted":18,"deduped":0}` |
| 2nd (identical, immediate) | 200 | `{"ok":true,"received":18,"upserted":18,"deduped":0}` |

Both sends succeeded identically with no error and no different behavior
on the repeat — consistent with a correct upsert-in-place (same 18 rows
updated, not accumulated). Caveat, stated plainly rather than overclaimed:
this is read from Lovable's own reported counts, not a direct row-count
check against the underlying table (no DB access from this side, same
limitation already noted for `hr_props_raw`) — if airtight certainty on
row counts matters, check the table directly on the Lovable/Supabase side.

`forward_to_lovable()` (`lovable_forward.py`) now captures and returns the
full response body on success too, not just on failure as before — needed
specifically to surface Lovable's real received/upserted/deduped numbers
rather than an opaque `"success": true`. Surfaced in this endpoint's own
response as `lovable_response`.

Tested via an isolated Vercel PREVIEW deployment (not touching
production/`main` and not requiring a git push first) — the only way to
exercise the real configured secret, since Vercel env values are
write-only from this side. This pipeline function is fully built, tested
against real data and the real live webhook, and ready to commit; the
Lovable side (`scored_picks` table + route) already exists per the schema
below.

### `scored_picks` table schema (for the Lovable side)

Same shape convention as the existing `hr_props_raw` table/`pipeline-write`
route — flat, queryable columns for everything a caller would filter or
sort on, plus one `jsonb` column for full drill-down detail:

| Column | Type | Notes |
|---|---|---|
| `id` | uuid / serial | primary key, auto-generated |
| `player_name` | text | |
| `mlbam_id` | integer | MLB Stats API player ID |
| `team` | text | batter's team abbreviation |
| `batting_order_slot` | integer | 1-9 |
| `opp_pitcher_name` | text | |
| `opp_pitcher_mlbam_id` | integer | |
| `game_pk` | integer | MLB Stats API game ID |
| `home_team` | text | |
| `away_team` | text | |
| `game_date_utc` | timestamptz | the game's own start time |
| `venue_name` | text | |
| `odds` | integer | American odds, best price across bookmakers |
| `bookmaker` | text | which book gave that best price |
| `num_bookmakers` | integer | how many books offered this player at all |
| `match_type` | text | `"exact"` or `"fuzzy"` — see matching section above |
| `skill_score` | numeric | pillar 1 |
| `matchup_score` | numeric | pillar 2 |
| `environment_score` | numeric | pillar 3 |
| `opportunity_score` | numeric | pillar 4 |
| `final_score` | numeric | weighted final |
| `star_rating` | integer | 1-5 |
| `score_tier` | text | Elite/Strong/Moderate/Weak/Poor |
| `passes_odds_filter` | boolean | odds ≥ +300 — see note below, this is now also enforced as a hard pre-scoring gate, not just this informational flag |
| `pillar_detail` | jsonb | full nested pillar components + notes |
| `temp_f` | numeric | raw game temperature — see note below |
| `wind_speed_mph` | numeric | raw wind speed |
| `wind_description` | text | raw wind direction text, e.g. `"Out To RF"` |
| `roof_status` | text | `"outdoor"` / `"closed"` / `"dome"` |
| `scored_at` | timestamptz | when this pick was computed |
| `created_at` | timestamptz | standard insert timestamp, default `now()` |

**Two real fixes, both confirmed against live data (not just unit tests):**

1. **The +300 odds filter is now a genuine hard pre-scoring gate**, matching
   the product spec (Tasty Pick Ems Master Product Blueprint §6:
   "anything under +300 is discarded before the AI or the rules engine
   ever touches it"). An earlier version scored every matched candidate
   regardless of odds and only recorded `passes_odds_filter` as an
   informational flag — real sub-+300 candidates (confirmed: Willson
   Contreras at +245) were reaching `scored_picks` anyway.
   `scored_picks.py`'s `build_scored_picks_for_game()` now filters matched
   candidates against `score_candidate.MIN_ODDS_FOR_FILTER` *before*
   `score_candidate()` is ever called — excluded candidates are reported
   by name in `match_summary.excluded_below_odds_filter`, never silently
   dropped. Re-confirmed against real live odds after the fix (odds move
   over time, so this wasn't the same players as the original bug, but
   the same mechanism): a real BAL @ DET pull excluded Riley Greene
   (+290) and Pete Alonso (+270) before scoring, while every one of the
   16 remaining real candidates scored had odds ≥ +300.
2. **Raw weather is now persisted**, not just the normalized 0-100
   `pillar_detail.environment` sub-scores. `build_game_candidates.py`
   already computes real `temp_f`/`wind_speed_mph`/`wind_description`/
   `roof_status` per candidate from the game's actual live weather —
   `score_candidate()` consumes them to compute `environment_score` but
   never returns them, so they were being discarded before this fix
   rather than stored. Needed for any caller (e.g. a future content
   writer) that wants to reference real conditions ("wind blowing out at
   7 mph") rather than only a percentile score.

## Game ID resolution (`api/game_lookup.py`) — the odds pipeline / MLB pipeline ID mismatch

Real problem, surfaced before wiring `/api/score-game-props` into Make.com:
Make.com's existing odds-fetch loop identifies each game by The Odds API's
own event ID (plus `home_team`, `away_team`, `commence_time` — everything
on The Odds API's own event object), never by MLB's numeric `game_pk`. But
`/api/score-game-props/game/<game_pk>` (and `/api/live-data/game/<game_pk>`)
both key off `game_pk` — a completely different ID system from a
completely different data source.

**Recommendation, and why the other options were rejected**: resolve
`game_pk` internally, inside a new route
(`POST /api/score-game-props/by-event`), from fields the raw odds event
ALREADY carries — not a separate lookup endpoint Make.com calls first, and
not new fields Make.com has to start passing.

- A standalone "team names + date → game_pk" endpoint that Make.com calls
  once per game *before* `score-game-props` (the first option on the
  table) would work, but adds a mandatory second HTTP round-trip per game
  for information the raw odds event already contains — `home_team`,
  `away_team`, and `commence_time` are already top-level fields on every
  real Odds API event object. Solving a problem Make.com doesn't actually
  have (missing data) isn't worth the extra call.
- Having Make.com fetch the day's schedule once and build its own
  team-name lookup table (the third option) was rejected for the same
  reason logic has consistently moved OUT of Make.com's UI throughout this
  whole project: it would mean re-implementing name-matching and
  date-window logic in Make.com's no-code formulas, un-unit-testable,
  instead of in code that can be — the exact anti-pattern this pipeline
  was built to get away from in the first place.
- So: resolve internally, using data already in hand. `game_lookup.py`'s
  `resolve_game_pk()` is a clean, independently-tested function; the new
  route is a thin wrapper around it plus the existing pipeline — zero new
  data Make.com needs to fetch or pass, zero extra round-trip, and the
  matching logic stays in Python where it's actually testable.

### `/api/score-game-props/by-event` — the route built for Make.com's real situation

`https://pipeline-coral.vercel.app/api/score-game-props/by-event`

POST body: a single raw Odds API event object (must have `home_team`,
`away_team`, `commence_time`, `bookmakers`) — not a list or
`{"events": [...]}`, since resolution needs exactly one target matchup.
Resolves `game_pk` internally, then runs the identical pipeline
`/game/<game_pk>` does. Response includes `resolved_game_pk`,
`resolved_via`, and `disambiguated_by_time` in addition to everything the
`game_pk` route already returns.

**Team name matching is deliberately NOT fuzzy**, unlike player names.
Pulled real data from both APIs for the same real dates specifically to
check, rather than assuming: The Odds API's and MLB Stats API's team-name
strings are **byte-identical for all 30 real teams** as of this writing —
including the "Athletics" rename (both APIs just say "Athletics", no
city) and "St. Louis Cardinals"'s period. Team names turned out to be a
much smaller, more stable problem than player names — `normalize_team_name()`
only handles case/period/whitespace, cheap insurance against future
formatting drift rather than a real observed mismatch. Given that,
`resolve_game_pk()` deliberately does NOT fuzzy-fallback the way
`match_players()` does for players: a wrong fuzzy match between two TEAMS
would misattribute an entire game's worth of lineups/stats/odds — a much
worse failure mode than one mismatched player — so an unresolved team pair
raises a clear error instead of guessing.

**Two real scheduling gotchas found and handled, not assumed**:

1. **Cross-UTC-midnight games.** MLB's schedule is keyed by `officialDate`
   — the game's LOCAL wall-clock date — which for a US evening game is
   often one UTC calendar day earlier than The Odds API's `commence_time`
   (always UTC). Confirmed with a real game: Houston Astros @ Los Angeles
   Angels, `commence_time` `"2026-07-29T01:38:00Z"`, but MLB's own
   `officialDate` is `"2026-07-28"` — querying MLB's schedule for
   `2026-07-29` directly does not find this game AT ALL. Handled by always
   searching both `commence_time`'s own UTC date and the day before.
2. **Two teams playing on consecutive days can make "a match was found"
   the WRONG signal**, not just doubleheaders needing disambiguation. An
   earlier version of this code only searched the day before when the
   first date came up empty — a real test caught why that's wrong: real
   series between the same two teams span consecutive days, so the naive
   date can already have exactly one match that's simply the *next* game
   in the series, not the one this specific `commence_time` refers to.
   The fix: always gather every team-name match from BOTH dates, then let
   actual game start time — never date, never match count — be the sole
   tiebreaker. This one rule also correctly handles the real doubleheader
   case confirmed in testing (Cleveland Guardians @ Cincinnati Reds,
   `game_pk`s 824490 and 824489, same date, 17:40Z and 23:10Z) without
   needing separate doubleheader-specific logic.

**Tested against real data** (`test_game_lookup.py`, 12/12 checks): the
ordinary case (Baltimore Orioles @ Detroit Tigers — itself a real
multi-game series, confirming the closest-time tiebreak works on everyday
data, not just crafted edge cases), the real cross-midnight game, the real
doubleheader (both directions), and a confirmed-nonexistent team pair
correctly raising an error instead of guessing.

## Shelf curation (`api/shelf_curation.py`) — NOT deployed or wired to Make.com yet

Decides which scored candidates (the same flat dicts `scored_picks.py`
already produces) populate each of the app's six shelves, plus the Tasty
Six. Pure logic, no new endpoint deployed yet — validated standalone
against real data first, same sequencing as everything else here.

### The real correction that shaped this: recent form needed a new data source

"Hot Hitters" and "Cold Pitchers to Attack" both imply RECENT form (a hot
streak / a recent slump) — but the core model's Skill and Matchup pillars
are built entirely from season-long aggregates. Using season-long pillar
dominance to populate these two shelves would be a real mismatch between
what the shelf name promises and what it actually measures. "Weather
Factors" didn't have this problem — weather is inherently about *today's*
specific game, not a historical trend — so it's still driven by the
Environment pillar being the dominant (highest) of the four pillar scores.

### `api/live_data/recent_form.py` — the new recent-form data source

**Source: the MLB Stats API's `gameLog` stats** (`/people/{id}/stats?stats=gameLog&...`)
— the same free API already used everywhere else in `live_data/`, NOT
pybaseball/Statcast. Deliberate: introducing pybaseball (pandas + real
Statcast pulls) would undo the exact latency work `build_game_candidates.py`
already went through (whole-slate → per-game, to stay under Vercel
Hobby's 10s timeout). A genuine recent-*window* Statcast pull (hard-hit%/
barrel% allowed over a pitcher's last 5 starts specifically, as opposed to
the season-long Baseball Savant leaderboards `savant_stats.py` already
uses) means pulling raw pitch-by-pitch data per player — multi-second,
per-player, not something that fits a live request budget multiplied
across every candidate on a shelf.

**Known, deliberate gap as a result**: pitcher recent-form here is ERA/
HR-per-9/K-per-9/BB-per-9 over their last N real starts — fast, official
box-score counting stats — NOT recent hard-hit%/barrel% allowed, which the
original spec listed as an "and/or" alternative to ERA/runs-allowed. Runs
allowed is the standard way to describe a pitcher's recent struggles, and
it's what's achievable here without a much heavier pull — flagged
explicitly rather than faking a "recent barrel%" from data that isn't
really recent-windowed.

**Window size — deliberately different units for hitters vs. pitchers**,
not "10-15 games" for both:
- **Hitters: last 15 games played.** A meaningful hot-streak window
  (~2.5-3 weeks) while staying genuinely recent.
- **Pitchers: last 5 starts**, not "10-15 games". Confirmed against real
  data that starters appear roughly every 5th calendar day — "10-15
  games" would span 2+ months for a starter, defeating the point of a
  RECENT signal. 5 starts (~3-4 weeks on a normal rotation turn) is the
  much closer real-world analog to "15 games" for a hitter.

**A real bug caught by hand-checking real output, not assumed away**: the
first version took a pitcher's last 5 game-log *appearances* regardless of
role. A real 2026 pitcher (Caleb Ferguson) is used almost entirely as a
reliever/opener — `gamesStarted: 0` for nearly every real appearance,
mostly 0.1-1.2 IP relief stints — and his "5 most recent appearances"
produced a wildly distorted "5-start" ERA (9.64 over just 4.7 total
innings) that doesn't represent a struggling starter at all. Fixed by
filtering to real starts (`gamesStarted == 1`) before windowing — a true
swingman/opener then correctly has too few real starts to clear the
eligibility threshold below, instead of ranking on relief-appearance
noise. Regression-tested in `test_recent_form.py`.

### The six shelves

Three odds-tier shelves, unchanged from the original design — filter by
odds range, rank by `final_score`:

| Shelf | Range |
|---|---|
| `+300-499` | 300 ≤ odds ≤ 499 |
| `+500-699` | 500 ≤ odds ≤ 699 |
| `Going Nuclear` | odds ≥ 700 |

Three themed shelves:

- **Hot Hitters** — ranked by the candidate's own `recent_ops` (real
  recent-window data, see above). Gated on `MIN_HITTER_RECENT_SAMPLE = 8`
  (of the 15-game window) — a candidate with too thin a recent sample
  (a rookie a few games into their debut) isn't ranked on noise.
- **Cold Pitchers to Attack** — still a shelf of BATTER picks (every
  candidate here is a batter HR prop); ranked by the candidate's
  **opposing pitcher's** `recent_era`. Gated on
  `MIN_PITCHER_RECENT_SAMPLE = 3` (of the 5-start window). Multiple
  batters facing the SAME cold pitcher is expected, not deduplicated —
  if a real pitcher is having a real historically bad stretch, every
  batter in that lineup is a legitimately good pick for the same reason.
- **Weather Factors** — ranked by `environment_score`, restricted to
  candidates where Environment is the single highest of the four pillar
  scores (not just "environment score is decent" — genuinely the
  dominant factor). No new data needed for this one.

### Design questions, answered against real data

**1. Candidates per shelf.** `DEFAULT_SHELF_SIZE = 8`. Proposed from real
eligible-pool sizes observed on a real 14-game, 239-candidate slate
(`test_shelf_curation.py`'s fixture):

| Shelf | Real eligible pool (uncapped) |
|---|---|
| `+300-499` | 12 |
| `+500-699` | 38 |
| `Going Nuclear` | 100 |
| Hot Hitters | ~all (most active hitters clear 8 games by late July) |
| Cold Pitchers to Attack | ~all (most opposing pitchers clear 3 starts) |
| Weather Factors | 25 |

8 fits comfortably under even the smallest real pool (`+300-499`, 12)
while still curating meaningfully on the larger pools — on a lighter
slate, `+300-499` may show fewer than 8, which is correct (shows what's
really there, same "don't fabricate" philosophy as the Tasty Six below),
not a bug.

**2. Can a candidate appear in multiple shelves?** Yes, confirmed
deliberate and confirmed nothing breaks. Shelves are computed completely
independently over the full pool — no cross-shelf dedup. On the real test
slate, 6 of the pooled candidates appeared in 2+ shelves (e.g., a real
elite hitter who was both a `+300-499` pick AND a Hot Hitter). Checked for
the failure mode this could hide — near-identical shelves — and confirmed
against real data it doesn't happen: the largest real pairwise overlap
between any two shelves was 2 of 8 entries, not a suspicious majority.

Real edge case this surfaced: on one real test slate, the SAME real player
(Riley Greene) was independently the #1 pick for both `Going Nuclear` and
`Cold Pitchers to Attack` — meaning a naive "just take each shelf's #1"
Tasty Six could show the same player twice. **Decision: the underlying
shelves stay exactly as designed (a candidate can appear in as many
shelves as it legitimately qualifies for — that's still correct and
unchanged), but the Tasty Six specifically enforces six distinct players**
via a deterministic fallback.

**3. Tasty Six** — `compute_tasty_six()`: processes shelves in a fixed
order (the three odds tiers, then Hot Hitters, then Cold Pitchers to
Attack, then Weather Factors — the same order `assign_shelves()` builds
them in every time, so which shelf "wins" a contested player is
deterministic, never incidental to run order). For each shelf, walks down
its own ranked list and picks the first candidate not already claimed by
an earlier shelf in this pass. If a shelf's entire list is already claimed
by earlier shelves — a real edge case on a thin slate — falls back to that
shelf's own #1 as a last resort rather than leaving the slot empty, and
that shelf name shows up in the returned `repeats` list so it's visible,
not silent. A shelf with zero eligible candidates at all still contributes
`None` rather than a fabricated pick (didn't happen on the real test
slate — all six were populated).

Returns `{"picks": {shelf_name: entry_or_None}, "repeats": [shelf_name, ...]}`.

Verified two ways in `test_shelf_curation.py`: (1) on the real current
pool, confirming six distinct real players with an empty `repeats` list;
(2) a deterministic regression check that doesn't depend on today's data
happening to collide — artificially rigs one shelf's #1 to exactly
duplicate another shelf's #1, then confirms the later shelf falls through
to its own #2 instead of accepting the duplicate. This proves the
mechanism itself works regardless of whether real data collides on any
given day.

### Tested against real data, hand-verified — `test_shelf_curation.py`, 21/21 checks

Real pool: 239 real scored picks across 14 real confirmed-lineup games
(built by `scripts/build_shelf_test_pool.py` — real odds + real live-data,
via the existing tested `game_lookup`/`scored_picks` pipeline, no
reimplementation). Checks cover: no empty shelves, odds-tier ranges/ranking
correctness, Hot Hitters/Cold Pitchers sample-size gating and ranking,
Weather Factors' dominant-pillar restriction, multi-shelf membership,
no near-identical shelves, and a fully-populated Tasty Six.

Every themed-shelf finding was hand-verified against independently-pulled
raw MLB Stats API data before being trusted, not just checked for internal
consistency:
- **Hot Hitters #1 (CJ Abrams, recent OPS 1.390, 8 HR/15g)**: independently
  re-pulled his raw game log and confirmed 8 real home runs across 6
  distinct real games in the window — a genuinely exceptional real hot
  streak, not a counting artifact.
- **Cold Pitchers to Attack (Dean Kremer, recent ERA 7.56; Gage Jump,
  recent ERA 8.31)**: both independently re-pulled and hand-computed from
  raw last-5-real-starts data — Kremer's is a real, severe, escalating
  decline (1, 4, 2, 6, 8 earned runs across his last 5 starts, most recent
  worst); both math checks matched the module's output exactly.

### `shelf_assignments` table schema (for the Lovable side)

Same convention as `scored_picks`/`hr_props_raw`: flat, queryable columns.
One row per (candidate, shelf) — deliberately a many-to-many bridge table,
not a column-per-shelf on `scored_picks`, since a candidate can legitimately
belong to several shelves at once (see above).

| Column | Type | Notes |
|---|---|---|
| `id` | uuid / serial | primary key, auto-generated |
| `mlbam_id` | integer | references the player in `scored_picks` |
| `game_pk` | integer | references the game in `scored_picks` |
| `shelf` | text | one of the six shelf names above |
| `rank` | integer | 1-based rank within that shelf |
| `is_tasty_six` | boolean | true for whichever row `compute_tasty_six()` picked for this shelf — usually `rank = 1`, but NOT always: the fallback rule (see below) can make a shelf's Tasty Six pick its `rank = 2` (or later) entry when `rank = 1` was already claimed by an earlier shelf. Explicit, queryable convenience column rather than assuming `rank = 1` client-side |
| `shelf_score` | numeric | the metric value that produced this rank — `final_score` for odds-tier shelves, `recent_ops` for Hot Hitters, the opposing pitcher's `recent_era` for Cold Pitchers, `environment_score` for Weather Factors |
| `shelf_score_label` | text | which metric `shelf_score` actually is for this row (`"final_score"` / `"recent_ops"` / `"opp_recent_era"` / `"environment_score"`) — `shelf_score`'s meaning differs per shelf, so this makes it self-describing without hardcoding a shelf-name-to-meaning mapping on the Lovable side |
| `assigned_at` | timestamptz | when this shelf assignment was computed |
| `created_at` | timestamptz | standard insert timestamp, default `now()` |

**Not wired up yet.** No endpoint deployed for this — `assign_shelves()`
and `compute_tasty_six()` are pure functions, tested standalone against a
real multi-game pool assembled locally (this pipeline has no read access
back to Lovable's `scored_picks` table, same constraint documented for
`scored_picks.py` itself). The real open question for wiring this in:
Make.com would need to accumulate a full day's `scored_picks` across its
per-game Iterator loop (e.g. an Array Aggregator module) and pass the
whole array to a future `/api/curate-shelves`-style endpoint in one call —
mirroring the same "Make.com already has what it needs, don't invent a new
read-permission problem" approach used for `by-event` resolution. Not
built yet, deliberately, per the same "validate standalone first" sequencing.

## Result grading (`api/live_data/grading.py`) — NOT wired to anything yet

The CORE, shared fact-checking primitive: did a given player actually hit
a home run in a given game? `grade_pick(mlbam_id, game_pk)` answers that
one question, real-time, from real MLB data — nothing else. Two separate
systems consume it (see below), but this function itself has no concept
of "official pick" or "user pick" — that separation lives entirely in the
orchestration layer built on top, never in this shared primitive. Pure,
standalone logic — no endpoint, no storage, no Make.com connection yet,
same sequencing as everything else here.

**Reuses the exact same MLB Stats API `feed/live` call `game_data.py`
already makes for pre-game lineups/weather** — just read after the game
concludes instead of before it starts. No new data source.

### Real findings that changed the design from the original spec

1. **The day-level `/schedule` endpoint can go stale for a rescheduled
   game — confirmed, not assumed.** A real game (Atlanta @ NY Mets,
   2026-07-28, `game_pk` 823598) showed `detailedState: "Postponed"` via
   `/schedule`, while `feed/live` for the *exact same* `game_pk`, checked
   moments later, showed `"In Progress"` — the game had been postponed
   from its original date and rescheduled under the same `game_pk`, and
   was actively being played. Grading off a cached schedule snapshot could
   have permanently voided a pick whose game was genuinely still going to
   produce a result. Fixed: `grading.py` always re-fetches `feed/live`
   fresh at grading time, never trusts an earlier schedule-level status.
   Confirmed correct against a second real case: Cleveland @ Cincinnati
   (`game_pk` 824490), also postponed the same day — `feed/live` now
   correctly shows `"Final"` with a complete real box score once its
   doubleheader makeup actually finished.
2. **"Postponed" almost never means "cancelled forever," so the original
   3-state design (won/lost/void) was wrong.** The overwhelming majority
   of real postponements get made up later — voiding immediately on
   "Postponed" would misgrade the common case, not the exception.
   `grade_pick()` returns a **4th status, `"pending"`**, covering
   not-yet-started, in-progress, AND postponed-awaiting-makeup games alike
   — "check again later," never a wrong final verdict. `"void"` is
   reserved for a game `feed/live` itself currently reports as terminally
   Postponed/Cancelled/Suspended (`abstractGameState == "Final"` but the
   game never actually completed real play). This is a deliberate
   refinement of the original spec, flagged rather than silently
   substituted — worth confirming before this gets wired into anything
   that acts on the result.
3. **A player can be on the active roster and get zero plate
   appearances — confirmed real, not hypothetical.** Two real players
   (Hao-Yu Lee, Jeremiah Jackson, both 2026-07-28) were on the game-day
   roster but never batted. Graded as `"void"`, not `"lost"` — matches
   standard prop-betting convention that a player who never played didn't
   get a fair chance to resolve the prop. A player scratched entirely off
   the day-of roster (not just benched) would likely be absent from the
   box score altogether rather than present with 0 PA — handled by the
   same code path, though a fully real example of that specific case
   wasn't found today.
4. **Extra innings and mid-game substitutions needed no special
   handling** — confirmed against real data, not just reasoned about. The
   box score is a running total for whatever the player actually did in
   the whole game regardless of length or when they entered/exited
   (confirmed: a real partial-game substitute with only 2 plate
   appearances graded correctly as a real `"lost"`, not mishandled as
   incomplete data).

### Idempotency

`grade_pick(mlbam_id, game_pk)` is pure and deterministic — no timestamp
in its own output, so grading the same real, truly-final game twice (or a
hundred times) returns byte-identical results. Verified directly in
`test_grading.py`: three repeated real calls compared for exact equality
— the same discipline as the duplicate-submission test that caught a real
upsert bug in `scored_picks` earlier in this project. A future storage
layer stamps its own `graded_at` at write time and should upsert on
`(mlbam_id, game_pk)`, mirroring the same pattern already used for
`scored_picks` and `shelf_assignments`.

### Tested against real data — `test_grading.py`, 13/13 checks

Real wins (2-HR and 1-HR games), real losses (including a real
partial-game substitute), two real "benched all game" void cases, and a
real live confirmation of the pending-vs-void distinction — one game
across two of these real cases was itself a real same-day postponement
that later completed, directly validating finding #1 and #2 together, not
just in isolation. One check is explicitly synthetic and labeled as such:
a genuinely cancelled-forever game wasn't found in real data today (every
real postponement encountered either resolved to Final-played or was
still pending), so that specific branch is verified with a hand-built
status dict rather than a real example.

### Recommended connection for USER-SAVED picks (not built — design only, per this phase's scope)

Same architectural boundary as everywhere else: the pipeline never reads
Lovable's tables directly. Make.com (which *can* query Lovable's
`bookmarks`/saved-picks table) would supply a batch of `{mlbam_id,
game_pk, pick_id}` needing grading to a new endpoint (e.g. `POST
/api/grade-picks`); the pipeline does the real-time MLB lookup and returns
verdicts; Make.com (or an Edge Function) writes results back to Lovable —
mirroring the exact pattern already used for `score-game-props`. **This
is the user-saved-picks path specifically** — see the next section for the
separate official-picks path, which shares `grade_pick()` but nothing
else (no table, no query, no endpoint in common).

**Cadence recommendation**: not a single fixed daily time. Games finish at
very different times through the evening (day games mid-afternoon, night
games as late as 1am local) — a recurring check every 30-60 minutes during
the real game-finishing window fits better than one daily pass, and
mirrors the trigger-based-not-clock-based philosophy the product blueprint
already applies to lineup-confirmation notifications. A pick whose game
grades `"pending"` should simply be left alone until the next scheduled
check — no special retry logic needed beyond "ask again next cycle."

## Official-pick grading (`api/official_pick_grading.py`) — feeds the Performance Tracker, NOT wired to anything yet

Grades every OFFICIAL Tasty Pick Ems pick — everything in `scored_picks`/
`shelf_assignments` — not just a user's individually saved picks. A
**deliberately separate system** from the user-saved-picks grading above,
per an explicit boundary: **official picks and user-saved picks must
never share a table or a query.** That separation is kept at the code
level here, not only the schema level — this module never imports
anything from, or writes anything resembling, a user-picks concept. It's
a genuinely different consumer: potentially 30-40+ picks a day across all
six shelves (confirmed real: 48 shelf appearances across a real 14-game
slate this session), versus a handful of individual user selections.

**Reuses, does not reimplement, the core fact-checking logic.**
`grade_official_picks()` calls the exact same `grade_pick()` from
`api/live_data/grading.py`, unmodified — this module is pure
orchestration on top of it, mirroring how `scored_picks.py` orchestrates
`live_data`/`live_scoring` primitives without reimplementing either.

### The one genuinely new problem this solves: multi-shelf candidates

Confirmed and tested repeatedly this session: a candidate can legitimately
appear on multiple shelves. The Performance Tracker needs a result **per
shelf appearance** (so "Hot Hitters: 12-8" tracks separately from "Going
Nuclear: 3-15" even for the same real player/game) — but the underlying
real-world fact (did this player actually hit a home run) cannot differ
by shelf. `grade_official_picks()` groups the input by `(mlbam_id,
game_pk)`, performs the real MLB lookup exactly **once** per unique pair,
and fans that single result out to every shelf appearance sharing it —
never re-querying MLB's API redundantly, and guaranteeing every shelf
appearance of the same real pick reports an identical verdict, never a
contradiction.

Confirmed against real data (`test_official_pick_grading.py`, reusing the
same real 239-candidate, 14-game pool `test_shelf_curation.py` uses): a
real day's 48 official shelf picks resolved to only 42 unique
`(mlbam_id, game_pk)` real MLB lookups — 6 real candidates each appeared
on 2 shelves (e.g. CJ Abrams: `+300-499` and `Hot Hitters`; Riley Greene:
`Going Nuclear` and `Cold Pitchers to Attack`), and every one of those 6
reported the exact same real verdict across both of its shelf rows.
Real status breakdown that day: 42 lost, 6 won — including real winners
Yordan Alvarez, Cal Raleigh, and Julio Rodríguez — zero errors, zero
pending/void (all 14 real games were genuinely Final).

### Idempotency

Inherits `grade_pick()`'s determinism directly — the orchestration layer
adds no randomness or accumulating state of its own. Verified explicitly:
grading the identical real 48-pick batch twice produces byte-identical
`results`, `errors`, and `unique_games_graded` counts.

### `official_pick_results` table schema (draft — not applied)

A new, standalone table — never `bookmarks`, never merged into
`scored_picks` or `shelf_assignments` directly. Grain matches
`shelf_assignments` exactly (`mlbam_id`, `game_pk`, `shelf`) since that's
the real unit the Performance Tracker needs to track — a candidate's two
shelf appearances get two independent result rows, both computed from one
real MLB lookup (see above).

```sql
CREATE TABLE public.official_pick_results (
  id uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  mlbam_id integer NOT NULL,
  game_pk text NOT NULL,
  shelf text NOT NULL,
  status text NOT NULL,               -- won | lost | void | pending
  home_runs integer,
  plate_appearances integer,
  reason text,
  game_detailed_state text,
  graded_at timestamp with time zone NOT NULL DEFAULT now(),
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT official_pick_results_shelf_assignment_fk
    FOREIGN KEY (mlbam_id, game_pk, shelf)
    REFERENCES public.shelf_assignments (mlbam_id, game_pk, shelf)
    ON DELETE CASCADE
);

GRANT ALL ON public.official_pick_results TO service_role;
ALTER TABLE public.official_pick_results ENABLE ROW LEVEL SECURITY;

CREATE UNIQUE INDEX official_pick_results_uniq
  ON public.official_pick_results (mlbam_id, game_pk, shelf);

CREATE INDEX official_pick_results_status_idx ON public.official_pick_results (status);
```

The unique key matches the natural upsert target for idempotent writes,
same pattern as `scored_picks`/`shelf_assignments`. Not applied — for your
review first, same workflow as the `scored_picks` schema fix.

### Recommended connection (not built — design only, per this phase's scope)

Same boundary as the rest of this pipeline: Make.com (or an Edge Function
with real Supabase read access) supplies the day's `shelf_assignments`
rows to a future endpoint (e.g. `POST /api/grade-official-picks`); the
pipeline grades them and returns results; the caller forwards them to the
new `official_pick_results` webhook via the same signed pattern already
used for `scored_picks`/`shelf_assignments`. Cadence: same 30-60-minute
recurring-check recommendation as user-picks grading above — both
consumers are checking the same real, shared fact (has this game finished
yet?), just applied to two different, never-overlapping datasets.

## Shelf curation orchestrator (`api/curate_shelves.py`) — NOT wired to Make.com yet, depends on two undeployed Lovable routes

`shelf_curation.py`'s logic (see above) needs an entire day's `scored_picks`
at once — every other piece of this pipeline works one game at a time, but
ranking "the best Hot Hitter on the slate" is inherently a cross-game
comparison. The pipeline was deliberately never given `service_role`
access to read the database directly (same least-privilege boundary as
everywhere else here), so this needed its own design decision.

**Chosen: a new signed read endpoint on the Lovable side**
(`POST /api/public/scored-picks-read`), mirroring the existing write
routes' HMAC pattern exactly, rather than either (a) an RLS read policy
grantable to the public `anon` key, or (b) having Make.com accumulate the
day's results in memory across its own execution and never touch the
database until a final write. (a) was rejected because the `anon` key
ships in every client bundle — that's "readable by the entire internet,"
not "readable by this one trusted caller." (b) was rejected because it's
actually the more fragile option against the real failure mode this needs
to guard against: `scored_picks` is already durable and survives a
Make.com scenario failing partway through the day; trusting one long
in-memory run to hold the whole day's state correctly reintroduces the
monolithic execution shape this pipeline has deliberately avoided
elsewhere (per-game scoring, `by-event` resolution).

Two new Lovable-side routes were drafted (staged for review, not yet
applied — same review-before-apply sequence as every other schema/route
change in this project):

- `POST /api/public/scored-picks-read` — same HMAC verification as the
  write routes; body `{"date": "YYYY-MM-DD"}` (optional, defaults to
  today UTC); queries `scored_picks` over a **widened UTC window** (date
  00:00 through the next day at noon UTC), not a naive
  `game_date_utc::date = date` equality check — the same real finding
  from `game_lookup.py` applies here too: a real evening game's UTC
  timestamp can fall on the calendar day after its local game date, and a
  naive equality filter would silently drop those games from "today's"
  slate. Response includes both `row_count` and `distinct_game_pk_count`
  specifically so the caller can sanity-check completeness before
  trusting the data.
- `POST /api/public/shelf-assignments-write` — mirrors
  `scored-picks-write.ts` exactly, upserting on the real
  `(mlbam_id, game_pk, shelf)` unique index. The one meaningful
  difference from `scored-picks-write.ts`'s dedup key: it must include
  `shelf`, not just `(mlbam_id, game_pk)` — a candidate legitimately
  appearing on multiple shelves produces multiple real rows that must NOT
  collide with each other during in-batch deduplication.

### `curate_shelves_for_date(date, secret, read_url, shelf_size=DEFAULT_SHELF_SIZE)`

The orchestrator, pure aside from the one real network call to the read
endpoint: fetches the day's `scored_picks`, sanity-checks the slate isn't
suspiciously incomplete, runs `assign_shelves()` + `compute_tasty_six()`
unmodified, then flattens the result into `shelf_assignments`-shaped rows
(one row per real (candidate, shelf) appearance, `is_tasty_six` set on
exactly the row `compute_tasty_six()` picked for that shelf). Does not
itself forward the result to Lovable — same separation between pure
orchestration and network calls used throughout this pipeline.

**Reliability gate:** `sanity_check_slate()` flags the run (does not
silently curate) whenever the read response reports fewer than
`MIN_EXPECTED_GAMES` (5) distinct `game_pk`s — a real full MLB day is
typically 10-15 games, so a suspiciously low count means an upstream
Make.com scoring run likely failed partway through, and curating shelves
from a broken partial day would be worse than not curating at all.

### `POST /api/curate-shelves`

Reuses the existing `LOVABLE_WEBHOOK_SECRET` (no new secret — same shared
secret every signed route in this pipeline already uses). Target URLs
come from `LOVABLE_SCORED_PICKS_READ_URL` and
`LOVABLE_SHELF_ASSIGNMENTS_WRITE_URL` (Vercel env vars, same pattern as
`LOVABLE_WEBHOOK_URL`/`LOVABLE_SCORED_PICKS_WEBHOOK_URL`), falling back to
the not-yet-real `https://tastypickems.lovable.app/api/public/scored-picks-read`
/ `.../shelf-assignments-write` placeholders if unset.

Body (all optional): `{"date": "YYYY-MM-DD", "shelf_size": 8}` — defaults
to today (UTC) and `DEFAULT_SHELF_SIZE`. Calls the read endpoint, runs
`curate_shelves_for_date()`, forwards the resulting rows to the write
endpoint via the same `lovable_forward.forward_to_lovable()` every other
route already uses. Returns `422` (not curated, not forwarded) if the
sanity check flags the slate; `502` if forwarding to the write endpoint
fails; `200` on a genuine success. NOT wired into any Make.com scenario
yet — deployed and independently callable once the two Lovable routes it
depends on are live, but that connection is a deliberately separate step.

### Tested against real data, no mocked HTTP — `test_curate_shelves.py`, 18/18 checks

Neither Lovable route is deployed yet, so there's no live endpoint to
round-trip against. Rather than mock `requests.post` or stub out
signature verification, the test spins up a genuine local Flask server in
a background thread implementing the exact same HMAC-SHA256
signature-verification logic as the two drafted TypeScript routes, and
serves/accepts the real cached 239-candidate/14-game pool
(`/tmp/shelf_test_pool.json`, same one `test_shelf_curation.py` and
`test_official_pick_grading.py` use). This exercises the real signed HTTP
round-trip in both directions, not just the orchestration logic in
isolation:

- Full chain output (shelf sizes, Tasty Six repeats, row counts, shelf
  names) matches `shelf_curation.py`'s own direct output exactly.
- Exactly one `is_tasty_six=True` row per shelf with a real pick, drawn
  from distinct `(mlbam_id, game_pk)` pairs — a real deduplicated Tasty
  Six survives the read → curate → write path.
- The write endpoint genuinely receives every curated row over the wire.
- Idempotent — the same real input curated twice produces byte-identical
  output.
- A deliberately truncated slate (2 of the real 14 games) is flagged and
  aborted, not silently curated — and the `MIN_EXPECTED_GAMES` boundary
  itself is checked on both sides (5 games: not suspicious; 4: suspicious).
- A wrong shared secret is genuinely rejected with a real HTTP 401 by the
  signature-verification code, not assumed to fail by code review alone.

## Official-pick grading, live (`api/grade_official_picks_live.py`) — NOT wired to Make.com yet, depends on two undeployed Lovable routes

The last piece connecting `official_pick_grading.py` (built earlier, already
validated against real data) to a live source of "which official picks
actually need grading right now." Grading runs on its own schedule — after
games finish, not when they're scored — so it can't reuse in-memory data
from the scoring/curation loop; it needs to independently determine what's
ungraded.

**"Needs grading" is an anti-join, not a game-status pre-filter.** A pick
needs grading iff its `shelf_assignments` row has no matching
`official_pick_results` row on the real `(mlbam_id, game_pk, shelf)` unique
index. Deliberately NOT pre-checking which games have finished before
querying — `grade_pick()` (in `live_data/grading.py`, reused unmodified)
already does a fresh `feed/live` lookup per unique game and returns
`status: "pending"` for anything not yet final, so a separate upfront
game-status check would just be a second live MLB lookup for information
grading already produces as a side effect. Instead: pull every ungraded
shelf pick regardless of the underlying game's status, grade all of them,
and only forward the TERMINAL results (`won`/`lost`/`void`) to the write
step. A still-in-progress game simply produces no `official_pick_results`
row this run — meaning it's still "ungraded" next run, with no separate
tracking needed. That's the same idempotency mechanism as the anti-join
itself: nothing marks a pick as "attempted," only "graded."

**A dedicated read endpoint, not an extension of `scored-picks-read.ts`.**
Different grain (`shelf_assignments` rows, not `scored_picks` rows) and a
fundamentally different filter (an anti-join against a second table, not a
date window) — reusing the HMAC/verification boilerplate is right, reusing
the query shape would not be.

Two new Lovable-side routes were drafted (staged for review, not yet
applied):

- `POST /api/public/picks-needing-grading-read` — body
  `{"lookback_days": 3}` (optional, defaults to 3). Fetches
  `shelf_assignments` rows and `official_pick_results` keys within the same
  bounded lookback window, builds the anti-join in application code (two
  plain `supabase-js` selects + an in-memory filter, not a raw SQL
  `NOT EXISTS` — cheap at real volume, and keeps this route a plain
  `supabase-js` call like every other route in this codebase). Response
  includes `total_shelf_assignments_in_window` and `already_graded_count`
  for observability. Deliberately has no minimum-count sanity gate like
  `curate_shelves.py`'s — a grading batch of 0 is a normal, expected
  outcome here, not a sign something broke.
- `POST /api/public/official-pick-results-write` — same fixed reporting
  discipline as the `shelf-assignments-write.ts` patch (`received` is the
  true pre-filter count; a row with an invalid `mlbam_id` is dropped and
  named with a reason, not silently excluded). `status` is a strict
  `z.enum(["won", "lost", "void"])` — a stray `"pending"` fails the WHOLE
  batch loud with a 400, rather than being silently dropped per-row like a
  bad `mlbam_id`. Different failure category: a malformed `mlbam_id` is
  expected real-world messiness; a `"pending"` status arriving here means
  the caller has a bug, since `grade_official_picks_live.py` should never
  forward a pending result by design.

### `grade_official_picks_for_pending(secret, read_url, write_url, lookback_days=None)`

The orchestrator: fetches picks needing grading, runs
`official_pick_grading.grade_official_picks()` unmodified (real MLB
lookups, deduplicated per unique game, fanned out per shelf appearance),
filters to terminal results only, forwards them. Treats "zero picks need
grading" and "some picks are still pending" as normal outcomes, not
errors — unlike `curate_shelves.py`, there's no minimum-batch-size gate
here.

### `POST /api/grade-official-picks`

Body (all optional): `{"lookback_days": 3}`. Reuses the existing
`LOVABLE_WEBHOOK_SECRET`. Target URLs come from
`LOVABLE_PICKS_NEEDING_GRADING_READ_URL` and
`LOVABLE_OFFICIAL_PICK_RESULTS_WRITE_URL` (Vercel env vars, same pattern as
the other Lovable URL env vars), falling back to the not-yet-real
`https://tastypickems.lovable.app/api/public/picks-needing-grading-read` /
`.../official-pick-results-write` placeholders if unset. Returns `502` if
the read endpoint is unreachable or forwarding fails; `200` on success
(including when there was nothing to grade). NOT wired into any Make.com
scenario yet.

### A REAL BUG this work caught: `grade_pick()` only ever worked with an `int` `game_pk`

`shelf_assignments.game_pk` is a real `text` column, so a genuine live
caller reading from it hands `grade_pick()` a numeric STRING. Every
existing call site (`test_grading.py`, `official_pick_grading.py`) only
ever passed a Python `int`, so this went uncaught until real data actually
flowed through the new live chain: `_fetch_feed_live()`'s defensive check
(`data.get("gamePk") != game_pk`, guarding against MLB's real behavior of
returning a placeholder body instead of a 404 for an unknown `game_pk`)
compares against the JSON response's integer `gamePk` — a string `game_pk`
always failed that comparison, so every real grading call through this new
chain would have incorrectly reported a genuinely valid game as "not a
known MLB game." Fixed by coercing `game_pk = int(game_pk)` once at the top
of `grade_pick()` (`live_data/grading.py`), rather than loosening the
equality check itself — keeps the placeholder-response defense exact while
making the function tolerant of either caller convention.

### Tested against real data, with a stateful test double — `test_grade_official_picks_live.py`, 19/19 checks

Reuses the exact real, hand-verified `(mlbam_id, game_pk)` pairs
`live_data/test_grading.py` already validated against real MLB box
scores — real players, real completed games, real `grade_pick()` calls —
rather than the cached odds-derived `shelf_test_pool.json` (expired from
`/tmp`; regenerating it spends real, budgeted Odds API requests that
grading itself doesn't need, since grading only ever reads
`mlbam_id`/`game_pk`/`shelf` off a pick).

The part neither `test_grading.py` nor `test_official_pick_grading.py`
proves: the anti-join itself. Both already prove `grade_pick()`/
`grade_official_picks()` are individually idempotent (same input -> same
output). Neither proves the LIVE CHAIN avoids re-grading something already
graded — that guarantee lives entirely in the Lovable-side read endpoint's
query, which isn't deployable code yet. So this test spins up a STATEFUL
local Flask double — an in-memory `shelf_assignments`/`official_pick_results`
pair that the read endpoint genuinely queries and the write endpoint
genuinely mutates — because a stateless double could confirm the write
step upserts correctly without ever proving the read step's exclusion
logic does anything at all:

- A real batch of 10 picks (9 unique real games/players, including one
  player on two real shelves for the same game) is graded through the full
  read → grade → write chain with zero grading errors.
- The two real shelf appearances for the same real multi-shelf player
  report an identical verdict — the one genuinely new guarantee
  `official_pick_grading.py` exists for, now proven through the live chain.
- **The key check**: a second read call after grading excludes exactly the
  picks just graded — real anti-join correctness, not just a clean
  re-upsert.
- Write-step idempotency: re-sending the same terminal results doesn't
  duplicate rows.
- Full-chain idempotency: running the whole chain again only sees the
  still-pending remainder.
- A synthetic invalid `mlbam_id` is dropped and named, not silently
  swallowed; a synthetic stray `"pending"` status is rejected with a real
  400 for the whole batch.
- A wrong shared secret is genuinely rejected with a real HTTP 401.

## Deployment

Auto-deploys on every push to `main` via Vercel's GitHub integration
(Root Directory: `pipeline`). No manual deploy step needed — just push.
Environment variable changes need a new deployment to take effect for
already-running functions — push something (even trivial) after adding or
changing one. `api/live_scoring/` has no route wired up yet (it's a
function other endpoints call into, not deployed standalone) — see the
section above. `api/live_data/` and `api/scored_picks.py` DO have live
routes (`/api/live-data/game/<game_pk>`, `/api/score-game-props/game/<game_pk>`)
once this is pushed, but neither is called from anywhere yet — no Make.com
scenario is configured to hit either.

## Local development

```bash
cd pipeline
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd api
python3 test_flatten_hr_props.py     # flatten/filter test suite
python3 test_malformed_input.py      # defensive-parsing test suite
python3 test_lovable_forward.py      # signing + forwarding test suite
python3 test_scored_picks.py         # name-matching + full orchestration test suite — real data, no mocks
python3 test_game_lookup.py          # game_pk resolution test suite — real data, no mocks
python3 ../scripts/build_shelf_test_pool.py  # regenerates the real pool test_shelf_curation.py reads (spends real Odds API requests — not automatic)
python3 test_shelf_curation.py       # shelf curation test suite — real pool, no mocks
python3 test_official_pick_grading.py  # official-picks batch grading test suite — reuses the same real pool, no mocks
python3 test_curate_shelves.py       # shelf curation read->curate->write chain — real pool, real local HMAC server, no mocks
python3 test_grade_official_picks_live.py  # official-pick grading live chain — real games, stateful HMAC double proving the anti-join, no mocks
cd live_scoring
python3 test_score_candidate.py      # live single-candidate scoring test suite
cd ../live_data
python3 test_live_data.py            # live-data test suite — hits real APIs, no mocks
python3 test_recent_form.py          # recent-form test suite — real player game logs, no mocks
python3 test_grading.py              # result-grading test suite — real completed games, no mocks
cd ..
FLASK_APP=index.py python3 -m flask run --port 5099   # run locally
```
