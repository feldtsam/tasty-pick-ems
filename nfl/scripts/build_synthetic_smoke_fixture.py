"""
Generate nfl/data/stub_weeks/synthetic_smoke_test.csv — the SAFE fixture
for any live / production smoke test of /api/build-stub-week and
/api/curate-and-write-drafts.

Derived from 2026_wk1.csv by rewriting every identity column to a
clearly-synthetic value while leaving all score / count / boolean
columns untouched, so curation behaves the same (same ~9 rows qualify)
but the output can NEVER collide with real production data:

  season      -> 2099              (no real season is 2099)
  game_id     -> 2099_01_TSxx_TSyy (fake season + fake team codes)
  posteam     -> TSxx              }
  defteam     -> TSyy              } consistent with the fake game_id
  player_id   -> TEST-000001 ...   (not an nflverse gsis id)
  player_name -> Test Player N (POS)

nfl_content_drafts' natural key is (player_id, event_id, shelf,
writer_type) and event_id == game_id in curate_home_shelves — so a fake
game_id alone already prevents any upsert-onto-real-row collision; the
fake season + fake player_id are belt-and-suspenders and make the test
rows unmistakable in any query.

The /api/build-stub-week stub_csv path still rebinds season/week/game_id
to the request body on top of this (see _rebind_stub_frame) — so a
"week 18" test writes game_id 2099_18_TSxx_TSyy.

Run: python3 nfl/scripts/build_synthetic_smoke_fixture.py
"""
import sys
from pathlib import Path

import pandas as pd

STUB_DIR = Path(__file__).resolve().parent.parent / "data" / "stub_weeks"
SRC = STUB_DIR / "2026_wk1.csv"
OUT = STUB_DIR / "synthetic_smoke_test.csv"
FAKE_SEASON = 2099


def build() -> pd.DataFrame:
    df = pd.read_csv(SRC)

    teams = sorted(set(df["posteam"].dropna()) | set(df["defteam"].dropna()))
    tmap = {t: f"TS{str(i + 1).zfill(2)}" for i, t in enumerate(teams)}

    df["season"] = FAKE_SEASON
    df["posteam"] = df["posteam"].map(tmap)
    df["defteam"] = df["defteam"].map(tmap)

    def remap_gid(gid):
        if not isinstance(gid, str):
            return gid
        parts = gid.split("_")
        if len(parts) != 4:
            return gid
        return f"{FAKE_SEASON}_{parts[1]}_{tmap.get(parts[2], 'TS00')}_{tmap.get(parts[3], 'TS00')}"

    df["game_id"] = df["game_id"].map(remap_gid)

    df = df.reset_index(drop=True)
    df["player_id"] = [f"TEST-{str(i + 1).zfill(6)}" for i in range(len(df))]
    df["player_name"] = [
        f"Test Player {i + 1} ({pg})" for i, pg in enumerate(df["position_group"].fillna("NA"))
    ]
    return df


if __name__ == "__main__":
    df = build()
    # sanity: no real-looking identifiers leaked through
    bad_season = (df["season"] != FAKE_SEASON).sum()
    bad_gid = df["game_id"].astype(str).str.startswith("20", na=False) & ~df["game_id"].astype(str).str.startswith(f"{FAKE_SEASON}_")
    bad_pid = ~df["player_id"].astype(str).str.startswith("TEST-")
    if bad_season or bad_gid.any() or bad_pid.any():
        print(f"REFUSING TO WRITE: bad_season={bad_season} bad_game_id={bad_gid.sum()} bad_player_id={bad_pid.sum()}",
              file=sys.stderr)
        raise SystemExit(1)
    df.to_csv(OUT, index=False)
    print(f"Wrote {len(df)} synthetic rows ({df['game_id'].nunique()} games, "
          f"{df['posteam'].nunique()} teams) to {OUT.relative_to(Path.cwd()) if OUT.is_relative_to(Path.cwd()) else OUT}")
