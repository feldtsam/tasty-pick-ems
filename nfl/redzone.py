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


def add_rolling_windows(weekly: pd.DataFrame) -> pd.DataFrame:
    """
    Add last-3 and last-5 game rolling means per player for rz_touches,
    rz_touch_share, and rz_tds — the trend-detection inputs the blueprint
    calls for (Last Game / Last 3 / Last 5 / Season).
    """
    weekly = weekly.sort_values(["player_id", "season", "week"]).copy()
    metrics = ["rz_touches", "rz_touch_share", "rz_tds"]

    for m in metrics:
        g = weekly.groupby("player_id")[m]
        weekly[f"{m}_last3"] = g.transform(lambda s: s.rolling(3, min_periods=1).mean().shift(1))
        weekly[f"{m}_last5"] = g.transform(lambda s: s.rolling(5, min_periods=1).mean().shift(1))
        weekly[f"{m}_season_avg"] = g.transform(lambda s: s.expanding().mean().shift(1))

    return weekly
