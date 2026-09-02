"""
Tasty Pick Ems — reconcile a played week's stub row into real history
(Phase 3 of the stub-row work; see scripts/build_stub_week.py for
Phase 1. The live pre-game odds merge is now done inside
/api/curate-and-write-drafts, Phase B — there is no longer a separate
poller script.)

Once a game has actually been played, its stub row (Phase 1's pre-game
placeholder, odds-refreshed at curation time from nfl_price_history) is
replaced by a real
historical row — built exactly the way every other historical row
always has been (run_pipeline against real play-by-play), with one
addition: the stub file's FINAL captured Market Value snapshot
(market_value_score, consensus_implied_probability, best_price) is
preserved into that historical row as new columns, rather than being
discarded once the game's played.

"FINAL SNAPSHOT" — explicit definition, not assumed: this is the most
recent Market Value poll that exists for that (player_id, season, week)
in nfl_price_history AT THE MOMENT reconciliation runs — i.e. whatever
the last /api/poll-market-value run wrote there. This is "last poll on
file," NOT "guaranteed last poll before kickoff": the Make.com polling
cadence isn't pinned here, so nothing guarantees the last write
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

STUB CLEANUP: on successful reconciliation, this week's nfl_stub_weeks
rows are flagged `reconciled = true` (not deleted) via stub_store.mark_
stub_week_reconciled() — the Phase A replacement for the old
data/stub_weeks/reconciled/ file-move. stub_week_snapshot() filters
reconciled rows out of curation; the rows themselves stay for audit.

Usage:
    python scripts/reconcile_week.py SEASON WEEK
"""
import json
import os
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
from market_value import market_value_snapshot_for_reconciliation, merge_market_value_and_rescore
from stub_store import mark_stub_week_reconciled

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


# Real, confirmed gap found during Phase 3 (generation-endpoint) investigation:
# the write route above has existed since Phase 1, but nothing ever read this
# table back until now — build_role_changes_stories()/build_defensive_trends_
# stories() (role_changes.py/defensive_trends.py) both need the FULL multi-
# week table as their own `weekly` input, not just one week's rows.
DEFAULT_NFL_PLAYER_REDZONE_WEEKLY_READ_URL = "https://tastypickems.com/api/public/nfl-player-redzone-weekly-read"


def read_player_redzone_weekly_rows(season: int, secret: str, read_url: str = None) -> dict:
    """
    One signed POST (body {"season": season}), returns {"ok": bool, "error":
    str|None, "status_code": int|None, "rows": [...]} for EVERY real
    nfl_player_redzone_weekly row for the WHOLE season — same real sign+POST+
    capture-response reuse of forward_to_lovable every other read route in
    this codebase already uses.

    Whole-SEASON, not a single week — mirrors read_price_history's own
    (season, week) scoping only in SPIRIT, not in shape: build_role_changes_
    stories()/build_defensive_trends_stories() both need multiple weeks of
    history within the season (games_played counts, trend deltas) to
    correctly score the TARGET week, so a single week's rows alone can't
    reproduce what they need — the read route itself is season-scoped for
    exactly this reason (see its own docstring).

    A real "zero rows" response (this season has no reconciled weeks yet) is
    a genuine, valid outcome (rows=[]), not an error — same "no rows is
    valid" convention every other read route in this codebase already uses.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
    from lovable_forward import forward_to_lovable, resolve_url_env

    url = read_url or resolve_url_env(
        "LOVABLE_NFL_PLAYER_REDZONE_WEEKLY_READ_URL", DEFAULT_NFL_PLAYER_REDZONE_WEEKLY_READ_URL,
    )
    result = forward_to_lovable({"season": season}, secret, url)
    if not result["success"]:
        return {"ok": False, "error": result["error"], "status_code": result["status_code"], "rows": []}
    try:
        body = json.loads(result["response_body"])
    except (json.JSONDecodeError, TypeError):
        return {
            "ok": False, "error": f"Non-JSON response body: {result['response_body']!r}",
            "status_code": result["status_code"], "rows": [],
        }
    if not body.get("ok"):
        return {
            "ok": False, "error": body.get("error", "Unknown error"),
            "status_code": result["status_code"], "rows": [],
        }
    return {"ok": True, "error": None, "status_code": result["status_code"], "rows": body.get("player_redzone_weekly", [])}


def role_defensive_weekly_snapshot(season: int, secret: str, read_url: str = None) -> pd.DataFrame:
    """
    The real input build_role_changes_stories()/build_defensive_trends_
    stories() both need: reads the whole real season back from
    nfl_player_redzone_weekly and reconstitutes each row's FULL original
    shape — every typed column PLUS its own `extra` jsonb unpacked back
    onto it — not just the 15-column NFL_PLAYER_REDZONE_WEEKLY_TYPED_COLUMNS
    subset.

    CORRECTED, not the original design: an earlier version of this function
    returned only the 15 typed columns, on the belief (from an earlier
    investigation) that those were the only columns either builder reads.
    A full re-audit during real Gate testing found that belief was stale —
    written before this project's own "NFL Expanded Card Phase 2" work
    (structured hero_metric/what_changed evidence) added several MORE real
    column reads to both builders (player_name, td_opportunity,
    allowed_rz_tds_season_avg, conversion_rate_allowed_pct, recent_tds_
    allowed_pct, defensive_matchup_vulnerability_season_avg, depth_chart_
    movement_pct, rz_touch_share_season_avg, snap_share_season_avg, snap_
    share_trend_pct_role, touch_share_trend_pct_role — confirmed via a
    fresh grep of every row[...]/row.get(...) reference in both modules,
    not a partial list). Rather than hand-maintain a second list that can
    go stale again the next time either builder reads one more field,
    unpacking `extra` back onto the row returns exactly what shape_player_
    redzone_weekly_rows() originally split apart — the full real run_
    pipeline() row, restored — which is what `extra` existed for in the
    first place (see that function's own docstring).

    A genuinely empty season (nothing reconciled yet) returns a correctly-
    shaped, zero-row DataFrame with every typed column present (at minimum)
    — both builders' own pool-filtering already degrades correctly to "no
    stories" against an empty/short frame, the same honest-degradation
    shape every other read-then-reduce wrapper in this codebase already has.
    """
    result = read_player_redzone_weekly_rows(season, secret, read_url)
    rows = result["rows"]
    if not rows:
        return pd.DataFrame(columns=NFL_PLAYER_REDZONE_WEEKLY_TYPED_COLUMNS)
    merged = []
    for row in rows:
        extra = row.get("extra") or {}
        full = {**extra, **{col: row.get(col) for col in NFL_PLAYER_REDZONE_WEEKLY_TYPED_COLUMNS}}
        merged.append(full)
    return pd.DataFrame(merged).reset_index(drop=True)


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
    mark_reconciled: bool = True,
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

    STUB CLEANUP (mark_reconciled=True by default): flips
    nfl_stub_weeks.reconciled = true for this (season, week) via
    stub_store.mark_stub_week_reconciled() — the Phase A replacement for
    the old archive_stub file-move. The stub rows are kept, not deleted:
    the raw pre-game snapshot stays available for audit ("what did the
    market look like before this game"), and stub_week_snapshot()
    filters `reconciled` rows out so a stray re-curate of an
    already-played week can't pull dead data. A missing secret or a
    failed flag update is logged loudly but does NOT fail
    reconciliation — the real work (compute + persist the reconciled
    historical rows) is already done by this point, same
    honest-degradation posture as the persistence-write step above.
    """
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
    # Drop stale market-value columns, left-merge the fresh snapshot,
    # re-run score_evidence_quality + score_universal_tpe so the 4th
    # pillar is reflected in evidence_quality / core_score / tpe_score.
    # Factored into market_value.merge_market_value_and_rescore() so
    # /api/curate-and-write-drafts (Phase B) shares the same sequence.
    reconciled = merge_market_value_and_rescore(
        reconciled, market_value_snapshot, ["market_value_score", "market_value_completeness"],
    )

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

    if mark_reconciled:
        if not resolved_secret:
            print(
                f"[reconcile_week] WARNING: no secret available -- skipping the "
                f"nfl_stub_weeks.reconciled flag update for {season} Week {week}. The real "
                f"reconciled rows are already persisted; only the stub tombstone was skipped.",
                flush=True,
            )
        else:
            flag_result = mark_stub_week_reconciled(season, week, resolved_secret)
            if flag_result["success"]:
                print(f"[reconcile_week] Marked nfl_stub_weeks rows reconciled for {season} Week {week} "
                      f"({flag_result.get('response_body')!r})")
            else:
                # Not fatal -- the stub rows are just a stale pre-game
                # placeholder at this point, and stub_week_snapshot()
                # would still serve them until the next build_stub_week()
                # run overwrites them. Logged loudly so it's visible.
                print(
                    f"[reconcile_week] WARNING: failed to mark nfl_stub_weeks reconciled for "
                    f"{season} Week {week}: status={flag_result['status_code']} "
                    f"error={flag_result['error']!r}",
                    flush=True,
                )

    return reconciled


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python scripts/reconcile_week.py SEASON WEEK", file=sys.stderr)
        raise SystemExit(1)
    season_arg, week_arg = int(sys.argv[1]), int(sys.argv[2])
    reconcile_week(season_arg, week_arg)
