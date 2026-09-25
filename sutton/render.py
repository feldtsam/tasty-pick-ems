"""Email subject and body.

SPEC.md "Email formats". The header line is rendered from the packet's status,
never from LLM output -- that is the structural half of acceptance test 5. An
interpretation can be rejected, empty, or absent entirely and the header still
reads the colour that checks.py assigned.

HARNESS_HEALTH is appended under its own heading and only when present, so a
collection gap never bleeds into the TPE status line.

Pure functions. No network, no LLM.
"""

from __future__ import annotations

__all__ = ["FALLBACK_NOTE", "render_email", "render_fallback_body"]

FALLBACK_NOTE = "Interpretation unavailable."

_HEADERS = {
    "GREEN": "SUTTON — GREEN",
    "YELLOW": "SUTTON — YELLOW",
    "RED": "SUTTON — RED (escalation)",
}

_GREEN_BODY = "No meaningful emerging risks detected across {n} watched scenarios."


def _primary(packet: dict) -> dict | None:
    """The signal the email leads with: worst tier first, then packet order."""
    signals = packet.get("signals") or []
    if not signals:
        return None
    rank = {"ESCALATE": 2, "RADAR": 1, "LOG": 0}
    return max(signals, key=lambda s: rank.get(s.get("tier"), 0))


def _fact_lines(signal: dict) -> list[str]:
    """Deterministic facts for one signal, for the no-LLM path.

    Only structured values. `flagged_runs` is expanded because it is the
    evidence for L1; nothing here can contain error text, since packet.py
    already stripped it.
    """
    lines = []
    facts = signal.get("facts") or {}
    for key, value in facts.items():
        if key == "flagged_runs" and isinstance(value, list):
            for run in value:
                lines.append(
                    "    flagged run {at}: {duration_s}s vs median {median}s "
                    "(+{excess}s)".format(
                        at=run.get("at"),
                        duration_s=run.get("duration_s"),
                        median=run.get("baseline_median_s"),
                        excess=run.get("excess_s"),
                    )
                )
        elif isinstance(value, (list, dict)):
            lines.append(f"    {key}: {value}")
        else:
            lines.append(f"    {key}: {value}")
    return lines


def render_fallback_body(packet: dict) -> str:
    """Deterministic facts plus the unavailable note (SPEC.md)."""
    blocks = []
    for sig in packet.get("signals") or []:
        head = f"{sig.get('scenario')}: {sig.get('signal_id')} ({sig.get('tier')})"
        blocks.append("\n".join([head, *_fact_lines(sig)]))
    body = "\n\n".join(blocks) if blocks else "No signals."
    return f"{body}\n\n{FALLBACK_NOTE}"


def _since_last_radar_block(packet: dict) -> str:
    """SPEC.md: "Signals that fired and cleared are listed as 'cleared.'"

    Only rendered on a daily radar, where signals carry a `cleared` flag from
    radar.aggregate_window(). On a collect run there is no window to summarise
    and no flag, so this is empty.
    """
    signals = [s for s in (packet.get("signals") or []) if "cleared" in s]
    if not signals:
        return ""
    lines = ["", "Since the last radar:"]
    for sig in signals:
        mark = " — cleared" if sig.get("cleared") else ""
        lines.append(
            f"  {sig.get('scenario')}: {sig.get('signal_id')} "
            f"({sig.get('tier')}){mark}"
        )
    return "\n".join(lines)


def _harness_block(harness_signals) -> str:
    loud = [s for s in (harness_signals or []) if s.get("tier") in ("RADAR", "ESCALATE", "LOG")]
    if not loud:
        return ""
    lines = ["", "HARNESS_HEALTH"]
    for sig in loud:
        reason = sig.get("reason")
        suffix = f" — {reason}" if reason else ""
        lines.append(f"  {sig.get('signal_id')} ({sig.get('tier')}){suffix}")
        for key, value in (sig.get("facts") or {}).items():
            lines.append(f"    {key}: {value}")
    return "\n".join(lines)


def render_email(packet: dict, interpretation: dict | None, *,
                 harness_signals=None, shadow: bool = False) -> tuple[str, str]:
    """Return (subject, body).

    `interpretation` is validate.py's accepted output, or None for the
    deterministic fallback.
    """
    status = packet.get("status", "GREEN")
    header = _HEADERS.get(status, f"SUTTON — {status}")
    primary = _primary(packet)
    scenario = (primary or {}).get("scenario") or "TPE"

    if status == "GREEN":
        body_lines = [
            header,
            _GREEN_BODY.format(n=packet.get("watched_scenarios", 0)),
        ]
        subject = f"{header}"
    elif interpretation is None:
        body_lines = [header, render_fallback_body(packet)]
        subject = f"{header} · {scenario}"
    else:
        head_line = f"{scenario}: {interpretation['headline']}"
        if status == "RED":
            tail = f"Recommendation: {interpretation['recommended_investigation']}"
        else:
            signal_id = (primary or {}).get("signal_id", "signal")
            tail = (
                f"Watching: {signal_id}. "
                f"{interpretation['recommended_investigation']}"
            )
        body_lines = [header, head_line, interpretation["interpretation"], tail]
        subject = f"{header} · {scenario}"

    body = (
        "\n".join(body_lines)
        + _since_last_radar_block(packet)
        + _harness_block(harness_signals)
    )
    if shadow:
        subject = f"[SHADOW] {subject}"
    return subject, body
