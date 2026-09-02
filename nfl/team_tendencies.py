"""
Coaching Trends — the fourth and final NFL Intelligence family (Market
Intelligence, Role Changes, Defensive Trends are all built). Reuses
intelligence_schema.build_story unchanged, same shared contract the
first three families established. Entity is a TEAM (not a player, not
a (team, position_group) pair like Defensive Trends) — confirmed
against real data below, not assumed.

CORE QUESTION: "Which teams are calling plays differently than they
used to, or differently than the league?" Three V1 detectors, per the
approved investigation (report-only task, prior turn):

  1. Red-zone play-calling — run/pass rate inside the 10.
  2. Fourth-down aggressiveness — go-for-it rate in realistic (non-
     garbage-time) situations.
  3. Pace — seconds per play, from real drive-level data.

THE ONLY FAMILY NEEDING GENUINELY NEW AGGREGATION, confirmed by the
prior investigation and unlike the other three: none of these three
signals exist anywhere in scoring.py or redzone.py today — this module
pulls directly from raw pbp, same as redzone.py does, but at a
different grain (team-week, not player-game) and for a different
purpose (Intelligence-only tendencies, never part of the 5-pillar
Universal TPE Score). Kept as its own module for exactly that reason —
not an extension of redzone.py (player grain) or defensive_trends.py
(consumes an already-scored pillar column, doesn't touch raw pbp at
all).

THREE INDEPENDENT AGGREGATION FUNCTIONS, deliberately not a shared
generic helper across them (investigated and declined in the prior
task): aggregate_redzone_play_calling, aggregate_fourth_down_
aggressiveness, and aggregate_pace each have genuinely different filter
logic and source columns. V1.5/V2 signals (formations, personnel,
motion) will be different enough in shape that a premature shared
abstraction now would likely be wrong for them later — matches this
session's repeated "don't design for hypothetical future requirements"
principle. What IS shared: all three roll their per-week score across
weeks via redzone.add_rolling_windows (the same generic, already-
validated helper defensive_trends.py already reuses for exactly this),
and two of three use a locally-reimplemented _shrink_rate (matches
scoring._shrink_rate's exact formula, reimplemented rather than
imported — same precedent as defensive_trends.py's/role_changes.py's
own local reimplementations of scoring.py's private helpers).

DATA RELIABILITY, confirmed against real 2022/2024/2025 pbp (the prior
investigation's own findings, re-verified in this build):

  Red-zone play-calling: rush_attempt/pass_attempt (not raw play_type)
  give clean data (down null 0.35-0.42%, yardline_100/posteam 0%) but a
  real single-week sample problem — inside-10 plays are median 5, min 1
  per team-week, with ~9% of team-weeks at zero. Needs season-to-date
  shrinkage (real).

  Fourth-down aggressiveness: score_differential/game_seconds_remaining/
  ydstogo/yardline_100 are all 0% null on 4th-down plays. A
  |score_differential|>=17 & game_seconds_remaining<=300 garbage-time
  filter cleanly separates real desperation attempts (spot-checked: real
  play descriptions confirm 21-36 point 4th-quarter deficits) from
  strategic aggression — checked against nflverse's own `wp` column too,
  which over-excludes (31% vs 6%) by also reacting to field position/
  down-distance, conflating "low win probability" with "not trying".
  Same single-week thinness as red-zone play-calling.

  Pace: play_clock is a dead column — 0% null but the constant "0" for
  EVERY row across all three seasons, despite looking clean by null-rate
  alone (worse than the route-column lesson: this one hides in plain
  sight). drive_time_of_possession / drive_play_count (already computed
  by nflverse per drive) gives real, realistic seconds-per-play values
  (median ~28 sec/play) and is the most single-week-robust of the three
  (median 10-11 drives/team-week, min 6-8). Filtered here to exclude
  degenerate drives (drive_play_count<2, or END_HALF/END_GAME clock-
  killing drives — confirmed real: 6.2% of drives have <2 plays, two-
  thirds of those are exactly these two transitions).

SAMPLE-SIZE TREATMENT, per the approved investigation: red-zone play-
calling and fourth-down aggressiveness get a STRUCTURAL gate (a story
cannot generate at all below a minimum cumulative-volume threshold,
mirroring defensive_trends.py's games_played-based mask, but volume-
based here rather than games-based — validated as the better fit:
defensive snaps against a position are guaranteed every game, but a
team can play a full game without a single red-zone trip or a single
non-garbage-time 4th down). Pace gets hedging only, no structural gate
— re-confirmed during THIS build (not just carried over from the
investigation unchanged): every generated pace story was checked and
none showed an obviously unstable/noise-driven reading (see test_team_
tendencies.py), so the lighter treatment holds.

ENTITY TYPE: team, not (team, position_group). Confirmed directly, not
assumed — unlike defensive_matchup_vulnerability (which is naturally
position-scoped, since it measures what a defense allows to a specific
position group), all three of these signals are whole-offense coaching/
play-calling decisions with no natural position-group split of their
own (a team's 4th-down aggressiveness or pace isn't "a WR's 4th-down
aggressiveness").

RELATED_PLAYERS — three different, specifically-reasoned relationships,
not one definition forced across all three (each function documents its
own reasoning inline below):
  - Red-zone play-calling: DIRECTIONAL — the team's own top red-zone
    rushers (if trending run-heavy) or top red-zone targets (if trending
    pass-heavy). Who benefits depends on which way the tendency moved.
  - Fourth-down aggressiveness: team-wide, ranked by td_opportunity — an
    aggressive team keeps more drives alive for everyone, not one
    position specifically.
  - Pace: team-wide, ranked by snap_share — more plays run mechanically
    amplifies whoever is already on the field the most, a genuinely
    different (volume, not opportunity) mechanism than the other two.

STORYTELLING HONESTY — the same class of bug already found and fixed in
three separate families this session (Shelf Ranking's storytelling
bug, Role Changes' snap-share claim check, Defensive Trends' CAR WR
case): a specific raw-rate claim is only cited when it genuinely agrees
in direction (at DISPLAY precision, not raw float precision — the
Defensive Trends rounding lesson applies here too) with the trend
being claimed, since these scores are built from SHRUNK, PERCENTILE-
RANKED values that can diverge from the simplest raw recent-vs-season
comparison. Verified with a full-backfill honesty scan, same standard
Defensive Trends' "0/126 mismatches" reporting set — see test_team_
tendencies.py for the real result.
"""
import pandas as pd

from intelligence_schema import build_story
from normalize import build_reference_scale, fill_neutral, percentile_lookup
from redzone import add_rolling_windows

CONFIG = {
    "shrinkage_k": 6.0,
    "garbage_time_score_diff": 17.0,
    "garbage_time_seconds_remaining": 300.0,
    # Minimum season-to-date cumulative volume before a story can
    # generate at all (structural gate, not just hedged language) —
    # checked against real data: by Week 8 2025, cumulative inside-10
    # plays are median 35/min 21 per team, and cumulative realistic
    # 4th-down decisions are median 49.5/min 38 — both thresholds below
    # sit comfortably under those real minimums, letting most teams
    # qualify by roughly midseason without accepting a 1-2-play "rate".
    "min_rz_plays_qualified": 20,
    "min_fourth_down_decisions_qualified": 25,
    "trend_window": 3,
    # Materiality thresholds on the rolled SCORE's delta (0-100 scale,
    # same shape as defensive_trends.py's trend_threshold=20.0) —
    # starting hypotheses, checked against real backfill distributions
    # in test_team_tendencies.py, same "tune later" treatment every
    # other threshold in this codebase gets.
    "redzone_trend_threshold": 20.0,
    "fourth_down_trend_threshold": 20.0,
    "pace_trend_threshold": 20.0,
    # "Full confidence" cumulative-volume targets for the completeness
    # field (graduated, beyond the binary structural gate) — same
    # min(actual/target,1)*100 shape as market_intelligence.py's
    # book_coverage_confidence. Checked against real full-season
    # cumulative totals (red-zone plays median ~86, 4th-down decisions
    # median ~125) — set below the typical full season so a team
    # reaches full confidence with real games still left to play, not
    # only in Week 18.
    "full_confidence_rz_plays": 60.0,
    "full_confidence_decisions": 60.0,
    "full_confidence_pace_games": 8.0,
    # Checked against real generated pace stories, not left at the
    # trend_window's own floor: since _trend_delta's games_played mask
    # (shared by all three detectors) already requires games_played >
    # trend_window before ANY trend exists, a real pace story's
    # sample_size is NEVER as low as 3 in practice (real 2025 minimum
    # observed: 6) — a thin_pace_games of 3 would be unreachable and
    # this hedging distinction would silently never fire. 8 sits
    # roughly at the bottom of the real observed distribution (6-20),
    # so genuinely early-season stories still read as hedged.
    "thin_pace_games": 8,
    "related_players_limit": 3,
    # evidence_classification (Universal Card v2) -- the REAL formula,
    # confirmed directly from Lovable's own trustIndicator() (same
    # thresholds already confirmed and shipped for Defensive Trends and
    # Role Changes).
    "evidence_strong_threshold": 80.0,
    "evidence_moderate_threshold": 60.0,
}


def _shrink_rate(numerator: pd.Series, denominator: pd.Series, league_avg_rate: float, k: float) -> pd.Series:
    """Same formula as scoring._shrink_rate, reimplemented locally — see module docstring for why."""
    return (numerator + k * league_avg_rate) / (denominator + k)


def _cumulative_through_prior_week(team_week: pd.DataFrame, col: str) -> pd.Series:
    """
    Season-to-date total of col through the PRIOR week only (cumsum then
    shift(1)) — same no-leakage discipline as every pillar in scoring.py
    (a team's current-week score must reflect what was knowable heading
    into that week, never that week's own plays).
    """
    g = team_week.groupby(["team", "season"])[col]
    return g.transform(lambda s: s.cumsum().shift(1)).fillna(0)


def _weekly_percentile(team_week: pd.DataFrame, value_col: str, qualified_col: str) -> pd.Series:
    """
    Percentile-rank value_col against the OTHER teams in the SAME
    (season, week) only — same (season, week) grouping precedent as
    scoring.score_market_value's own _group_percentile, not the whole
    multi-season population at once. The reference population is built
    from qualified rows only (real volume behind them); every row still
    gets ranked against that scale, including unqualified ones — the
    qualified flag itself is used separately as the story-generation
    gate, not to blank out the score (same separation of concerns as
    defensive_trends.py's games_played mask gating STORIES, not the
    underlying pillar value).
    """
    def _group_pct(g: pd.DataFrame) -> pd.Series:
        scale = build_reference_scale(g[value_col], g[qualified_col])
        return pd.Series(percentile_lookup(g[value_col], scale), index=g.index)

    raw = team_week.groupby(["season", "week"], group_keys=False).apply(_group_pct)
    return fill_neutral(raw)


def _trend_delta(team_week: pd.DataFrame, score_col: str, window: int) -> pd.Series:
    """
    Recent-window mean of the SCORE minus its own season-to-date average
    — same shape as defensive_trends._trend_delta, including the SAME
    games_played mask (the original n=1-game fix, applied here too):
    with <= window prior team-weeks, last{window} and season_avg are
    computed over the exact same games and are mathematically forced to
    be identical (delta=0.0), indistinguishable from a genuine flat
    trend by value alone. Caught directly during this build (real 2025
    Week 2 ARI pace_score delta read exactly 0.000000, the same forced-
    echo signature) — masked here rather than left in, matching
    precedent exactly rather than assuming the structural volume gate on
    the other two detectors would happen to catch every case.
    """
    games_played = team_week.groupby(["team", "season"]).cumcount()
    delta = team_week[f"{score_col}_last{window}"] - team_week[f"{score_col}_season_avg"]
    return delta.where(games_played > window)


# ============================================================
# Detector 1: Red-zone play-calling
# ============================================================

def aggregate_redzone_play_calling(pbp: pd.DataFrame) -> pd.DataFrame:
    """
    Team-week rush/pass attempt counts inside the 10 (primary) and
    inside the 20 (season-baseline comparison context, cited in stories
    but not the primary percentile driver). rush_attempt/pass_attempt,
    not raw play_type — same convention redzone.py already uses, and
    the one confirmed clean in the prior investigation (0.35-0.42% down-
    null among these plays vs. 16%+ on raw play_type, which includes
    kickoffs/no-plays/extra-points that were never a real run/pass call
    at all).
    """
    scrimmage = pbp[(pbp["rush_attempt"] == 1) | (pbp["pass_attempt"] == 1)]

    def _grouped(pop: pd.DataFrame, prefix: str) -> pd.DataFrame:
        g = pop.groupby(["posteam", "season", "week"]).agg(
            **{f"{prefix}_rush_attempts": ("rush_attempt", "sum"), f"{prefix}_pass_attempts": ("pass_attempt", "sum")}
        ).reset_index()
        g[f"{prefix}_plays"] = g[f"{prefix}_rush_attempts"] + g[f"{prefix}_pass_attempts"]
        return g

    rz10 = _grouped(scrimmage[scrimmage["yardline_100"] <= 10], "rz")
    rz20 = _grouped(scrimmage[scrimmage["yardline_100"] <= 20], "i20")

    out = rz10.merge(rz20, on=["posteam", "season", "week"], how="outer")
    count_cols = ["rz_rush_attempts", "rz_pass_attempts", "rz_plays", "i20_rush_attempts", "i20_pass_attempts", "i20_plays"]
    out[count_cols] = out[count_cols].fillna(0)
    return out.rename(columns={"posteam": "team"}).sort_values(["team", "season", "week"]).reset_index(drop=True)


def _score_redzone_play_calling(team_week: pd.DataFrame, config: dict) -> pd.DataFrame:
    tw = team_week.sort_values(["team", "season", "week"]).copy()
    cum_rush = _cumulative_through_prior_week(tw, "rz_rush_attempts")
    cum_plays = _cumulative_through_prior_week(tw, "rz_plays")
    league_avg = team_week["rz_rush_attempts"].sum() / team_week["rz_plays"].sum()

    tw["_shrunk_rush_rate"] = _shrink_rate(cum_rush, cum_plays, league_avg, config["shrinkage_k"])
    tw["_cum_rz_plays"] = cum_plays
    tw["_qualified"] = cum_plays >= config["min_rz_plays_qualified"]
    tw["redzone_run_tendency"] = _weekly_percentile(tw, "_shrunk_rush_rate", "_qualified").round(1)

    # Also roll the RAW (unshrunk) counts -- needed for the storytelling-
    # honesty check, not for the score itself. rush_last{w}/plays_last{w}
    # (both means-of-weekly-counts over the same window) equals sum/sum
    # over that window algebraically, so this reuses add_rolling_windows
    # rather than hand-rolling a parallel cumulative-sum computation.
    tw = add_rolling_windows(
        tw, metrics=["redzone_run_tendency", "rz_rush_attempts", "rz_plays"], group_cols=["team", "season"]
    )
    tw["_delta"] = _trend_delta(tw, "redzone_run_tendency", config["trend_window"])
    return tw


# ============================================================
# Detector 2: Fourth-down aggressiveness
# ============================================================

def aggregate_fourth_down_aggressiveness(pbp: pd.DataFrame, config: dict = CONFIG) -> pd.DataFrame:
    """
    Team-week go-for-it counts among REALISTIC 4th-down decisions only
    (excludes garbage time via |score_differential|>=17 & game_seconds_
    remaining<=config's thresholds — confirmed against real play
    descriptions in the prior investigation, not a guessed cutoff).
    Restricted to play_type in (run, pass, punt, field_goal) — the four
    real decision outcomes; no_play (penalty-nullified, no clean
    decision recorded) and qb_kneel (never a real 4th-down "decision")
    are excluded.
    """
    fourth = pbp[pbp["down"] == 4].copy()
    late = fourth["game_seconds_remaining"] <= config["garbage_time_seconds_remaining"]
    big_deficit = fourth["score_differential"].abs() >= config["garbage_time_score_diff"]
    realistic = fourth[~(late & big_deficit) & fourth["play_type"].isin(["run", "pass", "punt", "field_goal"])]

    g = realistic.groupby(["posteam", "season", "week"]).agg(
        go_attempts=("play_type", lambda s: s.isin(["run", "pass"]).sum()),
        fourth_down_decisions=("play_type", "size"),
    ).reset_index()
    return g.rename(columns={"posteam": "team"}).sort_values(["team", "season", "week"]).reset_index(drop=True)


def _score_fourth_down_aggressiveness(team_week: pd.DataFrame, config: dict) -> pd.DataFrame:
    tw = team_week.sort_values(["team", "season", "week"]).copy()
    cum_go = _cumulative_through_prior_week(tw, "go_attempts")
    cum_decisions = _cumulative_through_prior_week(tw, "fourth_down_decisions")
    league_avg = team_week["go_attempts"].sum() / team_week["fourth_down_decisions"].sum()

    tw["_shrunk_go_rate"] = _shrink_rate(cum_go, cum_decisions, league_avg, config["shrinkage_k"])
    tw["_cum_decisions"] = cum_decisions
    tw["_qualified"] = cum_decisions >= config["min_fourth_down_decisions_qualified"]
    tw["fourth_down_aggressiveness"] = _weekly_percentile(tw, "_shrunk_go_rate", "_qualified").round(1)

    tw = add_rolling_windows(
        tw, metrics=["fourth_down_aggressiveness", "go_attempts", "fourth_down_decisions"], group_cols=["team", "season"]
    )
    tw["_delta"] = _trend_delta(tw, "fourth_down_aggressiveness", config["trend_window"])
    return tw


# ============================================================
# Detector 3: Pace
# ============================================================

def _parse_time_of_possession(value) -> float:
    try:
        minutes, seconds = value.split(":")
        return int(minutes) * 60 + int(seconds)
    except (ValueError, AttributeError):
        return None


def aggregate_pace(pbp: pd.DataFrame) -> pd.DataFrame:
    """
    Team-week seconds-per-play from real drive-level data. play_clock is
    NOT used — confirmed dead (constant "0" for every row across all
    three real seasons checked). drive_time_of_possession / drive_
    play_count IS real and already computed by nflverse per drive.

    Filtered to exclude degenerate drives that would distort a "pace"
    reading: drive_play_count < 2 (mostly instant defensive/turnover
    scores, not an offensive tempo choice) and END_HALF/END_GAME
    endings (deliberate clock-killing kneels, the opposite of what
    "pace" is meant to measure) — confirmed real: 6.2% of drives have
    <2 plays, two-thirds of those end exactly one of these two ways.
    """
    drives = pbp.dropna(subset=["drive_time_of_possession", "drive_play_count"]).drop_duplicates(
        subset=["game_id", "posteam", "drive"]
    ).copy()
    drives = drives[(drives["drive_play_count"] >= 2) & (~drives["drive_end_transition"].isin(["END_HALF", "END_GAME"]))]
    drives["_top_seconds"] = drives["drive_time_of_possession"].apply(_parse_time_of_possession)
    drives = drives.dropna(subset=["_top_seconds"])

    g = drives.groupby(["posteam", "season", "week"]).agg(
        _total_seconds=("_top_seconds", "sum"), _total_plays=("drive_play_count", "sum"), drives_count=("drive", "count"),
    ).reset_index()
    g["seconds_per_play"] = g["_total_seconds"] / g["_total_plays"]
    return g.rename(columns={"posteam": "team"})[
        ["team", "season", "week", "drives_count", "seconds_per_play"]
    ].sort_values(["team", "season", "week"]).reset_index(drop=True)


def _score_pace(team_week: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    NO shrinkage and NO structural volume gate — per the approved
    investigation, pace is the most single-week-robust of the three
    (median 10-11 drives/team-week). qualified here just means "had any
    real qualifying drives at all" (avoids a divide-by-zero /
    percentile-ranking-a-NaN edge case), not a materiality bar.
    Percentile is inverted (100 - raw) so higher redzone_run_tendency-
    style semantics hold: higher pace score = FASTER (fewer seconds per
    play), matching this module's other two "higher = more of the named
    tendency" scores rather than forcing the reader to remember that
    fast is numerically low.
    """
    tw = team_week.sort_values(["team", "season", "week"]).copy()
    tw["_qualified"] = tw["drives_count"] > 0
    raw_pct = _weekly_percentile(tw, "seconds_per_play", "_qualified")
    tw["pace_score"] = (100.0 - raw_pct).round(1)

    tw = add_rolling_windows(tw, metrics=["pace_score", "seconds_per_play"], group_cols=["team", "season"])
    tw["_delta"] = _trend_delta(tw, "pace_score", config["trend_window"])
    return tw


# ============================================================
# related_players — three genuinely different relationships,
# not one definition forced across all three detectors.
# ============================================================

def _related_players_redzone(weekly: pd.DataFrame, season, week, team, direction: str, config: dict) -> list:
    """
    DIRECTIONAL: the team's own top red-zone rushers (if trending run-
    heavy) or top red-zone targets (if trending pass-heavy), ranked by
    rz_touch_share — the most directly, mechanically linked metric (red-
    zone touch share specifically), not a general opportunity score.

    Universal Card v2 shape: entity_type is always "player" (confirmed
    — always a real offensive player row from weekly). direction_
    indicator="up" for every entry, unconditionally — the SAME real
    reasoning as _signal_direction_redzone's own constant: this
    function already selects the beneficiary group per direction (top
    rushers when run-heavy, top targets when pass-heavy), so every real
    entry it returns is, by construction, someone this trend benefits.
    """
    position_groups = ["RB"] if direction == "growing-run-heavy" else ["WR", "TE"]
    pool = weekly[
        (weekly["season"] == season) & (weekly["week"] == week) & (weekly["posteam"] == team)
        & (weekly["position_group"].isin(position_groups))
    ]
    pool = pool.sort_values("rz_touch_share", ascending=False, na_position="last").head(config["related_players_limit"])
    return [
        {
            "player_id": r["player_id"],
            "display_label": r["player_name"],
            "entity_type": "player",
            "direction_indicator": "up",
            "note": f"Benefits from this team's real red-zone play-calling tendency · red-zone touch share {r['rz_touch_share']*100:.0f}%" if pd.notna(r.get("rz_touch_share"))
            else "Benefits from this team's real red-zone play-calling tendency",
        }
        for _, r in pool.iterrows()
    ]


def _related_players_team_wide(
    weekly: pd.DataFrame, season, week, team, rank_col: str, benefit_label: str, metric_label: str, metric_is_percent: bool,
    favorable_direction: str, direction: str, config: dict,
) -> list:
    """
    TEAM-WIDE (not directional in WHICH players are selected): used by
    both fourth-down aggressiveness (ranked by td_opportunity — an
    aggressive team keeps drives alive for everyone, not one position)
    and pace (ranked by snap_share — more plays run mechanically
    amplifies whoever's already on the field the most, a volume
    mechanism rather than an opportunity one).

    Universal Card v2 shape: entity_type is always "player" (confirmed
    — always a real offensive player row). direction_indicator IS
    genuinely directional here, unlike redzone above — this same fixed
    group either benefits or doesn't depending on which way the real
    trend moved (favorable_direction is the caller's own real
    growing-aggressive/growing-faster value; every entry gets "up" when
    the story's real direction matches it, "down" otherwise) — the
    identical real reasoning _signal_direction_fourth_down/_pace
    already use for the story itself, applied per related player.
    metric_is_percent distinguishes td_opportunity (already a real 0-100
    score, no *100 needed) from snap_share (a real 0-1 fraction,
    matching the exact same distinction role_changes.py's own snap_
    share citations already make) — real, confirmed unit difference,
    not a guess.
    """
    pool = weekly[
        (weekly["season"] == season) & (weekly["week"] == week) & (weekly["posteam"] == team)
        & (weekly["position_group"].isin(["RB", "WR", "TE"]))
    ]
    pool = pool.sort_values(rank_col, ascending=False, na_position="last").head(config["related_players_limit"])
    indicator = "up" if direction == favorable_direction else "down"
    return [
        {
            "player_id": r["player_id"],
            "display_label": r["player_name"],
            "entity_type": "player",
            "direction_indicator": indicator,
            "note": (
                f"{benefit_label} · {metric_label} {r[rank_col] * 100:.0f}%" if metric_is_percent
                else f"{benefit_label} · {metric_label} {r[rank_col]:.0f}/100"
            ) if pd.notna(r.get(rank_col)) else benefit_label,
        }
        for _, r in pool.iterrows()
    ]


# ============================================================
# Story generation — entity is a TEAM (confirmed against real data,
# see module docstring: none of these three signals have a natural
# position-group split of their own, unlike Defensive Trends).
#
# VOICE (TPE Editorial Voice Spec, Section 3): Coaching Trends sits at
# a ~4.5–5/10 personality ceiling — a coaching staff visibly changing
# how it calls a game reads as a story on its own, so the copy can be
# a touch more narrative than Defensive/Market Intelligence, but the
# season-rate -> recent-rate number contrast is what carries each
# claim. The 2026-09 voice pass re-toned the three _headline_and_story_*
# functions off the earlier "— a real, recent shift ... not just one
# X" over-explaining tail in the agrees=True branches (the numbers now
# stand on their own) and off the "not (yet) showing up as ... but a
# real shift in the overall tendency" diffuse-branch phrasing (now the
# tighter "No single X carries this one, but ... — a real shift in
# tendency, just a diffuse one" shape shared with Defensive Trends).
# The pace `thin` hedge append is unchanged, verbatim. Headlines were
# left as-is — plain declarative statements of a play-calling change,
# already at/below the ceiling.
# ============================================================

def _headline_and_story_redzone(row: pd.Series, direction: str) -> tuple:
    """
    Story first, per this project's established storytelling hierarchy.
    Only cites the specific recent-vs-season rush-rate numbers when they
    genuinely agree at DISPLAY precision (same Defensive Trends lesson —
    and a second-order version of it caught during THIS build: the
    story text displays whole percentage points via "{:.0f}%", but an
    earlier version of this check compared at 1-decimal precision, one
    digit finer than what's shown — two values differing only in that
    dropped digit, e.g. 36.4 vs 35.6, both PASSED the check yet both
    printed as the identical "36%" on screen, i.e. checking at ANY
    precision finer than the actual displayed text isn't sufficient;
    the check has to match the display exactly). Confirmed as a real
    case (2025 Week 7 MIA, Week 10 CHI), not hypothetical.
    """
    team = row["team"]
    recent_rate = row["rz_rush_attempts_last3"] / row["rz_plays_last3"] if row.get("rz_plays_last3", 0) > 0 else None
    season_rate = row["rz_rush_attempts_season_avg"] / row["rz_plays_season_avg"] if row.get("rz_plays_season_avg", 0) > 0 else None

    agrees = False
    if recent_rate is not None and season_rate is not None:
        recent_r, season_r = round(recent_rate * 100), round(season_rate * 100)
        if direction == "growing-run-heavy" and recent_r > season_r:
            agrees = True
        elif direction == "growing-pass-heavy" and recent_r < season_r:
            agrees = True

    i20_rate = row["i20_rush_attempts"] / row["i20_plays"] if row.get("i20_plays", 0) > 0 else None
    baseline = f" (compares to a {i20_rate*100:.0f}% run rate everywhere inside the 20 this week)" if i20_rate is not None else ""

    if direction == "growing-run-heavy":
        headline = f"{team} is leaning on the run much more once inside the 10."
        if agrees:
            story = (
                f"{team} has run the ball on {recent_rate*100:.0f}% of its plays inside the 10 over its last 3 "
                f"games, up from a {season_rate*100:.0f}% season rate{baseline}."
            )
        else:
            story = (
                f"No single play-call split carries this one, but {team}'s red-zone play-calling has trended "
                f"clearly more run-heavy on the model's shrunk, league-relative read — a real shift in "
                f"tendency, just a diffuse one."
            )
    else:
        headline = f"{team} is throwing the ball much more once inside the 10."
        if agrees:
            story = (
                f"{team} has passed on {100 - recent_rate*100:.0f}% of its plays inside the 10 over its last 3 "
                f"games, up from a {100 - season_rate*100:.0f}% season rate{baseline}."
            )
        else:
            story = (
                f"No single play-call split carries this one, but {team}'s red-zone play-calling has trended "
                f"clearly more pass-heavy on the model's shrunk, league-relative read — a real shift in "
                f"tendency, just a diffuse one."
            )

    return headline, story, agrees


def _headline_and_story_fourth_down(row: pd.Series, direction: str) -> tuple:
    team = row["team"]
    recent_rate = row["go_attempts_last3"] / row["fourth_down_decisions_last3"] if row.get("fourth_down_decisions_last3", 0) > 0 else None
    season_rate = (
        row["go_attempts_season_avg"] / row["fourth_down_decisions_season_avg"]
        if row.get("fourth_down_decisions_season_avg", 0) > 0 else None
    )

    # Checked at DISPLAY precision (whole percentage points, matching
    # the story text's "{:.0f}%") — see _headline_and_story_redzone's
    # docstring for why finer-than-displayed precision isn't sufficient
    # (a real case, confirmed here too: 2025 Week 19 PHI).
    agrees = False
    if recent_rate is not None and season_rate is not None:
        recent_r, season_r = round(recent_rate * 100), round(season_rate * 100)
        if direction == "growing-aggressive" and recent_r > season_r:
            agrees = True
        elif direction == "growing-conservative" and recent_r < season_r:
            agrees = True

    if direction == "growing-aggressive":
        headline = f"{team} is going for it on 4th down much more than usual."
        if agrees:
            story = (
                f"{team} has gone for it on {recent_rate*100:.0f}% of realistic 4th-down decisions over its "
                f"last 3 games, up from a {season_rate*100:.0f}% season rate."
            )
        else:
            story = (
                f"No single 4th-down rate carries this one, but {team}'s decision-making has trended clearly "
                f"more aggressive on the model's shrunk, league-relative read — a real shift in tendency, "
                f"just a diffuse one."
            )
    else:
        headline = f"{team} is playing it much safer on 4th down than usual."
        if agrees:
            story = (
                f"{team} has gone for it on just {recent_rate*100:.0f}% of realistic 4th-down decisions over "
                f"its last 3 games, down from a {season_rate*100:.0f}% season rate."
            )
        else:
            story = (
                f"No single 4th-down rate carries this one, but {team}'s decision-making has trended clearly "
                f"more conservative on the model's shrunk, league-relative read — a real shift in tendency, "
                f"just a diffuse one."
            )

    return headline, story, agrees


def _headline_and_story_pace(row: pd.Series, direction: str, thin: bool) -> tuple:
    """
    pace_score is inverted (100 - percentile of seconds_per_play), so
    growing-faster means the SCORE went up but seconds_per_play went
    DOWN — the sign check below is on seconds_per_play directly (lower
    = faster), not on the score, to avoid a sign-flip mistake.
    """
    team = row["team"]
    recent_sec = row.get("seconds_per_play_last3")
    season_sec = row.get("seconds_per_play_season_avg")

    agrees = False
    if pd.notna(recent_sec) and pd.notna(season_sec):
        recent_r, season_r = round(recent_sec, 1), round(season_sec, 1)
        if direction == "growing-faster" and recent_r < season_r:
            agrees = True
        elif direction == "growing-slower" and recent_r > season_r:
            agrees = True

    if direction == "growing-faster":
        headline = f"{team}'s offense has sped up recently."
        if agrees:
            story = (
                f"{team} is averaging {recent_sec:.1f} seconds per play over its last 3 games, down from a "
                f"{season_sec:.1f}-second season average — a faster clip, and more plays for both offenses."
            )
        else:
            story = (
                f"No single seconds-per-play number carries this one, but {team}'s pace has trended clearly "
                f"faster on the model's league-relative weekly read — a real shift, just a diffuse one."
            )
    else:
        headline = f"{team}'s offense has slowed down recently."
        if agrees:
            story = (
                f"{team} is averaging {recent_sec:.1f} seconds per play over its last 3 games, up from a "
                f"{season_sec:.1f}-second season average — a slower clip, and fewer plays for both offenses."
            )
        else:
            story = (
                f"No single seconds-per-play number carries this one, but {team}'s pace has trended clearly "
                f"slower on the model's league-relative weekly read — a real shift, just a diffuse one."
            )
    if thin:
        story += " Based on a still-developing sample this season — worth confirming as more games are played."

    return headline, story, agrees


# ============================================================
# Universal Card v2 fields — same "attach after build_story(), not
# threaded through STORY_FIELDS" approach Defensive Trends and Role
# Changes already established. Deliberately THREE separate signal-
# specific implementations per field below, not one shared helper
# across the three detectors -- matches this module's own existing,
# explicit precedent (its own docstring: "THREE INDEPENDENT AGGREGATION
# FUNCTIONS, deliberately not a shared generic helper... a premature
# shared abstraction now would likely be wrong for [signals] later").
# The same reasoning applies here: the three signals' real hero_metric
# shapes and signal_direction logic genuinely differ (see each
# function's own docstring for why), not just superficially.
# ============================================================

def _signal_direction_redzone() -> str:
    """
    REAL FINDING, confirmed against this signal's own related_players
    function before hardcoding: unlike fourth_down/pace below,
    redzone_run_tendency's related_players are DIRECTIONAL — the top
    red-zone RUSHERS when growing-run-heavy, the top red-zone TARGETS
    when growing-pass-heavy (see _related_players_redzone). The story's
    own related_players are, by construction, always the group
    positioned to benefit from whichever direction actually fired —
    the same "favorable for this story's own related_players"
    reasoning Defensive Trends already established, but here it's a
    real constant BECAUSE the beneficiary group itself flips with
    direction, not despite it varying.
    """
    return "favorable"


def _signal_direction_fourth_down(direction: str) -> str:
    """
    REAL FINDING, genuinely different shape from redzone_run_tendency
    above: fourth_down_aggressiveness's related_players are team-wide,
    ranked by td_opportunity, and do NOT change based on direction (see
    _related_players_team_wide) — the SAME group is cited whether the
    team is growing more or less aggressive. That's exactly Defensive
    Trends' own shape (one fixed related_players group, a real
    bidirectional trend_direction) — a more aggressive team keeps more
    drives alive for everyone on that same list (favorable); a more
    conservative team ends drives sooner (unfavorable).
    """
    return "favorable" if direction == "growing-aggressive" else "unfavorable"


def _signal_direction_pace(direction: str) -> str:
    """
    Same real shape as fourth_down_aggressiveness, confirmed
    independently rather than assumed to match just because both are
    "team-wide": pace's related_players are also team-wide (ranked by
    snap_share) and don't change with direction (see _related_players_
    team_wide) — more real plays run mechanically means more real
    volume for that same group (growing-faster -> favorable); fewer
    plays means less (growing-slower -> unfavorable).
    """
    return "favorable" if direction == "growing-faster" else "unfavorable"


def _hero_metric_for_redzone_row(row: pd.Series, agrees: bool) -> dict | None:
    """
    NULLABLE, same principle as every other family: populated only when
    the real agrees honesty check (the same one already gating whether
    the specific rush-rate line appears in supporting_evidence) says
    the real numbers back the claim. lower_is_better is deliberately
    left False here — a rush rate has no inherent "lower number is
    objectively better" quality the way TDs-allowed or depth_rank do;
    it's a real, neutral rate, not a defense-quality or role-quality
    metric.
    """
    if not agrees:
        return None
    before = round(float(row["rz_rush_attempts_season_avg"]) / float(row["rz_plays_season_avg"]) * 100, 1)
    after = round(float(row["rz_rush_attempts_last3"]) / float(row["rz_plays_last3"]) * 100, 1)
    return {
        "label": "Red-Zone Rush Rate", "before_value": before, "after_value": after,
        "unit": "%", "value_format": "percent", "delta_display_mode": "percentage_points",
        "delta_value": round(after - before, 1), "lower_is_better": False,
        "period_before_label": "SEASON", "period_after_label": "LAST 3",
    }


def _hero_metric_for_fourth_down_row(row: pd.Series, agrees: bool) -> dict | None:
    """Same real agrees gate as the go-for-it-rate evidence line. lower_is_better=False for the same "neutral rate, no inherent quality direction" reason as redzone above."""
    if not agrees:
        return None
    before = round(float(row["go_attempts_season_avg"]) / float(row["fourth_down_decisions_season_avg"]) * 100, 1)
    after = round(float(row["go_attempts_last3"]) / float(row["fourth_down_decisions_last3"]) * 100, 1)
    return {
        "label": "4th-Down Go-For-It Rate", "before_value": before, "after_value": after,
        "unit": "%", "value_format": "percent", "delta_display_mode": "percentage_points",
        "delta_value": round(after - before, 1), "lower_is_better": False,
        "period_before_label": "SEASON", "period_after_label": "LAST 3",
    }


def _hero_metric_for_pace_row(row: pd.Series, agrees: bool) -> dict | None:
    """
    Same real agrees gate as the seconds-per-play evidence line. REAL
    INVERSION, confirmed directly against _headline_and_story_pace's
    own docstring before writing this: pace_score is inverted relative
    to seconds_per_play (higher score = faster = LOWER seconds) — this
    function reports the real seconds_per_play values directly (what a
    person actually reads as "pace"), not the inverted 0-100 score, so
    before/after here move in the real, intuitive direction regardless
    of which way pace_score itself moved. value_format="seconds_per_play"
    is a real, new format value (added to the documented v2 enum
    specifically for this — no raw-seconds format existed before this
    family needed one). lower_is_better=False: faster isn't objectively
    "better" than slower on its own, unlike a real defense- or role-
    quality metric.
    """
    if not agrees:
        return None
    before, after = round(float(row["seconds_per_play_season_avg"]), 1), round(float(row["seconds_per_play_last3"]), 1)
    return {
        "label": "Seconds Per Play", "before_value": before, "after_value": after,
        "unit": "sec", "value_format": "seconds_per_play", "delta_display_mode": "absolute",
        "delta_value": round(after - before, 1), "lower_is_better": False,
        "period_before_label": "SEASON", "period_after_label": "LAST 3",
    }


def _what_changed_for_redzone_row(row: pd.Series, direction: str, agrees: bool, completeness: float, config: dict) -> list:
    """
    Real NEW editorial content — the primary evidence line here cites a
    raw internal field name ("redzone_run_tendency 80/100..."), the
    same real reason Defensive Trends needed fresh text rather than
    Role Changes' direct reuse; written fresh for consistency here too,
    including the agrees-gated rate line (already plain text in
    supporting_evidence, rewritten in the same voice as the primary
    item rather than mixed reuse-vs-rewrite within one story).
    """
    verb = "leaning more run-heavy" if direction == "growing-run-heavy" else "leaning more pass-heavy"
    items = [{
        "label": f"Red-zone play-calling {verb}",
        "observation": f"This team's real red-zone run/pass tendency has moved {row['_delta']:+.0f} points over its last {config['trend_window']} games.",
    }]
    if agrees:
        recent_rate = row["rz_rush_attempts_last3"] / row["rz_plays_last3"] * 100
        season_rate = row["rz_rush_attempts_season_avg"] / row["rz_plays_season_avg"] * 100
        items.append({
            "label": "Rush rate inside the 10",
            "observation": f"Running on {recent_rate:.0f}% of red-zone plays over its last 3 games, {'up' if direction == 'growing-run-heavy' else 'down'} from a {season_rate:.0f}% season rate.",
        })
    items.append({
        "label": "Sample size",
        "observation": f"Based on {int(row['_cum_rz_plays'])} real red-zone plays this season" + (" — still a developing sample." if completeness < 100 else ", a well-established read."),
    })
    return items[:3]


def _what_changed_for_fourth_down_row(row: pd.Series, direction: str, agrees: bool, completeness: float, config: dict) -> list:
    """Same real reasoning as redzone's own what_changed — fresh text, same real agrees gate."""
    verb = "getting more aggressive" if direction == "growing-aggressive" else "getting more conservative"
    items = [{
        "label": f"4th-down approach {verb}",
        "observation": f"This team's real 4th-down aggressiveness has moved {row['_delta']:+.0f} points over its last {config['trend_window']} games.",
    }]
    if agrees:
        recent_rate = row["go_attempts_last3"] / row["fourth_down_decisions_last3"] * 100
        season_rate = row["go_attempts_season_avg"] / row["fourth_down_decisions_season_avg"] * 100
        items.append({
            "label": "Go-for-it rate",
            "observation": f"Going for it on {recent_rate:.0f}% of realistic 4th-down decisions over its last 3 games, {'up' if direction == 'growing-aggressive' else 'down'} from a {season_rate:.0f}% season rate.",
        })
    items.append({
        "label": "Sample size",
        "observation": f"Based on {int(row['_cum_decisions'])} real realistic 4th-down decisions this season" + (" — still a developing sample." if completeness < 100 else ", a well-established read."),
    })
    return items[:3]


def _what_changed_for_pace_row(row: pd.Series, direction: str, agrees: bool, thin: bool, config: dict) -> list:
    """Same real reasoning as the other two -- fresh text, same real agrees gate. Sample-size language uses real games_played (pace's own real completeness basis), not cumulative play/decision volume like the other two signals."""
    verb = "speeding up" if direction == "growing-faster" else "slowing down"
    items = [{
        "label": f"Tempo {verb}",
        "observation": f"This team's real pace score has moved {row['_delta']:+.0f} points over its last {config['trend_window']} games.",
    }]
    if agrees:
        items.append({
            "label": "Seconds per play",
            "observation": f"Averaging {row['seconds_per_play_last3']:.1f} seconds per play over its last 3 games, {'down' if direction == 'growing-faster' else 'up'} from a {row['seconds_per_play_season_avg']:.1f}-second season average.",
        })
    items.append({
        "label": "Sample size",
        "observation": f"Based on {int(row['_games_played'])} real games this season" + (" — still a developing read." if thin else ", an established, well-populated read."),
    })
    return items[:3]


def _evidence_classification_for_row(completeness: float, confidence: float, config: dict) -> str:
    """Same real formula as Defensive Trends and Role Changes, confirmed directly from Lovable's own trustIndicator(): score = (confidence+completeness)/2, strong >= 80, moderate >= 60, else limited."""
    score = (confidence + completeness) / 2
    if score >= config["evidence_strong_threshold"]:
        return "strong"
    if score >= config["evidence_moderate_threshold"]:
        return "moderate"
    return "limited"


def build_redzone_play_calling_stories(pbp: pd.DataFrame, weekly: pd.DataFrame, season: int, week: int, config: dict = CONFIG) -> list:
    """One story per team whose red-zone run/pass tendency clears the structural volume gate AND the trend materiality threshold."""
    tw = _score_redzone_play_calling(aggregate_redzone_play_calling(pbp), config)
    pool = tw[
        (tw["season"] == season) & (tw["week"] == week) & tw["_qualified"] & tw["_delta"].notna()
        & (tw["_delta"].abs() >= config["redzone_trend_threshold"])
    ]

    stories = []
    for _, row in pool.iterrows():
        direction = "growing-run-heavy" if row["_delta"] > 0 else "growing-pass-heavy"
        headline, story_text, agrees = _headline_and_story_redzone(row, direction)
        completeness = round(min(row["_cum_rz_plays"] / config["full_confidence_rz_plays"], 1.0) * 100, 1)

        evidence = [
            f"redzone_run_tendency {row['redzone_run_tendency']:.0f}/100, moved {row['_delta']:+.1f} points "
            f"over the last {config['trend_window']} games vs. season-to-date",
            f"{int(row['_cum_rz_plays'])} red-zone play(s) of cumulative sample this season "
            f"({'a thin, still-developing sample' if completeness < 100 else 'a well-established read'})",
        ]
        if agrees:
            recent_rate = row["rz_rush_attempts_last3"] / row["rz_plays_last3"]
            season_rate = row["rz_rush_attempts_season_avg"] / row["rz_plays_season_avg"]
            evidence.insert(1, f"Rush rate inside the 10: {recent_rate*100:.0f}% (last 3 games) vs. {season_rate*100:.0f}% (season)")

        story = build_story(
            intelligence_family="coaching_trends",
            entity={"type": "team", "team": row["team"]},
            headline=headline,
            story=story_text,
            primary_signal={"name": "redzone_run_tendency", "value": float(row["redzone_run_tendency"])},
            supporting_evidence=evidence,
            trend_direction=direction,
            trend_strength=float(min(abs(row["_delta"]), 100.0)),
            sample_size=int(row["_cum_rz_plays"]),
            completeness=completeness,
            confidence=completeness,
            time_window=f"Season {season}, last {config['trend_window']} games through Week {week} vs. season-to-date",
            related_players=_related_players_redzone(weekly, season, week, row["team"], direction, config),
        )
        # Universal Card v2 fields -- attached after build_story(), not
        # part of its own hard STORY_FIELDS contract.
        story["hero_metric"] = _hero_metric_for_redzone_row(row, agrees)
        story["signal_direction"] = _signal_direction_redzone()
        story["what_changed"] = _what_changed_for_redzone_row(row, direction, agrees, completeness, config)
        story["evidence_classification"] = _evidence_classification_for_row(story["completeness"], story["confidence"], config)
        stories.append(story)

    return stories


def build_fourth_down_aggressiveness_stories(pbp: pd.DataFrame, weekly: pd.DataFrame, season: int, week: int, config: dict = CONFIG) -> list:
    """One story per team whose 4th-down aggressiveness clears the structural volume gate AND the trend materiality threshold."""
    tw = _score_fourth_down_aggressiveness(aggregate_fourth_down_aggressiveness(pbp, config), config)
    pool = tw[
        (tw["season"] == season) & (tw["week"] == week) & tw["_qualified"] & tw["_delta"].notna()
        & (tw["_delta"].abs() >= config["fourth_down_trend_threshold"])
    ]

    stories = []
    for _, row in pool.iterrows():
        direction = "growing-aggressive" if row["_delta"] > 0 else "growing-conservative"
        headline, story_text, agrees = _headline_and_story_fourth_down(row, direction)
        completeness = round(min(row["_cum_decisions"] / config["full_confidence_decisions"], 1.0) * 100, 1)

        evidence = [
            f"fourth_down_aggressiveness {row['fourth_down_aggressiveness']:.0f}/100, moved {row['_delta']:+.1f} "
            f"points over the last {config['trend_window']} games vs. season-to-date",
            f"{int(row['_cum_decisions'])} realistic 4th-down decision(s) of cumulative sample this season "
            f"({'a thin, still-developing sample' if completeness < 100 else 'a well-established read'})",
        ]
        if agrees:
            recent_rate = row["go_attempts_last3"] / row["fourth_down_decisions_last3"]
            season_rate = row["go_attempts_season_avg"] / row["fourth_down_decisions_season_avg"]
            evidence.insert(1, f"Go-for-it rate: {recent_rate*100:.0f}% (last 3 games) vs. {season_rate*100:.0f}% (season)")

        story = build_story(
            intelligence_family="coaching_trends",
            entity={"type": "team", "team": row["team"]},
            headline=headline,
            story=story_text,
            primary_signal={"name": "fourth_down_aggressiveness", "value": float(row["fourth_down_aggressiveness"])},
            supporting_evidence=evidence,
            trend_direction=direction,
            trend_strength=float(min(abs(row["_delta"]), 100.0)),
            sample_size=int(row["_cum_decisions"]),
            completeness=completeness,
            confidence=completeness,
            time_window=f"Season {season}, last {config['trend_window']} games through Week {week} vs. season-to-date",
            related_players=_related_players_team_wide(
                weekly, season, week, row["team"], "td_opportunity", "Benefits from sustained drives", "TD opportunity",
                False, "growing-aggressive", direction, config,
            ),
        )
        story["hero_metric"] = _hero_metric_for_fourth_down_row(row, agrees)
        story["signal_direction"] = _signal_direction_fourth_down(direction)
        story["what_changed"] = _what_changed_for_fourth_down_row(row, direction, agrees, completeness, config)
        story["evidence_classification"] = _evidence_classification_for_row(story["completeness"], story["confidence"], config)
        stories.append(story)

    return stories


def build_pace_stories(pbp: pd.DataFrame, weekly: pd.DataFrame, season: int, week: int, config: dict = CONFIG) -> list:
    """
    One story per team whose pace trend clears the materiality
    threshold — NO structural volume gate (per the approved
    investigation), just the games_played mask already inside _trend_
    delta plus hedged language for a still-thin season.
    """
    tw = _score_pace(aggregate_pace(pbp), config)
    tw["_games_played"] = tw.groupby(["team", "season"]).cumcount() + 1
    pool = tw[
        (tw["season"] == season) & (tw["week"] == week) & tw["_delta"].notna()
        & (tw["_delta"].abs() >= config["pace_trend_threshold"])
    ]

    stories = []
    for _, row in pool.iterrows():
        direction = "growing-faster" if row["_delta"] > 0 else "growing-slower"
        thin = row["_games_played"] < config["thin_pace_games"]
        headline, story_text, agrees = _headline_and_story_pace(row, direction, thin)
        completeness = round(min(row["_games_played"] / config["full_confidence_pace_games"], 1.0) * 100, 1)

        evidence = [
            f"pace_score {row['pace_score']:.0f}/100, moved {row['_delta']:+.1f} points over the last "
            f"{config['trend_window']} games vs. season-to-date",
            f"{int(row['_games_played'])} game(s) of data this season "
            f"({'a thin, still-developing sample' if thin else 'an established, well-populated read'})",
        ]
        if agrees:
            evidence.insert(1, f"Seconds per play: {row['seconds_per_play_last3']:.1f} (last 3 games) vs. {row['seconds_per_play_season_avg']:.1f} (season)")

        story = build_story(
            intelligence_family="coaching_trends",
            entity={"type": "team", "team": row["team"]},
            headline=headline,
            story=story_text,
            primary_signal={"name": "pace_score", "value": float(row["pace_score"])},
            supporting_evidence=evidence,
            trend_direction=direction,
            trend_strength=float(min(abs(row["_delta"]), 100.0)),
            sample_size=int(row["_games_played"]),
            completeness=completeness,
            confidence=completeness,
            time_window=f"Season {season}, last {config['trend_window']} games through Week {week} vs. season-to-date",
            related_players=_related_players_team_wide(
                weekly, season, week, row["team"], "snap_share", "Benefits from play volume", "Snap share",
                True, "growing-faster", direction, config,
            ),
        )
        story["hero_metric"] = _hero_metric_for_pace_row(row, agrees)
        story["signal_direction"] = _signal_direction_pace(direction)
        story["what_changed"] = _what_changed_for_pace_row(row, direction, agrees, thin, config)
        story["evidence_classification"] = _evidence_classification_for_row(story["completeness"], story["confidence"], config)
        stories.append(story)

    return stories


def build_team_tendencies_stories(pbp: pd.DataFrame, weekly: pd.DataFrame, season: int, week: int, config: dict = CONFIG) -> list:
    """All three Coaching Trends detectors combined into one feed for a given week."""
    return (
        build_redzone_play_calling_stories(pbp, weekly, season, week, config)
        + build_fourth_down_aggressiveness_stories(pbp, weekly, season, week, config)
        + build_pace_stories(pbp, weekly, season, week, config)
    )
