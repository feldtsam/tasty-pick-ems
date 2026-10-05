# CFB v1 Universal TPE Score — Design Spec
**Status:** Design complete for all 5 pillars. **Final pillar weights LOCKED. Cold-start UI decision LOCKED.** This document is fully build-ready for Claude Code.
## 0. Final locked weights
| Pillar | CFB v1 weight |
|---|--:|
| TD Opportunity | **53%** |
| Situation | **35%** |
| Role & Momentum | **12%** |
| Evidence Quality & Convergence | *applied as confidence multiplier, not part of this split* |
| Market Value | *deferred to v2* |
Derivation: Role & Momentum set directly to 12% (deliberately weaker pillar, not proportionally scaled). Remaining 88% split between TD Opportunity and Situation proportional to their original NFL weights (30:20 → 3:2 ratio), since both were audited as strong. Evidence Quality sits outside this additive split entirely — it's applied afterward as `tpe_score = core_score × confidence_multiplier`, so it never competes for weight here (this corrects an earlier draft that mistakenly treated it as an additive slice).
**Situation sub-scope (v1):** Situation ships its **defensive-matchup sub-component only** — `situation = defensive_matchup_vulnerability`. Its Environment sub-component (dome / wind / temp / heat-index) is **deferred to v2 alongside Market Value** — flagged, not silently dropped (see §3). `situation` is blended through the same present-components renormalization `score_universal_tpe` uses for Market Value's absence from `core_weights`, so it renormalizes to DMV cleanly and the 70/30 blend activates automatically once Environment is built. The 35% Situation weight is unchanged by this — Defensive Matchup Vulnerability was the audited-strong sub-component and carries the pillar alone in v1.
**Known accepted risk:** TD Opportunity alone decides ~53% of every CFB score — a real concentration, consciously accepted rather than capped lower, as a consequence of Market Value's deferral and Role & Momentum's deliberate down-weighting.
**Companion investigation reports (Claude Code artifacts):**
- CFB Market Value Findings
- CFB Stats Sourcing Options
- CFB Situation Pillar Audit
- CFB Evidence Quality Audit
- CFB Environment weather source (NWS) — 2026-09-04, v2 Environment sub-component data path
---
## 1. Pillar-by-pillar status
| Pillar | NFL weight | CFB status | Data source |
|---|--:|---|---|
| TD Opportunity | 30% | **Strong — clear go** | CFBD `/plays/stats`, filtered by `gameId` |
| Situation | 20% | **Built — v1 = defensive-matchup only** (§3); Environment sub-component **deferred to v2** alongside Market Value | Same `/plays/stats` ingest as TD Opportunity (grouped by `opponent`+`position` instead of ball-carrier). Environment (v2): weather source investigated 2026-09 — IEM ASOS / NOAA NCEI, not `/venues`+Open-Meteo |
| Evidence Quality & Convergence | 10% | **Mechanically strong** (meta-layer, no independent data) — inherits cold-start effects from the pillars it aggregates | N/A — pure computation over other pillars' outputs |
| Role & Momentum | 20% | **Redefined + built** (§4) — 2 scored trend inputs (touch-share trend 70%, PPA trend 30%) + a returning-player completeness modifier | CFBD `/games/players`, `/ppa/players/games` |
| Market Value | 20% | **Deferred to v2** — 0/90 games had player props at 4-6 days out (2 sweeps, 2026-08-30); decisive re-poll needed Sep 1-3 within 2 days of kickoff | The Odds API `americanfootball_ncaaf` |
**v1 ships 4 pillars.** `market_value_score` dropped from `core_weights`; `score_universal_tpe` renormalizes per-row (already-existing behavior, one-line config change). Final weights are locked — see §0.
---
## 2. TD Opportunity (30% in NFL) — data ports; two early-season calibration fixes
Confirmed feasible on CFBD. Red-zone touches are derivable (not pre-aggregated) from `/plays/stats` — per-athlete rows with `athleteId` + `statType` + `yardsToGoal`. `/plays/stats/types` confirms `Target` (id 2) and `Reception` (id 5) exist as distinct types, so end-zone target share is buildable including incompletions. Box scores (`/games/players`) do not carry targets — ingest must be `/plays/stats`-based. Filter by `gameId` to dissolve the 2,000-record/call cap (~200 rows/game).

**Scoring math changed from NFL in three places (2026-09, from the real-data weeks-1-8 validation):**
1. **Shrinkage `k` 6 → 12.** NFL-tuned k let a 2-for-2-goal-line game percentile-rank ~95th on a 2-touch sample. CFB players accumulate RZ touches far slower. Applies to `proven_heat`'s conversion rate and DMV's conversion-rate-allowed (DMV intuition re-validated, unchanged).
2. **Pillar-wide cold-start gate.** Until a player's cumulative RZ touches *through the prior week* reach `min_cumulative_rz_touches_for_rate` (=15, matching `min_rz_touches_for_qualification`), the row contributes **nothing**: every one of the 10 percentile inputs — `proven_heat`'s rates AND `emerging_heat`'s trends — is forced to neutral-50, `proven_heat`/`emerging_heat`/`td_opportunity` are exactly 50, completeness is 0. Same pattern as Role & Momentum's cold start. (An earlier version gated only `proven_heat`; the weeks-1-8 run showed the identical 1–3-touch backups then re-surfaced at the top via `emerging_heat`, because a trend on a handful of touches ranks just as high.) The output row carries `td_opportunity_gated` (bool), `cum_rz_touches_prior`, and `player_games_played` for filtering.
3. **FBS-opponent filter** (`scoring.drop_non_fbs_opponent_rows`, `ids.fbs_team_ids`). The caller drops rows where either team isn't FBS *before* scoring, so FCS blowout stats never enter a player's rolling windows or the percentile reference. Weeks 1–3 are ~25–33% FCS-opponent rows; negligible from week 5.

**Verified (2025 wk1–8, post-gate):** the small-sample flukes are gone from a score-sorted view — Wayshawn Parker / La'Vell Wright / Anderson Castle / London Montgomery all stay `td_opportunity == 50.0` every week (none clears 15 cumulative RZ touches). Real workhorses (Love, Pare, Allen, Martin) are gated while thin, then land top-5 the week they clear. Nicholas Singleton stays gated all season (max 11 cumulative RZ touches — PSU's RZ carries go to Kaytron Allen, who does clear) — the accepted tradeoff for a low-volume high-conversion back.

**Consequences to keep in mind:**
- **The pillar is inert for weeks 1–4** (≈100% of RBs gated; ~83% still gated at week 8). With Role & Momentum also cold-started, CFB September scores lean almost entirely on Situation.
- **Among *ungated* rows, `emerging_heat` (usage *trajectory*) still dominates the combine for low-conversion backs** — a high-RZ-volume back who rarely scores can rate ~75 off a rising touch trend (the `max(proven, emerging)` combine). Defensible for an *opportunity* pillar, but "TD Opportunity" measures opportunity *momentum* more than *level*. `corr(td_opportunity, rz_touches)` among ungated rows is ~+0.3 at week 8 — improved, not strong.

## 3. Situation (20% in NFL) — v1: defensive-matchup only; Environment deferred to v2
**v1 ships the defensive-matchup sub-component only:** `situation = defensive_matchup_vulnerability`. NFL's full form is `0.7·defensive_matchup_vulnerability + 0.3·environment_score`; the **Environment sub-component (dome / wind / temp / heat-index) is deferred to v2 alongside Market Value (§6)** — flagged here, not silently dropped.

Defensive Matchup Vulnerability derives from the *same* `/plays/stats` ingest TD Opportunity needs, grouped by `opponent` + `position` instead of by ball-carrier — one ingest, two pillars. Math is `nfl/scoring.py::score_situation`'s defensive-matchup block verbatim (percentile fn, shrinkage `k`=12, qualification at `min_touches_allowed_for_qualification`=20).

**Combination mechanism (built now, in `cfb/scoring.py::score_defensive_matchup_cfb`):** `situation` is combined through `CONFIG["situation"]["sub_weights"]` = `{defensive_matchup_vulnerability: 0.7, environment_score: 0.3}` using the **same present-columns renormalization `score_universal_tpe` applies to `market_value_score`'s absence from `core_weights`** — *not* a hardcoded weight-1.0 pass-through. With no `environment_score` column, DMV's 0.7 weight renormalizes 0.7/0.7 → 1.0, so `situation == defensive_matchup_vulnerability` exactly. Adding an `environment_score` column in v2 activates the 70/30 blend with **no code change**. `situation_completeness == defensive_matchup_completeness` in v1 (no environment input referenced anywhere); in v2 it becomes `mean(7 dmv inputs + 1 env input)`, matching NFL's 8-input form.

### 3.1 v2 Environment sub-component — weather source (investigated 2026-09, real API calls)
Deferred, but the data path is scoped so v2 isn't a cold start:
- **`api.weather.gov` (NWS) cannot backfill** — forecast + a rolling ~7-day observation window only; no historical archive endpoint. Rejected as the backfill source.
- **NOAA NCEI** (`local-climatological-data` / `global-hourly`, no token): 2023 and 2024 seasons fully archived with temp + RH (LCD) or temp + dew point (global-hourly). **But the ISD archive runs ~12 months behind** — as of 2026-09 it has *nothing* for the 2025 season (cutoff ~2025-08-27, confirmed across stations). Usable for 2023–24 backfill only until the lag catches up.
- **Iowa State IEM ASOS archive** (`mesonet.agron.iastate.edu`): same raw NWS/FAA METAR stream, near-real-time, decades deep. Verified end-to-end on the standard test case (Alabama @ FSU, Doak Campbell, 2025-08-30): 19:53Z → 85 °F, dew 70 °F, **RH 61%**, wind 6 kt → **NWS Heat Index ≈ 89 °F**. Public-domain data, soft rate limit (fine for a weekly pull with ~1–2 s throttle). Single university service — best-effort availability, not an SLA.
- **NWS Heat Index (Rothfusz) needs only temp °F + RH %** — available on every path (RH direct from IEM/LCD; from dew point on global-hourly).
- **Recommended v2 split:** NCEI LCD for 2023–24 backfill, IEM ASOS for the 2025 season + ongoing weekly post-game pulls. Venue→station via `api.weather.gov/points/{lat,lon}` (nearest station 2–7 mi for every venue tested, rural included) or NCEI `search/v1` bbox. Dome/retractable state still comes from CFBD `/venues.dome` + game-time roof status, not weather.
- Full detail: **"CFB Environment weather source (NWS)" investigation artifact**, 2026-09-04.
---
## 4. Role & Momentum (20% in NFL) — REDEFINED for CFB
### 4.1 Why NFL's version can't port
NFL's `role_momentum` has four inputs: `snap_share_trend`, `depth_chart_movement`, `external_opportunity`, `touch_share_trend`. CFBD/free-source audit found:
| Input | CFB availability |
|---|---|
| `snap_share_trend` | **Unavailable** — no CFB snap-count data exists anywhere free |
| `depth_chart_movement` | **Unavailable** — ESPN's unofficial API confirmed CFB depth charts return `400 "not supported for college-football"` (tested directly; works for NFL, not CFB) |
| `external_opportunity` | **Unavailable** — needs both of the above plus a CFB injury feed; ESPN CFB rosters have only weak `injuredReserveOrOut`/`suspended` flags, keyless |
| `touch_share_trend` | **Survives** — derivable from `/games/players` box scores, same as NFL |
Verdict from the original audit: this pillar needs to be **redefined**, not re-pointed at a new source. Do not port `role_changes.py` as-is.

### 4.2 New input set — LOCKED (2026-09), built in `cfb/scoring.py::score_role_momentum_cfb` + `cfb/role_momentum.py`
Two scored trend inputs + one completeness modifier. `usage_share_weekly` from an earlier draft collapsed into `touch_share_trend` (both are "share of the offense's touches" — one signal). PPA feasibility re-audited via real CFBD calls (both inputs Class A on a shared `athleteId` key).

| Input | Weight | Source | Notes |
|---|--:|---|---|
| `touch_share_trend` | **70%** | `/games/players` box scores (`CAR` + `REC`) / team's total CAR+REC (QB keepers in the denominator), trended | Direct port of NFL's proven signal; the one input with an NFL track record, hence the majority weight |
| `ppa_trend` | **30%** | `/ppa/players/games` `averagePPA.all`, trended (1 whole-slate call/week) | CFB-native efficiency axis, no NFL precedent. `NaN` where CFBD attributed no PPA (~3% of player-games, almost all 1-touch cameos) |

```
role_momentum = 0.70 · pctile(touch_share_trend)
              + 0.30 · pctile(ppa_trend)
```
Both trends are last-3-game mean vs season-to-date (the shared `_trend_delta`). **Renormalization fallback:** if a player has *no* prior-game PPA at all when the trend is needed, the PPA term is dropped and `touch_share_trend` carries 100% weight for that row — same clean-degrade pattern as `score_universal_tpe`'s per-row pillar renorm. Verified rare (0 of 104 rows on a full real team-season).

**Dropped for CFB, not proxied:** `depth_chart_movement`, `external_opportunity` — no viable free data path, and PPA does not conceptually substitute for opportunity context. This dimension is cut rather than filled with a weak proxy.

### 4.3 Cold start — both inputs are trends, so the pillar is dark for a player's first ~4 games
`_trend_delta` masks to `NaN` until a player's `(window+1)`th game (window = 3). So real `role_momentum` values appear only from a player's **5th game played** (bye weeks don't count) — before that every row is neutral-50 with `role_momentum_completeness ≈ 0`. This is honest (`track_fallback` reports it), the score doesn't drag the core score (it sits at 50, contributing nothing), and it's a 12% pillar that Evidence Quality further down-weights while completeness is low. Replaces the earlier universal weeks-of-data weight gate.

### 4.4 `role_momentum_completeness`
`input_realness × team_history_factor`, both 0–1, × 100:
- **`input_realness`** — weight-proportional realness of the two trend inputs: `0.70·(touch_share_trend real) + 0.30·(ppa_trend real)`. 1.0 = both real, 0.70 = PPA renormed/absent, 0.0 = thin-history early-season row.
- **`team_history_factor`** — `1.0` for a returning/established player; `new_team_completeness_factor` (config, default **0.75**) when the player has **no prior-season box-score row for this team in any stat category** (`is_returning == False`). Transfer-in and true freshman are deliberately conflated — both a genuinely thin CFB-team sample, and the flag touches completeness ONLY, never the score.
- **`is_returning`** is derived from box-score id-continuity in `cfb/role_momentum.py::prior_season_team_athletes` — **not** a `/player/portal` or `/player/search` call (both evaluated in the 2026-09 feasibility audit and passed over; the id-continuity proxy is Class A at zero marginal API cost — the prior-season box scores are pulled anyway for the trend baseline).
---
## 5. Evidence Quality & Convergence (10% in NFL) — reference only, not redefined
No independent data feed — pure meta-layer over the other pillars' `*_completeness` and family-score outputs. Ports mechanically unchanged (Class A) except two card booleans (`signal_convergence`, `signal_breach`) whose thresholds were reverse-engineered from NFL's historical backfill and need re-derivation from a CFB backfill before shipping (Class B) — **hard-gated**, do not ship these two booleans for CFB until a tuned backfill exists.
```
completeness = mean(td_opportunity_completeness, role_momentum_completeness, situation_completeness)
convergence  = 100 − range(present family_scores)   # direction-agnostic
evidence_quality = sqrt(completeness × convergence)
confidence_multiplier = 0.5 + 0.5 · (evidence_quality / 100)
tpe_score = core_score × confidence_multiplier
```
### 5.1 Compounding cold-start effect
Two independent mechanisms both suppress scores in CFB's early weeks:
1. **Lagged completeness windows** (Evidence Quality audit): every `*_completeness` sub-input needs `last3`/`last5` week windows to fill. Week 1 → completeness ≈5–15 → evidence_quality ≈10–35 → confidence multiplier ≈0.55–0.68 (near the 0.5 floor) for essentially every pick.
2. **Role & Momentum's universal weeks-of-data gate** (§4.3, new): compounds the above specifically for Role & Momentum, which reads as *fully excluded* (not just noisy) in Week 1.
**Net effect: TPE scores compress toward `~0.6×core_score` across the board for roughly the first 4 weeks of the CFB season**, self-correcting by ~Week 5 as windows fill (multiplier climbs to 0.85–0.92). This is fine for *ranking* picks relative to each other within a week, but understates *absolute* conviction — especially misleading if a user compares a Week 1 CFB score against an NFL score from a week where NFL's own windows are already full.
### 5.2 Cold-start UI decision — LOCKED (2026-08-30)
- **Primary displayed score during early season: `core_score`** (uncompressed, pre-confidence-multiplier), not `tpe_score`. Rationale: `tpe_score` is honest but misleadingly low/confusing without context in Weeks 1–4; `core_score` is a fairer ranking signal while evidence accumulates.
- **UI treatment: tooltip/info icon only, no badge or label.** Explains why the displayed score differs from the full evidence-adjusted score.
- **Switch trigger: dynamic per-player-week, not a fixed week cutoff.** Once a given player-week's actual `evidence_quality` crosses a threshold (**~40**, starting estimate — not yet backtested against real CFB data), that card switches to displaying `tpe_score` as primary instead of `core_score`. This is deliberately per-player rather than a global "Week 5+" cutoff, since accumulation rate can vary player to player (byes, missed games, etc.) — consistent with the standing principle of reporting the real number rather than forcing a uniform outcome.
- `tpe_score` and `evidence_quality` continue to be computed and stored for every player-week regardless of what's displayed — this is a display-layer decision only, not a change to the underlying scoring pipeline.
- **Open validation item:** the 40 threshold is an estimate based on the Evidence Quality audit's projected Week-1-to-Week-5 curve, not backtested against real CFB `evidence_quality` distributions (none exist yet). Revisit once real CFB season data accumulates.
---
## 6. Market Value (20% in NFL) — deferred to v2
Two independent blockers found, not one:
1. **Zero prop liquidity** at the lead times tested so far (4–6 days out): 0/90 games, two sweeps, both ranked and non-ranked games affected equally. CFB books are known to post props on a compressed Thu/Fri/gameday cadence — decisive test (Sep 1–3, within 2 days of kickoff) not yet run.
2. **Identity/matching layer hard-bound to `nfl_data_py`**: `fetch_week_events` matches teams via `nfl.import_team_desc()`, `match_attd_players` matches via NFL rosters — both return zero matches for CFB regardless of liquidity. This must be rebuilt (CFB schedule + team map + roster source) before any `market_value_score` can compute, independent of the liquidity question.
Response shape from The Odds API is byte-identical to NFL's — `parse_attd_event` would run unchanged once identity matching is solved.
**v2 path, contingent on the Sep 1–3 re-poll:** if that poll shows ranked-games-only coverage, build the CFB identity layer and scope Market Value to marquee games at a reduced weight (~10% suggested, not finalized).
---
## 7. Situation → Environment sub-component (0.3 of NFL Situation) — deferred to v2
Same status as Market Value: **built around, not built.** v1 Situation is `defensive_matchup_vulnerability` alone; `score_defensive_matchup_cfb` already combines `situation` through a present-components renormalization (§3), so no `environment_score` column means `situation == defensive_matchup_vulnerability` with zero special-casing, and a v2 `environment_score` column activates the 70/30 blend with no code change.

**Why deferred:** not a data blocker (unlike Market Value) — a scope call. Environment is a 0.3 sub-weight of a 35% pillar (~10.5% of core), the weather-source path needed its own real-API investigation (done 2026-09-04, see §3.1), and heat-index scoring logic + a 2023–25 backfill validation is a self-contained build better done as one v2 unit than bolted on now.

**v2 build, when picked up:** IEM ASOS (2025 + weekly) + NOAA NCEI LCD (2023–24 backfill) → temp + RH → NWS Heat Index (Rothfusz); dome/retractable state from CFBD `/venues.dome` + game-time roof. Full source detail in §3.1 and the "CFB Environment weather source (NWS)" artifact. NFL's `_environment_score` (deterministic wind/temp, cold-only) is a starting reference but its heat-blindness is exactly the gap CFB Environment exists to close — expect a redefinition, not a port.
---
## 8. Implementation parameters (added 2026-08-30, in response to Claude Code's pre-Step-1 blocking questions)
**Direct ports from NFL (reuse exactly, no change):**
- Rolling windows/weights: `last1/3/5/season_avg` = `.35/.30/.20/.15`
- Bands: RZ ≤20 / i10 ≤10 / GL ≤5
- Recency weights for `allowed_rz_tds_last{1,3,5}`/`season_avg`: same as NFL
- `min_rz_touches_for_qualification`: **15** (NFL's value, reused for v1 — no CFB backtest data yet to justify a different number)
- Shrinkage constant `k`: **6** (NFL's value, reused for v1 — audit flagged CFB likely wants it higher, but no CFB data exists yet to derive a real number; flag for recalibration once Week 5+ CFB data accumulates)
- `min_touches_allowed_for_qualification`: **20** (NFL's value, reused for v1, same reasoning)
**TD Opportunity's `snap_share` sub-input:** left as a **permanent structural fallback** (contributes neutral-50, tracked via `track_fallback`) rather than dropped or substituted — no CFB snap data exists anywhere free. This is already reflected in the Evidence Quality audit's finding that `td_opportunity_completeness` is structurally capped ~90% all season for CFB.
**Identity:**
- `player_id` = CFBD `athleteId`
- Team identity = CFBD's stable integer team `id` (not the `school` string) — preserves the identity-model advantage flagged in the Situation audit
**Storage/code location:**
- Supabase migrations: `tastypickems/supabase/migrations/`
- Pipeline code: new `cfb/` directory in `tasty-pick-ems/`, mirroring `nfl/`
- Table schema (typed core columns vs. `extra jsonb`): **left to Claude Code's Step 1 proposal**, using `nfl_player_redzone_weekly` as the template — not pre-specified here by design, consistent with the project's confirm-first pattern
**Trigger mechanism (v1):** manually-triggered endpoint only, mirroring how NFL Intelligence's `/api/generate-and-write-intelligence` was built and tested before any Make.com wiring existed. Formal scheduling is a later phase, not part of this build.

### 8a. Touch definition & TD attribution — amended 2026-08-31 after the first live CFBD smoke test

The original §8 rules (`Rush` + `Target` for touches; `statType='Touchdown'` join for TDs) were written from the CFBD *OpenAPI schema* plus the earlier verification round, before any aggregation had been run against a real week. The first live `preview_only` run (2026 Week 1, 8 completed FBS games, 1,510 `/plays/stats` rows) showed **both rules are wrong against how CFBD's `/plays/stats` data is actually shaped**. Corrected rules below; the reasoning is recorded so a future session doesn't "restore" the original wording.

**CFBD stat-type quirk 1 — `Target` means "targeted on an *incomplete* pass", not "all targets".** Live `statType` counts for those 8 games:

| `statType` | count | what it actually is |
|---|--:|---|
| `Rush` | 454 | a rush attempt by the ball carrier |
| `Reception` | 301 | the receiver on a **completed** pass |
| `Target` | 157 | the receiver on an **incomplete** pass |
| `Completion` / `Incompletion` | 308 / 165 | the QB's side of those same pass plays |

`Reception` (301) *exceeds* `Target` (157) — impossible if `Target` meant every target. So `Rush` + `Target` (original §8) silently dropped **every completed catch** — a red-zone pass-catching back or a TE who scores on a 4-yard reception would be invisible. To recover the original intent ("opportunity regardless of completion"), a pass target is `Target` **+** `Reception`.

- **Touch = `Rush` + `Target` + `Reception` stat rows.**
  - `*_rush_touches` = count of `Rush` rows
  - `*_target_touches` = count of `Target` + `Reception` rows (all pass targets, complete or not)
  - `*_touches` = sum of the two
- A `Reception` row and its matching `Completion` row are the same play from two sides — only the receiver-side row (`Reception`) is a touch; the QB-side rows (`Completion` / `Incompletion` / `Target`-when-it's-a-QB… n/a) are never touches.

**CFBD stat-type quirk 2 — `statType='Touchdown'` essentially does not exist in `/plays/stats`.** Only **3** `Touchdown` rows across all 8 games (~35–40 real offensive TDs were scored). There is no `Rushing Touchdown` / `Receiving Touchdown` stat type either. The `Touchdown` rows that *do* appear are correct when present (2 of the 3 matched an offensive touch; the 3rd was a 68-yard defensive return, correctly excluded) — the type is just almost never emitted. Unusable as the TD source.

- **TD source = the `/plays` endpoint.** Each `/plays` row carries `scoring: boolean`, `playType` (e.g. `"Rushing Touchdown"`, `"Passing Touchdown"`), `id` (the same play id `/plays/stats` rows carry as `playId`), and `offense`/`defense`.
- **TD attribution:** build the set of `playId`s where `scoring == true` and `playType` is an **offensive** touchdown type (`Rushing Touchdown`, `Passing Touchdown` — *not* the return/recovery/block types, which are defensive/ST scores; the live `/plays/types` list was checked and those are the only two offensive scrimmage-TD types). **Then intersect that set with the `playId`s that actually appear in `/plays/stats`** (see the next bullet for why). A touch row scored iff its `playId` is in the intersected set **and** it is a `Rush` or `Reception` (never a `Target` — an incompletion cannot score). On a rushing TD the `Rush` row is the scorer; on a passing TD the `Reception` row is the scorer; the QB's `Completion` row is not a touch, so the QB is never mis-credited with a receiving TD.
- **CFBD `/plays` quirk 3 — the raw TD-id set is intersected with `/plays/stats`.** On some rushing/passing TDs (mostly on not-yet-settled data) `/plays` carries a separate play-by-play row whose `id` differs from the scoring-summary row's, both tagged `playType="… Touchdown"`; only the scoring-summary `id` appears in `/plays/stats`, carrying the scorer's `Rush`/`Reception` row. Intersecting the raw `/plays` TD-id set with `/plays/stats`' own `playId`s keeps exactly the ids a touch can attach to. On **fully settled data** (the production case — 2025 wk3, 70 games) this is nearly a no-op: 475 raw → 474 in `/plays/stats` → 474 credited, `unmatched: 0`. On hours-old data more raw ids are missing (2026 wk1, 8 games, played that day: 42 → 20 → 19, `unmatched: 1`) — so **run the ingestion after `/plays/stats` settles** (~14h+ post-game). The `statType="Touchdown"` rows that also sit on the summary id are far too sparse (3 in a 70-game week) to use as the source.
- **`statType='Touchdown'` in `/plays/stats` is NOT a usable TD source** — only 3 rows across the 8-game smoke week (~20 offensive TDs). CFBD emits it inconsistently. It is counted in diagnostics only.
- `/plays` has no `gameId` filter, and its `playType` query param wants an *abbreviation*, not the text. **Every** touchdown play type — Rushing, Passing, and all four return/recovery types — shares the abbreviation `TD` (confirmed via `/plays/types`). So the TD-play set for a week is **one call**, `/plays?year=&week=&classification=fbs&playType=TD` (~450–600 rows, ~0.25s), filtered in memory to `Rushing Touchdown` + `Passing Touchdown`. Verified byte-equivalent to the earlier per-team pull (identical raw id set, identical `/plays/stats` intersection).

**`td_attribution` diagnostic** in the ingestion response reports `td_plays_from_plays_raw`, `td_plays_in_play_stats`, `td_plays_not_in_play_stats` (benign — see quirk 3), `credited`, and `in_play_stats_but_no_touch` / `unmatched` (the number to watch — a TD-play id in `/plays/stats` with no rush/reception on it). On settled data `unmatched` runs 0; a persistent non-zero means the join has drifted and needs re-checking against live data.

### 8b. Full-week call volume & latency — added 2026-08-31

A full FBS week is ~70–90 completed games. The run makes **~74–94 CFBD calls**: `/games` ×1, `/plays/types` ×1, `/plays?playType=TD` ×1, `/roster?classification=fbs` ×1, and `/plays/stats?gameId=` ×~(games). The `/plays/stats` fan-out is fetched **concurrently** — a thread pool at **4 workers** (8 tripped CFBD's `429 "Too many concurrent requests for this endpoint"` on 8/70 games; `cfbd_get` also has a jittered 429 retry as backstop). **Measured full-week run** (2025 wk3, 70 games, `preview_only`): **HTTP 200 in ~14s** — `/plays/stats` ~9.3s, aggregation ~2.5s, `/roster` ~1.2s, `/plays?playType=TD` ~0.5s. Vercel plan is **Pro** (`maxDuration` ceiling 300s); `cfb/vercel.json` is **120s** as headroom (was 60 — the original timeout).

The ingestion response always carries a `timing` block (per-phase wall-clock) and a `cost_estimate` block (`estimate_week_cost()` — projected call count + wall-clock for the slate, with a `high_call_volume` flag at >150 calls). `POST {"dry_run": true}` returns just the estimate without ingesting. `cfb/test_plays_stats.py` asserts a 90-game week stays well under the timeout, the 429-resilient concurrent fetch works, and a pathological game count trips the call-volume tripwire — a guard against silent regression as game/roster counts grow.

**Correctness at full scale, verified** (2025 wk3, 70 games): 583 player rows / 261 defense rows / 8,542 touch rows; `/plays?playType=TD` → 474 offensive TDs credited, `unmatched: 0`; `rz_tds` 257 + `qb_rz_tds` 81 = 338 of 474 credited TDs (**71%**) scored from the red zone — the expected football ratio; 0 team-name→id misses; 287 (~5%) unresolved athletes, logged not dropped.

**Known follow-up (not a v1 blocker):** ~94 calls/week × ~15 in-season weeks ≈ 1,400/month — over the CFBD free-tier cap if that cap is monthly. v1 is manually triggered and won't run a full season in a testing month; if scheduled ingestion lands, this needs CFBD Tier 1 ($1/mo) or a caching layer.
## 9. Open items before this spec is fully build-ready
1. ~~Final pillar weight redistribution~~ — **LOCKED, see §0.**
2. ~~Cold-start UI/product decision~~ — **LOCKED, see §5.2.**
3. **Sep 1–3 Market Value re-poll** — determines whether v2 planning starts with "build the identity layer" or stays fully deferred. Not a v1 blocker.
4. **CFB backfill** for `signal_convergence`/`signal_breach` boolean tuning — not started; those two card fields should not ship for CFB without it. Not a v1 blocker (booleans can simply not ship for CFB initially).
5. **Cold-start threshold validation** (§5.2) — the evidence_quality ≥40 switch trigger is an estimate, not backtested. Not a v1 blocker, but should be revisited once real CFB season data exists.
**Everything above is either resolved or explicitly non-blocking for v1.** This spec is ready to hand to Claude Code as a build reference.
