"""
Tasty Pick Ems — NFL stub-week persistence (Phase A of the weekly-
automation plan).

Moves build_stub_week()'s output off the committed CSV
(nfl/data/stub_weeks/{season}_wk{week}.csv, git-tracked and read from the
deployed bundle by /api/curate-and-write-drafts) and into the real
nfl_stub_weeks table, so refreshing a week's stub data no longer needs a
git commit + Vercel redeploy.

Direct adaptation of reconcile_week.py's nfl_player_redzone_weekly
helpers (shape_player_redzone_weekly_rows / write_player_redzone_weekly_
rows / read_player_redzone_weekly_rows / role_defensive_weekly_snapshot)
— same narrow-typed-core + `extra` jsonb tail, same signed POST reuse of
forward_to_lovable, same "read the table, unpack `extra` back onto each
row, return a DataFrame" round trip. Nothing about the scoring/pipeline
math lives here; this is storage plumbing only.

STORAGE CONVENTION unchanged from the CSV era: a stub week is
regenerated wholesale on every build_stub_week() run, so the write is a
full-batch upsert on (player_id, season, week), not a partial patch.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

# The typed core of nfl_stub_weeks — the natural key plus the columns a
# human eyeballs when sanity-checking a stub run (the three pillar
# scores, the final tpe_score, the market-value column Phase 2 fills in).
# Everything else in build_stub_week()'s ~112-column row folds into
# `extra`, same narrow-core-plus-jsonb-tail pattern nfl_player_redzone_
# weekly already uses. Order matches the migration's column order — not
# required, just easier to eyeball against it.
STUB_WEEKS_TYPED_COLUMNS = [
    "player_id", "season", "week", "posteam", "position_group",
    "td_opportunity", "role_momentum", "situation",
    "market_value_score", "tpe_score",
]

# Table-management columns the read route echoes back at the top level of
# every row (select("*")) that must NOT be reconstructed onto the
# DataFrame handed to curate_nfl_shelves — the CSV never had them, and
# `extra` never contains them (it was built as "everything NOT in
# STUB_WEEKS_TYPED_COLUMNS" from a frame that itself had none of these).
_DB_MANAGED_COLUMNS = {"id", "created_at", "updated_at", "reconciled", "extra"}

DEFAULT_NFL_STUB_WEEKS_WRITE_URL = "https://tastypickems.com/api/public/nfl-stub-weeks-write"
DEFAULT_NFL_STUB_WEEKS_READ_URL = "https://tastypickems.com/api/public/nfl-stub-weeks-read"


def shape_stub_rows(stub_week: pd.DataFrame) -> list:
    """
    Split each row of build_stub_week()'s output into the nfl_stub_weeks
    shape: the typed core columns top-level, everything else folded into
    `extra`. Adapted verbatim from reconcile_week.shape_player_redzone_
    weekly_rows — same to_json()-round-trip JSON-safety idiom (a numpy
    int64/float64/NaN can survive a naive .to_dict() call untouched;
    to_json()'s own encoder handles them, NaN -> null included).

    A typed column genuinely missing from the frame (shouldn't happen for
    a real build_stub_week() run, but keeps this total) lands as None,
    same as record.get() returning None for any absent key.
    """
    records = json.loads(stub_week.to_json(orient="records"))
    rows = []
    for record in records:
        typed = {col: record.get(col) for col in STUB_WEEKS_TYPED_COLUMNS}
        typed["extra"] = {k: v for k, v in record.items() if k not in STUB_WEEKS_TYPED_COLUMNS}
        rows.append(typed)
    return rows


def rows_to_stub_frame(rows: list) -> pd.DataFrame:
    """
    Reconstruct build_stub_week()'s original frame shape from a list of
    nfl_stub_weeks rows (as returned by the read route): every typed
    column PLUS its own `extra` jsonb unpacked back onto it. Pure — no
    network — so the round trip can be tested offline.

    Mirrors reconcile_week.role_defensive_weekly_snapshot's own unpack:
    {**extra, **typed} so a typed column always wins over a same-named
    key that somehow ended up in `extra` too. DB-managed columns (id,
    created_at, updated_at, reconciled) are dropped — the CSV never
    carried them and curate_nfl_shelves neither expects nor reads them.

    `reconciled` rows are filtered out: once reconcile_week() has marked
    a week reconciled, its pre-game stub rows are stale (superseded by
    real play-by-play in nfl_player_redzone_weekly) and must not feed a
    stray re-curate of an already-played week.
    """
    live = [r for r in rows if not r.get("reconciled")]
    if not live:
        return pd.DataFrame(columns=STUB_WEEKS_TYPED_COLUMNS)
    merged = []
    for row in live:
        extra = row.get("extra") or {}
        typed = {col: row.get(col) for col in STUB_WEEKS_TYPED_COLUMNS}
        full = {**{k: v for k, v in extra.items() if k not in _DB_MANAGED_COLUMNS}, **typed}
        merged.append(full)
    return pd.DataFrame(merged).reset_index(drop=True)


def _resolve_lovable(url_env: str, default_url: str):
    """Lazy import of the api/ helpers, same sys.path dance reconcile_week.py
    already does for its own write/read wrappers (they live in api/, this
    file lives in scripts/)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
    from lovable_forward import forward_to_lovable, resolve_url_env

    return forward_to_lovable, resolve_url_env(url_env, default_url)


def write_stub_rows(rows: list, secret: str, write_url: str = None) -> dict:
    """
    Sign and POST a full week's stub rows to nfl-stub-weeks-write —
    upsert-on-conflict on (player_id, season, week), server-side
    idempotent on re-run. Adapted from reconcile_week.write_player_
    redzone_weekly_rows. Returns forward_to_lovable's own
    {"success", "status_code", "error", "response_body"} dict unchanged.
    """
    forward_to_lovable, resolved_url = _resolve_lovable(
        "LOVABLE_NFL_STUB_WEEKS_WRITE_URL", DEFAULT_NFL_STUB_WEEKS_WRITE_URL,
    )
    return forward_to_lovable(rows, secret, write_url or resolved_url)


def mark_stub_week_reconciled(season: int, week: int, secret: str, write_url: str = None) -> dict:
    """
    Flip `reconciled = true` on every nfl_stub_weeks row for (season,
    week) via the write route's dedicated {"mark_reconciled": {...}}
    payload — the tombstone reconcile_week()'s archive_stub file-move
    used to be. A targeted UPDATE, not an upsert: the pre-game snapshot
    stays intact for audit, just flagged stale.
    """
    forward_to_lovable, resolved_url = _resolve_lovable(
        "LOVABLE_NFL_STUB_WEEKS_WRITE_URL", DEFAULT_NFL_STUB_WEEKS_WRITE_URL,
    )
    return forward_to_lovable(
        {"mark_reconciled": {"season": int(season), "week": int(week)}}, secret, write_url or resolved_url,
    )


def read_stub_week_rows(season: int, week: int, secret: str, read_url: str = None) -> dict:
    """
    One signed POST (body {"season", "week"}), returns {"ok": bool,
    "error": str|None, "status_code": int|None, "rows": [...]} for every
    nfl_stub_weeks row for that (season, week). Adapted from reconcile_
    week.read_player_redzone_weekly_rows — a real "zero rows" response
    (build_stub_week hasn't run for this week yet) is a valid outcome,
    rows=[], not an error.
    """
    forward_to_lovable, resolved_url = _resolve_lovable(
        "LOVABLE_NFL_STUB_WEEKS_READ_URL", DEFAULT_NFL_STUB_WEEKS_READ_URL,
    )
    result = forward_to_lovable({"season": int(season), "week": int(week)}, secret, read_url or resolved_url)
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
    return {"ok": True, "error": None, "status_code": result["status_code"], "rows": body.get("stub_weeks", [])}


def stub_week_snapshot(season: int, week: int, secret: str, read_url: str = None) -> pd.DataFrame:
    """
    The real curation input: read one week's stub rows back from
    nfl_stub_weeks and reconstitute build_stub_week()'s original frame
    (rows_to_stub_frame — typed columns + `extra` unpacked, reconciled
    rows dropped). The drop-in replacement for
    `pd.read_csv(STUB_WEEKS_DIR / f"{season}_wk{week}.csv")` in
    /api/curate-and-write-drafts.

    A genuinely empty week (build_stub_week hasn't run, or every row was
    reconciled) returns a correctly-shaped zero-row DataFrame — the
    endpoint turns that into its own 404, same as the old `.exists()`
    check did.

    Raises RuntimeError on a real read failure (bad secret, route down,
    non-JSON) rather than silently returning empty — a transport failure
    is not the same as "no stub for this week," and curation must not
    run against a frame that's empty for the wrong reason.
    """
    result = read_stub_week_rows(season, week, secret, read_url)
    if not result["ok"]:
        raise RuntimeError(
            f"nfl_stub_weeks read failed for {season} Week {week}: "
            f"status={result['status_code']} error={result['error']!r}"
        )
    return rows_to_stub_frame(result["rows"])


if __name__ == "__main__":
    print(
        "stub_store.py is a library (shape_stub_rows / write_stub_rows / "
        "stub_week_snapshot / mark_stub_week_reconciled). Nothing to run directly — "
        "see scripts/build_stub_week.py and api/index.py's /api/build-stub-week.",
        file=sys.stderr,
    )
    raise SystemExit(1)
