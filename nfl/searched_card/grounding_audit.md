# Grounding Audit — 7 Claim Types (Stage 1b, Task 3)

STAGE 1 DESIGN DOC, this revision. Read-only investigation of real code
(`nfl/content_writer/nfl_writer_common.py`, `nfl/scoring.py`,
`nfl/shelves.py`, `nfl/redzone.py`, the real CSV column headers in
`nfl/data/stub_weeks/` and `nfl/scripts/player_redzone_weekly.csv`) —
every claim below was checked directly against those files, not assumed.
**This document does not modify `searched_card_citable_fields.py`'s
actual field list.** Everything under "proposed, not implemented" is
exactly that.

## Method

- **(a) reaches the writer today** — the field is a member of
  `NFL_TOP_LEVEL_CITABLE_FIELDS` (`nfl/content_writer/
  nfl_writer_common.py`) and therefore, after the `tpe_score`/
  `market_value_score` exclusion, of `SEARCHED_CARD_TOP_LEVEL_CITABLE_
  FIELDS` too (`searched_card_citable_fields.py` derives from the former
  by subtraction/addition, never by a second hand-typed list — see that
  file's own docstring).
- **(b) exists in the frame/candidate dict, filtered before the citable
  set** — the raw column is present on the real weekly scored row (and
  therefore on `build_nfl_writer_candidate`'s output too, confirmed:
  that function does `c = dict(row)` — the candidate dict is the real
  row, not a hand-picked subset), but is not in `NFL_TOP_LEVEL_CITABLE_
  FIELDS`, so `flatten_source_facts` never surfaces it to the model.
- **(c) does not exist anywhere in this repo's own code/schema** — no
  raw column, computed column, or trailing-window helper anywhere in
  `nfl/` produces it; noted separately when it plausibly lives on the
  external app database instead (confirmed real tables this repo writes
  to/reads from: `nfl_player_redzone_weekly`, `nfl_player_season_
  evidence` — see `nfl/redzone.py`, `nfl/player_season_history.py`).

## The 7 claim types

| # | Claim type | Category | Real field(s) | Confirmed by |
|---|---|---|---|---|
| 1 | Depth-chart status | **(b)** | `depth_rank` exists on the raw row; NOT citable. Only `depth_chart_movement_pct` (the week-over-week delta) is citable. | `nfl/scoring.py:632-638` (`score_role_momentum`); CSV headers in `nfl/data/stub_weeks/*.csv` and `nfl/scripts/player_redzone_weekly.csv` both carry `depth_rank` |
| 2 | Inside-the-10 touches | **(a)** | `i10_touches_trail3` (trailing-3-game, combined rush+target) | `nfl_writer_common.NFL_TOP_LEVEL_CITABLE_FIELDS` line listing `i10_touches_trail3` |
| 3 | Inside-the-5 touches | **(a)** | `gl_touches_trail3` — "gl" = **goal line**, defined as `yardline_100 <= 5` (confirmed `GOAL_LINE = 5` in `nfl/redzone.py:19`), i.e. literally inside-the-5. Trailing-3-game, combined rush+target. | `nfl/redzone.py:17-19`; `nfl_writer_common.NFL_TOP_LEVEL_CITABLE_FIELDS` |
| 4 | End-zone targets | **(b)** | `i10_target_touches` / `gl_target_touches` exist on the raw row (computed per-game by `redzone._band_agg`, confirmed splitting `rush`/`target` touch types); NOT citable, and — unlike the combined-touch trailing windows — **no trailing/trend version of either exists anywhere in this repo at all**, not just "not yet wired to citable" | `nfl/redzone.py:53-70` (`_band_agg`, the `{label}_target_touches` column); `nfl/shelves.py:380-384` (`add_red_zone_trend_windows` only ever builds `i10_touches_trail3`/`gl_touches_trail3`/`rz_tds_trail3` — no target-only variant) |
| 5 | Red-zone targets | **(b)** | `rz_target_touches` exists on the raw row (same `_band_agg` origin as #4, zone_max=20); NOT citable. Red zone's only citable trailing field at all is `rz_tds_trail3` (TDs, not touches or targets) — there is no `rz_touches_trail3` of any kind, target-only or combined. | same as #4 |
| 6 | Snap-share trend | **(a)** — doubly so | **Two** real citable fields already cover this: `snap_share_trend_pct` (td_opportunity pillar) and `snap_share_trend_pct_role` (role_momentum pillar) — same underlying `snap_share` rolling trend, scored under two different pillars for two different narrative purposes (see `NFL_PILLAR_FIELD_GROUPS` in `nfl_writer_common.py`) | `nfl/scoring.py:626-630` (`score_role_momentum`'s `snap_share_trend_pct`); `nfl_writer_common.NFL_TOP_LEVEL_CITABLE_FIELDS` lists both `snap_share_trend_pct` and `snap_share_trend_pct_role` |
| 7 | Opponent TD rate to position | **(a)** | `recent_tds_allowed_pct` — already **position-specific**, not a generic defense-wide rate: it's built from `allowed_rz_tds_last1/3/5/season_avg`, which are joined from `allowed_weekly`, documented as "one row per (defteam, position_group, week)" | `nfl/scoring.py:718-788` (`score_situation`'s own docstring, line 724: "one row per (defteam, position_group, week)"); `nfl_writer_common.NFL_TOP_LEVEL_CITABLE_FIELDS` |

**Summary: 4 of 7 reach the writer today (2, 3, 6, 7); 3 of 7 are
filtered out before the citable set, all of them category (b), not (c)
— every claim type this audit was asked to check already has its raw
column somewhere in this repo's own weekly frame. None required
reaching for the external app database at all**, contrary to what might
have been assumed going in — the separate-database tables named in this
task's own briefing (`nfl_player_redzone_weekly`, `nfl_player_season_
evidence`) turn out to be relevant to a *different* question (see "A
related, separate finding" below), not to whether these 7 claim types
exist locally.

## Proposed, not implemented — what adding each (b) field would cost

### 1. Depth-chart status (`depth_rank`)

- **Scale/tolerance**: trivial. `depth_rank` is a small discrete integer
  (1 = starter-ish, higher = further down the chart). `searched_card_
  tolerance_for_key` already defers to `nfl_tolerance_for_key` for any
  key it doesn't special-case, and that function's own safe default
  (when a key is in neither `_NFL_EXACT_FIELDS` nor `_NFL_ROUNDING_
  FIELDS`) is `EXACT_TOLERANCE` — correct for a discrete rank already,
  no code change needed anywhere for the tolerance question specifically.
- **The real cost is a data-quality one, found directly in `nfl/
  scoring.py`, not invented for this report**: `score_role_momentum`'s
  own docstring states plainly that `depth_rank` **is NaN for every
  2025 row** (a documented "2025-schema gap"). Adding `depth_rank` to
  the Searched Card's citable fields today would make a real-sounding
  "he's the backup" claim citable on paper while silently having no
  real value to ground it on an entire season of this repo's own
  historical data — `flatten_source_facts` already skips None/NaN
  values automatically (same harmless-not-broken pattern `market_value_
  score` uses today, per `nfl_writer_common.py`'s own comment), so this
  wouldn't produce a wrong card, just a field that's citable in name
  only until a real 2026+ row with populated `depth_rank` exists.
- **A real editorial question, not just a technical one**: the existing
  codebase already made a choice here — only `depth_chart_movement_pct`
  (the delta) is citable, never the raw snapshot. That could be a
  deliberate call (a depth-chart snapshot is a point-in-time roster
  designation, not evidence of a trend, and this pipeline's whole
  orientation is toward trend/momentum fields) or simply an
  oversight — this audit found no comment in `nfl_writer_common.py`
  stating the reasoning either way, so it should be asked of whoever
  made that original citable-fields decision before assuming it's safe
  to add the snapshot back in for this writer type specifically.

### 4. End-zone targets (`i10_target_touches` / `gl_target_touches`)

- **No trailing-window version exists to add** — this is the real cost,
  not a rescale. `shelves.add_red_zone_trend_windows` only builds three
  trailing sums today (`i10_touches_trail3`, `gl_touches_trail3`,
  `rz_tds_trail3`), all combined rush+target. Making a target-only claim
  groundable would require either:
  1. A new upstream helper (e.g. `i10_target_touches_trail3`) added to
     `shelves.add_red_zone_trend_windows` — **out of scope for this
     package** (that file is outside `nfl/searched_card/`, explicitly
     off-limits for this task), or
  2. Citing the raw single-game count directly — which this pipeline's
     own existing convention argues against: every other touch/TD count
     already citable is a trailing-3-game sum specifically because a
     single game's count is noisy, and `searched_card_prompt.py`'s
     carried-over hard rule ("do not state a number of games... describe
     the sample as limited, early, or still forming instead") already
     treats single-sample claims as something to avoid stating plainly,
     not something to newly enable.
- **Tolerance, if a trailing version existed**: would be `EXACT_
  TOLERANCE`, same reasoning `nfl_tolerance_for_key`'s own docstring
  already gives for `i10_touches_trail3`/`gl_touches_trail3` ("exactness
  IS the claim").

### 5. Red-zone targets (`rz_target_touches`)

- Same cost profile as #4, one level up: no trailing-window version of
  *any* red-zone touch/target count exists (`rz_tds_trail3` is TDs, not
  touches), so adding this claim type would need the same new upstream
  helper, in the same out-of-scope file, before this package could cite
  it honestly at all.

## A related, separate finding (not one of the 7, surfaced while checking category (c))

While confirming that none of the 7 claim types are category (c), this
audit did find a genuinely separate, real data source worth naming:
`nfl/player_season_history.py` (`build_player_history_package`, Table 1
`nfl_player_season_evidence`) tracks SEASON-level `targets`/
`red_zone_opportunities`/`goal_line_opportunities` per player, distinct
from the weekly trailing-window columns audited above. It is:

- Real code, in this repo, not hypothetical.
- NOT wired into `story_interrogation.py`'s `prior_history` parameter
  yet (per that module's own docstring) and NOT reachable without a
  live network read (`read_player_season_evidence_rows`, a signed-POST
  call) — which this task's hard constraints forbid making, and which
  the Searched Card writer's own citable-fields pipeline has no
  existing hook into regardless.
- **Cost if someone wanted this as a future citable source**: a new
  data-fetch step (the live network read above) feeding into
  `build_searched_card_candidate`, plus a decision about whether a
  SEASON-level opportunity count should be tolerance-classified the
  same way as the WEEKLY trailing-3 counts it would sit next to on a
  card, or differently (a season total and a trailing-3 count answer
  different questions and a reader could reasonably confuse them if
  both appear under similar-sounding labels).

This is flagged here because it's the one place this audit's search for
"does this exist elsewhere in the real app" surfaced something concrete
and real, not because it answers any of the 7 claim types directly —
none of their real backing fields turned out to require it.

## Stage 1c addendum: trend-word grounding on the 9 example-card claims

A DIFFERENT check from the 7-claim-type audit above: being backed by a
real, citable field (what the rest of this document checks) is
necessary but not sufficient. A claim's own WORDING can imply a
before/after comparison ("cooled off," "down," "up," "climbing,"
"trending," "starting to show up," "stable," "emerging") that the
backing field doesn't actually encode. Fields with "trend" or
"movement" in their name DO encode a real before/after delta in the
single number itself (confirmed: `snap_share_trend_pct`/`snap_share_
trend_pct_role`, `touch_share_trend_pct_role`, `depth_chart_movement_
pct` are all computed as week-over-week or rolling deltas, not bare
sums — see `nfl/scoring.py`'s own `score_role_momentum`). A bare
trailing-window SUM like `i10_touches_trail3`/`gl_touches_trail3` does
NOT — it is a single-window magnitude with no comparison baked in at
all (this document's own claim types #2/#3 above already establish
these as combined-touch trailing-3-game sums, nothing more).

Every one of the 9 claims across `test_example_cards.py`'s three example
cards was checked against this, claim by claim:

| # | Card | Claim | Trend word? | Field | Field encodes a delta? | Verdict |
|---|---|---|---|---|---|---|
| 1 | 1 | "snap share is down vs. a prior stretch" | yes ("down") | `snap_share_trend_pct_role` | yes ("trend" field) | **Fine as written.** |
| 2 | 1 | "touches inside the ten have cooled off" | yes ("cooled off") | `i10_touches_trail3` | **no** (bare trail3 sum) | **Ungrounded wording — rewritten.** |
| 3 | 1 | "matchup is not generous" | no | `defensive_matchup_vulnerability` | n/a | **Fine as written** (magnitude claim, no comparison implied). |
| 4 | 2 | "a real pile of touches inside the ten" | no | `i10_touches_trail3` | n/a | **Fine as written** (magnitude claim). |
| 5 | 2 | "a stable role" | yes ("stable") | `touch_share_trend_pct_role` | yes ("trend" field) | **Fine as written.** |
| 6 | 2 | "a generous opponent" | no | `recent_tds_allowed_pct` | n/a | **Fine as written** (magnitude claim). |
| 7 | 3 | "he's been climbing the depth chart" | yes ("climbing") | `depth_chart_movement_pct` | yes ("movement" field) | **Fine as written.** |
| 8 | 3 | "snaps are trending up" | yes ("trending," "up") | `snap_share_trend_pct_role` | yes ("trend" field) | **Fine as written.** |
| 9 | 3 | "goal-line touches are starting to show up" | yes ("starting to show up") | `gl_touches_trail3` | **no** (bare trail3 sum) | **Ungrounded wording — rewritten.** |

**2 of 9 failed and were rewritten** (claims #2 and #9 — both backed by
a bare trail3 sum, both using wording that implies a before/after
change the field can't support). For both, option (a) — find a
different real field that supplies both sides of the comparison — was
checked and confirmed unavailable: `nfl/content_writer/nfl_writer_
common.py`'s own citable-fields list carries only `i10_touches_trail3`/
`gl_touches_trail3`/`rz_tds_trail3` as red-zone trailing-window fields
(no trail6, no prior-window, no delta variant of either — the same gap
this document's own claim types #4/#5 already found for red-zone
TARGET counts specifically, now confirmed for the TOUCH counts too).
Both were therefore rewritten under option (b) — state only the
magnitude the field actually supports, no directional word:

- #2: "touches inside the ten have cooled off" -> **"touches inside the
  ten have been thin"** (still `i10_touches_trail3`, magnitude only).
- #9: "goal-line touches are starting to show up" -> **"a real
  goal-line touch count"** (still `gl_touches_trail3`, magnitude only —
  deliberately mirroring claim #4's "a real pile of..." phrasing for
  the same field type).

**The other 7 of 9 needed no change** — either they used no trend word
at all (claims #3, #4, #6), or the trend word they used was backed by a
field whose name carries "trend" or "movement" and which this audit
confirmed actually encodes a delta, not just a snapshot (claims #1, #5,
#7, #8).
