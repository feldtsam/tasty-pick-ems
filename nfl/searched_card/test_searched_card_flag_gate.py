"""
Confirms the default-OFF flag actually gates execution -- not just by
convention, but by a real raise at call time, with the flag at its
checked-in default (False).

Run: cd nfl/searched_card && ../.venv/bin/python -m pytest test_searched_card_flag_gate.py -v
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from searched_card_flags import SEARCHED_CARD_WRITER_ENABLED  # noqa: E402
from searched_card_writer_entry import generate_searched_card_draft  # noqa: E402


def test_flag_defaults_to_false():
    assert SEARCHED_CARD_WRITER_ENABLED is False


def test_entry_point_refuses_to_run_while_flag_is_false():
    """Stage 2: generate_searched_card_draft's real signature is now
    (row, pool, anthropic_api_key, **kwargs) -- pool is a required
    positional, not optional, since the real implementation needs it to
    classify the row. Still raises before touching either argument."""
    with pytest.raises(RuntimeError, match="SEARCHED_CARD_WRITER_ENABLED is False"):
        generate_searched_card_draft({"player_id": "test"}, [], "not-a-real-key")
