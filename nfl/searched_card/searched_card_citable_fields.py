"""
Searched Card writer — citable fields (Deliverable 3).

STAGE 1 DELIVERABLE, design-and-tests only. See searched_card_flags.py.

Derived from the REAL existing allowlist (nfl_writer_common.
NFL_TOP_LEVEL_CITABLE_FIELDS), read-only imported, not copied by hand and
not guessed — a hand-copied duplicate would silently drift the moment the
real list changes. Two changes, both explicit:

  EXCLUDED: tpe_score, market_value_score — per product rule 2, neither
  may be used as narrative evidence for this writer type. They may still
  reach the final CARD object as display-only fields (see searched_card_
  schema.build_searched_card), just never through this citable-fields
  set, so validate_citations()/validate_numeric_grounding() (the shared
  card_writer_common functions, unmodified) would reject a why_reason
  that tried to cite either one — the exclusion isn't just a prompt
  instruction, it's load-bearing at validation time, the same hard-gate
  discipline every citable-fields list in this codebase already has.

  ADDED: implied_probability_pct — consensus_implied_probability RESCALED
  from its real 0-1 fraction storage to 0-100, the exact same rescale
  nfl_writer_common.build_nfl_writer_candidate already applies to
  snap_share_last1_pct/snap_share_season_avg_pct, and for the identical
  reason (see that function's own docstring): citing the raw 0.24 value
  would never match prose written the natural way ("roughly 24%"), and
  no tolerance tier fixes a scale mismatch, only rescaling at the source
  does. consensus_implied_probability is NOT itself added here — only
  the rescaled 0-100 field is citable, so a why_reason can never cite the
  raw fraction and have it silently misread as a percentage.

  NOT added: consensus_implied_probability raw, market_value_completeness
  stays EXCLUDED too, by inheritance from the base list's own real
  gap — the background investigation behind this whole task confirmed
  NFL_TOP_LEVEL_CITABLE_FIELDS already excludes consensus_implied_
  probability (only consensus_price_american/market_value_score/
  market_value_completeness are listed there) — implied_probability_pct
  is this file's own new addition, not a restoration of something that
  was already there.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "content_writer"))
from card_writer_common import EXACT_TOLERANCE, ROUNDING_TOLERANCE  # noqa: E402 -- shared, read-only import
from nfl_writer_common import NFL_TOP_LEVEL_CITABLE_FIELDS, nfl_tolerance_for_key  # noqa: E402 -- shared, read-only import

_EXCLUDED_FROM_NARRATIVE = frozenset({"tpe_score", "market_value_score"})

SEARCHED_CARD_TOP_LEVEL_CITABLE_FIELDS = tuple(
    f for f in NFL_TOP_LEVEL_CITABLE_FIELDS if f not in _EXCLUDED_FROM_NARRATIVE
) + ("implied_probability_pct",)

# Sanity assertions, checked at import time -- fail loud if the base list
# this derives from ever changes shape in a way that would silently
# break the exclusion/addition this module exists to make.
assert "tpe_score" not in SEARCHED_CARD_TOP_LEVEL_CITABLE_FIELDS
assert "market_value_score" not in SEARCHED_CARD_TOP_LEVEL_CITABLE_FIELDS
assert "consensus_implied_probability" not in SEARCHED_CARD_TOP_LEVEL_CITABLE_FIELDS
assert "implied_probability_pct" in SEARCHED_CARD_TOP_LEVEL_CITABLE_FIELDS


def searched_card_tolerance_for_key(key: str) -> float:
    """
    This writer's own tolerance classification, passed to card_writer_
    common.validate_numeric_grounding exactly the way nfl_tolerance_for_
    key already is for the existing writer. Defers to nfl_tolerance_for_
    key for every field it already knows (unmodified, read-only import)
    and special-cases only the one new field: implied_probability_pct is
    a 0-100-scale rescaled percentage, same category as snap_share_
    last1_pct — ROUNDING_TOLERANCE (0.5), not EXACT_TOLERANCE. Without
    this, nfl_tolerance_for_key's own safe default (EXACT_TOLERANCE) would
    apply to a field it has never heard of, which would reject ordinary,
    reasonable rounding ("roughly 24%" for a real 24.3) as if it were a
    fabricated number.
    """
    if key == "implied_probability_pct":
        return ROUNDING_TOLERANCE
    return nfl_tolerance_for_key(key)


def build_searched_card_candidate(row: dict) -> dict:
    """
    Turns one real scored weekly row into the flat dict this writer's
    citable-fields set expects — the Searched Card analog of
    nfl_writer_common.build_nfl_writer_candidate, with the SAME snap-
    share/injury-list shaping (delegated, not reimplemented) plus the one
    new rescale this writer needs.

    consensus_implied_probability is a 0-1 fraction on the real scored
    row (see nfl/market_value.py's implied_probability()/snapshot_
    scoring_inputs() — the median-of-books fraction, not a percentage).
    Rescaled to implied_probability_pct here, the same way snap_share_
    last1/season_avg are rescaled by build_nfl_writer_candidate.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "content_writer"))
    from nfl_writer_common import build_nfl_writer_candidate  # noqa: E402 -- local import, see module docstring

    c = build_nfl_writer_candidate(row)
    if c.get("consensus_implied_probability") is not None:
        c["implied_probability_pct"] = round(float(c["consensus_implied_probability"]) * 100, 1)
    return c
