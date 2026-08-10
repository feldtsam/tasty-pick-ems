"""
Shared red zone / inside-10 / goal-line usage aggregation for NFL play-by-play.

Zone definitions match the three bands named explicitly in the NFL Master
Blueprint (Red Zone Trends, RB/WR/TE Trends). Imported by both
scripts/backfill_redzone.py (batch backfill) and the live weekly job, so
there is exactly one implementation of this logic to keep in sync.
"""

import numpy as np
import pandas as pd

# yardline_100 is distance from the opponent's end zone, so smaller = closer
# to scoring.
RED_ZONE = 20
INSIDE_10 = 10
GOAL_LINE = 5


def _touches(df: pd.DataFrame) -> pd.DataFrame:
    """
    Collapse a play-by-play frame down to one row per touch (rush attempt
    by the rusher, or target by the receiver), tagging which player and
    which team touched the ball, and whether that specific player scored
    on the play. Excludes plays where there's no rusher/receiver id (e.g.
    penalties, spikes, kneels are naturally excluded since they have no
    rush/pass attempt id).

    Touchdown attribution uses rush_touchdown / pass_touchdown rather than
    the play-level touchdown column, which fires on any score on the play
    (offense or defense) — e.g. a fumble recovered and run in by a
    teammate would otherwise be miscredited to the fumbling player.
    """
    rush = df[df["rush_attempt"] == 1].copy()
    rush["player_id"] = rush["rusher_player_id"]
    rush["player_name"] = rush["rusher_player_name"]
    rush["touch_type"] = "rush"
    rush["own_touchdown"] = rush["rush_touchdown"]

    rec = df[df["pass_attempt"] == 1].copy()
    rec["player_id"] = rec["receiver_player_id"]
    rec["player_name"] = rec["receiver_player_name"]
    rec["touch_type"] = "target"
    rec["own_touchdown"] = rec["pass_touchdown"]

    touches = pd.concat([rush, rec], ignore_index=True)
    touches = touches[touches["player_id"].notna()]
    return touches


def _band_agg(touches: pd.DataFrame, label: str, group_keys: list[str], zone_max: int) -> pd.DataFrame:
    """
    Shared touch/TD counting for a single yardline band (red zone /
    inside-10 / goal-line), grouped by group_keys. Used both by
    aggregate_redzone_game (grouped by offensive player) and
    aggregate_redzone_allowed (grouped by defending team + position group)
    — the same band-counting logic, just a different grouping axis.
    """
    min_df = touches[touches["yardline_100"] <= zone_max]
    return (
        min_df.groupby(group_keys)
        .agg(
            **{
                f"{label}_touches": ("touch_type", "count"),
                f"{label}_rush_touches": ("touch_type", lambda s: (s == "rush").sum()),
                f"{label}_target_touches": ("touch_type", lambda s: (s == "target").sum()),
                f"{label}_tds": ("own_touchdown", "sum"),
            }
        )
        .reset_index()
    )


def aggregate_redzone_game(pbp: pd.DataFrame) -> pd.DataFrame:
    """
    Produce one row per player per game with red zone / inside-10 /
    goal-line touch and TD counts, plus each player's share of his team's
    total red-zone touches that game (the "opportunity concentration"
    signal from the blueprint).
    """
    touches = _touches(pbp)

    keys = ["game_id", "season", "week", "posteam", "player_id", "player_name"]

    rz = _band_agg(touches, "rz", keys, RED_ZONE)
    i10 = _band_agg(touches, "i10", keys, INSIDE_10)
    gl = _band_agg(touches, "gl", keys, GOAL_LINE)

    out = rz.merge(i10, on=keys, how="left").merge(gl, on=keys, how="left")
    for c in out.columns:
        if c.endswith(("_touches", "_tds")):
            out[c] = out[c].fillna(0).astype(int)

    # Team red-zone touch totals per game, for share calculation
    team_rz_totals = (
        rz.groupby(["game_id", "posteam"])["rz_touches"]
        .sum()
        .rename("team_rz_touches")
        .reset_index()
    )
    out = out.merge(team_rz_totals, on=["game_id", "posteam"], how="left")
    out["rz_touch_share"] = (out["rz_touches"] / out["team_rz_touches"]).round(3)

    return out.sort_values(["season", "week", "game_id", "rz_touches"], ascending=[True, True, True, False])


def _position_lookup(seasonal_rosters: pd.DataFrame) -> pd.DataFrame:
    """
    (player_id, season) -> position_group (RB/WR/TE) from
    import_seasonal_rosters(), which has clean per-player position labels
    keyed on the same gsis-style player_id play-by-play uses. NOT sourced
    from play-by-play's own offense_positions/defense_positions columns —
    spot-checked those and they're noisy 11-player personnel-package
    strings (e.g. one sample row lists "RB"/"K" among *defensive*
    positions, clearly a tracking artifact), not reliable per-play tags.

    Positions outside RB/WR/TE (QB, OL, DL, LB, DB, K, P, LS) are dropped —
    not meaningful for a receiving/rushing position-group aggregation.
    """
    ros = seasonal_rosters[seasonal_rosters["position"].isin(["RB", "WR", "TE"])]
    return (
        ros[["player_id", "season", "position"]]
        .drop_duplicates(subset=["player_id", "season"])
        .rename(columns={"position": "position_group"})
    )


def add_player_position(weekly: pd.DataFrame, seasonal_rosters: pd.DataFrame) -> pd.DataFrame:
    """
    Attach each player's own position group (RB/WR/TE) for that season —
    needed to know which position group's defensive-vulnerability numbers
    apply to a given player's matchup (see add_defensive_matchup_context).
    Players outside RB/WR/TE (mostly QBs picking up a rush attempt) get
    NaN position_group and fall back to neutral through
    scoring.score_situation's standard missing-data path.
    """
    return weekly.merge(_position_lookup(seasonal_rosters), on=["player_id", "season"], how="left")


def aggregate_redzone_allowed(pbp: pd.DataFrame, seasonal_rosters: pd.DataFrame) -> pd.DataFrame:
    """
    Same red zone / inside-10 / goal-line touch and TD counting as
    aggregate_redzone_game, but grouped by the DEFENDING team and the
    toucher's position group instead of the offensive player — one row
    per (defteam, season, week, position_group) with touches/TDs allowed.
    Reuses _touches and _band_agg directly rather than reimplementing the
    counting logic.

    Touches with no resolvable RB/WR/TE position (mostly QB scrambles) are
    dropped via the inner join against _position_lookup — a defense's
    QB-scramble-allowed rate isn't a receiving/rushing "position group
    vulnerability" in the sense this table measures.
    """
    touches = _touches(pbp)
    touches = touches.merge(_position_lookup(seasonal_rosters), on=["player_id", "season"], how="inner")

    keys = ["defteam", "season", "week", "position_group"]
    rz = _band_agg(touches, "rz", keys, RED_ZONE)
    i10 = _band_agg(touches, "i10", keys, INSIDE_10)
    gl = _band_agg(touches, "gl", keys, GOAL_LINE)

    out = rz.merge(i10, on=keys, how="left").merge(gl, on=keys, how="left")
    for c in out.columns:
        if c.endswith(("_touches", "_tds")):
            out[c] = out[c].fillna(0).astype(int)

    return out.sort_values(["season", "week", "defteam", "position_group"])


def _cumulative_through_prior_week(df: pd.DataFrame, col: str, group_cols: list[str]) -> pd.Series:
    """
    Season-to-date cumulative total of col through the prior week only
    (cumsum then shift(1)) within each group_cols group. Same cumsum+shift
    pattern as scoring.py's _season_cumulative — duplicated here rather
    than imported, since this needs to run on the un-fanned defense-allowed
    table before add_defensive_matchup_context's join (which happens in
    this module), and nfl/'s modules don't cross-import each other's
    private helpers.
    """
    g = df.groupby(group_cols)[col]
    return g.transform(lambda s: s.cumsum().shift(1)).fillna(0)


def add_opponent(weekly: pd.DataFrame, schedules: pd.DataFrame) -> pd.DataFrame:
    """
    Attach each row's opponent (defteam — the team on defense that game)
    via import_schedules()'s home_team/away_team, keyed by game_id. Needed
    to look up the right defense's numbers in add_defensive_matchup_context.
    """
    sched = schedules[["game_id", "home_team", "away_team"]].drop_duplicates()
    weekly = weekly.merge(sched, on="game_id", how="left")
    weekly["defteam"] = np.where(weekly["posteam"] == weekly["home_team"], weekly["away_team"], weekly["home_team"])
    return weekly.drop(columns=["home_team", "away_team"])


def add_environment_data(weekly: pd.DataFrame, schedules: pd.DataFrame) -> pd.DataFrame:
    """
    Attach roof/temp/wind onto weekly by game_id — a plain data join, no
    scoring math (see scoring.score_situation for the environment
    formula). import_schedules() already has this; checked null rates
    before building anything new: temp/wind are populated for ~83% of
    outdoor games and correctly NaN for dome/closed games.
    """
    sched = schedules[["game_id", "roof", "temp", "wind"]].drop_duplicates()
    return weekly.merge(sched, on="game_id", how="left")


def add_defensive_matchup_context(weekly: pd.DataFrame, allowed_weekly: pd.DataFrame) -> pd.DataFrame:
    """
    Join the defense-allowed rolling/trend columns (aggregate_redzone_allowed
    + add_rolling_windows with group_cols=["defteam","position_group","season"])
    onto each offensive player's row, keyed by (this row's own opponent
    defteam, season, week, this player's own position_group). Requires
    weekly to already have defteam (add_opponent) and position_group
    (add_player_position).

    Only the already-lagged _last1/_last3/_last5/_season_avg columns are
    brought over — never allowed_weekly's raw per-week touches/TDs, which
    would leak that week's own outcome into the defense that same
    offensive players are being scored against that week. Also computes
    and brings over season-to-date cumulative touches/TDs allowed per band
    (for scoring.score_situation's shrinkage-adjusted conversion rate,
    same reasoning as Proven Heat's — a per-game rate mean isn't the right
    denominator for TDs-per-touch) and each defense-position_group's full
    season-total touches allowed (for reference-population qualification —
    a stable, season-long measure, not lagged, matching how offensive
    qualification uses a player's full season total too).

    Columns are prefixed allowed_ to avoid colliding with the offensive
    player's own identically-named rz_touches_last1-style columns.
    """
    allowed_weekly = allowed_weekly.copy()
    group_keys = ["defteam", "position_group", "season"]

    allowed_weekly["season_total_rz_touches_allowed"] = (
        allowed_weekly.groupby(group_keys)["rz_touches"].transform("sum")
    )
    cum_cols = []
    for band in ("gl", "i10", "rz"):
        for stat in ("touches", "tds"):
            col = f"{band}_{stat}"
            cum_col = f"cum_{col}"
            allowed_weekly[cum_col] = _cumulative_through_prior_week(allowed_weekly, col, group_keys)
            cum_cols.append(cum_col)

    keep = [c for c in allowed_weekly.columns if c.endswith(("_last1", "_last3", "_last5", "_season_avg"))]
    keep += cum_cols + ["season_total_rz_touches_allowed"]

    renamed = allowed_weekly[["defteam", "season", "week", "position_group"] + keep].rename(
        columns={c: f"allowed_{c}" for c in keep}
    )
    return weekly.merge(renamed, on=["defteam", "season", "week", "position_group"], how="left")


def build_id_crosswalk(id_table: pd.DataFrame, seasonal_rosters: pd.DataFrame) -> pd.DataFrame:
    """
    Build a pfr_id -> gsis-style player_id crosswalk for joining PFR-sourced
    data (e.g. snap counts) onto play-by-play-derived tables, which key
    players by gsis id.

    Combines nfl_data_py's career-spanning import_ids() table with
    season-specific import_seasonal_rosters() rows, since import_ids() is
    fantasy-platform-curated and is missing some players outright (in
    practice, mostly offensive linemen — irrelevant for fantasy but not for
    play-by-play, since a botched-snap recovery can occasionally give a
    lineman a rush attempt). Any pfr_id or gsis_id that maps to more than one
    partner id across the two sources is dropped rather than guessed at —
    these are rare (single digits) but real data-quality conflicts in the
    upstream crosswalks.
    """
    a = id_table.dropna(subset=["gsis_id", "pfr_id"])[["gsis_id", "pfr_id"]]
    b = (
        seasonal_rosters.dropna(subset=["player_id", "pfr_id"])[["player_id", "pfr_id"]]
        .rename(columns={"player_id": "gsis_id"})
    )
    combined = pd.concat([a, b], ignore_index=True).drop_duplicates()

    ambiguous_gsis = combined[combined.duplicated("gsis_id", keep=False)]["gsis_id"].unique()
    ambiguous_pfr = combined[combined.duplicated("pfr_id", keep=False)]["pfr_id"].unique()
    clean = combined[
        ~combined["gsis_id"].isin(ambiguous_gsis) & ~combined["pfr_id"].isin(ambiguous_pfr)
    ]
    return clean.reset_index(drop=True)


def add_snap_shares(weekly: pd.DataFrame, snap_counts: pd.DataFrame, id_crosswalk: pd.DataFrame) -> pd.DataFrame:
    """
    Join snap-count data (nfl_data_py.import_snap_counts, sourced from Pro
    Football Reference) onto the red zone weekly table by (game_id, player).

    snap_counts identifies players by pfr_player_id rather than the
    gsis-style player_id play-by-play uses, so id_crosswalk (see
    build_id_crosswalk) translates between the two.

    Adds:
      offense_snaps      - the player's own offensive snap count that game
      team_offense_snaps - the team's total offensive snaps that game, taken
                            as the max offense_snaps across the team's roster
                            that game. More robust than back-solving from
                            PFR's own offense_pct, which is rounded to 2
                            decimals and disagrees by several snaps in some
                            games once you invert it.
      snap_share          - offense_snaps / team_offense_snaps

    Note: nfl_data_py's snap-count data is a game-level aggregate only —
    it has no play-by-play/personnel granularity, so it cannot tell you
    which red-zone plays specifically a player was on the field for (as
    opposed to touched the ball on). That's a real gap, not an oversight;
    closing it needs a separate participation/personnel dataset this
    module doesn't have access to.

    Rows are never dropped for a missing snap match — a player who doesn't
    resolve to a snap_counts row (crosswalk gap, or genuinely didn't play
    an offensive snap that game) gets NaN in the three new columns rather
    than being silently removed. Check the NaN rate before trusting
    downstream aggregates.
    """
    snaps = snap_counts[snap_counts["offense_pct"].notna()].copy()
    snaps = snaps.merge(id_crosswalk, left_on="pfr_player_id", right_on="pfr_id", how="left")

    team_totals = (
        snaps.groupby(["game_id", "team"])["offense_snaps"]
        .max()
        .rename("team_offense_snaps")
        .reset_index()
    )
    snaps = snaps.merge(team_totals, on=["game_id", "team"], how="left")
    snaps["snap_share"] = (snaps["offense_snaps"] / snaps["team_offense_snaps"]).round(3)

    snaps_for_join = (
        snaps.dropna(subset=["gsis_id"])[
            ["game_id", "gsis_id", "offense_snaps", "team_offense_snaps", "snap_share"]
        ]
        .drop_duplicates(subset=["game_id", "gsis_id"])
        .rename(columns={"gsis_id": "player_id"})
    )

    return weekly.merge(snaps_for_join, on=["game_id", "player_id"], how="left")


def _skill_position_depth_chart(depth_charts: pd.DataFrame) -> pd.DataFrame:
    """
    Shared prep for both depth-chart functions below: skill-position rows
    only (RB/WR/TE), excluding special-teams/dual-role side-listings (e.g.
    a RB also listed as a punt returner), collapsed to each player's own
    min(depth_team) per (team, season, week, depth_position) — depth_team
    is not a clean 1/2/3 ranking (teams routinely list multiple players at
    the same rank for the same depth_position, e.g. a 3-WR personnel
    package listing all three as "1"), so this only ever compares a player
    against his own rank history over time, never against teammates at a
    single point in time.

    KNOWN GAP, bigger than a coverage footnote: nflverse changed the
    depth-chart source schema at some point in 2025 — the new file has
    entirely different columns (dt/team/player_name/pos_abb/pos_slot/
    pos_rank), no gsis_id at all, and no `season` column, so
    import_depth_charts([2022, 2024, 2025]) silently drops every 2025 row
    rather than erroring. This function only works against the old schema
    (2022, 2024). Every 2025 row — and critically, the live 2026 season
    this whole pillar is meant to launch into — gets NaN depth_rank and
    falls back to neutral through score_role_momentum's standard
    missing-data path. That means depth-chart movement does NOT "just
    work" once this pillar ships: the live weekly job needs a new parser
    built against the new schema (name-based player matching, no gsis_id,
    a raw timestamp instead of a week number) before this signal means
    anything for the season that actually matters. This is an explicit
    open item, not a resolved one.
    """
    dc = depth_charts[
        depth_charts["position"].isin(["RB", "WR", "TE"])
        & (depth_charts["depth_position"] == depth_charts["position"])
    ].copy()
    dc["depth_team"] = pd.to_numeric(dc["depth_team"], errors="coerce")
    return (
        dc.groupby(["gsis_id", "season", "week", "club_code", "depth_position"])["depth_team"]
        .min()
        .rename("depth_rank")
        .reset_index()
    )


def add_depth_chart_rank(weekly: pd.DataFrame, depth_charts: pd.DataFrame) -> pd.DataFrame:
    """
    Join each player's own skill-position depth-chart rank onto the weekly
    table by (player_id, season, week). See _skill_position_depth_chart for
    the tie-handling and 2025-schema-gap caveats — both apply here.
    """
    dc = _skill_position_depth_chart(depth_charts)[
        ["gsis_id", "season", "week", "depth_rank"]
    ].rename(columns={"gsis_id": "player_id"})
    return weekly.merge(dc, on=["player_id", "season", "week"], how="left")


def add_injury_context(weekly: pd.DataFrame, depth_charts: pd.DataFrame, injuries: pd.DataFrame) -> pd.DataFrame:
    """
    For each player/week, attach the set of report_status values (Out/
    Doubtful/Questionable) among teammates at the same skill-position depth
    chart with a strictly lower (better) depth_rank that week — i.e.
    players this player is currently behind. Mapping a status to a
    severity score is a scoring decision (see scoring.CONFIG), not made
    here — this only attaches the raw statuses found, as
    ahead_injury_statuses (a list per row, possibly empty). An empty list
    means either no one is ranked ahead, or no one ranked ahead has an
    injury designation — those are collapsed together deliberately, since
    both cases mean the same thing for this signal: no vacated opportunity
    this week.

    Same 2025 depth-chart schema gap as add_depth_chart_rank applies —
    this needs depth_rank to determine who's "ahead," so 2025 rows get an
    empty list here regardless of injuries' own data (which does have
    clean gsis_id coverage across all three seasons — the gap is entirely
    on the depth-chart side).
    """
    dc = _skill_position_depth_chart(depth_charts)

    inj = injuries[["gsis_id", "season", "week", "report_status"]].drop_duplicates(
        subset=["gsis_id", "season", "week"], keep="last"
    )
    dc = dc.merge(inj, on=["gsis_id", "season", "week"], how="left")

    ahead = dc.merge(
        dc, on=["season", "week", "club_code", "depth_position"], suffixes=("", "_teammate")
    )
    ahead = ahead[ahead["depth_rank_teammate"] < ahead["depth_rank"]]
    ahead = ahead[ahead["report_status_teammate"].notna()]

    ahead_statuses = (
        ahead.groupby(["gsis_id", "season", "week"])["report_status_teammate"]
        .apply(list)
        .rename("ahead_injury_statuses")
        .reset_index()
        .rename(columns={"gsis_id": "player_id"})
    )

    weekly = weekly.merge(ahead_statuses, on=["player_id", "season", "week"], how="left")
    weekly["ahead_injury_statuses"] = weekly["ahead_injury_statuses"].apply(
        lambda v: v if isinstance(v, list) else []
    )
    return weekly


def add_rolling_windows(weekly: pd.DataFrame, metrics: list[str] = None, group_cols: list[str] = None) -> pd.DataFrame:
    """
    Add last-1 (most recent game), last-3, last-5, and season-to-date
    rolling means within each group_cols group — the trend-detection
    inputs the blueprint calls for (Last Game / Last 3 / Last 5 / Season).

    group_cols defaults to ["player_id", "season"] (the offensive-side
    grouping). Pass group_cols=["defteam", "position_group", "season"] to
    roll the defense-allowed table (aggregate_redzone_allowed's output)
    the same way — same function, same shift(1) discipline, just a
    different grouping axis, so the defense side reuses this rather than
    duplicating its own rolling-window logic.

    Defaults to rz_touches, rz_touch_share, rz_tds, and snap_share — call
    add_snap_shares before this so snap_share exists to roll (order matters:
    reversing it silently drops snap_share from this function's default
    metrics list since the column wouldn't exist yet). Pass an explicit
    metrics list for the defense-allowed table, e.g.
    ["rz_touches", "rz_tds", "i10_touches", "i10_tds", "gl_touches", "gl_tds"]
    — it has no touch_share/snap_share concept.

    Every window is shift(1)'d, including last1 (a plain shift with no
    window at all): the value landing on a given row is always what was
    true heading INTO that game, never anything from the game itself. This
    applies uniformly across every metric — a row's own current-game value
    must never leak into that same row's pre-game score.

    Grouped by (..., season), not just the entity id: a player's (or
    defense's) week-1 row in a new season gets NaN across every
    _last*/_season_avg column (no prior-season carryover), which the
    scoring functions already route through their standard missing-data ->
    neutral-50 path. Previously this grouped by player_id alone, so a
    player's first game of a new season (or a new team, with an unbacked
    season gap in between) would silently pull rolling values from
    wherever their history last left off — e.g. a player's week 1 with a
    new team showing a "recent" TD rate that was actually from a different
    team two real-world seasons earlier.
    """
    if group_cols is None:
        group_cols = ["player_id", "season"]
    weekly = weekly.sort_values(group_cols + ["week"]).copy()
    if metrics is None:
        metrics = ["rz_touches", "rz_touch_share", "rz_tds", "snap_share"]

    for m in metrics:
        g = weekly.groupby(group_cols)[m]
        weekly[f"{m}_last1"] = g.transform(lambda s: s.shift(1))
        weekly[f"{m}_last3"] = g.transform(lambda s: s.rolling(3, min_periods=1).mean().shift(1))
        weekly[f"{m}_last5"] = g.transform(lambda s: s.rolling(5, min_periods=1).mean().shift(1))
        weekly[f"{m}_season_avg"] = g.transform(lambda s: s.expanding().mean().shift(1))

    return weekly
