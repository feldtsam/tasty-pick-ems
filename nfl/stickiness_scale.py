"""
The one place STICKINESS_PERCENTILE_SCALE is read.

curate_home_shelves._compute_sticky_assignment compares a player's
candidate shelf against their current home shelf on RAW signal values,
and those values come from different columns on different scales:
trend shelves rank on role_momentum, the odds bands on tpe_score, Red
Zone Trends on td_opportunity. A 20-point margin across two of those is
not the same bar as 20 points within one of them, which is why that
function's own docstring already flags the comparison as "worth a real
second look with Sam, not silently assumed fine".

With this flag on, the stickiness comparison uses the SAME percentile
scale the fresh pick in assign_home_shelves already uses for its
trend-vs-trend tiebreak: each shelf's own qualifying pool as the
reference population. The two-consecutive-runs rule is untouched.

OFF BY DEFAULT, and only the exact string "true" turns it on -- an
unset variable, "1", "TRUE", "yes" and anything else all read as off.
With it off, every output is byte-identical to the pre-flag pipeline.

Read ONCE per run and cached, for the same reason trend_frame caches:
one curation run must not straddle an environment change and end up
with half its assignments made on one scale and half on the other.
Every consumer takes the resolved boolean as a parameter -- os.environ
is touched here and nowhere else.
"""
import os

STICKINESS_PERCENTILE_SCALE_ENV = "STICKINESS_PERCENTILE_SCALE"

_cached: bool | None = None


def percentile_stickiness_enabled() -> bool:
    """True only when STICKINESS_PERCENTILE_SCALE is exactly "true"."""
    global _cached
    if _cached is None:
        _cached = os.environ.get(STICKINESS_PERCENTILE_SCALE_ENV) == "true"
    return _cached


def reset_cache() -> None:
    """Clear the cached read. Tests only."""
    global _cached
    _cached = None


def resolve(flag: bool | None) -> bool:
    """Caller-supplied override, else the environment."""
    return percentile_stickiness_enabled() if flag is None else bool(flag)
