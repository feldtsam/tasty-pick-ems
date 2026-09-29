"""Evidence packet: checks.py signals -> the exact object the LLM receives.

SPEC.md "Sutton contract". This is the ONLY thing interpret.py sends to the
Anthropic API, so it is also the security boundary for error text.

Three guarantees, each enforced here rather than trusted:

  1. No `error_message` and no `detail`, redacted or not. checks.py never
     emits them, but a future rule might, and this module is the last place
     to catch that before text leaves the machine.
  2. Only RADAR and ESCALATE signals. LOG is the quiet majority and is not
     evidence of anything.
  3. HARNESS_HEALTH signals are not in the packet at all. They describe
     Sutton's own plumbing, not TPE, and SPEC.md keeps them out of the TPE
     status line. render.py appends them to the email from the evaluation
     directly. H2 in particular cannot be in the packet -- it is raised by
     the failure of the very call the packet feeds.

Pure functions. No network, no LLM.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

__all__ = ["FORBIDDEN_FACT_KEYS", "build_packet", "fact_numbers", "packet_signals"]

# Free text that must never reach the Anthropic API.
FORBIDDEN_FACT_KEYS = frozenset({"error_message", "detail"})

LOUD_TIERS = ("RADAR", "ESCALATE")

# The signal fields the LLM sees. `scenario_id` is deliberately excluded: it
# is a large number that would widen the anti-fabrication check's allowed set
# for no interpretive benefit.
#
# `cleared` is present only on a daily radar, where signals are aggregated
# across the window: True means the signal fired during the window but was not
# firing at its end. That changes the interpretation materially -- "failed twice
# overnight, recovered by morning" is a different story from "failing now" -- so
# the LLM needs it. It is a boolean, so it cannot widen the anti-fabrication
# number set. Fields absent from a signal are simply omitted.
_SIGNAL_FIELDS = (
    "signal_id", "class", "tier", "scenario", "facts", "recent_edits", "cleared",
)


def _strip_forbidden(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: _strip_forbidden(v)
            for k, v in value.items()
            if k not in FORBIDDEN_FACT_KEYS
        }
    if isinstance(value, list):
        return [_strip_forbidden(v) for v in value]
    return value


def packet_signals(evaluation: dict) -> list[dict]:
    """The loud TPE signals, shaped for the packet."""
    out = []
    for sig in evaluation.get("tpe_signals") or []:
        if sig.get("tier") not in LOUD_TIERS:
            continue
        shaped = {k: sig.get(k) for k in _SIGNAL_FIELDS if k in sig}
        shaped["facts"] = _strip_forbidden(shaped.get("facts") or {})
        out.append(shaped)
    return out


def build_packet(evaluation: dict, *, watched_scenarios: int | None = None,
                 date: str | None = None) -> dict:
    """Build the evidence packet from a checks.evaluate() result."""
    if date is None:
        evaluated = evaluation.get("evaluated_at")
        date = (
            str(evaluated)[:10]
            if evaluated
            else datetime.now(timezone.utc).strftime("%Y-%m-%d")
        )
    return {
        "date": date,
        "status": evaluation.get("status", "GREEN"),
        "watched_scenarios": (
            watched_scenarios
            if watched_scenarios is not None
            else evaluation.get("watched_scenarios", 0)
        ),
        "signals": packet_signals(evaluation),
    }


def fact_numbers(packet: dict) -> set[float]:
    """Every number reachable inside the packet's `facts`.

    This is the allowed set for validate.py's anti-fabrication check. Digit
    runs inside fact strings count too, so an ISO timestamp in `facts` lets
    the model name that date without being accused of inventing a number.
    """
    import re

    found: set[float] = set()
    digits = re.compile(r"\d+(?:\.\d+)?")

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, bool):
            return
        elif isinstance(node, (int, float)):
            found.add(float(node))
        elif isinstance(node, str):
            for m in digits.findall(node):
                try:
                    found.add(float(m))
                except ValueError:
                    pass

    for sig in packet.get("signals") or []:
        walk(sig.get("facts") or {})
    return found
