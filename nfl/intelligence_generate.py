"""
NFL Intelligence — Phase 3 of the live-wiring project: the real
generation endpoint. Ties together, for the first time on a real
deployed call, every piece that already existed only as tested-but-
never-invoked machinery: each family's own build_*_stories() function,
Phase 1's nfl_player_redzone_weekly / nfl_price_history read-back,
Phase 2's real prior_history read-back (intelligence_lifecycle.
read_prior_history), and intelligence_write.process_family()'s already-
complete sanity-check + lifecycle + row-shaping orchestration.

FAMILY-AGNOSTIC BY A SMALL DISPATCH TABLE, not by literally sharing one
code path with zero branching — confirmed against process_family()'s own
body (Phase 3 investigation) that sanity-check/lifecycle/row-shaping are
ALREADY 100% shared across families; the only real per-family variance
is (a) which real data to fetch, (b) which build_*_stories() function to
call with it, and (c) whether the family participates in lifecycle
tracking at all (lifecycle_eligible — Market Intelligence is permanently
False, an existing, approved design decision, not new here). FAMILIES
below captures exactly those three things per family, nothing more.

Coaching Trends is deliberately NOT included — confirmed, not assumed:
zero production callers exist anywhere in this codebase (grepped
directly), its own module docstring still frames it as the fourth,
not-yet-wired family, and its primary input (raw pbp) has no persisted/
read-back design at all yet, a genuinely different, larger problem than
either of the other three families' data-fetch step. Stays deferred to
the existing plan's later phase.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

import pandas as pd

from defensive_trends import build_defensive_trends_stories
from intelligence_lifecycle import read_prior_history
from intelligence_write import process_family, write_intelligence_rows
from market_intelligence import build_market_intelligence_stories
from market_value import market_intelligence_snapshot_for_generation
from role_changes import build_role_changes_stories

# Deliberately NOT a single shared CONFIG import: role_changes.py,
# defensive_trends.py, and market_intelligence.py each define their OWN
# module-level CONFIG dict with different, non-overlapping keys (role_
# momentum_threshold vs. trend_threshold vs. this family's own tuning) —
# confirmed directly (an earlier version of this module wrongly imported
# scoring.CONFIG for all three, which crashed with a real KeyError the
# first time this was actually tested end-to-end, since scoring.CONFIG
# has neither key). Every build_*_stories() function already defaults its
# own `config` param to its own module's CONFIG, so simply never passing
# one here lets each family use the right one automatically.


def _fetch_weekly(builder):
    """
    Returns a fetch_fn(season, week, secret, read_url=None) closure for a
    `weekly`-shaped family (Role Changes / Defensive Trends) — both read
    the SAME real nfl_player_redzone_weekly season snapshot (see
    scripts.reconcile_week.role_defensive_weekly_snapshot's own docstring
    for why this is season-, not week-, scoped), so this is one shared
    closure factory, not two near-duplicate functions.
    """
    def fetch(season, week, secret, read_url=None):
        from reconcile_week import role_defensive_weekly_snapshot
        weekly = role_defensive_weekly_snapshot(season, secret, read_url)
        return builder(weekly, season, week)
    return fetch


def _fetch_market_intelligence(season, week, secret, read_url=None):
    snapshot = market_intelligence_snapshot_for_generation(season, week, secret, read_url)
    return build_market_intelligence_stories(snapshot)


# Per-family: build_stories_fn(season, week, secret, read_url=None) -> list
# of real story dicts (fetch + build combined, since each family's fetch
# step is genuinely different but always feeds straight into exactly one
# build_*_stories() call) and lifecycle_eligible (see module docstring).
FAMILIES = {
    "role_changes": {
        "build_stories_fn": _fetch_weekly(build_role_changes_stories),
        "lifecycle_eligible": True,
    },
    "defensive_trends": {
        "build_stories_fn": _fetch_weekly(build_defensive_trends_stories),
        "lifecycle_eligible": True,
    },
    "market_intelligence": {
        "build_stories_fn": _fetch_market_intelligence,
        "lifecycle_eligible": False,
    },
}


def generate_family(
    family: str, season: int, week: int, secret: str,
    stories: list = None, prior_history: dict = None, read_url: str = None, history_read_url: str = None,
) -> dict:
    """
    One real family's full "curate fully, then write"-shaped unit, minus
    the actual write call (see generate_and_write_intelligence for that).

    `stories`/`prior_history`: OPTIONAL overrides, for real local/synthetic
    testing without a real deployed secret/URL — when omitted, both are
    fetched for real (stories via this family's own FAMILIES[...]
    ["build_stories_fn"], prior_history via intelligence_lifecycle.
    read_prior_history, skipped entirely when lifecycle_eligible=False
    since process_family() ignores prior_history in that case anyway).
    Mirrors the same "real fetch, with a synthetic override for testing"
    shape this whole project's prior phases already established (e.g.
    apply_lifecycle's own history param).

    Returns process_family()'s own {"story_rows", "history_rows",
    "updated_history"} dict, plus "family" for the caller's own bookkeeping.
    An unrecognized family name raises ValueError immediately — a caller
    typo here should fail loudly, not silently produce zero rows.
    """
    if family not in FAMILIES:
        raise ValueError(f"Unknown intelligence family {family!r} — expected one of {sorted(FAMILIES)}")
    spec = FAMILIES[family]

    if stories is None:
        stories = spec["build_stories_fn"](season, week, secret, read_url)

    if prior_history is None:
        prior_history = (
            read_prior_history(family, season, week, secret, history_read_url)
            if spec["lifecycle_eligible"] else {}
        )

    result = process_family(family, stories, prior_history, season, week, lifecycle_eligible=spec["lifecycle_eligible"])
    result["family"] = family
    result["stories_generated"] = len(stories)
    return result


def generate_and_write_intelligence(
    season: int, week: int, secret: str, families: list = None, preview_only: bool = False,
    data_overrides: dict = None,
) -> dict:
    """
    The real, family-agnostic "curate fully, then write" call this whole
    phase exists to build — mirrors /api/curate-and-write-drafts's own
    shape (see that endpoint's docstring, Phase 3 investigation item 1):
    every family is fully generated (fetch real data -> build stories ->
    sanity-check -> lifecycle -> shape) BEFORE a single combined write.

    families: which families to run, default ALL of FAMILIES (Coaching
    Trends is never in FAMILIES at all — see module docstring). An
    unrecognized name raises via generate_family, same "fail loud on a
    caller typo" reasoning.

    preview_only: real generation still runs in full for every requested
    family (including a real prior_history read-back for lifecycle-
    eligible families) — nothing is written to Lovable. Mirrors curate-
    and-write-drafts's own preview_only semantics exactly, and is the
    concrete tool this task's own Gate uses: no real deployed secret/URL
    is available to this session, so a genuinely correct local/synthetic
    round trip through this exact function (preview_only=True, with
    data_overrides supplying synthetic stories/prior_history per family)
    is what "reviewable before wiring the trigger" means in practice here.

    data_overrides: {family: {"stories": [...] | None, "prior_history":
    {...} | None}} — passed straight through to generate_family() per
    family, for real local/synthetic testing without needing a live
    secret. Omit entirely (or omit a given family's key) for a real fetch.

    Returns {"season", "week", "preview_only", "families": {family:
    {stories_generated, story_rows, history_rows}}, "story_rows_written",
    "history_rows_written", "forwarded", "lovable_status_code",
    "forward_error"} — one real combined write covering every requested
    family's rows in a single call, same as curate-and-write-drafts's own
    single forward_result per request.
    """
    if families is None:
        families = list(FAMILIES)
    data_overrides = data_overrides or {}

    per_family = {}
    all_story_rows = []
    all_history_rows = []
    for family in families:
        overrides = data_overrides.get(family, {})
        result = generate_family(
            family, season, week, secret,
            stories=overrides.get("stories"), prior_history=overrides.get("prior_history"),
        )
        per_family[family] = {
            "stories_generated": result["stories_generated"],
            "story_rows": result["story_rows"],
            "history_rows": result["history_rows"],
        }
        all_story_rows.extend(result["story_rows"])
        all_history_rows.extend(result["history_rows"])

    forward_result = {"success": None, "status_code": None, "error": None}
    if not preview_only:
        forward_result = write_intelligence_rows(all_story_rows, all_history_rows, secret)

    return {
        "season": season,
        "week": week,
        "preview_only": preview_only,
        "families": per_family,
        "story_rows_written": len(all_story_rows) if not preview_only else 0,
        "history_rows_written": len(all_history_rows) if not preview_only else 0,
        "story_rows_generated": len(all_story_rows),
        "history_rows_generated": len(all_history_rows),
        "forwarded": forward_result["success"],
        "lovable_status_code": forward_result["status_code"],
        "forward_error": forward_result["error"],
    }
