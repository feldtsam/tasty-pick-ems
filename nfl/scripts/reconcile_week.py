"""
Tasty Pick Ems — reconcile a played week's stub row into real history
(Phase 3 of the stub-row work; see scripts/build_stub_week.py for
Phase 1, scripts/poll_market_value_for_stub.py for Phase 2).

Once a game has actually been played, its stub row (Phase 1's pre-game
placeholder, live-updated by Phase 2's poller) is replaced by a real
historical row — built exactly the way every other historical row
always has been (run_pipeline against real play-by-play), with one
addition: the stub file's FINAL captured Market Value snapshot
(market_value_score, consensus_implied_probability, best_price) is
preserved into that historical row as new columns, rather than being
discarded once the game's played.

"FINAL SNAPSHOT" — explicit definition, not assumed: this is the most
recent Market Value poll that exists for that (player_id, season, week)
in the stub file AT THE MOMENT reconciliation runs — i.e. whatever
scripts/poll_market_value_for_stub.py last wrote there. This is "last
poll on file," NOT "guaranteed last poll before kickoff": Phase 2 has
no defined polling cadence yet (scheduling was explicitly out of scope
for that phase), so nothing here guarantees the stub's last write
actually happened right before kickoff rather than, say, a day or a
week earlier. No interpolation or "true kickoff price" estimation is
attempted — that would need an actual price-history table this project
hasn't built (see market_value.py's own PRICE_HISTORY_COLUMNS design,
still unpopulated). Once Phase 2 gets a real polling cadence, "final"
here will mean whatever that cadence's last run captured — this
function doesn't change if/when that happens.

SCHEMA: player_redzone_weekly.csv gains three new columns —
market_value_score, consensus_implied_probability, best_price. Every
row before this point (all of 2022/2024/2025, and any week reconciled
before a live market existed for it) is correctly NaN for these — real
missing data (no live market ever existed for those games), not an
invented placeholder.

A player with a stub row who never actually recorded a real red-zone
touch has no historical row to attach Market Value to — this needs no
special handling: `reconciled` below comes from run_pipeline's real
play-by-play aggregation, exactly like every other historical week, so
a non-touching player is simply absent from it, same as today.

STUB CLEANUP: on successful reconciliation, the week's stub file is
MOVED (not deleted) to nfl/data/stub_weeks/reconciled/ — see
reconcile_week's docstring below for the concrete reasoning (a real
data-loss risk this avoids, not just tidiness).

Usage:
    python scripts/reconcile_week.py SEASON WEEK
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor"))

import numpy as np
import pandas as pd

from backfill_redzone import (
    SEASONS,
    load_depth_charts,
    load_id_crosswalk,
    load_injuries,
    load_pbp,
    load_schedules,
    load_seasonal_rosters,
    load_snap_counts,
    run_pipeline,
)
from poll_market_value_for_stub import MARKET_VALUE_COLUMNS, STUB_DIR
from scoring import CONFIG, score_evidence_quality, score_universal_tpe

HISTORICAL_CSV = Path(__file__).resolve().parent / "player_redzone_weekly.csv"

# The 3 columns this task asks to preserve permanently into history —
# a deliberate subset of MARKET_VALUE_COLUMNS (which also has n_books/
# best_book/consensus_price_american/market_value_completeness).
# market_value_completeness is used transiently below to drive score_
# evidence_quality/score_universal_tpe's own renormalization, but isn't
# part of the permanent historical schema — every other pillar's
# completeness column already lives only on the (ephemeral) stub/
# scoring layer, not in player_redzone_weekly.csv, so this matches that
# existing convention rather than inventing a new one.
RECONCILED_MARKET_VALUE_COLUMNS = ["market_value_score", "consensus_implied_probability", "best_price"]


def week_is_complete(season: int, week: int) -> dict:
    """
    Real per-game completeness check — the readiness gate reconcile_week()
    itself has never had (see this module's own investigation: the only
    existing guard, `len(reconciled) == 0`, checks "did any real red-zone
    touch get recorded," not "have all of this week's games finished").

    Reuses backfill_redzone.load_schedules (a thin wrapper around
    nfl_data_py.import_schedules()) rather than calling import_schedules()
    a second, redundant way — same data, already imported into this file.

    A game is "final" when BOTH home_score and away_score are non-null —
    confirmed directly against a real, fully-completed season (2025):
    0% null rate on both columns. Neither column is ever partially
    populated in practice (a game reports both scores or neither), but
    checking both rather than either alone costs nothing and doesn't
    depend on that always holding.

    total_games == 0 (a week number with no scheduled games at all, e.g.
    a bye week for the whole league or a nonexistent week) deliberately
    reports all_final=False, not True — "nothing to check" should never
    read as "everything's done."
    """
    schedules = load_schedules([season])
    week_games = schedules[schedules["week"] == week]
    total_games = len(week_games)

    final_mask = week_games["home_score"].notna() & week_games["away_score"].notna()
    final_games = int(final_mask.sum())
    pending = week_games[~final_mask]

    return {
        "season": season,
        "week": week,
        "all_final": total_games > 0 and final_games == total_games,
        "total_games": total_games,
        "final_games": final_games,
        "pending_games": [
            {"home_team": r["home_team"], "away_team": r["away_team"], "gameday": r["gameday"]}
            for _, r in pending.iterrows()
        ],
    }


def reconcile_week(
    season: int,
    week: int,
    historical_seasons: list[int] = None,
    historical_csv_path: Path = None,
    stub_dir: Path = None,
    archive_stub: bool = True,
    pbp: pd.DataFrame = None,
    snap_counts: pd.DataFrame = None,
    id_crosswalk: pd.DataFrame = None,
    depth_charts: pd.DataFrame = None,
    injuries: pd.DataFrame = None,
    seasonal_rosters: pd.DataFrame = None,
    schedules: pd.DataFrame = None,
) -> pd.DataFrame:
    """
    Build this week's real historical rows (run_pipeline against real
    play-by-play, no stub injection — the games have actually happened,
    so there's nothing to inject), merge in the stub file's final
    Market Value snapshot by player_id, refresh evidence_quality/
    core_score/tpe_score so they reflect the real 4th pillar (not the
    3-pillar renormalization fallback), then splice the result into
    player_redzone_weekly.csv — replacing any prior rows at this
    (season, week) so re-running is idempotent, not duplicating.

    Raises if no real rows exist for (season, week) — either the games
    haven't been played yet, or genuinely no RB/WR/TE recorded a
    red-zone touch (both real reasons not to reconcile, not something
    to silently paper over).

    STUB CLEANUP REASONING (archive_stub=True by default): the archived
    copy is moved out of stub_dir, not deleted, and not left in place.
    Concretely, leaving it in place is a real data-loss risk, not just
    untidiness: poll_market_value_for_stub.py always does a full drop-
    then-remerge of MARKET_VALUE_COLUMNS on every call. If someone (or
    an eventual scheduled job) re-polls an already-played week, The
    Odds API's /events endpoint won't return a closed game, so the
    poller would harmlessly fetch zero events — but its own merge logic
    would still DROP the stub's already-captured real market_value_
    score first, then re-merge against that empty fetch, silently
    wiping the exact final-snapshot data this function needs. Moving
    the file out of stub_dir turns that into a loud, immediate
    FileNotFoundError for anyone who tries to poll a reconciled week,
    instead of a silent data loss discovered only later at
    reconciliation time. Archived rather than deleted so the raw
    snapshot stays available for later inspection/audit (e.g. "what did
    the market actually look like before this game") — cheap to keep,
    consistent with this project's flat-file, no-database convention
    (a moved file, not a new mechanism).
    """
    historical_csv_path = historical_csv_path or HISTORICAL_CSV
    stub_dir = stub_dir or STUB_DIR
    stub_path = stub_dir / f"{season}_wk{week}.csv"

    load_seasons = sorted(set(historical_seasons or SEASONS) | {season})
    if pbp is None:
        pbp = load_pbp(load_seasons)
    if snap_counts is None:
        snap_counts = load_snap_counts(load_seasons)
    if id_crosswalk is None:
        id_crosswalk = load_id_crosswalk(load_seasons)
    if depth_charts is None:
        depth_charts = load_depth_charts(load_seasons)
    if injuries is None:
        injuries = load_injuries(load_seasons)
    if seasonal_rosters is None:
        seasonal_rosters = load_seasonal_rosters(load_seasons)
    if schedules is None:
        schedules = load_schedules(load_seasons)

    weekly, _allowed_weekly = run_pipeline(
        pbp, snap_counts, id_crosswalk, depth_charts, injuries, seasonal_rosters, schedules,
    )
    reconciled = weekly[(weekly["season"] == season) & (weekly["week"] == week)].copy()
    if len(reconciled) == 0:
        raise ValueError(
            f"No real play-by-play rows for {season} Week {week} -- either the games haven't "
            f"been played yet, or no RB/WR/TE recorded a real red-zone touch that week."
        )

    stub = pd.read_csv(stub_path)
    market_value_for_scoring = stub[["player_id", "season", "week", "market_value_score", "market_value_completeness"]]

    # Merge market_value_score onto this week's real rows BEFORE
    # re-scoring -- score_evidence_quality's convergence needs it
    # already present as a column to pick it up as the 4th family (see
    # CONFIG["evidence_quality"]["family_score_columns"]), and must run
    # before the final score_universal_tpe call.
    reconciled = reconciled.drop(columns=["market_value_score", "market_value_completeness"], errors="ignore")
    reconciled = reconciled.merge(market_value_for_scoring, on=["player_id", "season", "week"], how="left")

    reconciled = score_evidence_quality(reconciled, CONFIG)
    reconciled = score_universal_tpe(reconciled, market_value=market_value_for_scoring, config=CONFIG)

    # The two remaining permanent historical columns (market_value_score
    # is already present from the scoring step above). market_value_
    # completeness was only ever needed transiently to drive the
    # rescoring above -- dropped before writing to history, see
    # RECONCILED_MARKET_VALUE_COLUMNS.
    reconciled = reconciled.merge(
        stub[["player_id", "season", "week", "consensus_implied_probability", "best_price"]],
        on=["player_id", "season", "week"], how="left",
    )
    reconciled = reconciled.drop(columns=["market_value_completeness"])

    historical = pd.read_csv(historical_csv_path)
    for col in RECONCILED_MARKET_VALUE_COLUMNS:
        if col not in historical.columns:
            historical[col] = np.nan

    # Idempotent: replace any rows already at this (season, week) rather
    # than duplicating on a re-run.
    historical = historical[~((historical["season"] == season) & (historical["week"] == week))]
    reconciled = reconciled[historical.columns.tolist()]
    combined = pd.concat([historical, reconciled], ignore_index=True)
    combined.to_csv(historical_csv_path, index=False)
    print(f"Reconciled {len(reconciled)} rows for {season} Week {week} into {historical_csv_path} "
          f"({reconciled['market_value_score'].notna().sum()} with a real final Market Value snapshot)")

    if archive_stub:
        # Relative to the ACTUAL stub_dir this call used, not a fixed
        # module-level path -- caught directly in testing: an earlier
        # version hardcoded this against poll_market_value_for_stub's
        # own STUB_DIR, so a test call with an overridden stub_dir still
        # silently archived into the real production directory instead
        # of the test one.
        reconciled_dir = stub_dir / "reconciled"
        reconciled_dir.mkdir(parents=True, exist_ok=True)
        archived_path = reconciled_dir / stub_path.name
        shutil.move(str(stub_path), str(archived_path))
        print(f"Archived stub file to {archived_path}")

    return reconciled


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python scripts/reconcile_week.py SEASON WEEK", file=sys.stderr)
        raise SystemExit(1)
    season_arg, week_arg = int(sys.argv[1]), int(sys.argv[2])
    reconcile_week(season_arg, week_arg)
