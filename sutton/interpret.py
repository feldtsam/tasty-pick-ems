"""The one LLM call.

SPEC.md: "interpret.py  ONE LLM call, only if status != GREEN". The model
receives the evidence packet and nothing else -- no error text, no raw Make
payload, no conversation history. It returns JSON that validate.py then has
to accept before a single word of it reaches an email.

The call is deliberately unforgiving: a hard 30s timeout and no retries, so
a slow or flaky API cannot stretch a Sutton run. Every failure mode --
timeout, network, HTTP error, refusal, empty content -- returns the same
(ok=False, ...) shape, and the caller turns that into deterministic fallback
text plus an H2 signal. Sutton going quiet is a harness problem, never a TPE
problem.

The model is read from SUTTON_MODEL rather than pinned here, so no code
change is needed to move it. No `thinking` or `output_config.effort` is sent:
those are model-gated, and this module must work with whatever SUTTON_MODEL
holds.
"""

from __future__ import annotations

import json
import os
from typing import Any

__all__ = ["SYSTEM_PROMPT", "TIMEOUT_SECONDS", "MAX_TOKENS", "interpret"]

TIMEOUT_SECONDS = 30.0
MAX_TOKENS = 1024

SYSTEM_PROMPT = """\
You are Sutton, the upstream agent for Tasty Pick Ems (TPE).

TPE is an automated sports-content pipeline. Scheduled Make.com scenarios call
HTTP endpoints that score players, generate picks and grade results. Your job is
to help Sam notice a meaningful pipeline problem early, without adding work.

You will be given one evidence packet as JSON. Deterministic code has already
decided everything that matters: which signals fired, each signal's tier, and
the overall status colour. You do not decide any of that and must never state or
imply a tier, a status, a colour, or a severity ranking.

Your only job is to interpret the evidence in the packet.

Rules:
- Use only what is in the packet. Never introduce a fact, a number, a scenario,
  a date, a cause, or a consequence that is not there.
- Every number you write must appear in the packet's facts. If you cannot say
  something without inventing a number, say it without the number.
- Never estimate or mention time or effort cost: no hours, no minutes, no days,
  no "wasted time", no maintenance or opportunity cost. You cannot know these.
- Correlation is a hypothesis, not a cause. If two things moved together, say
  they moved together and mark the explanation as a possibility.
- If the evidence does not support a conclusion, say so and recommend
  "Continue observing."
- `recent_edits` lists recent configuration changes to the scenario. A change
  close to a signal is worth naming as a possible explanation, never as a proven
  one.
- Be calm and plain. No urgency, no alarm, no drama, no praise, no filler, no
  speculation dressed as insight. Short declarative sentences.

Respond with a single JSON object and nothing else. No prose before or after it,
no markdown fence. Exactly these four keys:

{
  "headline": "one sentence naming what was observed",
  "interpretation": "at most two sentences; separate what was observed from what
                     you think it might mean",
  "recommended_investigation": "one concrete sentence, or exactly 'Continue observing.'",
  "confidence": "low" | "medium" | "high"
}

Hard limit: 80 words total across all four fields. Aim for fewer. An output that
breaks any rule above is discarded and replaced by a plain facts listing, so
brevity and restraint beat completeness."""


def _extract_text(message: Any) -> str:
    parts = []
    for block in getattr(message, "content", None) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    return "".join(parts).strip()


def interpret(packet: dict, *, model: str | None = None,
              client: Any = None) -> tuple[bool, Any, str | None, str | None]:
    """Make the single interpretation call.

    Returns (ok, raw_text, model_name, error). `ok` False means the caller
    must fall back and log H2. Never raises.

    `client` is injectable so tests never touch the network.
    """
    model_name = model or os.environ.get("SUTTON_MODEL") or ""
    if not model_name:
        return False, None, None, "SUTTON_MODEL is not configured"

    try:
        if client is None:
            import anthropic

            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                return False, None, model_name, "ANTHROPIC_API_KEY is not configured"
            # Hard ceiling: 30s, no retries, so one Sutton run cannot be
            # stretched by a slow or flapping API.
            client = anthropic.Anthropic(
                api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=0
            )

        message = client.messages.create(
            model=model_name,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(packet, sort_keys=True)}],
        )
    except Exception as exc:  # noqa: BLE001 - every failure is the same to us
        return False, None, model_name, f"{type(exc).__name__}: {exc}"

    # A safety decline is a failure for our purposes, not a partial success.
    if getattr(message, "stop_reason", None) == "refusal":
        return False, None, model_name, "refusal"

    text = _extract_text(message)
    if not text:
        return False, None, model_name, "empty response"
    return True, text, getattr(message, "model", model_name), None
