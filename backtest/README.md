# HR Prop Scoring Backtest

Standalone environment for building and validating the five-pillar HR prop
scoring model against real historical MLB data, before it touches anything
live. Separate Python venv from the rest of `tasty-pick-ems` (the Flask
dashboard's dependencies are unrelated).

## Setup

```bash
cd backtest
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Data pulled so far

Everything is cached under `data/` as parquet (gitignored — regenerate with
the scripts below rather than committing raw data).

- `scripts/fetch_season_baselines.py <season>` — season-level batter skill
  and pitcher matchup leaderboards (pillars 1 & 2 inputs). Currently pulled
  for **2022**.
  - `data/processed/batter_skill_2022.parquet` — barrel%, hard-hit%, avg exit
    velo, sweet-spot%, xSLG, xwOBA, HR, PA, HR/PA per batter (693 players).
  - `data/processed/pitcher_matchup_2022.parquet` — K/9, HR/9, hard-hit%
    allowed, barrel% allowed, xSLG/xwOBA allowed per pitcher (871 players).
- `scripts/fetch_month_statcast.py <start> <end>` — raw pitch-by-pitch
  Statcast data plus a derived batter-game outcome table. Currently pulled
  for **June 1–30, 2023**.
  - `data/raw/statcast_2023-06-01_2023-06-30.parquet` — 115,544 raw pitches.
  - `data/processed/batter_games_2023-06-01_2023-06-30.parquet` — 7,800
    batter-game rows: did this batter hit a HR in this game, PA count,
    handedness, opposing starting pitcher + handedness, teams. **11.0% HR
    rate**, which lines up with league math (~3% HR/PA × ~4 PA/game).
- `scripts/fetch_park_factors.py <start> <end> <season>` — empirical HR park
  factor per venue, computed directly from real Statcast batted-ball data
  (pillar 3 input). Currently pulled for **2022** (matches the skill/matchup
  baseline year, avoids look-ahead bias).
  - `data/processed/park_factors_2022.{parquet,csv}` — 30 venues. Checks out
    against known park reputations: Comerica Park (Detroit) lowest at 68.8,
    Cincinnati (127.3) and Yankee Stadium (124.8) — both known bandboxes —
    near the top. FanGraphs/Savant's own published park factors are
    multi-year and player-mix-adjusted; this is a simpler single-season,
    unadjusted version — see gaps below.
- `scripts/fetch_game_context.py <batter_games_parquet>` — real historical
  weather and starting batting order per game, from the free official MLB
  Stats API (`statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live`, no key
  required — pillars 3 & 4 inputs). Currently pulled for the June 2023
  sample's 390 games.
  - `data/processed/game_context_2023-06-01_2023-06-30.parquet` — 7,020 rows
    (390 games × 2 teams × 9 starters, exactly). Batting order 1–9, temp
    (48–97°F, mean 74°F), wind speed (0–21 mph, mean 6.4), wind direction
    text (e.g. "Out To LF"), and condition (Clear/Cloudy/Roof Closed/Dome/
    etc). 90% match rate against the June batter-game table — the other 10%
    are pinch hitters/subs, correctly outside the starting lineup.
- `scripts/fetch_bullpen_quality.py <season>` — team bullpen quality
  (pillar 4 input), computed directly from the same real Statcast data as
  park factors. Baseball-Reference's team pitching tables use ambiguous
  full city names ("Los Angeles", "New York", "Chicago") that don't
  distinguish Dodgers/Angels, Yankees/Mets, or Cubs/White Sox — unusable for
  team aggregation — so this derives everything from Statcast's unambiguous
  3-letter team codes instead. A pitch counts as "bullpen" if the pitcher
  isn't that team's game-1 starter (same starter-detection logic as
  `fetch_month_statcast.py`). Currently pulled for **2022**.
  - `data/processed/bullpen_quality_2022.{parquet,csv}` — 30 teams: bullpen
    HR/PA, K%, BB%, hard-hit% allowed. Checks out against known 2022
    reputations: Yankees and Astros — both known for elite 2022 bullpens —
    land at #1 and #3 by HR/PA allowed with the best strikeout rates; Cubs
    and Angels — both known for weak 2022 relief corps — land at the
    bottom.

### Why 2022 stats score 2023 games

Pillar 1/2 inputs use full-season **2022** aggregates to score **June 2023**
games — skill/matchup traits known *entering* the season, never stats from
games that haven't happened yet relative to the game being scored. This
avoids look-ahead bias. A more realistic "current form" version (rolling
in-season stats through the day before each game) is a natural next step,
but needs pulling additional months of pitch-level data to compute rolling
windows — worth doing once the weighting/formula is validated on the
simpler baseline.

- `scripts/fetch_batted_ball_profile.py <season>` — Pull% and FB% per
  batter, closing the last gap in the Player Skill pillar's original spec.
  Computed directly from cached raw Statcast batted-ball data rather than
  FanGraphs (still blocked): FB% is Statcast's own `bb_type == "fly_ball"`
  classification, no formula needed; Pull% needs a spray angle derived from
  `hc_x`/`hc_y` hit coordinates, with pull side depending on batter
  handedness (RHB pull to LF/negative angle, LHB pull to RF/positive
  angle) — exactly the kind of handedness-dependent sign that was wrong in
  an earlier pass at pitcher FB% (see above), so this script validates
  itself before saving: home runs are near-universally pulled regardless of
  exact rate, for both handedness groups, independent of any specific
  player's remembered stats. Passed cleanly on 2022 data — 64.6% of RHB HRs
  and 68.0% of LHB HRs classified as pulled.
  - `data/processed/batted_ball_profile_2022.parquet` — 685 batters. Note:
    this FB% excludes popups as their own Statcast category (`bb_type ==
    "popup"`), unlike FanGraphs' legacy convention which folds popups into
    FB% — deliberately excluded here since popups essentially never become
    home runs and would only dilute the signal for this specific use case.
    Don't be surprised if this FB% (league mean 25.1%) reads lower than
    FanGraphs' commonly cited ~34-36% for the same reason.

### Quick validation (already checked)

Joining the June 2023 outcomes to 2022 barrel% and bucketing into quartiles
shows a clean, monotonic signal:

| barrel% quartile | HR rate in June 2023 games |
|---|---|
| Q1 (low) | 6.0% |
| Q2 | 10.4% |
| Q3 | 13.3% |
| Q4 (high) | 15.1% |

That's real signal in the raw Statcast data before any scoring formula is
even built — a good sign pillar 1 is on solid ground.

## Known gaps / open decisions

- **Market Intelligence (pillar 5)**: skipped per plan, no historical odds
  data yet.
- **FanGraphs scraping is blocked (HTTP 403)** — `pybaseball.batting_stats`,
  `pitching_stats`, and related FanGraphs-leaderboard functions all fail
  right now (FanGraphs added bot protection on `leaders-legacy.aspx`). This
  is where Pull% and hitter FB% would normally come from. Worked around HR
  count/PA/K-rate via Baseball-Reference (`batting_stats_bref` /
  `pitching_stats_bref`, a different site, not blocked). **Pull%/hitter FB%:
  resolved** — see `scripts/fetch_batted_ball_profile.py` and the Player
  Skill pillar completion writeup below.
- **Pitcher fly-ball rate allowed**: the Baseball-Reference `GB/FB`/`LD`/`PU`
  columns pybaseball scrapes didn't check out against known pitcher
  profiles (e.g. Framber Valdez, an extreme groundballer, came back with an
  implausible 67% fly-ball rate allowed) — dropped rather than shipped a
  wrong number. Standing in for now: `hard_hit_pct_allowed` /
  `barrel_pct_allowed` / `xslg_allowed` (all real Statcast metrics) as the
  matchup-quality proxy. Same fix as above (compute from raw Statcast
  `bb_type`) would give a trustworthy FB% allowed too.
- **"Expected home runs"**: not a standard published Statcast/FanGraphs
  stat. Using `barrels_per_pa` and `xslg` as the closest proxies unless a
  custom xHR model is wanted later.
- **Environment — weather & park factors: resolved.** Real historical
  weather (temp, wind speed + direction, condition) comes from the MLB Stats
  API per-game feed; HR park factors are computed empirically from real
  Statcast data (see above). Two things to bake into the scoring formula,
  not the data pull:
  - **"Roof Closed" games still report an outdoor wind reading** (e.g. "16
    mph L To R") even though the roof shields the field from it. Treat
    `condition in ("Roof Closed", "Dome")` as neutral wind regardless of the
    recorded value — same idea as how the existing
    `tasty-pick-ems/src/lib/api/weather.py` / `hr_score.py` treats indoor
    parks as neutral.
  - The naive park factor doesn't adjust for which teams' hitters happen to
    play at a venue more — a real limitation of the single-season/unadjusted
    approach, not a bug. Fine for validating the scoring logic; revisit with
    a multi-year blend if it turns out to matter.
  - One 2023 game was the London Series (neutral site, `venue_name` =
    "London Stadium") — its `home_team` code still maps to that team's own
    park factor, which is wrong for that specific game. Affects at most a
    couple of games in the full season; not worth special-casing yet.
- **Opportunity — batting order & bullpen quality: resolved.** Batting
  order comes from `fetch_game_context.py` (real per-game starting lineup
  slot 1–9); bullpen quality from `fetch_bullpen_quality.py` (see above).
  **Projected PA is still not a real pre-game input** — game-level PA count
  in `batter_games_*.parquet` is the *actual* PA that game, which isn't
  knowable in advance; batting order slot is the honest proxy to use for
  projected opportunity until rolling in-season PA/game rates exist (see
  "current form" note above).
- Two known bugs were caught and fixed during setup (see git history /
  script comments): `player_name` in raw Statcast data is the **pitcher's**
  name not the batter's, and `at_bat_number` is a whole-game counter, not
  per-team, so naively taking `at_bat_number == 1` as "the starter" silently
  drops the home team's starting pitcher for every game. Both are fixed in
  `fetch_month_statcast.py`, worth remembering if this logic gets
  reimplemented elsewhere.

## Scoring model (`scoring/`)

Four-pillar weighted model, pillar 5 (Market Intelligence) skipped. Full
design writeup is in the PR/commit history; short version:

- **Normalization**: every raw stat becomes a 0-100 percentile against a
  qualified prior-season reference population (`scoring/normalize.py`).
  Qualification minimums (100 PA / 20 IP / 500 bullpen PA) control who
  defines the scale, not who gets scored — every row is still looked up
  against it; only a truly missing stat falls back to neutral 50.
- **Pillars** (`scoring/pillars.py`): skill groups into Contact Quality
  (avg exit velo, hard-hit%, sweet-spot%, FB%, Pull%) / Power Production
  (barrel%, xSLG, xwOBA) / Track Record (HR/PA) to avoid double-counting
  correlated metrics; matchup splits into pitcher quality-allowed plus a
  platoon adjustment; environment blends park factor (min-max scaled) with
  wind+temp (roof-closed/dome forced neutral); opportunity blends batting
  order with bullpen quality.
- **Calibration, not guesswork** (`scoring/calibrate.py`): two adjustments
  are measured from real data instead of assumed —
  - **Platoon bonus**: calibrated from the full 182K-PA 2022 season
    (`stand` vs actual `p_throws` per pitch), not the June sample being
    scored — first attempt calibrated from June alone came back with an
    implausible *negative* platoon lift (small-sample noise, ~7,800 rows
    split two ways); full-season data confirmed the textbook-expected
    positive lift (+5.1% relative). Calibrating a model parameter on the
    same data used to validate the model would also be mild leakage in the
    validation itself, so this uses the prior-season baseline like every
    other pillar input.
  - **Batting order curve**: real order-slot → avg-PA-that-game relationship
    from the sample (leadoff avg 4.53 PA/game down to 3.48 for the 9-hole),
    min-max scaled, instead of assuming a linear 1-9 taper.
- **Weights** (`scoring/config.py`): pillar weights are the midpoints of the
  originally stated ranges, rescaled to sum to 100 after dropping pillar 5
  — skill 40% / matchup 26% / environment 21% / opportunity 13%. All
  internal sub-weights live in the same config object, meant to be tuned
  after seeing backtest results, not hardcoded through the logic.

### Backtest results

Three runs, included together because the comparisons are the useful
result: June-only vs. full-season shows what more data does to noise;
full-season before vs. after completing the Player Skill pillar shows what
completing the pillar spec did to predictive power (short answer: almost
nothing — see below).

**June 2023 only** (7,800 batter-games, 780/decile):

| Decile (bottom → top) | Avg score | HR rate |
|---|---|---|
| 0 | 31.4 | 5.1% |
| 1 | 39.1 | 8.0% |
| 2 | 43.5 | 9.0% |
| 3 | 46.9 | 8.2% |
| 4 | 50.0 | 10.1% |
| 5 | 53.0 | 11.8% |
| 6 | 56.2 | 11.5% |
| 7 | 59.5 | 14.9% |
| 8 | 63.8 | 13.9% |
| 9 | 70.6 | 17.4% |

3 interior dips, 3.40x bottom-to-top lift.

**Full 2023 regular season** (48,764 batter-games, 4,876/decile —
`scripts/fetch_month_statcast.py 2023-03-30 2023-10-01`,
`scripts/fetch_game_context.py`, `scripts/run_backtest.py 2023-03-30_2023-10-01 2022`):

| Decile (bottom → top) | Avg score | HR rate |
|---|---|---|
| 0 | 31.8 | 6.2% |
| 1 | 39.7 | 7.6% |
| 2 | 44.0 | 8.8% |
| 3 | 47.4 | 9.8% |
| 4 | 50.4 | 9.8% |
| 5 | 53.3 | 11.6% |
| 6 | 56.4 | 12.1% |
| 7 | 59.8 | 14.2% |
| 8 | 63.9 | 15.0% |
| 9 | 70.7 | 17.9% |

Essentially clean monotonic — only one negligible wobble (decile 3 vs 4,
9.82% vs 9.76%, a 0.06pp difference). **2.88x lift** bottom to top. This is
the number to trust: the June-only 3.40x was partly small-sample noise at
the tails inflating the apparent separation. 6x the sample size didn't just
confirm the signal, it cleaned it up — exactly what the recommendation to
scale before tuning was betting on.

`scripts/fetch_game_context.py` now fetches concurrently (`ThreadPoolExecutor`,
8 workers) rather than one request at a time — the full season is ~2,430
games, and sequential fetching at the original polite pace would have taken
~30+ minutes; concurrent fetching took about 6-7. Also fixed a latent bug
in the same script: the output filename was hardcoded to the June date
range regardless of which input file was passed, so running it against a
different month would have silently mislabeled (or overwritten) the output.

**Full 2023 regular season, Player Skill pillar completed** (Pull%/FB%
added to Contact Quality, same 48,764 batter-games, same 2022 baselines,
`scripts/fetch_batted_ball_profile.py 2022` then re-running
`scripts/run_backtest.py 2023-03-30_2023-10-01 2022`):

| Decile (bottom → top) | Avg score | HR rate |
|---|---|---|
| 0 | 32.3 | 6.3% |
| 1 | 39.9 | 7.1% |
| 2 | 44.2 | 9.2% |
| 3 | 47.4 | 9.7% |
| 4 | 50.4 | 9.6% |
| 5 | 53.3 | 11.7% |
| 6 | 56.2 | 11.9% |
| 7 | 59.5 | 14.5% |
| 8 | 63.3 | 15.2% |
| 9 | 69.8 | 17.8% |

**2.85x lift** — essentially unchanged from 2.88x before adding Pull%/FB%.
Same shape, same single interior wobble (decile 3→4, now 0.10pp vs 0.06pp
before — still noise-level). **Flagging this as a real, checked finding,
not a shrug**: completing the pillar to its full original spec didn't
move the model's separation power. Checked why rather than just reporting
the number — correlating Pull%/FB% against the 2022 batter population's
existing skill metrics shows they're *not* redundant with their own Contact
Quality groupmates (near-zero correlation with avg exit velo, hard-hit%,
sweet-spot%: -0.02, 0.01, -0.04/0.30 respectively for FB%), but both
correlate substantially with barrel% (0.21 / 0.54) and HR/PA (0.37 / 0.54)
— which live in the *other* two sub-groups of the same pillar. So the
redundancy isn't within Contact Quality itself; it's that the skill pillar
as a whole was already capturing most of what Pull%/FB% represent, via
barrel%/HR-per-PA elsewhere in the same weighted average. The two metrics
are real, correctly computed (validated against the near-universal
pulled-HR fact independent of any specific player's stats), and now present
per the original spec — they just don't add much marginal predictive value
on top of what barrel%/exit velo/xSLG/xwOBA/HR-per-PA already capture for
this specific outcome (HR likelihood).

## Data-fit weights vs. hand-set weights

Tested whether replacing the hand-set pillar weights with logistic
regression coefficients — fit on real data instead of assigned by hand —
performs better. Short answer: **no meaningful performance gain, and a
harder-to-explain story. Recommendation is to keep the hand-set weights.**
Full reasoning below.

### Methodology

Same principle that caught the platoon-bonus bug earlier, applied
throughout: **fit on 2022, validate out-of-sample on 2023** — never the
same data for both. Concretely:

- Built a 2022 batter-game training set the same way the 2023 test set was
  built: derived `batter_games_2022-04-07_2022-10-05.parquet` from the
  already-cached full 2022 raw Statcast pull (no new pitch-level pull
  needed), then pulled weather/lineup context for those ~2,430 games via
  the same concurrent `fetch_game_context.py`.
- Refactored `scoring/pillars.py` to build on a new shared
  `scoring/features.py`, which computes each individual normalized (0-100)
  feature once — Pull%, FB%, barrel%, xSLG, etc. all become separate
  columns rather than being pre-blended into pillar sub-group averages.
  This is what makes it possible to fit — and report — a coefficient for
  Pull%/FB% specifically, rather than only for the Contact Quality group
  they belong to. **Verified the refactor is behavior-preserving**: re-ran
  the hand-set backtest immediately after and confirmed identical numbers
  to what's documented above before touching anything else.
- **Found and fixed one more instance of the exact bug pattern being
  guarded against here**: `scoring/model.py`'s batting-order-curve
  calibration was still using the dataframe being scored/validated (2023)
  as its own calibration source — the same class of leakage as the
  original platoon bug, just never caught because it wasn't specifically
  asked about. Fixed to calibrate from the prior season (2022) instead,
  consistent with the platoon fix and everything else in this project.
  Impact on the hand-set baseline was small (batting-order patterns are
  fairly stable year to year) — lift is still **2.85x**, decile values
  shifted by <0.3pp each, still the number in the tables above.
- Fit `statsmodels.Logit` on the 2022 training set: `hit_hr` (binary)
  against all 23 individual normalized features plus an intercept.
- Validated out-of-sample: applied the *same* fitted coefficients, the
  *same* 2022 reference scales, and the *same* 2022 batting-order curve to
  score the full 2023 season — exactly parallel to how the hand-set model
  uses 2022 baselines to score 2023, so the comparison is apples to apples.

**One documented limitation of the fitting step itself** (not the
validation): training features come from 2022's own season aggregates
explaining 2022 game outcomes, which is mildly self-referential (a
September home run partly informs the season aggregate used to explain a
June game). Solving this properly would mean pulling a full 2021 baseline
as the "prior year" predictor set for 2022 — not done here, flagged as a
possible follow-up rather than silently accepted. It does not affect the
actual out-of-sample test on 2023, which is clean either way.

### The coefficients

`scripts/fit_data_weights.py`, output saved to
`data/processed/fitted_coefficients_2022.csv`:

| Feature | Coef | p-value | | Feature | Coef | p-value |
|---|---|---|---|---|---|---|
| avg_exit_velo_pct | 0.0002 | 0.864 | | hr9_allowed_pct | 0.0021 | 0.032 |
| hard_hit_pct_pct | -0.0005 | 0.786 | | k9_allowed_pct_inv | 0.0010 | 0.301 |
| sweet_spot_pct_pct | -0.0003 | 0.691 | | platoon_opposite_pct | 0.0003 | 0.344 |
| **fb_pct_pct** | **-0.0005** | **0.541** | | park_factor_pct | 0.0041 | 0.000 |
| **pull_pct_pct** | **-0.0004** | **0.557** | | wind_pct | 0.0034 | 0.001 |
| barrel_pct_pct | -0.0001 | 0.916 | | temp_pct | 0.0091 | 0.000 |
| xslg_pct | 0.0022 | 0.303 | | batting_order_pct | 0.0010 | 0.086 |
| xwoba_pct | 0.0013 | 0.411 | | bullpen_hr_pa_pct | 0.0021 | 0.000 |
| **hr_per_pa_pct** | **0.0202** | **0.000** | | bullpen_hard_hit_pct_pct | 0.0027 | 0.000 |
| hard_hit_allowed_pct | 0.0018 | 0.015 | | bullpen_k_pct_inv_pct | 0.0001 | 0.800 |
| barrel_allowed_pct | -0.0007 | 0.501 | | | | |
| xslg_allowed_pct | 0.0003 | 0.875 | | | | |
| xwoba_allowed_pct | -0.0013 | 0.416 | | | | |

**Condition number: 2,057.9** (>30 is the conventional multicollinearity
flag — this is two orders of magnitude past it).

**Pull%/FB% specifically** — the question this was partly meant to
settle: both come back statistically insignificant (p=0.557, p=0.541) with
near-zero, even slightly negative coefficients. This confirms, now with an
actual fitted model rather than pairwise correlations, that they carry no
meaningful independent signal once the rest of the pillar is present —
consistent with the earlier correlation check.

But the bigger story in this table is the multicollinearity. `hr_per_pa`
alone (coef 0.0202) is roughly 10x the size of any other skill or matchup
feature and is the only one of the granular skill metrics with p < 0.001.
Meanwhile **hard-hit% and barrel% — two of the most theoretically
well-established HR predictors — come back negative and statistically
insignificant** (p=0.786, p=0.916). That's not a real "hard-hit% is bad for
you" finding; it's collinear credit-splitting among a cluster of metrics
(barrel%/hard-hit%/xSLG/xwOBA/HR-per-PA) that are all substantially
measuring the same underlying thing. Same pattern on the matchup side:
barrel%-allowed and xwOBA-allowed both come back negative/insignificant
despite being real, meaningful pitcher-quality signals.

### Implied pillar weights vs. hand-set

Summed `|coefficient|` within each pillar's feature group, normalized to
100%, for a like-for-like comparison against the hand-set weights:

| Pillar | Hand-set | Fitted (implied) |
|---|---|---|
| Skill | 40% | 46.1% |
| Matchup | 26% | 13.5% |
| Environment | 21% | 29.8% |
| Opportunity | 13% | 10.6% |

Matchup nearly halves and environment jumps by ~40% relative. Environment's
rise is at least individually explainable — temperature's coefficient
(0.0091) is the second-largest in the entire model, physically grounded
(warmer air is less dense, ball carries further) and highly significant
(p<0.001), so that part of the story holds up on its own. Matchup's drop is
less clean: it's largely the same multicollinearity issue as skill
(barrel%-allowed/xwOBA-allowed washing out against hard-hit%-allowed/HR-9-
allowed), not a real finding that pitcher matchup matters less.

### Out-of-sample validation on 2023

| Decile (bottom → top) | Avg score | HR rate |
|---|---|---|
| 0 | 3.0 | 6.6% |
| 1 | 4.4 | 7.6% |
| 2 | 5.8 | 8.4% |
| 3 | 7.1 | 9.8% |
| 4 | 8.4 | 9.9% |
| 5 | 9.9 | 11.0% |
| 6 | 11.9 | 12.9% |
| 7 | 14.6 | 13.0% |
| 8 | 17.9 | 15.2% |
| 9 | 23.1 | 18.6% |

**2.82x lift** — fully monotonic (every single decile step increases, no
wobble at all, actually cleaner than the hand-set model's one 0.3pp dip) but
essentially the same overall separation as the hand-set model's **2.85x**,
if anything a hair lower. The two models' scores correlate at 0.80 (Pearson)
/ 0.82 (Spearman) on the 2023 test set — they largely agree on rank order,
consistent with similar aggregate performance despite the internal weight
story diverging.

### Recommendation: keep the hand-set weights

Against the decision criteria: the fitted weights don't perform
meaningfully better (2.82x vs 2.85x — a wash, arguably slightly worse), and
they don't tell a similar, explainable story (matchup's implied weight
nearly halves for multicollinearity reasons, not a real signal finding;
individually well-established predictors like barrel% and xwOBA-allowed
come back negative and insignificant). Per the stated criteria, that's
exactly the case for keeping the explainable hand-set weights over the
fitted ones, even though nothing about the fitted model is *wrong* — it's
a legitimate regression, correctly validated out-of-sample, it's just being
asked to do more with correlated inputs than 48K rows can individually
resolve.

What this exercise *did* deliver: a real, rigorous answer on Pull%/FB%
(confirmed near-zero marginal contribution, not just correlated-therefore-
suspect), a second caught instance of the train/test leakage pattern
(batting-order curve) fixed alongside the one already caught, and a
regression-verified refactor (`scoring/features.py`) that the hand-set
model itself is now built on — worth having even without adopting the
fitted weights.

If this gets revisited later, the two changes most likely to help: fit at
the **pillar-group level** (4-6 aggregated features) rather than 23
individual ones, which would sidestep the multicollinearity entirely at
the cost of losing the per-metric detail; and/or add L2 regularization or
collect a 2021 baseline to remove the self-referential training caveat.

## Red-flag penalties

Implemented the last unbuilt piece of the original spec: heavy but
non-veto penalties for specific risk conditions, layered on top of the
four-pillar weighted score. Two versions, both documented — v1 (initial
attempt, per the original spec) made things worse; v2 (fixed based on what
v1's investigation found) is better than v1 but still doesn't beat the
no-flags baseline. Reported straight, not spun.

### v1 (original spec) — design

- **Multiplicative, not additive**: `final_score_rf = final_score * (1 -
  penalty_per_flag) ** n_flags_triggered`. Chosen specifically so the
  "never a hard veto" requirement holds structurally — a proportional
  reduction can't zero out an elite score the way an additive penalty with
  a floor clip effectively would. Starting penalty: 15%/flag (config-driven,
  meant to be tuned).
- **Three flags, calibrated 2022 → validated 2023** (same principle as the
  platoon bonus and batting-order curve):
  - `flag_high_k_pitcher`: opposing starter's K/9 in the top quartile
    (≥75th percentile) among qualified 2022 starters — reuses the same
    reference scale already built for pillar 2.
  - `flag_wind_in`: wind blowing in, at ≥8 mph — that speed floor is the
    **median** wind speed among real 2022 in-blowing, non-indoor games
    (`scoring/calibrate.py:compute_wind_in_threshold`), not a guessed round
    number; a 1-2mph "in" reading is noise, not a real condition.
  - `flag_low_order`: batting 8th or 9th, per spec.
- Missing data never triggers a flag (no opposing-starter match, no wind
  reading, no lineup slot → flag stays False), same stance as the
  neutral-50 fallback used everywhere else.
- New `scoring/red_flags.py`; wired into `scoring/model.py` alongside the
  existing pillar aggregation. `scored["final_score_rf"]` sits next to the
  existing `final_score` so both can be validated side by side.
- **Found and fixed a second stray instance of the train/test-leakage
  pattern while building this**: extended `build_calibration_games()` to
  carry wind/condition columns for the wind-threshold calibration — no new
  leakage introduced, but worth noting the discipline is holding up as more
  pieces get added.

### v1 trigger rates (2023 full season, 48,764 batter-games)

| Flag | Trigger rate |
|---|---|
| `flag_high_k_pitcher` | 13.0% |
| `flag_wind_in` | 7.3% |
| `flag_low_order` | 19.9% |
| 0 flags | 64.9% |
| 1 flag | 30.3% |
| 2 flags | 4.6% |
| 3 flags | 0.2% |

### v1 result: lift got worse, not better

| | Hand-set (no flags) | With red flags |
|---|---|---|
| Bottom decile HR rate | 6.25% | 6.54% |
| Top decile HR rate | 17.82% | 17.31% |
| **Lift** | **2.85x** | **2.65x** |

Both ends moved the *wrong* direction — the bottom decile got slightly
worse (higher HR rate, meaning some genuinely weaker batter-games got
displaced out of it), and the top decile got slightly worse too (lower HR
rate). Still roughly monotonic, still no veto behavior (worst-case
multiplier with 3 flags is 0.614x, confirmed nobody's score got zeroed),
but strictly less predictive than the pillars alone.

### v1: why two of the three flags don't hold up against real data

Checked each flag's actual relationship to `hit_hr`, not just whether it
changed the aggregate number:

**`flag_low_order` — real effect, but fully redundant.** Actual HR rate is
genuinely lower when flagged (8.4% vs 12.0% unflagged) — directionally
correct. But `pillar_opportunity` already encodes this continuously
(mean 24.5 when flagged vs 60.0 unflagged — batting order is *the*
dominant input to that pillar). The red flag isn't adding new information,
it's penalizing the same thing twice.

**`flag_wind_in` — the "out" half of wind clearly matters; the "in" half
doesn't, in this data.** By direction category: out-wind games run **12.75%**
HR rate, "in"-wind games run **11.47%**, and neutral/indoor games run
**10.56%** — "in" games hit HRs *more* often than neutral ones, not less.
Checked whether a different speed threshold would reveal the expected
suppression instead: binning "in" games by speed shows no clean pattern
either (11.3% at 0-5mph, 11.6% at 5-10mph, 12.1% at 10-15mph, dropping to
8.7% only in the small 15mph+ tail, n=288). The wind-matters concept is
real and already validated (`out` wind clearly boosts HR rate, matching
known physics and validating the Environment pillar's continuous
treatment) — it's specifically "wind blowing in suppresses HRs" that
isn't showing up as a clean, threshold-able effect here.

**`flag_high_k_pitcher` — backwards, and this one is the real finding.**
Checked HR rate by opposing-starter K/9 quartile on *both* seasons
independently:

| K/9 quartile | 2022 (calibration) | 2023 (validation) |
|---|---|---|
| Q1 (low K) | 9.5% | 10.6% |
| Q2 | 10.7% | 11.2% |
| Q3 | 10.1% | 11.7% |
| Q4 (high K) | 10.4% | **11.9%** |

2023 shows a clean *monotonic increase* — batters face **more** HRs against
high-K/9 starters, not fewer. 2022 is flatter but never shows the assumed
decrease either. This isn't noise (it replicates across two independent
seasons) and it isn't a classification bug (spot-checked the wind-string
parsing separately and it's exact-match clean, no substring false
positives). It's a real baseball mechanism: **K/9 measures a pitcher's
ability to miss bats, not their ability to suppress damage when contact
happens** — those are different skills. Power pitchers who rack up
strikeouts with elevated velocity are frequently the same pitchers who give
up more damage on the contact that *does* land (classic "swing-and-miss or
get crushed" profile), so K rate and HR rate allowed aren't the inverse
relationship the original flag assumed. This project's matchup pillar
already uses better-validated signals for exactly this (HR/9 allowed,
hard-hit%/barrel%-allowed) — this red flag was measuring the wrong thing.

### v1 recommendation (superseded by v2 below)

The mechanism (calibrated thresholds, multiplicative stacking, no-veto
guarantee) worked as designed — the problem was entirely in which three
conditions were chosen. Acted on that: dropped `flag_high_k_pitcher`,
removed `flag_low_order` entirely, downgraded `flag_wind_in` to
narrative-only. See v2 below for what that changed.

## v2: fixed red flags

Three changes, each acting on what the v1 investigation actually found —
not just tuning the same three flags harder:

1. **Replaced `flag_high_k_pitcher`.** Before picking a replacement,
   checked whether the obvious alternatives would actually do better —
   bottom-quartile (elite suppression) vs. the rest, on both HR/9-allowed
   and hard-hit%-allowed, both seasons independently:

   | Metric | 2022: bottom-Q rate | 2022: rest | 2023: bottom-Q rate | 2023: rest |
   |---|---|---|---|---|
   | HR/9-allowed | 10.42% | 10.11% | 11.78% | 11.21% |
   | Hard-hit%-allowed | 10.10% | 10.16% | 12.19% | 11.09% |

   **Both show the same backwards pattern as K/9** — pitchers who
   season-aggregate as elite at suppressing damage correlate with *higher*,
   not lower, batter HR rate, on both metrics, in both seasons. This isn't
   a fluke of the metric choice; it looks like a broader pattern in how
   season-aggregate pitcher quality relates to single-game HR outcomes
   (plausible mechanism: pitchers who season-aggregate as elite tend to
   pitch deeper into starts, facing more third-time-through-the-order
   plate appearances, where *any* pitcher — including elite ones — is more
   hittable; the aggregate label doesn't condition on that). Went with
   **hard-hit%-allowed anyway**, per instructions to implement one and
   report the real result honestly rather than conclude nothing was
   buildable — chosen over HR/9-allowed because it's built on a much
   larger per-pitcher sample (batted-ball count vs. a comparatively rare
   raw HR count), so a threshold calibrated on it should generalize more
   reliably even though neither showed a clean signal in this check.
   Threshold: bottom quartile (≤25th percentile) among qualified 2022
   starters, same reference scale already used in pillar 2.
2. **Removed `flag_low_order` entirely** (not just excluded from
   scoring) — redundant with `pillar_opportunity`, confirmed in v1.
3. **Downgraded `flag_wind_in` to narrative-only** — still computed and
   exposed (`scoring/red_flags.py:NARRATIVE_ONLY_FLAG_COLUMNS`) for a
   card's "why" text if the context is useful, but excluded from
   `red_flag_count` and the penalty multiplier.

With only one penalty-relevant flag left, "stacking" is currently moot in
practice (max is 0 or 1 flags per batter-game) — the stacking mechanism
itself is unchanged and still exercised the moment a second scored flag
gets added back.

### v2 result

| | Trigger rate |
|---|---|
| `flag_elite_suppression_pitcher` (penalty) | 19.2% |
| `flag_wind_in` (narrative-only) | 7.3% |

| Decile | No flags | v1 (broken) | v2 (fixed) |
|---|---|---|---|
| Bottom | 6.25% | 6.54% | 6.15% |
| Top | 17.82% | 17.31% | 17.18% |
| **Lift** | **2.85x** | **2.65x** | **2.79x** |

**v2 is a real improvement over v1** (2.65x → 2.79x) — removing the
redundant flag and the backwards K/9 flag recovered most of the lost
ground. **But it still doesn't beat the no-flags baseline** (2.79x vs.
2.85x). Given the pre-implementation check above already showed
hard-hit%-allowed has the same backwards bottom-quartile relationship
K/9 had, this isn't a surprising result in hindsight — it's the expected
outcome of the check, now confirmed through the actual scoring pipeline
rather than an isolated quartile table. Bottom decile is essentially a
wash (6.15% vs 6.25%, within noise); the shortfall is entirely at the top
end (17.18% vs 17.82%), consistent with the flag penalizing some
batter-games that the pillar score alone was already ranking correctly.

### Recommendation

**Don't ship a scored red flag on this axis at all, in its current form.**
This isn't a "pick a different threshold" problem — bottom-quartile
season-aggregate pitcher-quality metrics (K/9, HR/9-allowed, and hard-hit%-
allowed all three) show the same backwards-or-flat relationship with
single-game batter HR outcomes, replicated across two independent seasons.
That's a real pattern about how season aggregates relate to individual
games, not a metric-selection mistake fixable by trying a fourth metric
the same way.

What's actually working: `pillar_matchup` (the *continuous*, multivariate
blend of these same metrics plus platoon) is already validated as
contributing to the model's overall 2.85x lift — the problem is
specifically with collapsing pitcher matchup quality into a single
discrete threshold/flag, not with the underlying metrics or the pillar
that already uses them properly.

Two paths forward, not mutually exclusive:
1. **Ship with red flags off** (`final_score`, not `final_score_rf`) until
   a matchup-based flag can be built that survives the same real-data check
   this project applies everywhere — e.g. conditioning on times-through-
   the-order rather than a raw season aggregate, which the mechanism
   proposed above suggests could be the actual fix.
2. Keep `flag_wind_in` available narrative-only (already the case in v2)
   and consider `flag_low_order` for narrative purposes too, despite both
   being excluded from scoring — neither needs the same predictive bar
   that a scored flag does.

The code (`scoring/red_flags.py`, `final_score_rf` column, `PENALTY_FLAG_COLUMNS`/
`NARRATIVE_ONLY_FLAG_COLUMNS` split) is left in place — this is a "the one
remaining flag isn't ready yet" finding, not a reason to rip out the
mechanism.

## Final robustness check: 2021 (fully independent season)

Every validation so far — the pillar backtests, the platoon-bonus
calibration, the batting-order curve, the data-fit weights regression, the
red-flag thresholds — used 2022 as the calibration source and 2023 as the
held-out test. That's real out-of-sample discipline, but it's still only
one held-out season, calibrated by one team of decisions. Before calling
historical backtesting done, ran the *exact same model* — same code, same
2022-sourced calibration, zero new tuning — against **2021**, a season
nothing in this project has touched until now.

**Methodology note, disclosed rather than glossed over**: this scores 2021
games using 2022 baselines — a later season informing an earlier one,
backwards from the "always prior-season" principle used everywhere else
here (2022→2023 was forward and clean). Building a proper prior-season
(2020) baseline just for this check would itself be new work beyond "run
the existing model," which the brief for this check explicitly excluded.
The purpose here isn't a realistic live-forecasting simulation — it's
confirming the *model* (its pillar structure, weights, and calibrated
constants) generalizes to a season it has genuinely never seen in any
capacity, which this still cleanly tests.

`scripts/fetch_month_statcast.py 2021-04-01 2021-10-03` (713K+ pitches,
51,482 batter-games, 10.8% HR rate — consistent with 2022's 10.15% and
2023's 11.3%) → `scripts/fetch_game_context.py` (43,722 rows, matching the
established 2-teams-×-9-starters pattern) → `scripts/run_backtest.py
2021-04-01_2021-10-03 2022`, unchanged.

### Result: holds up, and separates even more cleanly than 2023

| | 2023 (primary OOS test) | 2021 (independent confirmation) |
|---|---|---|
| Bottom decile HR rate | 6.25% | 6.23% |
| Top decile HR rate | 17.82% | **19.81%** |
| **Lift** | **2.85x** | **3.18x** |

Full 2021 decile table (no red flags): 6.23% → 7.44% → 6.64% → 8.06% →
9.32% → 9.91% → 11.99% → 13.50% → 14.98% → 19.81%. One real (if minor)
wobble — decile 2 (6.64%) dips below decile 1 (7.44%), a slightly bigger
blemish than anything seen in the 2023 curve — but every other step
increases cleanly, and the bottom-to-top separation is the strongest of
any full-season run in this project.

**Red flags on 2021**: lift is 3.20x with `final_score_rf` vs. 3.18x
without — flat-to-marginally-positive, the opposite direction from 2023
(where red flags cost 0.06x). Read this as *reinforcing* the earlier
decision to hold off shipping the flag, not reversing it: an effect that
flips sign between the only two out-of-sample seasons tested isn't one to
trust in production regardless of which direction it happened to point
this time.

### Overall read: ready to move past historical backtesting

Two independent, never-simultaneously-touched seasons (2023 forward from
2022, 2021 now checked against the same 2022-sourced model with zero new
tuning) both show clean, strongly monotonic separation, with 2021 actually
outperforming 2023 on lift. That's the result this check was hoping for —
not just "no regression," but a genuine second confirmation that the
model's structure and calibrated constants aren't overfit to the
specifics of the one season this project spent the most time tuning
against.

This is a good stopping point for historical backtesting. What would
still be worth doing, but as live-integration work rather than more
backtesting: closing the documented data gaps that matter operationally
(projected PA as a real pre-game input, opposing bullpen availability day-
of), and deciding how `final_score` vs `final_score_rf` gets exposed once
real games are being scored rather than replayed.

## Next steps

Historical backtesting is done — see the 2021 robustness check above.
Remaining items are either live-integration work or optional follow-ups,
not blockers:

1. Live integration: decide how `final_score` vs `final_score_rf` gets
   exposed once real games are being scored (current recommendation is
   `final_score` only — see red-flags section above), and close the data
   gaps that matter operationally once this touches real pre-game
   predictions rather than replayed history — projected PA as a real
   pre-game input (not the actual PA count this project has been scoring
   against) and opposing bullpen availability day-of are the two that
   matter most; multi-year park factor smoothing and true
   fly-ball-rate-allowed for pitchers are lower priority.
2. Consider the pillar-level (not per-metric) regression variant if the
   data-fit-weights question comes back up.
3. Red flags: ship with `final_score` (no red flags applied) until a
   matchup-based penalty flag is found that survives the same
   out-of-sample check applied throughout this project — the 2021 check
   reinforced this rather than changing it (the one remaining flag's
   effect flipped sign between the two tested seasons). The
   times-through-the-order hypothesis is the most promising lead if this
   gets revisited.
4. Proposed but not implemented: a "very cold weather" red flag — given
   every red-flag candidate tried so far turned out weaker or backwards
   versus the original assumption, this would need the same two-season
   real-data check before being trusted, not just physical plausibility.
