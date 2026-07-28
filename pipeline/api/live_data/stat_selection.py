"""
Decides, per player, whether to trust their current-season stat line or
fall back to their own prior-full-season (2025) number — the "blend vs.
straight current-season" question raised when this module was scoped.

RECOMMENDATION (stated explicitly, not defaulted silently): sample-size-
gated fallback to the player's own real 2025 season value, NOT a weighted
blend of current-season and 2025 numbers. Reasoning:

  1. backtest/scoring/config.py already has real, validated qualification
     thresholds for exactly this question — min_pa=100 for hitters,
     min_ip=20 for pitchers — chosen for the reference *population*
     (who's included when building the percentile scale), not for this
     use case, but the underlying judgment ("below this sample, a rate
     stat is too noisy to trust") transfers directly to "should THIS
     player's current-season number be trusted".
  2. A weighted blend (e.g. current*w + 2025*(1-w), w scaled by PA) invents
     a new weighting function with no backtested evidence for what w
     should be at any given sample size — it would need its own validation
     pass against real outcomes before being trustworthy, the same way
     hand-set pillar weights were preferred over data-fit weights in
     backtest/ only after being checked against real decile results.
     Building that validation is real, separate work, not a one-line
     choice.
  3. A hard fallback (all current-season, or all prior-season, never
     mixed) keeps every number that reaches score_candidate() a REAL stat
     a real player actually posted in a real season — never an invented
     interpolation. That matches the project's consistent preference for
     real data over synthesized approximations (see: red-flag penalties
     rejected in backtest/, hand-set weights preferred over data-fit).

  This can be revisited into an actual weighted blend later if the
  straight-fallback version looks unstable in practice — same sequencing
  precedent as red-flag penalties (shipped, then rejected once evidence
  came in), not a permanent decision made in this file.

Below the threshold AND missing from the 2025 lookup (true rookies with no
2025 MLB time), falls back further to the small current-season sample
rather than leaving the field blank — a noisy real number beats no number,
and score_candidate() only defaults to neutral when a field is truly None.
"""

BATTER_CURRENT_FIELDS = ["avg_exit_velo", "sweet_spot_pct", "hard_hit_pct", "barrel_pct", "xslg", "xwoba", "hr_per_pa"]
PITCHER_CURRENT_FIELDS = ["opp_hard_hit_pct_allowed", "opp_barrel_pct_allowed", "opp_xslg_allowed", "opp_xwoba_allowed", "opp_hr_per_9", "opp_k_per_9"]

# 2025 lookup dicts (batter_lookup_by_id / pitcher_lookup_by_id in the
# bundled snapshot) use bare stat names — no "opp_" prefix, and hr_per_9/
# k_per_9 instead of opp_hr_per_9/opp_k_per_9. This maps this module's
# (score_candidate-matching) field names to the lookup's key names.
_PITCHER_LOOKUP_KEY = {
    "opp_hard_hit_pct_allowed": "hard_hit_pct_allowed",
    "opp_barrel_pct_allowed": "barrel_pct_allowed",
    "opp_xslg_allowed": "xslg_allowed",
    "opp_xwoba_allowed": "xwoba_allowed",
    "opp_hr_per_9": "hr_per_9",
    "opp_k_per_9": "k_per_9",
}


def select_batter_metrics(mlbam_id: int, current: dict, current_pa, lookup_2025: dict, min_pa: int) -> dict:
    """
    current: dict with BATTER_CURRENT_FIELDS (may have None values for any
    field the player wasn't found in a given source for).
    lookup_2025: the bundled snapshot's batter_lookup_by_id, keyed by
    str(mlbam_id).
    Returns the selected metrics dict (BATTER_CURRENT_FIELDS keys) plus
    "_source" and "_note" describing the decision, for the orchestrator's
    diagnostics.
    """
    has_any_current = any(current.get(f) is not None for f in BATTER_CURRENT_FIELDS)
    prior = lookup_2025.get(str(mlbam_id))

    if current_pa is not None and current_pa >= min_pa:
        out = {f: current.get(f) for f in BATTER_CURRENT_FIELDS}
        out["_source"] = "current_season"
        out["_note"] = f"{current_pa} PA this season >= qualification minimum ({min_pa}) — using current-season stats."
        return out

    if prior is not None:
        out = {f: prior.get(f) for f in BATTER_CURRENT_FIELDS}
        out["_source"] = "prior_season_2025_fallback"
        out["_note"] = (
            f"only {current_pa if current_pa is not None else 0} PA this season "
            f"(below the {min_pa}-PA qualification minimum) — falling back to this player's real 2025 season stats."
        )
        return out

    if has_any_current:
        out = {f: current.get(f) for f in BATTER_CURRENT_FIELDS}
        out["_source"] = "current_season_small_sample_no_fallback"
        out["_note"] = (
            f"only {current_pa if current_pa is not None else 0} PA this season and not found in the 2025 "
            f"reference (no prior-season MLB time) — using the small current-season sample rather than nothing."
        )
        return out

    out = {f: None for f in BATTER_CURRENT_FIELDS}
    out["_source"] = "unavailable"
    out["_note"] = "no current-season Statcast/MLB stats and no 2025 season on record — fields left unset (neutral fallback in score_candidate)."
    return out


def select_pitcher_metrics(mlbam_id: int, current: dict, current_ip, lookup_2025: dict, min_ip: int) -> dict:
    """Mirror of select_batter_metrics for the opposing starter's
    matchup-pillar fields, gated on innings pitched instead of PA."""
    has_any_current = any(current.get(f) is not None for f in PITCHER_CURRENT_FIELDS)
    prior = lookup_2025.get(str(mlbam_id))

    if current_ip is not None and current_ip >= min_ip:
        out = {f: current.get(f) for f in PITCHER_CURRENT_FIELDS}
        out["_source"] = "current_season"
        out["_note"] = f"{current_ip:.1f} IP this season >= qualification minimum ({min_ip}) — using current-season stats."
        return out

    if prior is not None:
        out = {f: prior.get(_PITCHER_LOOKUP_KEY[f]) for f in PITCHER_CURRENT_FIELDS}
        out["_source"] = "prior_season_2025_fallback"
        ip_display = f"{current_ip:.1f}" if current_ip is not None else "0"
        out["_note"] = (
            f"only {ip_display} IP this season "
            f"(below the {min_ip}-IP qualification minimum) — falling back to this pitcher's real 2025 season stats."
        )
        return out

    if has_any_current:
        out = {f: current.get(f) for f in PITCHER_CURRENT_FIELDS}
        out["_source"] = "current_season_small_sample_no_fallback"
        out["_note"] = (
            f"only {current_ip if current_ip is not None else 0} IP this season and not found in the 2025 "
            f"reference (no prior-season MLB time) — using the small current-season sample rather than nothing."
        )
        return out

    out = {f: None for f in PITCHER_CURRENT_FIELDS}
    out["_source"] = "unavailable"
    out["_note"] = "no current-season stats and no 2025 season on record — fields left unset (neutral fallback in score_candidate)."
    return out
