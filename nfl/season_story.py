"""
Season Story — eligibility, the deterministic material-change rule, and
the per-run cap. No LLM, no writes, not wired into anything yet. See the
plan this was built from for why this is deliberately a separate module
from player_season_history.py: that module is about Table 1's own broad
factual population; this one is about the narrower, reader-facing
question of who gets a Season Story at all, and when a new version is
worth generating.
"""

# ---------------------------------------------------------------------------
# ELIGIBILITY
# ---------------------------------------------------------------------------
# Season Story eligibility is DELIBERATELY NOT "every player with a Table 1
# row" -- that's Table 1's own broad population (hundreds of players a
# season, most of whom never appear anywhere a reader sees). Eligibility is
# narrower: a player has to actually be visible to a reader THIS WEEK,
# either on a published Picks shelf or in a visible Intelligence story.
#
# Real tables/columns, confirmed directly, not assumed:
#   Picks shelves: public.nfl_content_drafts, filtered review_status =
#     'approved' (the exact filter get_published_nfl_shelf_picks() itself
#     uses -- confirmed by reading that function's own real SQL). Read via
#     the existing nfl-content-drafts-read.ts route / this codebase's own
#     curate_home_shelves.read_content_draft_review_states(season, week,
#     secret) -- no new route needed.
#   Intelligence stories: public.nfl_intelligence_stories, filtered
#     is_visible AND sanity_check_passed (the exact filter get_published_
#     nfl_intelligence_latest() itself uses), entity->>'type' = 'player'
#     (the other three entity types -- team, defense -- aren't a Season
#     Story's subject). Read via the existing nfl-intelligence-read.ts
#     route / intelligence_write.read_intelligence_stories(season, week,
#     secret) -- no new route needed here either.


def eligible_players_for_season_story(content_draft_rows: list, intelligence_story_rows: list) -> set:
    """
    Pure function: real rows in, a set of real player_ids out. Behind ONE
    function specifically so a future source (a watchlist, Radar) is one
    more set unioned in here, not a rewrite of every caller that currently
    computes eligibility.

    content_draft_rows: real rows from nfl-content-drafts-read.ts /
    read_content_draft_review_states()'s own "rows" key -- each a dict
    with at least player_id/review_status, already scoped to the target
    (season, week) by the caller (this function does no season/week
    filtering of its own -- it trusts the caller already scoped the read).

    intelligence_story_rows: real rows from nfl-intelligence-read.ts /
    read_intelligence_stories()'s own "rows" key -- each a real
    nfl_intelligence_stories row (entity jsonb, is_visible,
    sanity_check_passed), likewise already scoped to (season, week).
    """
    from_picks = {
        r["player_id"] for r in content_draft_rows
        if r.get("review_status") == "approved" and r.get("player_id")
    }
    from_intelligence = set()
    for r in intelligence_story_rows:
        if not (r.get("is_visible") and r.get("sanity_check_passed")):
            continue
        entity = r.get("entity") or {}
        if entity.get("type") == "player" and entity.get("player_id"):
            from_intelligence.add(entity["player_id"])
    return from_picks | from_intelligence


# ---------------------------------------------------------------------------
# MATERIAL-CHANGE RULE
# ---------------------------------------------------------------------------
# THRESHOLDS ARE PROVISIONAL STARTING HYPOTHESES, same treatment every other
# "hypothesis to tune" constant in this codebase gets (scoring.CONFIG,
# shelves.py's completeness_threshold, intelligence_lifecycle.
# FAMILY_SIGNAL_THRESHOLDS) -- NOT validated against real season-length
# variance (there are at most two real reconciled weeks in Table 1 as of
# this writing), and explicitly expected to need real tuning once a real
# season's worth of data exists.
SEASON_STORY_MATERIAL_CHANGE_CONFIG = {
    # A player with zero NEW real weeks since the last published version
    # can never fire -- see is_material_change's own comment on why an
    # absent week is never treated as data, let alone as a change.
    "min_new_weeks_since_last_version": 1,
    # Per-metric: fires if season_baseline.avg moved by at least this much
    # (absolute, in the metric's own units) since the last version's own
    # evidence_snapshot.
    "season_baseline_delta_thresholds": {
        "snap_share": 0.08,
        "targets": 1.5,
        "carries": 1.5,
        "red_zone_opportunities": 0.5,
        "goal_line_opportunities": 0.3,
    },
    # A single-week value this many x the metric's own threshold away from
    # the established season_baseline is material on its own, even if the
    # season_baseline itself hasn't moved much yet (one real outlier game).
    "current_week_outlier_multiplier": 2.0,
    # weeks_present growing by at least this many real weeks since the
    # last version is its own reason to regenerate -- the SAME numbers are
    # now backed by more real evidence, independent of whether any single
    # metric moved.
    "min_new_coverage_weeks_to_trigger": 2,
    # PROVISIONAL, not derived from any real measurement -- a placeholder
    # ceiling chosen to be "clearly enough to matter, clearly not enough
    # to be the real production answer." Real sizing needs a real week's
    # worth of real fire-rate data, which doesn't exist yet (see the dry
    # run this was built from: 100% fire rate on an n=4 real sample is not
    # a basis for sizing a real cap).
    "max_new_versions_per_run": 40,
}


def is_material_change(new_package: dict, previous_evidence_snapshot: dict, config: dict) -> dict:
    """
    Returns {"fires": bool, "reason": str, "magnitude": float}.

    magnitude is the largest real (delta / threshold) ratio found across
    every signal checked -- used ONLY for ranking when rank_and_cap_
    material_changes() below has to choose among more fired candidates
    than max_new_versions_per_run allows. It never affects whether fires
    is True; a candidate either crosses a real threshold or it doesn't,
    full stop -- magnitude only answers "by how much," after the fact.

    ABSENCE IS NOT DATA, real and load-bearing here: len(weeks_present) is
    read directly off the package (not the "N of M" coverage string,
    which a caller would otherwise have to re-parse) specifically so a
    week where this player simply has no row -- a bye, or no real
    red-zone touch, Table 1's own accepted population gap -- can never by
    itself look like "a new week of data arrived." A player who gained
    ZERO real new weeks since the last version cannot fire this rule no
    matter what their season_baseline looks like, because nothing new is
    actually known about them -- the early return below is this rule
    refusing to manufacture a change out of an absence, the same
    discipline build_player_history_package() already applies to
    computing the baseline itself.
    """
    if previous_evidence_snapshot is None:
        return {"fires": True, "reason": "no previous story -- first appearance", "magnitude": float("inf")}

    new_weeks = set(new_package["weeks_present"]) - set(previous_evidence_snapshot["weeks_present"])
    if len(new_weeks) < config["min_new_weeks_since_last_version"]:
        return {"fires": False, "reason": "no new real weeks since the last version", "magnitude": 0.0}

    best_magnitude = 0.0
    best_reason = None
    for metric, threshold in config["season_baseline_delta_thresholds"].items():
        old_avg = previous_evidence_snapshot["season_baseline"][metric]["avg"]
        new_avg = new_package["season_baseline"][metric]["avg"]
        if old_avg is not None and new_avg is not None and threshold:
            ratio = abs(new_avg - old_avg) / threshold
            if ratio > best_magnitude:
                best_magnitude = ratio
                best_reason = f"season_baseline.{metric} moved {old_avg}->{new_avg} (threshold {threshold})"

        cw = new_package["current_week"]
        if cw is not None and cw.get(metric) is not None and old_avg is not None and threshold:
            outlier_threshold = threshold * config["current_week_outlier_multiplier"]
            ratio = abs(cw[metric] - old_avg) / outlier_threshold
            if ratio > best_magnitude:
                best_magnitude = ratio
                best_reason = f"current_week.{metric}={cw[metric]} is an outlier vs season_baseline={old_avg}"

    old_n = len(previous_evidence_snapshot["weeks_present"])
    new_n = len(new_package["weeks_present"])
    coverage_threshold = config["min_new_coverage_weeks_to_trigger"]
    if coverage_threshold:
        ratio = (new_n - old_n) / coverage_threshold
        if ratio > best_magnitude:
            best_magnitude = ratio
            best_reason = f"weeks_present grew {old_n}->{new_n} (threshold {coverage_threshold})"

    fires = best_magnitude >= 1.0
    return {
        "fires": fires,
        "reason": best_reason if fires else "no threshold crossed",
        "magnitude": best_magnitude,
    }


def rank_and_cap_material_changes(evaluations: dict, config: dict) -> dict:
    """
    evaluations: {player_id: is_material_change(...)'s own return dict}.

    Returns a NEW dict, same keys, each value with a "capped" key added:
    True for a candidate that fired but didn't make this run's cut,
    False for everything else (never fired, or fired and made the cut).
    A capped candidate is still a REAL material change -- "capped" means
    "deferred to a later run," never "treated as if nothing changed."

    Ranks fired candidates by magnitude descending (first-appearances,
    magnitude=inf, always sort first -- ties broken by player_id for a
    deterministic order run to run) and keeps the top max_new_versions_
    per_run; everything beyond that is marked capped.
    """
    fired = [(pid, ev) for pid, ev in evaluations.items() if ev["fires"]]
    fired.sort(key=lambda item: (-item[1]["magnitude"], item[0]))
    keep_player_ids = {pid for pid, _ in fired[: config["max_new_versions_per_run"]]}

    result = {}
    for pid, ev in evaluations.items():
        result[pid] = {**ev, "capped": ev["fires"] and pid not in keep_player_ids}
    return result
