"""
The one place TREND_PARTICIPATION_FRAME is read.

Option 2a (participation-based trend windows) computes the role-trend
windows and the games-played mask from a participation-complete frame
(snap counts + play-by-play) instead of from the red-zone weekly rows,
which only exist for games where a player actually touched the ball in
the red zone. See redzone.build_participation_frame for what that frame
is and why `games_played` taken off the weekly rows understates real
participation.

OFF BY DEFAULT, and only the exact string "true" turns it on: an unset
variable, "1", "TRUE", "yes" and anything else all read as off. With it
off every output is byte-identical to the pre-flag pipeline.

Read ONCE per run and cached, so a long pipeline run cannot straddle an
environment change half way through and produce a frame whose windows
and whose mask disagree about which design they came from. Every
consumer takes the resolved boolean as a parameter -- os.environ is
touched here and nowhere else, so there is exactly one answer per run
and one place to change how it is spelled.
"""
import os

TREND_PARTICIPATION_FRAME_ENV = "TREND_PARTICIPATION_FRAME"

_cached: bool | None = None


def participation_frame_enabled() -> bool:
    """
    True only when TREND_PARTICIPATION_FRAME is exactly "true". Cached
    for the life of the process after the first call.
    """
    global _cached
    if _cached is None:
        _cached = os.environ.get(TREND_PARTICIPATION_FRAME_ENV) == "true"
    return _cached


def reset_cache() -> None:
    """
    Clear the cached read. For tests only -- a real run reads the
    environment once and keeps that answer.
    """
    global _cached
    _cached = None


def resolve(flag: bool | None) -> bool:
    """
    Caller-supplied override, else the environment. Every entry point
    takes `participation_frame: bool | None = None` and routes it
    through here, so a test (or a preview caller) can force either mode
    without touching os.environ, and production gets the flag.
    """
    return participation_frame_enabled() if flag is None else bool(flag)
