"""
Follow the Story — the deterministic weekly history package per player,
built from Table 1 (nfl_player_season_evidence). No LLM, no writes,
not wired into run_pipeline()/reconcile_week()/shelves.py/Interrogation
yet — see the plan this was built from for why a plain-code package is
the right first slice, and why it's deliberately NOT yet fed to
story_interrogation.py's own `prior_history` parameter (that wiring is
a later, separate step once this package itself is confirmed right).

build_player_history_package() is a PURE function: it takes `rows` as a
plain list of dicts (whatever a caller already fetched, from the real
read route below or a test fixture) rather than querying anything
itself, so it's fully testable offline. read_player_season_evidence_rows()
is the one real-network piece, a thin wrapper around the new
nfl-player-season-evidence-read.ts route (same signed-POST pattern every
other read route in this codebase already uses) — kept in this same
module since it's the one real way to get `rows` for a real player, but
never called by the pure function itself.

REAL VS. MISSING, the one rule everything here is built around: a week
simply absent from `rows` (no reconciled row at all for that player that
week) is NEVER treated as a real zero and NEVER included in any average
— it could be a bye week, a played game with no real red-zone touch
(Table 1's own accepted V1 population limitation — see its migration),
or a week that genuinely wasn't reconciled yet. A week that DOES have a
row, with a metric stored as 0, is a real, counted zero. These are not
interchangeable, and nothing in this module ever conflates them.
"""

# How many of the most recent present (<= as_of_week) weeks to include in
# recent_weeks — matches this codebase's own established "last3" rolling-
# window convention (snap_share_last3/target_share_last3/etc. throughout
# redzone.py/shelves.py), not invented fresh for this module.
RECENT_N_WEEKS = 3

METRIC_KEYS = ("snap_share", "targets", "carries", "red_zone_opportunities", "goal_line_opportunities")

MISSING_VS_BYE_NOTE = (
    "A week absent from this package's weeks_present is not distinguishable "
    "from a bye week or a played game where this player recorded no real "
    "red-zone touch (Table 1's own accepted population limitation) -- this "
    "package cannot tell those three apart, only report which weeks have a "
    "real row and which don't."
)


def _metric_row(row: dict) -> dict:
    """Pulls just the five real Table 1 metrics off a raw row dict, each
    left exactly as stored -- None stays None (a real, honest null, e.g.
    snap_share when the PFR crosswalk didn't resolve that player), 0
    stays 0 (a real, counted zero). Never substitutes one for the other."""
    return {k: row.get(k) for k in METRIC_KEYS}


def build_player_history_package(
    player_id: str, season: int, as_of_week: int, rows: list, reconciled_weeks: list,
) -> dict:
    """
    Pure, offline-testable. `rows` is defensively filtered to this
    player_id/season (a caller can pass a raw, unfiltered read-route
    response without pre-filtering it correctly first) and to week <=
    as_of_week (this package describes what was knowable AS OF that week
    -- a row for a later week, if one somehow got passed in, must never
    leak into current_week/recent_weeks/season_baseline). `reconciled_weeks`
    is likewise filtered to <= as_of_week before use as coverage's
    denominator, for the same as-of-week reason.

    reconciled_weeks should come from the DISTINCT real weeks present in
    nfl_player_redzone_weekly for this season (confirmed the more reliable
    source over nfl_stub_weeks.reconciled: reconcile_week() unconditionally
    writes real rows there on every successful run -- it raises otherwise,
    per that function's own docstring -- so a week's presence there is a
    direct, unconditional signal that reconciliation happened; the stub-
    weeks reconciled flag only ever gets set for a week that went through
    the OPTIONAL pre-game stub mechanism first, and would under-report any
    week reconciled without one), never from this player's own rows (a
    player can have zero rows in a real reconciled week -- a bye, or no
    real red-zone touch -- and that week must still count in the
    denominator, which is the whole reason coverage needs a season-wide
    list, not just this player's own weeks_present).
    """
    own_rows = [
        r for r in rows
        if r.get("player_id") == player_id and r.get("season") == season and r.get("week", r.get("period_index")) is not None
        and (r.get("week") if r.get("week") is not None else r.get("period_index")) <= as_of_week
    ]
    # Table 1's real column is period_index (see the table's own migration
    # for why it isn't literally named `week`), but a caller may well have
    # already reshaped rows to use `week` (e.g. reconcile_week.py's own
    # season_evidence shaping) -- accept either key rather than forcing
    # every caller to rename first.
    def _week_of(r: dict) -> int:
        return r.get("week") if r.get("week") is not None else r.get("period_index")

    own_rows.sort(key=_week_of)
    weeks_present = [_week_of(r) for r in own_rows]
    by_week = {_week_of(r): r for r in own_rows}

    current_week = _metric_row(by_week[as_of_week]) if as_of_week in by_week else None

    recent = own_rows[-RECENT_N_WEEKS:]
    recent_weeks = [{"week": _week_of(r), **_metric_row(r)} for r in recent]

    season_baseline = {}
    for metric in METRIC_KEYS:
        values = [r.get(metric) for r in own_rows if r.get(metric) is not None]
        season_baseline[metric] = {
            "avg": round(sum(values) / len(values), 4) if values else None,
            "weeks_used": len(values),
        }

    reconciled_as_of = sorted({w for w in (reconciled_weeks or []) if w <= as_of_week})
    covered = sorted(set(weeks_present) & set(reconciled_as_of))
    coverage = f"{len(covered)} of {len(reconciled_as_of)} reconciled weeks"

    return {
        "player_id": player_id,
        "season": season,
        "as_of_week": as_of_week,
        "current_week": current_week,
        "recent_weeks": recent_weeks,
        "season_baseline": season_baseline,
        "weeks_present": weeks_present,
        "coverage": coverage,
        "note": MISSING_VS_BYE_NOTE,
    }


DEFAULT_NFL_PLAYER_SEASON_EVIDENCE_READ_URL = "https://tastypickems.com/api/public/nfl-player-season-evidence-read"


def read_player_season_evidence_rows(player_id: str, season: int, secret: str, read_url: str = None) -> dict:
    """
    One signed POST (body {"player_id", "season"}), returns {"ok": bool,
    "error": str|None, "status_code": int|None, "rows": [...]} for every
    real nfl_player_season_evidence row for this player/season -- same
    real sign+POST+capture-response reuse of forward_to_lovable every
    other read route in this codebase already uses (see reconcile_week.
    read_player_redzone_weekly_rows for the direct precedent this mirrors).

    A real "zero rows" response (this player/season has no reconciled
    weeks yet, or never had a real red-zone touch) is a genuine, valid
    outcome (rows=[]), not an error -- same "no rows is valid" convention
    every other read route in this codebase already uses.

    NOT YET LIVE as of this writing -- the matching route (nfl-player-
    season-evidence-read.ts) has been built but not deployed (no commit/
    push made building this module). A real call here will fail (404 or
    connection error) until that route ships; callers should expect and
    handle that the same honest way every other real-network call in
    this codebase already does, not treat it as a surprise.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent / "api"))
    from lovable_forward import forward_to_lovable, resolve_url_env

    url = read_url or resolve_url_env(
        "LOVABLE_NFL_PLAYER_SEASON_EVIDENCE_READ_URL", DEFAULT_NFL_PLAYER_SEASON_EVIDENCE_READ_URL,
    )
    result = forward_to_lovable({"player_id": player_id, "season": season}, secret, url)
    if not result["success"]:
        return {"ok": False, "error": result["error"], "status_code": result["status_code"], "rows": []}
    try:
        import json
        body = json.loads(result["response_body"])
    except (ValueError, TypeError):
        return {
            "ok": False, "error": f"Non-JSON response body: {result['response_body']!r}",
            "status_code": result["status_code"], "rows": [],
        }
    if not body.get("ok"):
        return {
            "ok": False, "error": body.get("error", "Unknown error"),
            "status_code": result["status_code"], "rows": [],
        }
    return {
        "ok": True, "error": None, "status_code": result["status_code"],
        "rows": body.get("player_season_evidence", []),
    }
