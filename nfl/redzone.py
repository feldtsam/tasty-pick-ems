"""
Shared red zone / inside-10 / goal-line usage aggregation for NFL play-by-play.

Zone definitions match the three bands named explicitly in the NFL Master
Blueprint (Red Zone Trends, RB/WR/TE Trends). Imported by both
scripts/backfill_redzone.py (batch backfill) and the live weekly job, so
there is exactly one implementation of this logic to keep in sync.
"""

import numpy as np
import pandas as pd

from roster_match import match_player_names

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


def _canonical_player_names(seasonal_rosters: pd.DataFrame) -> pd.DataFrame:
    """
    (player_id, season) -> player_name from import_seasonal_rosters() —
    the single canonical display name for a player that season, used to
    attach player_name onto aggregate_redzone_game's output AFTER
    aggregation, rather than grouping by whatever name string a given
    play-by-play row happens to carry (see aggregate_redzone_game for
    why that used to be unsafe).

    NOT filtered to RB/WR/TE (unlike _position_lookup) — aggregate_
    redzone_game's own population isn't position-restricted either (a
    QB scramble into the red zone still gets a row), so this needs a
    name for every player who might touch the ball, not just skill
    positions.

    Confirmed directly before relying on this: every (player_id,
    season) pair that appears in real 2022/2024/2025 touch data has
    exactly one seasonal_rosters entry (100% coverage, zero rows with
    more than one distinct name per player per season) — no fallback
    path needed for a missing or ambiguous name.
    """
    return (
        seasonal_rosters.dropna(subset=["player_id"])
        .drop_duplicates(subset=["player_id", "season"])[["player_id", "season", "player_name"]]
    )


def aggregate_redzone_game(pbp: pd.DataFrame, seasonal_rosters: pd.DataFrame) -> pd.DataFrame:
    """
    Produce one row per player per game with red zone / inside-10 /
    goal-line touch and TD counts, plus each player's share of his team's
    total red-zone touches that game (the "opportunity concentration"
    signal from the blueprint).

    Grouped by (game_id, season, week, posteam, player_id) only —
    player_name is deliberately NOT part of the grouping/merge key.
    REAL BUG this fixes, not a hypothetical: play-by-play's own
    rusher_player_name/receiver_player_name can change value WITHIN a
    single game for the same player_id — confirmed directly (Diontae
    Johnson, 00-0035216, game 2024_03_CAR_LV: receiver_player_id
    constant across all 14 targets, but receiver_player_name flips from
    "Dio.Johnson" to "Di.Johnson" partway through) — an nflverse play-
    by-play naming quirk, not a join bug on this side. With player_name
    previously IN the grouping key, that flip silently split his real
    red-zone touches across two output rows for the same game instead
    of one. Scanned the full 2022/2024/2025 touches table: 15 distinct
    player_ids carry more than one name string somewhere, but only 2
    ever flip WITHIN a single game, and only this one lands inside the
    red-zone band this function actually aggregates over — so this was
    a live, if rare, correctness bug, not just a cosmetic one.

    player_name is attached as a separate step after aggregation, from
    seasonal_rosters' own canonical full name (see
    _canonical_player_names) by (player_id, season) — never from play-
    by-play's own name fields, which is what made the grouping key
    unsafe in the first place. This also means every row's player_name
    is now in the same full-name format build_stub_week.py's stub rows
    already use (also sourced from seasonal_rosters), rather than the
    abbreviated "F.Last" play-by-play style — a real, visible format
    change across the whole historical table, not a side effect to
    discover later.
    """
    touches = _touches(pbp)

    keys = ["game_id", "season", "week", "posteam", "player_id"]

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

    out = out.merge(_canonical_player_names(seasonal_rosters), on=["player_id", "season"], how="left")

    return out.sort_values(["season", "week", "game_id", "rz_touches"], ascending=[True, True, True, False])


def aggregate_whole_game_targets(pbp: pd.DataFrame) -> pd.DataFrame:
    """
    One row per player per game with WHOLE-FIELD target count and share of
    team targets that game — NOT red-zone-scoped, unlike every other
    aggregation in this file. Built specifically to support WR Trends'
    target-share-movement signal (see shelves.py), which the Trend Shelf
    Architecture review found needs a whole-game denominator that the
    red-zone-scoped pillars structurally don't have (Role & Momentum's own
    touch-share trend is deliberately red-zone-scoped — the right choice
    for its own purpose, just not this one).

    Deliberately kept OUT of the scored pipeline (run_pipeline, scoring.py)
    — this is shelf-display-layer data, not a pillar input. Reuses _touches
    (already validated, already the source every other aggregation in this
    file is built from) rather than re-parsing pbp; the only new logic here
    is restricting to touch_type == "target" (receiving only — target
    share is a receiving-hierarchy concept, rushing touches don't belong
    in this denominator) and NOT restricting by yardline_100 at all.
    """
    touches = _touches(pbp)
    targets = touches[touches["touch_type"] == "target"]

    keys = ["game_id", "season", "week", "posteam", "player_id"]
    by_player = targets.groupby(keys).size().rename("targets").reset_index()

    team_targets = (
        targets.groupby(["game_id", "posteam"]).size().rename("team_targets").reset_index()
    )
    out = by_player.merge(team_targets, on=["game_id", "posteam"], how="left")
    out["target_share"] = (out["targets"] / out["team_targets"]).round(3)
    return out


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
    Parses the PRE-2025 depth-chart schema only (season/week/gsis_id/
    depth_team/depth_position/club_code columns) — see
    _new_schema_depth_chart for 2025+, which nflverse changed to a
    different schema entirely. Both are combined in add_depth_chart_rank.

    CORRECTION to a claim made earlier in this file's history: the new
    schema does NOT lack a gsis_id — it has one, populated for ~99% of
    rows. That was wrong, based on a truncated column preview rather than
    a full schema inspection; verified directly before building
    _new_schema_depth_chart. gsis_id is the primary join key there too,
    same as here — name-matching is only a fallback for the small slice
    missing it.

    Skill-position rows only (RB/WR/TE), excluding special-teams/dual-role
    side-listings (e.g. a RB also listed as a punt returner), collapsed to
    each player's own min(depth_team) per (team, season, week,
    depth_position) — depth_team is not a clean 1/2/3 ranking (teams
    routinely list multiple players at the same rank for the same
    depth_position, e.g. a 3-WR personnel package listing all three as
    "1"), so this only ever compares a player against his own rank
    history over time, never against teammates at a single point in time.
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


def _new_schema_depth_chart(
    depth_charts: pd.DataFrame, schedules: pd.DataFrame, seasonal_rosters: pd.DataFrame
) -> pd.DataFrame:
    """
    Parses the 2025+ depth-chart schema (dt/team/player_name/gsis_id/
    pos_abb/pos_rank — no season/week columns at all) into
    (player_id, season, week, team, position_group, depth_rank) — the
    same shape _skill_position_depth_chart produces for the old schema
    (club_code/depth_position renamed to team/position_group at the
    combination step), so _combined_depth_chart can concatenate both
    transparently for its two callers, add_depth_chart_rank and
    add_injury_context.

    MATCHING: gsis_id is present and correct for ~99% of rows here too —
    used directly, same as the old schema, no name-matching needed for
    the vast majority. For the small slice missing it, falls back to
    roster_match.match_player_names — the same 3-way-classified matcher
    (rookie_or_new / position_out_of_scope / team_mismatch)
    market_value.py's match_attd_players uses — with the row's own `team`
    column (already an abbreviation) as its single candidate team. Unlike
    the old schema, pos_rank has NO tie-mass problem: spot-checked WR/RB/TE
    across the full 2025 pull and found zero ties (every player at a
    given team/snapshot/position has a distinct pos_rank), so there's no
    equivalent of the old schema's "compare only against own history"
    workaround needed here — this still does it anyway for consistency
    with the old schema's output shape, not because it's required.

    WEEK/SEASON DERIVATION — the part most likely to have a real edge
    case, read carefully: this schema has no week or season column at
    all, only a raw `dt` timestamp (roughly one snapshot per team per
    day). Both are derived via a per-team "next upcoming game" as-of join
    (pd.merge_asof, direction="forward") against schedules, using each
    game's PRECISE kickoff timestamp — gameday + gametime, localized as
    America/New_York and converted to UTC — not gameday alone. This
    matters: gameday is a bare calendar date (defaults to midnight UTC),
    and verified concretely (Kansas City's 2025 schedule) that using it
    alone silently misclassifies any snapshot taken on gameday itself,
    before kickoff, as belonging to the FOLLOWING week — because a
    same-day snapshot's timestamp (e.g. 7am UTC) is already "after"
    midnight UTC of gameday, so a forward as-of join skips past that
    week's game to the next one. Switching to the real kickoff timestamp
    (e.g. 8pm ET Thursday -> 00:20 UTC Friday) fixed it: a 7am UTC
    Friday-morning snapshot now correctly stays mapped to that week
    (still hours before kickoff), and only rolls over to the next week
    once the actual kickoff has passed.

    Bye weeks are handled correctly for free — the as-of join simply
    skips a team's bye and lands on their next real game, no special
    casing needed. A dt with no future scheduled game in the schedules
    passed in (true off-season activity, or a season/week not covered by
    the schedules argument) gets no season/week match and is dropped —
    not guessed at.

    RESIDUAL AMBIGUITY, not fully resolved: multiple daily snapshots land
    in the same (team, season, week) bucket (roughly one per day across
    a ~7-day window) — the LATEST snapshot before that week's kickoff is
    kept as authoritative (freshest depth chart heading into the game),
    matching the old schema's one-row-per-team-per-week grain. This is a
    deliberate choice, not a resolved non-issue: a genuinely meaningful
    in-week promotion (e.g. an injury elevates a backup mid-week) is
    captured correctly by using the latest snapshot, but it also means
    any earlier-in-the-week depth chart state for that game is discarded
    entirely, not retained anywhere.
    """
    dc = depth_charts[depth_charts["dt"].notna() & depth_charts["pos_abb"].isin(["RB", "WR", "TE"])].copy()
    dc["dt"] = pd.to_datetime(dc["dt"])
    # depth_charts is the combined old+new-schema pull — old-schema's own
    # season/week columns exist on this frame too (all-NaN for the
    # dt-having subset filtered above), which would collide with
    # team_games' season/week below. Drop them; they're about to be
    # derived fresh from the schedule join instead.
    dc = dc.drop(columns=["season", "week"], errors="ignore")

    sched = schedules.copy()
    kickoff_et = pd.to_datetime(sched["gameday"] + " " + sched["gametime"]).dt.tz_localize("America/New_York")
    sched["kickoff_utc"] = kickoff_et.dt.tz_convert("UTC")
    team_games = pd.concat(
        [
            sched[["season", "week", "kickoff_utc", "home_team"]].rename(columns={"home_team": "team"}),
            sched[["season", "week", "kickoff_utc", "away_team"]].rename(columns={"away_team": "team"}),
        ]
    ).sort_values("kickoff_utc")

    dc = dc.sort_values("dt")
    dc = pd.merge_asof(dc, team_games, left_on="dt", right_on="kickoff_utc", by="team", direction="forward")
    dc = dc.dropna(subset=["season", "week"])
    dc["season"] = dc["season"].astype(int)
    dc["week"] = dc["week"].astype(int)

    # Keep only the latest snapshot per (team, season, week, player) — see
    # RESIDUAL AMBIGUITY above.
    dc = dc.sort_values("dt").drop_duplicates(subset=["team", "season", "week", "player_name", "pos_abb"], keep="last")

    has_gsis = dc[dc["gsis_id"].notna()].copy()
    has_gsis["player_id"] = has_gsis["gsis_id"]

    no_gsis = dc[dc["gsis_id"].isna()].copy()
    matched_by_name = pd.DataFrame(columns=["player_id", "season", "week", "pos_rank"])
    if len(no_gsis):
        no_gsis["_candidate_teams"] = no_gsis["team"].apply(lambda t: {t})
        matched_parts = []
        for season, grp in no_gsis.groupby("season"):
            m, _unmatched = match_player_names(
                grp, seasonal_rosters, season, name_col="player_name", candidate_teams_col="_candidate_teams"
            )
            if len(m):
                matched_parts.append(m)
        if matched_parts:
            matched_by_name = pd.concat(matched_parts, ignore_index=True)

    combined = pd.concat([has_gsis, matched_by_name], ignore_index=True)
    return (
        combined.groupby(["player_id", "season", "week", "team", "pos_abb"])["pos_rank"]
        .min()
        .rename("depth_rank")
        .reset_index()
        .rename(columns={"pos_abb": "position_group"})
    )


def _combined_depth_chart(
    depth_charts: pd.DataFrame, schedules: pd.DataFrame = None, seasonal_rosters: pd.DataFrame = None
) -> pd.DataFrame:
    """
    Shared unified depth-chart lookup: (player_id, season, week, team,
    position_group, depth_rank), combining the pre-2025 schema (via
    _skill_position_depth_chart) and the 2025+ schema (via
    _new_schema_depth_chart) into one consistent table — parsed once,
    used by both add_depth_chart_rank (just the depth_rank column) and
    add_injury_context (also needs team/position_group, for the "who's
    ahead on the same depth chart" self-join). Neither caller re-parses
    the new schema separately.

    schedules and seasonal_rosters are required only to resolve 2025+
    rows (week/season derivation and the gsis_id-fallback name match —
    see _new_schema_depth_chart). Pass neither to fall back to
    old-schema-only behavior.
    """
    old = _skill_position_depth_chart(depth_charts).rename(
        columns={"gsis_id": "player_id", "club_code": "team", "depth_position": "position_group"}
    )
    parts = [old]
    if schedules is not None and seasonal_rosters is not None:
        parts.append(_new_schema_depth_chart(depth_charts, schedules, seasonal_rosters))
    return pd.concat(parts, ignore_index=True)


def add_depth_chart_rank(
    weekly: pd.DataFrame, depth_charts: pd.DataFrame, schedules: pd.DataFrame = None,
    seasonal_rosters: pd.DataFrame = None,
) -> pd.DataFrame:
    """
    Join each player's own skill-position depth-chart rank onto the weekly
    table by (player_id, season, week, team, position_group). Drop-in
    replacement regardless of which schema a given season's rows use —
    see _combined_depth_chart, which both this function and add_injury_
    context share. Callers (score_role_momentum included) see no
    difference.

    Requires weekly to already have position_group (add_player_position)
    — run_pipeline calls add_player_position before this for exactly that
    reason.

    Matches on team (weekly's own posteam — ground truth from play-by-play)
    as well as player_id/season/week, not just the latter — caught via a
    real case during validation: a player traded mid-season (Adam Thielen,
    CAR -> MIN -> PIT in 2025) can have depth-chart entries on TWO teams
    that each independently resolve to the same (season, week) — his early
    CAR snapshots and his later MIN snapshots both landed on "week 1"
    because each team's own week-1 kickoff hadn't happened yet as of those
    respective snapshots. Dropping team before merging silently fanned
    weekly's join out to 2 rows for those players. Matching on posteam
    picks the entry for whichever team he actually played for that week,
    the same ground truth the rest of this table already uses.

    ALSO matches on position_group, same reasoning, a second real case
    caught the same way: a player genuinely listed on TWO position-
    specific depth charts for the same team/week (Jackson Meeks, TE
    rank 4 AND WR rank 8, DET 2026 Week 1; Eli Heidenreich, RB rank 6
    AND WR rank 7, PIT 2026 Week 1 — 5 such cases confirmed across
    2022-2026) fanned weekly out to 2 rows the same way the Thielen case
    did, just along a different axis. Matching on weekly's own
    position_group (the player's single canonical position from
    seasonal_rosters, not the depth chart's — see add_player_position)
    resolves to exactly the one depth-chart listing that actually applies
    to this player, rather than merging in both.

    schedules and seasonal_rosters are required only to resolve 2025+
    rows — pass neither to fall back to old-schema-only behavior.
    """
    dc = _combined_depth_chart(depth_charts, schedules, seasonal_rosters)[
        ["player_id", "season", "week", "team", "position_group", "depth_rank"]
    ]
    return weekly.merge(
        dc, left_on=["player_id", "season", "week", "posteam", "position_group"],
        right_on=["player_id", "season", "week", "team", "position_group"], how="left",
    ).drop(columns=["team"])


def add_injury_context(
    weekly: pd.DataFrame, depth_charts: pd.DataFrame, injuries: pd.DataFrame, schedules: pd.DataFrame = None,
    seasonal_rosters: pd.DataFrame = None,
) -> pd.DataFrame:
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

    Now handles both schemas, same as add_depth_chart_rank — via the same
    _combined_depth_chart lookup (not re-parsed a second time here).
    schedules and seasonal_rosters are required only to resolve 2025+ rows;
    pass neither to fall back to old-schema-only behavior (2025+ rows get
    an empty ahead_injury_statuses list, same as before this fix).

    Requires weekly to already have position_group (add_player_position)
    — run_pipeline calls add_player_position before this for exactly that
    reason, same as add_depth_chart_rank.

    Same team-matching fix as add_depth_chart_rank, same reason: a player
    traded mid-season can have depth-chart entries on two teams that both
    resolve to the same (season, week), so the final merge onto weekly
    matches on team (weekly's own posteam) too, not just player_id/season/
    week — otherwise a traded player's ahead_injury_statuses could mix in
    a "teammate" from the team he'd already left.

    ALSO matches on position_group in that same final merge, same
    reasoning as add_depth_chart_rank's own position_group fix: a player
    genuinely listed on two position-specific depth charts for the same
    team/week (Jackson Meeks, TE and WR; Eli Heidenreich, RB and WR — see
    add_depth_chart_rank) doesn't fan out INTO A DUPLICATE ROW here (the
    groupby below already collapses back to one row per player/team), but
    without position_group in that groupby, the collapse silently BLENDS
    both listings' teammate lists together — Meeks' ahead_injury_statuses
    would include teammates ahead of him on the TE chart (not his real
    position) as well as the WR chart (his real one, per seasonal_
    rosters), a wrong-teammates bug that produced no visible symptom
    (row count looked fine) until traced directly. Grouping by position_
    group too keeps each position's teammate list separate, and the final
    merge onto weekly then picks only the one matching the player's own
    canonical position.

    ALSO attaches ahead_injured_teammates — the same "who's ahead and
    hurt" population as ahead_injury_statuses, but as
    [{player_id, player_name, status}, ...] instead of just [status,
    ...]. Added for nfl/role_changes.py: a Role Changes story about
    externally-created opportunity needs to name the specific teammate
    whose injury opened the door (e.g. "Bucky Irving (Out)"), not just
    report that someone ahead was hurt. Purely additive — every existing
    caller gets one more column it can ignore; ahead_injury_statuses
    itself is unchanged. player_name comes from _canonical_player_names
    (seasonal_rosters), same source aggregate_redzone_game already uses,
    since _combined_depth_chart's own output never carries a name column
    (both source schemas drop it during their groupby/reset_index) —
    confirmed directly, not assumed, before adding this. Falls back to
    None when seasonal_rosters isn't passed (old-schema-only callers),
    same degrade-gracefully contract the rest of this function already has.
    """
    dc = _combined_depth_chart(depth_charts, schedules, seasonal_rosters)

    inj = (
        injuries[["gsis_id", "season", "week", "report_status"]]
        .drop_duplicates(subset=["gsis_id", "season", "week"], keep="last")
        .rename(columns={"gsis_id": "player_id"})
    )
    dc = dc.merge(inj, on=["player_id", "season", "week"], how="left")
    if seasonal_rosters is not None:
        dc = dc.merge(_canonical_player_names(seasonal_rosters), on=["player_id", "season"], how="left")
    else:
        dc["player_name"] = None

    ahead = dc.merge(dc, on=["season", "week", "team", "position_group"], suffixes=("", "_teammate"))
    ahead = ahead[ahead["depth_rank_teammate"] < ahead["depth_rank"]]
    ahead = ahead[ahead["report_status_teammate"].notna()]

    ahead_statuses = (
        ahead.groupby(["player_id", "season", "week", "team", "position_group"])["report_status_teammate"]
        .apply(list)
        .rename("ahead_injury_statuses")
    )

    ahead_teammates = (
        ahead.groupby(["player_id", "season", "week", "team", "position_group"], group_keys=False)
        .apply(
            lambda g: [
                {"player_id": pid, "player_name": name, "status": status}
                for pid, name, status in zip(
                    g["player_id_teammate"], g["player_name_teammate"], g["report_status_teammate"]
                )
            ],
            include_groups=False,
        )
        .rename("ahead_injured_teammates")
    )

    ahead_agg = pd.concat([ahead_statuses, ahead_teammates], axis=1).reset_index()

    weekly = weekly.merge(
        ahead_agg, left_on=["player_id", "season", "week", "posteam", "position_group"],
        right_on=["player_id", "season", "week", "team", "position_group"], how="left",
    ).drop(columns=["team"])
    weekly["ahead_injury_statuses"] = weekly["ahead_injury_statuses"].apply(
        lambda v: v if isinstance(v, list) else []
    )
    weekly["ahead_injured_teammates"] = weekly["ahead_injured_teammates"].apply(
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
