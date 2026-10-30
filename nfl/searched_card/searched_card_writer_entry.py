"""
Searched Card writer — the (gated, uncalled) entry point shape.

STAGE 1 DELIVERABLE, design-and-tests only. Nothing in this file makes a
network call, writes to a database, or is reachable from any existing
pipeline entry point — see searched_card_flags.py.

This documents the shape a real pipeline wiring would use
(generate_nfl_shelf_card_draft's own shape, one level up) WITHOUT
actually wiring it: generate_searched_card_draft below immediately
raises if SEARCHED_CARD_WRITER_ENABLED is False, which it is, by default,
everywhere in this package. There is no code path, anywhere in this
repository, that flips it to True — a human has to do that by hand after
reviewing every file in this directory, which is the entire point of a
default-OFF flag.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from searched_card_flags import SEARCHED_CARD_WRITER_ENABLED  # noqa: E402


def generate_searched_card_draft(row: dict, anthropic_api_key: str = None, **kwargs) -> dict:
    """
    The real pipeline shape this WOULD be (classify -> build candidate ->
    build prompts -> call the model -> validate -> assemble the card),
    mirroring generate_nfl_shelf_card_content.generate_nfl_shelf_card_
    draft's own real sequence one level up. Never executes that sequence:
    this function's entire body, today, is the gate check below.

    Calling this with the flag at its default (False) raises
    RuntimeError immediately — no row processing, no prompt building, no
    API call, no DB access happens before that raise. This is the
    mechanism, not just a comment, by which "nothing gets activated"
    holds even if some future code imported this function and called it
    by mistake.
    """
    if not SEARCHED_CARD_WRITER_ENABLED:
        raise RuntimeError(
            "SEARCHED_CARD_WRITER_ENABLED is False -- the Searched Card writer is design-and-tests only "
            "(Stage 1). Set searched_card_flags.SEARCHED_CARD_WRITER_ENABLED = True only after a human has "
            "reviewed every file in nfl/searched_card/ and wired a real caller; this function performs none "
            "of that wiring itself."
        )
    # Unreachable while the flag is False -- intentionally left as a
    # shape sketch, not an implementation, since Stage 1 is design-and-
    # tests only and no API key / live caller exists yet to exercise it
    # against.
    raise NotImplementedError("Stage 1 scope: this writer type has no real caller yet.")
