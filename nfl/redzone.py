"""
Shared red zone / inside-10 / goal-line usage aggregation for NFL play-by-play.

Zone definitions match the three bands named explicitly in the NFL Master
Blueprint (Red Zone Trends, RB/WR/TE Trends). Imported by both
scripts/backfill_redzone.py (batch backfill) and the live weekly job, so
there is exactly one implementation of this logic to keep in sync.
"""

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


def aggregate_redzone_game(pbp: pd.DataFrame) -> pd.DataFrame:
    """
    Produce one row per player per game with red zone / inside-10 /
    goal-line touch and TD counts, plus each player's share of his team's
    total red-zone touches that game (the "opportunity concentration"
    signal from the blueprint).
    """
    touches = _touches(pbp)

    keys = ["game_id", "season", "week", "posteam", "player_id", "player_name"]

    def band_agg(min_df: pd.DataFrame, label: str) -> pd.DataFrame:
        g = (
            min_df.groupby(keys)
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
        return g

    rz = band_agg(touches[touches["yardline_100"] <= RED_ZONE], "rz")
    i10 = band_agg(touches[touches["yardline_100"] <= INSIDE_10], "i10")
    gl = band_agg(touches[touches["yardline_100"] <= GOAL_LINE], "gl")

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


def add_rolling_windows(weekly: pd.DataFrame, metrics: list[str] = None) -> pd.DataFrame:
    """
    Add last-1 (most recent game), last-3, last-5, and season-to-date
    rolling means per player-season — the trend-detection inputs the
    blueprint calls for (Last Game / Last 3 / Last 5 / Season).

    Defaults to rz_touches, rz_touch_share, rz_tds, and snap_share — call
    add_snap_shares before this so snap_share exists to roll (order matters:
    reversing it silently drops snap_share from this function's default
    metrics list since the column wouldn't exist yet).

    Every window is shift(1)'d, including last1 (a plain shift with no
    window at all): the value landing on a given row is always what was
    true heading INTO that game, never anything from the game itself. This
    applies uniformly across all four metrics, snap_share included — a
    player's own current-game snap_share must never leak into that same
    row's pre-game score.

    Grouped by (player_id, season), not just player_id: a player's week-1
    row in a new season gets NaN across every _last*/_season_avg column
    (no prior-season carryover), which score_td_opportunity already routes
    through its standard missing-data -> neutral-50 path — matches
    scoring.py's _season_cumulative, which was written season-scoped from
    the start. Previously this grouped by player_id alone, so a player's
    first game of a new season (or a new team, with an unbacked season gap
    in between) would silently pull rolling values from wherever their
    history last left off — e.g. a player's week 1 with a new team showing
    a "recent" TD rate that was actually from a different team two
    real-world seasons earlier.
    """
    weekly = weekly.sort_values(["player_id", "season", "week"]).copy()
    if metrics is None:
        metrics = ["rz_touches", "rz_touch_share", "rz_tds", "snap_share"]

    for m in metrics:
        g = weekly.groupby(["player_id", "season"])[m]
        weekly[f"{m}_last1"] = g.transform(lambda s: s.shift(1))
        weekly[f"{m}_last3"] = g.transform(lambda s: s.rolling(3, min_periods=1).mean().shift(1))
        weekly[f"{m}_last5"] = g.transform(lambda s: s.rolling(5, min_periods=1).mean().shift(1))
        weekly[f"{m}_season_avg"] = g.transform(lambda s: s.expanding().mean().shift(1))

    return weekly
