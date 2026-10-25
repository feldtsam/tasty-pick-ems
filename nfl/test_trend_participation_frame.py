"""
Option 2a: the participation frame, the merge onto `weekly`, the
games-played mask, and the flag that gates all of it.

Same standalone-script shape as every other test in this directory: no
pytest, a check() helper, a final count, exit 1 on any failure.

    python3 test_trend_participation_frame.py

Nothing here touches a network, a database or an environment variable
belonging to a real run: the flag is exercised through trend_frame's own
reset_cache()/resolve() seam and through explicit booleans.
"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "scripts"))

import trend_frame  # noqa: E402
from redzone import (  # noqa: E402
    PARTICIPATION_WINDOW_METRICS,
    build_participation_frame,
    merge_participation_windows,
)
from role_changes import _games_played  # noqa: E402
from scoring import _trend_delta  # noqa: E402

results = []


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    results.append(bool(cond))
    return bool(cond)


# ---------------------------------------------------------------------------
# Fixtures. One team (SEA), four weeks, built so every rule in
# build_participation_frame's docstring has a row that exercises it.
#
#   G1 RB  wk1 red-zone touch
#          wk2 snaps, no red-zone touch, team DID reach the red zone -> real 0
#          wk3 no snaps at all (bye/inactive)            -> no row
#          wk4 snaps, team never reached the red zone    -> NaN, not 0
#   G2 FB  wk1 snaps only                 -> FB is in the population
#   G3 WR  wk1 snaps only, NOT in the crosswalk -> no participation row
#   G4 WR  wk2 red-zone touch, so SEA has team_rz_touches > 0 in week 2
# ---------------------------------------------------------------------------
def _play(game_id, week, yardline, rusher=None, receiver=None, td=0):
    return {
        "game_id": game_id, "season": 2026, "week": week, "posteam": "SEA",
        "home_team": "SEA", "away_team": "ARI", "yardline_100": yardline,
        "rush_attempt": 1 if rusher else 0, "pass_attempt": 1 if receiver else 0,
        "rusher_player_id": rusher, "rusher_player_name": rusher,
        "receiver_player_id": receiver, "receiver_player_name": receiver,
        "rush_touchdown": td if rusher else 0, "pass_touchdown": td if receiver else 0,
    }


def fixtures():
    pbp = pd.DataFrame([
        _play("2026_01_SEA_ARI", 1, 8, rusher="G1", td=1),
        _play("2026_02_SEA_ARI", 2, 5, receiver="G4"),
        _play("2026_03_SEA_ARI", 3, 12, rusher="G4"),
        _play("2026_04_SEA_ARI", 4, 55, rusher="G4"),   # nothing inside the 20
    ])
    snaps = []
    for week, game in [(1, "2026_01_SEA_ARI"), (2, "2026_02_SEA_ARI"),
                       (3, "2026_03_SEA_ARI"), (4, "2026_04_SEA_ARI")]:
        # G1 plays every week except week 3
        if week != 3:
            snaps.append({"game_id": game, "season": 2026, "week": week, "team": "SEA",
                          "pfr_player_id": "P1", "position": "RB",
                          "offense_snaps": 40, "offense_pct": 0.8})
        snaps.append({"game_id": game, "season": 2026, "week": week, "team": "SEA",
                      "pfr_player_id": "P4", "position": "WR",
                      "offense_snaps": 50, "offense_pct": 1.0})
    snaps.append({"game_id": "2026_01_SEA_ARI", "season": 2026, "week": 1, "team": "SEA",
                  "pfr_player_id": "P2", "position": "FB", "offense_snaps": 10, "offense_pct": 0.2})
    snaps.append({"game_id": "2026_01_SEA_ARI", "season": 2026, "week": 1, "team": "SEA",
                  "pfr_player_id": "P3", "position": "WR", "offense_snaps": 30, "offense_pct": 0.6})
    snap_counts = pd.DataFrame(snaps)
    crosswalk = pd.DataFrame([
        {"pfr_id": "P1", "gsis_id": "G1"},
        {"pfr_id": "P2", "gsis_id": "G2"},
        {"pfr_id": "P4", "gsis_id": "G4"},
    ])  # P3 deliberately absent
    rosters = pd.DataFrame([
        {"player_id": p, "season": 2026, "player_name": p} for p in ("G1", "G2", "G3", "G4")
    ])
    return pbp, snap_counts, crosswalk, rosters


pbp, snap_counts, crosswalk, rosters = fixtures()
P = build_participation_frame(pbp, snap_counts, crosswalk, rosters)


def row(frame, pid, week):
    hit = frame[(frame["player_id"] == pid) & (frame["week"] == week)]
    return hit.iloc[0] if len(hit) else None


print("\n-- participation frame: which games count")
check("a game with a red-zone touch is a row", row(P, "G1", 1) is not None)
check("a game with snaps and no red-zone touch is a REAL row, not a gap", row(P, "G1", 2) is not None)
check("a week with no snaps at all has NO row (bye/inactive)", row(P, "G1", 3) is None)
check("an FB with snaps is in the population", row(P, "G2", 1) is not None)
check("a player who misses the crosswalk gains no participation row",
      row(P, "G3", 1) is None)

print("\n-- participation frame: the zero rule")
wk2 = row(P, "G1", 2)
check("snaps, no red-zone touch, team DID reach the red zone -> rz_touches 0",
      wk2 is not None and float(wk2["rz_touches"]) == 0.0)
check("...and rz_touch_share is a real 0.0, not NaN",
      wk2 is not None and pd.notna(wk2["rz_touch_share"]) and float(wk2["rz_touch_share"]) == 0.0)
wk4 = row(P, "G1", 4)
check("snaps, but the team never reached the red zone -> rz_touch_share is NaN, not 0",
      wk4 is not None and pd.isna(wk4["rz_touch_share"]))
check("...and that row still counts as a game played",
      wk4 is not None and float(wk4["rz_touches"]) == 0.0)

print("\n-- participation frame: games_played counts games, not calendar weeks")
g1 = P[P["player_id"] == "G1"].sort_values("week")
check("games_played is 0-indexed prior games, matching _trend_delta's cumcount",
      list(g1["games_played"]) == [0, 1, 2])
check("the bye week is skipped rather than counted as a game",
      len(g1) == 3 and list(g1["week"]) == [1, 2, 4])

print("\n-- participation frame: the curation-week pseudo-row")
P7 = build_participation_frame(pbp, snap_counts, crosswalk, rosters, curation_week=(2026, 5))
stub = row(P7, "G1", 5)
check("a pseudo-row lands on the curation week", stub is not None)
check("the pseudo-row carries a real trailing window from the games actually played",
      stub is not None and pd.notna(stub["rz_touches_last3"]))
check("the pseudo-row inherits the player's most recent team",
      stub is not None and stub["posteam"] == "SEA")
check("the pseudo-row's games_played counts the three games played before it",
      stub is not None and int(stub["games_played"]) == 3)
check("no pseudo-row is added when no curation week is given",
      row(P, "G1", 5) is None)

print("\n-- the merge onto weekly: columns overwritten ON THE ROWS")
weekly = pd.DataFrame([
    {"player_id": "G1", "season": 2026, "week": 4,
     "rz_touch_share_last3": 0.9, "rz_touch_share_last5": 0.9, "rz_touch_share_season_avg": 0.9,
     "snap_share_last3": 0.9, "snap_share_last5": 0.9, "snap_share_season_avg": 0.9,
     "rz_touches_last3": 9.0, "rz_touches_last5": 9.0, "rz_touches_season_avg": 9.0},
    {"player_id": "GX", "season": 2026, "week": 4,
     "rz_touch_share_last3": 0.5, "rz_touch_share_last5": 0.5, "rz_touch_share_season_avg": 0.5,
     "snap_share_last3": 0.5, "snap_share_last5": 0.5, "snap_share_season_avg": 0.5,
     "rz_touches_last3": 5.0, "rz_touches_last5": 5.0, "rz_touches_season_avg": 5.0},
])
merged = merge_participation_windows(weekly, P)
m_g1 = merged[merged["player_id"] == "G1"].iloc[0]
src = row(P, "G1", 4)
overwritten = all(
    (pd.isna(m_g1[f"{m}{s}"]) and pd.isna(src[f"{m}{s}"]))
    or float(m_g1[f"{m}{s}"]) == float(src[f"{m}{s}"])
    for m in PARTICIPATION_WINDOW_METRICS for s in ("_last3", "_last5", "_season_avg")
)
check("every window column is replaced with the participation frame's own", overwritten)
check("the window columns actually moved (the fixture's 0.9 is gone)",
      float(m_g1["snap_share_last3"]) != 0.9)
check("games_played lands on the frame", "games_played" in merged.columns)
check("games_played is the participation count", int(m_g1["games_played"]) == 2)
m_gx = merged[merged["player_id"] == "GX"].iloc[0]
check("a row with no participation match KEEPS its existing windows rather than being blanked",
      float(m_gx["snap_share_last3"]) == 0.5)
check("...and still gets a defined games_played, from the same cumcount used today",
      int(m_gx["games_played"]) == 0)
check("_last1 is deliberately untouched",
      not any(c.endswith("_last1") for c in merged.columns if c not in weekly.columns))

print("\n-- _trend_delta masking")
td = pd.DataFrame([
    {"player_id": "A", "season": 2026, "week": 7, "games_played": 5,
     "snap_share_last3": 0.6, "snap_share_last5": 0.6, "snap_share_season_avg": 0.4},
    {"player_id": "B", "season": 2026, "week": 7, "games_played": 2,
     "snap_share_last3": 0.6, "snap_share_last5": 0.6, "snap_share_season_avg": 0.4},
])
on3 = _trend_delta(td, "snap_share", 3, participation_frame=True)
check("flag on, window 3: games_played 5 > 3 is unmasked", pd.notna(on3.iloc[0]))
check("flag on, window 3: games_played 2 is masked", pd.isna(on3.iloc[1]))
on5 = _trend_delta(td, "snap_share", 5, participation_frame=True)
check("flag on, window 5: games_played 5 is NOT > 5, so it masks", pd.isna(on5.iloc[0]))
check("flag on: the rule is unchanged, only the count that feeds it",
      pd.notna(_trend_delta(td.assign(games_played=6), "snap_share", 5, participation_frame=True).iloc[0]))
off3 = _trend_delta(td, "snap_share", 3, participation_frame=False)
check("flag off: the merged column is ignored and the cumcount masks both rows",
      pd.isna(off3.iloc[0]) and pd.isna(off3.iloc[1]))
no_col = td.drop(columns=["games_played"])
check("flag on but no merged column: falls back to the cumcount, never half-applied",
      pd.isna(_trend_delta(no_col, "snap_share", 3, participation_frame=True).iloc[0]))

print("\n-- role_changes games_played")
rc = pd.DataFrame([
    {"player_id": "A", "season": 2026, "week": 6, "games_played": 4},
    {"player_id": "A", "season": 2026, "week": 7, "games_played": 5},
])
check("flag on: 1-indexed display count comes off the participation column",
      list(_games_played(rc, participation_frame=True)) == [5, 6])
check("flag off: unchanged cumcount+1 semantics",
      list(_games_played(rc, participation_frame=False)) == [1, 2])
check("flag on, column absent: falls back to cumcount+1",
      list(_games_played(rc.drop(columns=["games_played"]), participation_frame=True)) == [1, 2])

print("\n-- item 6: games_played survives the write/read round trip in `extra`, with no migration")
sys.path.insert(0, str(HERE / "scripts"))
from reconcile_week import (  # noqa: E402
    NFL_PLAYER_REDZONE_WEEKLY_TYPED_COLUMNS,
    shape_player_redzone_weekly_rows,
)

check("games_played is NOT a typed column, so it needs no migration",
      "games_played" not in NFL_PLAYER_REDZONE_WEEKLY_TYPED_COLUMNS)
reconciled = pd.DataFrame([{
    **{c: None for c in NFL_PLAYER_REDZONE_WEEKLY_TYPED_COLUMNS},
    "player_id": "A", "season": 2026, "week": 7, "games_played": 5,
}])
shaped = shape_player_redzone_weekly_rows(reconciled)
check("it is folded into the row's `extra` jsonb automatically",
      shaped and shaped[0]["extra"].get("games_played") == 5)
check("...and not smuggled into the typed columns",
      shaped and "games_played" not in {k for k in shaped[0] if k != "extra"})
# role_defensive_weekly_snapshot unpacks `extra` back onto the row; emulate that
# one line here rather than standing up a signed read route in a unit test.
restored = pd.DataFrame([{**{k: v for k, v in shaped[0].items() if k != "extra"}, **shaped[0]["extra"]}])
check("unpacked back onto the row, role_changes reads the participation count",
      int(_games_played(restored, participation_frame=True).iloc[0]) == 6)

print("\n-- the flag itself")
trend_frame.reset_cache()
saved = os.environ.get("TREND_PARTICIPATION_FRAME")
try:
    for value, expected in [(None, False), ("true", True), ("TRUE", False), ("1", False),
                            ("yes", False), ("", False), (" true", False)]:
        trend_frame.reset_cache()
        if value is None:
            os.environ.pop("TREND_PARTICIPATION_FRAME", None)
        else:
            os.environ["TREND_PARTICIPATION_FRAME"] = value
        check(f"env {value!r} -> {expected}", trend_frame.participation_frame_enabled() is expected)
    trend_frame.reset_cache()
    os.environ["TREND_PARTICIPATION_FRAME"] = "true"
    trend_frame.participation_frame_enabled()
    os.environ["TREND_PARTICIPATION_FRAME"] = "false"
    check("the read is cached, so one run cannot straddle an environment change",
          trend_frame.participation_frame_enabled() is True)
    check("an explicit boolean overrides the environment", trend_frame.resolve(False) is False)
    check("None defers to the environment", trend_frame.resolve(None) is True)
finally:
    trend_frame.reset_cache()
    if saved is None:
        os.environ.pop("TREND_PARTICIPATION_FRAME", None)
    else:
        os.environ["TREND_PARTICIPATION_FRAME"] = saved

print()
if all(results):
    print(f"All {len(results)} checks passed.")
else:
    print(f"{len(results) - sum(results)} of {len(results)} checks FAILED — see above.")
    raise SystemExit(1)
