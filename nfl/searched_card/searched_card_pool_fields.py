"""
Searched Card writer — the real request-body pool field contract (Stage 2).

SEARCHED_CARD_POOL_FIELDS is the exact set of fields the app MUST send on
EVERY pool row in a POST /api/generate-searched-card request body —
nothing more. Derived directly from evidence_price_classifier.
REQUIRED_ROW_FIELDS and OPTIONAL_COMPLETENESS_FIELDS, not hand-copied —
those two tuples are the real, only fields classify_evidence_vs_price and
pillar_context.build_pillar_context read from a pool member (confirmed by
reading both functions directly: td_opportunity/role_momentum/situation/
evidence_quality/consensus_price_american are REQUIRED_ROW_FIELDS;
td_opportunity_completeness/role_momentum_completeness are
OPTIONAL_COMPLETENESS_FIELDS). Deriving rather than duplicating means this
constant can never silently drift from what those functions actually read.

player_id is added on top of that, for ONE reason only: identification/
debugging on the app and pipeline side (which real player a given pool row
belongs to, in logs or an error report) — neither classify_evidence_vs_
price nor build_pillar_context reads it for any pool member. Nothing else
belongs here: player_name/posteam/position_group/tpe_score/market_value_
score/consensus_implied_probability are all real columns in the real local
week-7 export, and all genuinely unused by either function for a pool
member — see test_searched_card_pool_fields.py for the real, measured
proof that trimming to exactly this set keeps a real week's full pool
comfortably under the request-body cap.

NOTE: this governs POOL rows only, not the single `row` being classified
— `row` still needs its own real identity/display fields for the rest of
generate_searched_card_draft's pipeline (prompt building, citable facts,
the assembled card). This constant is deliberately scoped to the part of
the request body that dominates its byte size (one real week's pool runs
in the hundreds of rows; there is exactly one `row`).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evidence_price_classifier import OPTIONAL_COMPLETENESS_FIELDS, REQUIRED_ROW_FIELDS  # noqa: E402

SEARCHED_CARD_POOL_FIELDS = REQUIRED_ROW_FIELDS + OPTIONAL_COMPLETENESS_FIELDS + ("player_id",)
