"""Daily radar aggregation.

SPEC.md, Email formats: "Daily radar goes out at 7:00am CT. It reports the
worst status, and every signal that fired, across all collection ticks since
the previous daily radar, not just the state at 7:00am. Signals that fired
and cleared are listed as 'cleared.'"

This exists because L1 flickers. In the first replay the poller's L1 fired on
20 of 48 ticks across Sep 8-11 and cleared on the other 28, so a radar that
sampled one tick reported a coin flip. Aggregating the window makes the report
independent of which tick the radar happens to land on.

Pure functions. No network, no LLM. Aggregation only -- it never changes a
tier, and it never computes a status that checks.py did not already assign to
some tick in the window.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

__all__ = ["RADAR_HOUR_UTC", "aggregate_window", "daily_radars", "loud_signals"]

# 7:00am CT. September is CDT (UTC-5), so 12:00 UTC.
RADAR_HOUR_UTC = 12

_STATUS_RANK = {"GREEN": 0, "YELLOW": 1, "RED": 2}


def loud_signals(signals: Iterable[dict]) -> list[dict]:
    """RADAR and ESCALATE only. LOG is the quiet majority and is not reported."""
    return [s for s in signals if s.get("tier") in ("RADAR", "ESCALATE")]


def _key(sig: dict) -> tuple:
    return (sig.get("scenario_id"), sig["signal_id"])


def aggregate_window(rows: list[dict]) -> dict:
    """Aggregate one radar window.

    `rows` is the ascending list of {"tick": datetime, "result": <evaluate()>}
    for every collection tick since the previous radar.

    Returns the worst status seen, every loud signal that fired at any tick,
    and for each one whether it was still firing at the window's final tick.
    """
    if not rows:
        return {"status": "GREEN", "signals": [], "harness_signals": [], "ticks": 0}

    worst = "GREEN"
    fired: dict[tuple, dict] = {}
    harness: dict[tuple, dict] = {}

    for row in rows:
        result = row["result"]
        if _STATUS_RANK[result["status"]] > _STATUS_RANK[worst]:
            worst = result["status"]
        for sig in loud_signals(result["tpe_signals"]):
            entry = fired.setdefault(
                _key(sig), {"signal": sig, "first_seen": row["tick"], "ticks_seen": 0}
            )
            # Keep the most recent facts -- they describe the current state.
            entry["signal"] = sig
            entry["last_seen"] = row["tick"]
            entry["ticks_seen"] += 1
        for sig in loud_signals(result["harness_signals"]):
            entry = harness.setdefault(
                _key(sig), {"signal": sig, "first_seen": row["tick"], "ticks_seen": 0}
            )
            entry["signal"] = sig
            entry["last_seen"] = row["tick"]
            entry["ticks_seen"] += 1

    final = rows[-1]["result"]
    present = {_key(s) for s in loud_signals(final["tpe_signals"])}
    present_harness = {_key(s) for s in loud_signals(final["harness_signals"])}

    def shape(entry, still_present):
        return {
            **entry["signal"],
            "cleared": not still_present,
            "first_seen": entry["first_seen"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "last_seen": entry["last_seen"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ticks_seen": entry["ticks_seen"],
        }

    return {
        "status": worst,
        "signals": [shape(e, k in present) for k, e in fired.items()],
        "harness_signals": [shape(e, k in present_harness) for k, e in harness.items()],
        "ticks": len(rows),
        "window_from": rows[0]["tick"].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_to": rows[-1]["tick"].strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def daily_radars(results: list[dict], radar_hour_utc: int = RADAR_HOUR_UTC) -> list[dict]:
    """Split replay results into radar windows and aggregate each one.

    A window covers every tick after the previous radar hour, up to and
    including the next radar hour. The window is labelled with the date the
    radar is sent.
    """
    if not results:
        return []

    windows: list[dict] = []
    current: list[dict] = []
    for row in results:
        current.append(row)
        tick = row["tick"]
        if tick.hour == radar_hour_utc:
            windows.append({"sent_at": tick, **aggregate_window(current)})
            current = []
    if current:
        # Trailing partial window -- the next radar has not been sent yet.
        windows.append(
            {"sent_at": None, "partial": True, **aggregate_window(current)}
        )
    return windows


def render_radar_line(radar: dict, short_names: dict | None = None) -> str:
    """One-line summary for the replay table. Not the email body -- the email
    is rendered from the evidence packet plus the LLM interpretation."""
    short_names = short_names or {}
    if not radar["signals"]:
        return f"{radar['status']:<7} --"
    parts = []
    for sig in sorted(radar["signals"], key=lambda s: (str(s.get("scenario_id")), s["signal_id"])):
        name = short_names.get(sig.get("scenario_id"), sig.get("scenario_id"))
        mark = " cleared" if sig["cleared"] else ""
        parts.append(f"{name}:{sig['signal_id']}({sig['tier']}){mark}")
    return f"{radar['status']:<7} " + "  ".join(parts)
