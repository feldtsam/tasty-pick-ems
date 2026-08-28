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
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor"))

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
from poll_market_value_for_stub import STUB_DIR
from market_value import market_value_snapshot_for_reconciliation
from scoring import CONFIG, score_evidence_quality, score_universal_tpe

# player_redzone_weekly.csv itself is NOT touched by this module anymore
# (see reconcile_week()'s own docstring) — this constant is intentionally
# gone from here. The FILE and every OTHER reader of it (shelves.py's
# add_td_opportunity_history_lookup, test_role_changes.py/test_defensive_
# trends.py/test_team_tendencies.py/test_intelligence_lifecycle.py/
# content_writer/nfl_writer_common.py/api/test_curate_home_shelves.py/
# api/test_stickiness.py) are UNCHANGED and still read the real, existing
# local file exactly as before — confirmed via grep, not assumed; see the
# Phase 1 report for the full list. Removing reconcile_week.py's own
# write to it means that file now stops gaining fresh rows going
# forward (frozen at whatever it currently contains) while those other
# readers still consume it — flagged explicitly, not silently left for
# someone to discover later.

# Real, confirmed 15-column set role_changes.py's/defensive_trends.py's
# own build_*_stories() functions actually read from the multi-week
# table (Phase 1 investigation) — the typed core of the new persistence
# table. Order matches the migration's own column order, not required,
# just easier to eyeball against it.
NFL_PLAYER_REDZONE_WEEKLY_TYPED_COLUMNS = [
    "player_id", "season", "week", "posteam", "defteam", "position_group",
    "role_momentum", "role_trend", "external_opportunity", "role_momentum_completeness",
    "depth_rank", "ahead_injury_statuses", "ahead_injured_teammates",
    "defensive_matchup_vulnerability", "defensive_matchup_completeness",
]

# Fallback only for resolve_url_env — same convention as every other NFL
# write route's own DEFAULT_ constant (see curate_home_shelves.py's
# DEFAULT_NFL_CONTENT_DRAFTS_WRITE_URL). The real value comes from the
# LOVABLE_NFL_PLAYER_REDZONE_WEEKLY_WRITE_URL Vercel env var.
DEFAULT_NFL_PLAYER_REDZONE_WEEKLY_WRITE_URL = "https://tastypickems.com/api/public/nfl-player-redzone-weekly-write"


def shape_player_redzone_weekly_rows(reconciled: pd.DataFrame) -> list:
    """
    Splits each real reconciled row into the new persistence table's
    shape: the 15 confirmed-real-consumer typed columns (NFL_PLAYER_
    REDZONE_WEEKLY_TYPED_COLUMNS) top-level, everything else in run_
    pipeline()'s real ~109-column row folded into `extra` — same narrow-
    core-plus-jsonb-tail pattern nfl_intelligence_stories already uses
    for entity/primary_signal/supporting_evidence, not invented here.
    market_value_score/consensus_implied_probability/best_price (the 3
    columns the old CSV permanently preserved) land in `extra` now, same
    as every other non-typed column — no longer special-cased, since
    `extra` already covers "preserve everything not explicitly typed."

    Uses the same to_json()-round-trip JSON-safety idiom api/index.py's
    poll_market_value_endpoint already established as more reliable than
    .to_dict("records") (a numpy int64/float64/NaN can survive a naive
    .to_dict() call untouched; to_json()'s own encoder handles them
    correctly) — applied to the WHOLE reconciled frame once, so ahead_
    injury_statuses/ahead_injured_teammates' real nested list/dict
    values round-trip as real JSON arrays/objects, not Python repr
    strings (the str(list) round-trip shelves.py's _as_list has to
    work around for the CSV path doesn't apply here — this never goes
    through a CSV at all).
    """
    records = json.loads(reconciled.to_json(orient="records"))
    rows = []
    for record in records:
        typed = {col: record.get(col) for col in NFL_PLAYER_REDZONE_WEEKLY_TYPED_COLUMNS}
        typed["extra"] = {k: v for k, v in record.items() if k not in NFL_PLAYER_REDZONE_WEEKLY_TYPED_COLUMNS}
        rows.append(typed)
    return rows


def write_player_redzone_weekly_rows(rows: list, secret: str, write_url: str = None) -> dict:
    """
    Signs and POSTs to the new persistence table's write route — same
    HMAC/X-Signature pattern every other NFL webhook write already uses
    (see curate_home_shelves.write_content_draft_rows, the closest real
    precedent for this exact lazy-import-into-api/ shape). Upsert-on-
    conflict on (player_id, season, week) happens server-side — the
    same idempotent-on-rerun property the old CSV splice enforced in
    Python (drop this week's old rows, re-add), now enforced by the DB.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
    from lovable_forward import forward_to_lovable, resolve_url_env

    url = write_url or resolve_url_env(
        "LOVABLE_NFL_PLAYER_REDZONE_WEEKLY_WRITE_URL", DEFAULT_NFL_PLAYER_REDZONE_WEEKLY_WRITE_URL,
    )
    return forward_to_lovable(rows, secret, url)


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
    stub_dir: Path = None,
    archive_stub: bool = True,
    pbp: pd.DataFrame = None,
    snap_counts: pd.DataFrame = None,
    id_crosswalk: pd.DataFrame = None,
    depth_charts: pd.DataFrame = None,
    injuries: pd.DataFrame = None,
    seasonal_rosters: pd.DataFrame = None,
    schedules: pd.DataFrame = None,
    secret: str = None,
    write_url: str = None,
    price_history_read_url: str = None,
) -> pd.DataFrame:
    """
    Build this week's real historical rows (run_pipeline against real
    play-by-play, no stub injection — the games have actually happened,
    so there's nothing to inject), merge in the real Market Value
    snapshot for this week, refresh evidence_quality/core_score/
    tpe_score so they reflect the real 4th pillar (not the 3-pillar
    renormalization fallback), then writes the result to the real
    nfl_player_redzone_weekly persistence table (Phase 1 of the Role
    Changes/Defensive Trends live-wiring project) via an upsert on
    (player_id, season, week) — server-side idempotent on re-run, same
    property the old player_redzone_weekly.csv splice enforced in
    Python. See shape_player_redzone_weekly_rows/write_player_redzone_
    weekly_rows for the actual shaping/write — this function's own job
    stops at building `reconciled`; the persistence step is a call out,
    not inlined here, so it can be tested/swapped independently.

    MARKET VALUE SOURCE, REDIRECTED (real fix, confirmed not assumed):
    used to read a local stub CSV (data/stub_weeks/{season}_wk{week}.csv)
    for the final pre-game Market Value snapshot — that file is
    gitignored/never deployed, and this exact read crashed a real
    deployed reconciliation call (FileNotFoundError, confirmed live).
    Now calls market_value.market_value_snapshot_for_reconciliation(),
    which reads the real nfl_price_history table (a signed read, same
    `secret` as the persistence write below) and computes market_value_
    score/market_value_completeness fresh via the real scoring.
    score_market_value() — the SAME real function scripts/poll_market_
    value_for_stub.py already calls, not reimplemented. Same "last real
    poll on file, not guaranteed pre-kickoff" semantic as before (see
    that function's own docstring) — unchanged by this swap, only the
    storage layer moved. A player with no real price-history row yet
    (the expected, current state — nfl_price_history's own Make.com
    polling scenario is separate, already-tracked work, not built as
    part of this fix) gets honest NaN market_value_score/completeness
    via the same LEFT merge the old stub-CSV path already used for a
    player missing from the stub — a preserved graceful degradation
    (low completeness, not a crash), not new behavior invented here.

    REDIRECTED, not extended: this function no longer touches player_
    redzone_weekly.csv AT ALL (confirmed broken in production — see
    this module's own investigation notes below the imports) — it does
    NOT dual-write to both the CSV and the new table. The CSV file
    itself, and every OTHER reader of it, are untouched by this change
    (see the module-level comment above NFL_PLAYER_REDZONE_WEEKLY_
    TYPED_COLUMNS for the confirmed full list) — only THIS function's
    own write target moved.

    `secret`/`write_url`: same resolution shape as curate_home_shelves.
    write_content_draft_rows — `secret` falls back to the
    NFL_PIPELINE_WEBHOOK_SECRET env var when not passed explicitly (so
    both the deployed endpoint and this file's own `__main__` entry
    point work without duplicating env-var-reading logic); if still
    unresolved, the write is SKIPPED with a loud printed warning, not a
    raise — reconciliation's own real output (the returned `reconciled`
    DataFrame, and the real run_pipeline/scoring work that produced it)
    is not gated on persistence config being present, matching this
    module's own general "missing optional input -> honest degradation,
    not a hard failure" philosophy elsewhere.

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

    # Resolved once, early -- reused for BOTH the price-history read below
    # AND the persistence write further down, same NFL_PIPELINE_WEBHOOK_
    # SECRET env var either way (confirmed: both nfl-price-history-read.ts
    # and nfl-player-redzone-weekly-write.ts check the identical env var).
    resolved_secret = secret or os.environ.get("NFL_PIPELINE_WEBHOOK_SECRET")

    weekly, _allowed_weekly = run_pipeline(
        pbp, snap_counts, id_crosswalk, depth_charts, injuries, seasonal_rosters, schedules,
    )
    reconciled = weekly[(weekly["season"] == season) & (weekly["week"] == week)].copy()
    if len(reconciled) == 0:
        raise ValueError(
            f"No real play-by-play rows for {season} Week {week} -- either the games haven't "
            f"been played yet, or no RB/WR/TE recorded a real red-zone touch that week."
        )

    # REDIRECTED from the old local stub-CSV read (confirmed broken in
    # production -- see this function's own docstring) to the real
    # nfl_price_history table. Without a real secret, this degrades to
    # an honestly-empty snapshot (every player gets NaN market_value_
    # score/completeness via the left merges below) rather than raising
    # -- same "missing optional config -> honest degradation" philosophy
    # as the persistence write further down, not a special case.
    if resolved_secret:
        market_value_snapshot = market_value_snapshot_for_reconciliation(
            season, week, resolved_secret, price_history_read_url,
        )
    else:
        print(
            f"[reconcile_week] WARNING: no secret available (pass secret= or set "
            f"NFL_PIPELINE_WEBHOOK_SECRET) -- skipping the real nfl_price_history read for "
            f"{season} Week {week}. Every player's market_value_score/completeness will be "
            f"honest NaN, same as a player genuinely missing a real poll.",
            flush=True,
        )
        market_value_snapshot = pd.DataFrame(columns=[
            "player_id", "season", "week", "market_value_score", "market_value_completeness",
            "consensus_implied_probability", "best_price",
        ])
    market_value_for_scoring = market_value_snapshot[["player_id", "season", "week", "market_value_score", "market_value_completeness"]]

    # Merge market_value_score onto this week's real rows BEFORE
    # re-scoring -- score_evidence_quality's convergence needs it
    # already present as a column to pick it up as the 4th family (see
    # CONFIG["evidence_quality"]["family_score_columns"]), and must run
    # before the final score_universal_tpe call.
    reconciled = reconciled.drop(columns=["market_value_score", "market_value_completeness"], errors="ignore")
    reconciled = reconciled.merge(market_value_for_scoring, on=["player_id", "season", "week"], how="left")

    reconciled = score_evidence_quality(reconciled, CONFIG)
    reconciled = score_universal_tpe(reconciled, market_value=market_value_for_scoring, config=CONFIG)

    # The two remaining market-value columns (market_value_score is
    # already present from the scoring step above); market_value_
    # completeness was only ever needed transiently to drive the
    # rescoring above -- dropped before persisting. None of these three
    # are in NFL_PLAYER_REDZONE_WEEKLY_TYPED_COLUMNS -- they land in
    # `extra` at the persistence step below, same as every other
    # non-typed column, no longer a specially-tracked permanent triple.
    reconciled = reconciled.merge(
        market_value_snapshot[["player_id", "season", "week", "consensus_implied_probability", "best_price"]],
        on=["player_id", "season", "week"], how="left",
    )
    reconciled = reconciled.drop(columns=["market_value_completeness"])

    if not resolved_secret:
        print(
            f"[reconcile_week] WARNING: no secret available -- skipping the persistence write for "
            f"{season} Week {week}. reconcile_week() still returns the real "
            f"{len(reconciled)} reconciled rows; nothing computed here was lost, "
            f"only the write to nfl_player_redzone_weekly.",
            flush=True,
        )
    else:
        rows = shape_player_redzone_weekly_rows(reconciled)
        result = write_player_redzone_weekly_rows(rows, resolved_secret, write_url)
        if not result["success"]:
            raise RuntimeError(
                f"Persisting {len(rows)} rows for {season} Week {week} to nfl_player_redzone_weekly "
                f"failed: status={result['status_code']} error={result['error']!r}"
            )
        print(f"Persisted {len(rows)} rows for {season} Week {week} to nfl_player_redzone_weekly "
              f"({reconciled['market_value_score'].notna().sum()} with a real final Market Value snapshot)")

    if archive_stub and stub_path.exists():
        # stub_path may genuinely not exist -- the SEPARATE, still-open
        # stub_weeks blocker this fix does not address (nothing live
        # ever creates data/stub_weeks/{season}_wk{week}.csv on Vercel;
        # see this project's own investigation notes). Archiving is a
        # local-dev nicety for the Phase 1/2 stub-file workflow, not a
        # precondition for THIS function's own real job (compute +
        # persist real reconciled data) -- skipped gracefully, not a
        # crash, when there's genuinely nothing to archive.
        #
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
    elif archive_stub:
        print(f"[reconcile_week] No stub file at {stub_path} to archive -- skipping (not an error).")

    return reconciled


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python scripts/reconcile_week.py SEASON WEEK", file=sys.stderr)
        raise SystemExit(1)
    season_arg, week_arg = int(sys.argv[1]), int(sys.argv[2])
    reconcile_week(season_arg, week_arg)
