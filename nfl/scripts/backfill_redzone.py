"""
Tasty Pick Ems — NFL Red Zone Backfill

Pulls play-by-play data for the specified seasons and derives per-player,
per-game red zone / inside-10 / goal-line usage via the shared
redzone.aggregate_redzone_game logic, so this backfill and the live weekly
job stay in sync.

This is a standalone, re-runnable batch script — NOT wired into the live
pipeline yet. Run it, inspect/spot-check the output, then wire the live
weekly job up to import redzone.aggregate_redzone_game directly.

Usage:
    python scripts/backfill_redzone.py

Output:
    player_redzone_weekly.csv — one row per player per game
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nfl_data_py as nfl
import pandas as pd

from redzone import add_rolling_windows, aggregate_redzone_game

SEASONS = [2022, 2024, 2025]


def load_pbp(seasons: list[int]) -> pd.DataFrame:
    """Load raw play-by-play for the given seasons."""
    pbp = nfl.import_pbp_data(seasons, downcast=True)
    return pbp


def spot_check(weekly: pd.DataFrame, season: int, player_name_contains: str) -> None:
    """Print a player's game log for manual cross-check against a public
    red-zone stats source (e.g. a known bellcow RB's inside-5 carries)."""
    sub = weekly[
        (weekly["season"] == season)
        & (weekly["player_name"].str.contains(player_name_contains, case=False, na=False))
    ]
    cols = ["week", "player_name", "posteam", "rz_touches", "rz_tds", "gl_touches", "gl_tds", "rz_touch_share"]
    print(sub[cols].to_string(index=False))


if __name__ == "__main__":
    print(f"Loading play-by-play for seasons {SEASONS} ...")
    pbp = load_pbp(SEASONS)

    print("Aggregating red zone / inside-10 / goal-line usage ...")
    weekly = aggregate_redzone_game(pbp)

    print("Adding rolling trend windows ...")
    weekly = add_rolling_windows(weekly)

    out_path = "player_redzone_weekly.csv"
    weekly.to_csv(out_path, index=False)
    print(f"Wrote {len(weekly)} rows to {out_path}")

    # Example spot check — swap in a known bellcow RB to sanity-check
    # against a public red-zone-touches stat before trusting this table.
    spot_check(weekly, season=2024, player_name_contains="Henry")
