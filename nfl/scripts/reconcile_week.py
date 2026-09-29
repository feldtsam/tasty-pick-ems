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
    load_weekly_stats,
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
DEFAULT_NFL_PLAYER_SEASON_EVIDENCE_WRITE_URL = "https://tastypickems.com/api/public/nfl-player-season-evidence-write"

# Follow the Story V1, Table 1: extra's real key -> nfl_player_season_
# evidence's real column name. rz_touches/gl_touches get renamed to
# their documented, reader-facing names; the other three keep their
# real Python-side name unchanged. See shape_player_season_evidence_
# rows' own docstring for the accepted V1 population limitation.
NFL_PLAYER_SEASON_EVIDENCE_METRIC_KEYS = {
    "snap_share": "snap_share",
    "targets": "targets",
    "carries": "carries",
    "rz_touches": "red_zone_opportunities",
    "gl_touches": "goal_line_opportunities",
}


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


def shape_player_season_evidence_rows(redzone_weekly_rows: list) -> list:
    """
    Follow the Story V1, Table 1: derives nfl_player_season_evidence rows
    from the SAME shaped rows shape_player_redzone_weekly_rows() already
    built for THIS week's nfl_player_redzone_weekly write -- no separate
    read-back, no recomputation. Each input row is one of that
    function's own typed-core-plus-extra dicts.

    ACCEPTED V1 POPULATION LIMITATION (confirmed via a real trace,
    2026-09-29, not an oversight): one Table 1 row per input row, no
    additional eligibility filter -- the input population is already
    exactly nfl_player_redzone_weekly's own (i.e. reconcile_week()'s own
    `reconciled`), which is itself scoped to players with a real
    red-zone touch that game (aggregate_redzone_game's own base
    population). A player with real offensive participation but no
    red-zone touch that week has no row here for that week. Widening
    that base population is a separately scoped future task -- not
    attempted here, and explicitly not blocking this pass.

    Each of the five metrics is read independently from `extra` via
    NFL_PLAYER_SEASON_EVIDENCE_METRIC_KEYS and left as None when absent
    -- a real, honest null (e.g. carries for a receiver who never
    rushed, or snap_share when the PFR crosswalk didn't resolve that
    player), never a reason to drop the row or fabricate a 0.

    period_type is always "game" for V1 (period_index is the real week
    number) -- see the table's own migration for why this isn't a
    column literally named `week`.
    """
    out = []
    for row in redzone_weekly_rows:
        extra = row.get("extra") or {}
        shaped = {
            "player_id": row["player_id"],
            "season": row["season"],
            "period_type": "game",
            "period_index": row["week"],
        }
        for extra_key, column_name in NFL_PLAYER_SEASON_EVIDENCE_METRIC_KEYS.items():
            shaped[column_name] = extra.get(extra_key)
        out.append(shaped)
    return out


def write_player_season_evidence_rows(rows: list, secret: str, write_url: str = None) -> dict:
    """
    Signs and POSTs to nfl_player_season_evidence's write route -- same
    real HMAC/X-Signature pattern write_player_redzone_weekly_rows above
    already uses. Upsert-on-conflict on (player_id, season, period_type,
    period_index) happens server-side, same idempotent-on-rerun shape.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
    from lovable_forward import forward_to_lovable, resolve_url_env

    url = write_url or resolve_url_env(
        "LOVABLE_NFL_PLAYER_SEASON_EVIDENCE_WRITE_URL", DEFAULT_NFL_PLAYER_SEASON_EVIDENCE_WRITE_URL,
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
    weekly_stats: pd.DataFrame = None,
    secret: str = None,
    write_url: str = None,
    price_history_read_url: str = None,
    season_evidence_write_url: str = None,
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
    unresolved, this now RAISES (real fix, confirmed 2026-09-28 — see
    the persistence-write block's own comment) rather than silently
    skipping the write. Unlike the market-value read above, a skipped
    persistence write is invisible to every caller (reconcile_week()
    still returns the real `reconciled` DataFrame, and the calling
    endpoint still reports status="success"), so it does NOT get this
    module's general "missing optional input -> honest degradation"
    treatment — the persisted table is not optional output, it's the
    entire point of this function being called at all.

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
    already-played week can't pull dead data. resolved_secret is always
    truthy by the time this runs (the persistence-write raise above
    already stops the function otherwise) — only a genuinely FAILED flag
    update (a real, non-secret-related error) is logged loudly here, not
    fatal, since the real work (compute + persist the reconciled
    historical rows) is already done by this point.
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
    if weekly_stats is None:
        weekly_stats = load_weekly_stats(load_seasons)

    # Resolved once, early -- reused for BOTH the price-history read below
    # AND the persistence write further down, same NFL_PIPELINE_WEBHOOK_
    # SECRET env var either way (confirmed: both nfl-price-history-read.ts
    # and nfl-player-redzone-weekly-write.ts check the identical env var).
    resolved_secret = secret or os.environ.get("NFL_PIPELINE_WEBHOOK_SECRET")

    weekly, _allowed_weekly = run_pipeline(
        pbp, snap_counts, id_crosswalk, depth_charts, injuries, seasonal_rosters, schedules, weekly_stats,
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

    # REAL FIX (confirmed, not hypothetical -- see this module's own
    # investigation notes): a missing/blank NFL_PIPELINE_WEBHOOK_SECRET
    # used to be a warn-and-continue here, same "honest degradation"
    # posture as the market-value read above. That's wrong for THIS
    # write specifically: unlike a missing market-value snapshot (which
    # shows up as a visible NaN in the persisted data), a skipped
    # persistence write is invisible -- the function still returns the
    # real `reconciled` DataFrame and the calling endpoint still reports
    # status="success", so a blank-secret misconfiguration (Vercel
    # "Sensitive" env vars are write-only after creation -- a blank save
    # goes undetected, see resolve_url_env's own docstring) could drop a
    # whole week's real data with nothing anywhere to show it happened.
    # Raising here surfaces it as status="error" at the calling endpoint
    # instead, the same treatment a genuinely failed write already gets
    # two lines below.
    if not resolved_secret:
        raise RuntimeError(
            f"No secret available (pass secret= or set NFL_PIPELINE_WEBHOOK_SECRET) -- refusing "
            f"to silently skip the nfl_player_redzone_weekly persistence write for {season} Week "
            f"{week}. {len(reconciled)} real reconciled rows were computed and then discarded; "
            f"fix the secret and re-run reconciliation for this week."
        )
    rows = shape_player_redzone_weekly_rows(reconciled)
    result = write_player_redzone_weekly_rows(rows, resolved_secret, write_url)
    if not result["success"]:
        raise RuntimeError(
            f"Persisting {len(rows)} rows for {season} Week {week} to nfl_player_redzone_weekly "
            f"failed: status={result['status_code']} error={result['error']!r}"
        )
    print(f"Persisted {len(rows)} rows for {season} Week {week} to nfl_player_redzone_weekly "
          f"({reconciled['market_value_score'].notna().sum()} with a real final Market Value snapshot)")

    # Follow the Story V1, Table 1 -- derived from `rows` above (this
    # week's own already-shaped nfl_player_redzone_weekly rows), never
    # read back separately. Deliberately NOT fatal on failure, unlike
    # the persistence write above: nfl_player_redzone_weekly is the
    # real, load-bearing table every live Intelligence family already
    # depends on; nfl_player_season_evidence has no reader yet (Table
    # 2/3 aren't built), so a failure here shouldn't sink an otherwise-
    # successful reconciliation run -- same "logged loudly, not fatal"
    # treatment the stub-flag update below already gets, for the same
    # reason (the real, load-bearing work is already done by this point).
    season_evidence_rows = shape_player_season_evidence_rows(rows)
    season_evidence_result = write_player_season_evidence_rows(
        season_evidence_rows, resolved_secret, season_evidence_write_url,
    )
    if season_evidence_result["success"]:
        print(f"[reconcile_week] Persisted {len(season_evidence_rows)} rows for {season} Week {week} "
              f"to nfl_player_season_evidence")
    else:
        print(
            f"[reconcile_week] WARNING: failed to persist nfl_player_season_evidence for "
            f"{season} Week {week}: status={season_evidence_result['status_code']} "
            f"error={season_evidence_result['error']!r}",
            flush=True,
        )

    # resolved_secret is unconditionally truthy by this point -- the
    # persistence write above already raises when it isn't, so the
    # separate falsy-secret warning this branch used to print (and its
    # own now-inaccurate claim that "the real reconciled rows are
    # already persisted") can never fire. Removed rather than left as
    # dead, misleading text.
    if mark_reconciled:
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
