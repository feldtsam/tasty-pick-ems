"""
Searched Card writer — output schema (Deliverable 3).

STAGE 1 DELIVERABLE, design-and-tests only. See searched_card_flags.py.
Mirrors the SHAPE of nfl/content_writer/nfl_shelf_card_writer_schema.py
(title + story + why_reasons, forced tool-use, a defense-in-depth
validate_*_schema_shape() re-check of the pillar enum) but is its own
distinct schema/type, not an edit of that file and not a subclass of it:

  - Its own tool name (emit_searched_card), its own pillar enum
    (SEARCHED_CARD_PILLAR_NAMES — four pillars, not NFL_PILLAR_NAMES'
    five: td_opportunity/role_momentum/situation (the three football-
    evidence pillars the classifier itself reads) plus "market_price", a
    NEW pillar name distinct from the existing writer's "market_value" —
    named differently on purpose, so a reason tagged this way can never
    be confused with a market_value_score-grounded claim the existing
    star-consistency check would expect. A market_price reason is
    grounded in implied_probability_pct ONLY (see searched_card_
    citable_fields.py) and must pass the arithmetic-only framing check
    in searched_card_pricing_lexicon.py.
  - No star_consistency check against market_price (there is no
    comparable 0-100 "market_price score" to check a star rating
    against — the whole point of this pillar is a plain arithmetic
    number, not a percentile score with an expected star range).
  - evidence_vs_price_label is NOT a field the model writes. It is
    computed deterministically by evidence_price_classifier.py BEFORE the
    writer ever runs (per the approved product rule) and handed to the
    model as part of its instructions — see searched_card_prompt.py. It
    reappears here only as a field on the ASSEMBLED CARD (build_
    searched_card below), never inside SEARCHED_CARD_TOOL_SCHEMA itself.

  - evidence_vs_price (Stage 1b addition, same non-model-written
    treatment as evidence_vs_price_label above) is the relative-
    ranking-only field from evidence_price_classifier.classify_evidence_
    vs_price() — "evidence_ranks_higher" / "evidence_ranks_lower" /
    "aligned" / None (STAGE 1c: None whenever classification["label"] is
    "insufficient_evidence" — see that module's "STAGE 1c REVERSAL").
    Also carried onto the assembled card as a display/context field,
    also never inside SEARCHED_CARD_TOOL_SCHEMA — the model never writes
    this field either, only translates it (see searched_card_prompt.py).
    CONFIRMED (Stage 1c, Task 2), not just assumed: build_searched_card
    below carries a None value through to the assembled card's own
    evidence_vs_price field exactly as given — no coercion to a string,
    no silent default to "aligned" or any other value that would
    misrepresent a gated row as having a real rank-position fact. See
    test_searched_card_schema.py for the test that checks this directly.

WHY_REASONS_ARRAY_SCHEMA is imported read-only from card_writer_common.py
— the shared min/max-items contract every writer type already uses
unmodified, not forked.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "content_writer"))
from card_writer_common import WHY_REASONS_ARRAY_SCHEMA  # noqa: E402 -- shared, read-only import

SEARCHED_CARD_PILLAR_NAMES = ("td_opportunity", "role_momentum", "situation", "market_price")

MIN_STORY_LENGTH = 60
MAX_STORY_LENGTH = 900

EVIDENCE_VS_PRICE_LABELS = ("supports_expectation", "diverges_from_expectation", "insufficient_evidence")

REVIEW_STATUSES = ("pending_review", "flagged")  # NEVER "approved" — see build_searched_card()

SEARCHED_CARD_TOOL_SCHEMA = {
    "name": "emit_searched_card",
    "description": (
        "Emit one Searched Card for a single real, curated NFL anytime-touchdown candidate. "
        "Every claim in why_reasons must be traceable to the real source facts provided — never "
        "invent a stat, trend, or fact not present in the source data. A why_reason tagged "
        "'market_price' may state only what the price arithmetically implies, never a true/fair/"
        "mispriced judgment."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "The headline for this card.",
                "minLength": 1,
                "maxLength": 120,
            },
            "story": {
                "type": "string",
                "description": (
                    "2 short paragraphs. Evidence for the headline's claim, built from the real "
                    "football-evidence facts given — never a restatement of a raw score field, and "
                    "never a true/fair-value claim about the price."
                ),
                "minLength": MIN_STORY_LENGTH,
                "maxLength": MAX_STORY_LENGTH,
            },
            "why_reasons": {
                **WHY_REASONS_ARRAY_SCHEMA,
                "items": {
                    **WHY_REASONS_ARRAY_SCHEMA["items"],
                    "properties": {
                        **WHY_REASONS_ARRAY_SCHEMA["items"]["properties"],
                        "pillar": {
                            "type": "string",
                            "enum": list(SEARCHED_CARD_PILLAR_NAMES),
                            "description": "Which of the four real pillars this reason is drawn from.",
                        },
                    },
                },
            },
        },
        "required": ["title", "story", "why_reasons"],
    },
}


def validate_searched_card_schema_shape(output: dict, label: str = None) -> list:
    """
    Defense-in-depth structural check, same discipline as nfl_shelf_
    card_writer_schema.validate_schema_shape — re-checks the pillar enum
    against SEARCHED_CARD_PILLAR_NAMES rather than trusting the shared
    WHY_REASONS_ARRAY_SCHEMA's own (MLB-pillar-enum-free, but still
    generic) item shape alone.

    STAGE 2 ADDITION: `label` (optional, defaults to None — every caller
    from before this stage keeps its old behavior, always requiring 2-3
    items) implements illustrative_sample_cards.md's Card E proposal, now
    approved rather than merely proposed: when `label ==
    "insufficient_evidence"`, exactly 1 why_reason is required instead of
    the usual 2-3. A fully-gated card's pillar_context is withheld down to
    just its `limits` list (see pillar_context.writer_facing_pillar_
    context) — there is no second real pillar claim available to write
    honestly, so forcing 2-3 would either invent one or pad with something
    uncited. See searched_card_tool_schema_for_label below for the
    matching change to the tool schema actually sent to the model, so this
    isn't only a post-hoc check on a model that was never told the real
    rule.
    """
    errors = []
    if not isinstance(output.get("title"), str) or not output["title"].strip():
        errors.append("title is missing or empty")

    story = output.get("story")
    if not isinstance(story, str) or not story.strip():
        errors.append("story is missing or empty")
    elif not (MIN_STORY_LENGTH <= len(story) <= MAX_STORY_LENGTH):
        errors.append(f"story must be {MIN_STORY_LENGTH}-{MAX_STORY_LENGTH} characters, got {len(story)}")

    reasons = output.get("why_reasons")
    if not isinstance(reasons, list):
        errors.append("why_reasons must be a list")
        return errors

    if label == "insufficient_evidence":
        if len(reasons) != 1:
            errors.append(f"why_reasons must have exactly 1 item for an insufficient_evidence card, got {len(reasons)}")
    elif not (2 <= len(reasons) <= 3):
        errors.append(f"why_reasons must have 2-3 items, got {len(reasons)}")

    for i, r in enumerate(reasons):
        if not isinstance(r, dict):
            errors.append(f"why_reasons[{i}] is not an object")
            continue
        if r.get("pillar") not in SEARCHED_CARD_PILLAR_NAMES:
            errors.append(f"why_reasons[{i}].pillar is missing or not one of {SEARCHED_CARD_PILLAR_NAMES}")
        stars = r.get("stars")
        if not isinstance(stars, int) or not (1 <= stars <= 5):
            errors.append(f"why_reasons[{i}].stars must be an integer 1-5")
        if not isinstance(r.get("reason_text"), str) or not r["reason_text"].strip():
            errors.append(f"why_reasons[{i}].reason_text is missing or empty")
        keys = r.get("source_fact_keys")
        if not isinstance(keys, list) or not keys:
            errors.append(f"why_reasons[{i}].source_fact_keys must be a non-empty list")

    return errors


def searched_card_tool_schema_for_label(label: str) -> dict:
    """
    STAGE 2 ADDITION. Returns SEARCHED_CARD_TOOL_SCHEMA unmodified for
    every label except "insufficient_evidence", where why_reasons'
    minItems/maxItems are both overridden to 1 — the real tool schema
    forced on the model should ask for exactly what the product rule
    allows (see validate_searched_card_schema_shape's own matching
    exception above), not rely on a post-hoc count check alone to catch a
    model that followed the generic "2-3" description instead.
    """
    if label != "insufficient_evidence":
        return SEARCHED_CARD_TOOL_SCHEMA
    import copy
    schema = copy.deepcopy(SEARCHED_CARD_TOOL_SCHEMA)
    schema["input_schema"]["properties"]["why_reasons"]["minItems"] = 1
    schema["input_schema"]["properties"]["why_reasons"]["maxItems"] = 1
    schema["input_schema"]["properties"]["why_reasons"]["description"] = (
        "Exactly 1 reason -- this card is gated to insufficient_evidence, so only the price "
        "arithmetic (market_price / implied_probability_pct) is available to cite honestly."
    )
    return schema


def build_searched_card(
    llm_output: dict,
    classification: dict,
    *,
    player_id, shelf_context: str,
    tpe_score=None, market_value_score=None,
    confidence_band: str = None,
    review_status: str = "pending_review",
) -> dict:
    """
    Assembles the final CARD object a human reviewer would see: the
    model's own title/story/why_reasons, PLUS the deterministic
    evidence_vs_price_label/evidence/price-rank fields (computed before
    the writer ran — see evidence_price_classifier.classify_evidence_vs_
    price), PLUS tpe_score/market_value_score as DISPLAY-ONLY fields
    (per product rule 2: usable on the card, never as narrative evidence
    — this function does not and must not feed them into why_reasons or
    story, and nothing calls this with them already baked into llm_output
    because the writer never had them in its citable fields to begin with
    — see searched_card_citable_fields.py).

    Pure assembly — no API call, no DB write, no network of any kind.
    Not called by anything yet; see searched_card_writer_entry.py for the
    (also-gated, also-uncalled) real pipeline shape this would plug into.

    review_status is asserted into REVIEW_STATUSES ("pending_review" or
    "flagged") — NEVER "approved", per the approved product rule that
    this writer type (like the existing one) only ever produces a draft
    for human review, never a self-approved card.
    """
    if review_status not in REVIEW_STATUSES:
        raise ValueError(f"review_status must be one of {REVIEW_STATUSES}, never 'approved' -- got {review_status!r}")
    if classification["label"] not in EVIDENCE_VS_PRICE_LABELS:
        raise ValueError(f"classification['label'] must be one of {EVIDENCE_VS_PRICE_LABELS}")

    return {
        "player_id": player_id,
        "shelf_context": shelf_context,
        "writer_type": "searched_card",
        "title": llm_output["title"],
        "story": llm_output["story"],
        "why_reasons": llm_output["why_reasons"],
        "evidence_vs_price_label": classification["label"],
        "evidence_vs_price_reason": classification["reason"],
        "evidence_vs_price": classification["evidence_vs_price"],
        "evidence_rank_pct": classification["evidence_rank_pct"],
        "price_rank_pct": classification["price_rank_pct"],
        "implied_probability_pct": classification["implied_probability_pct"],
        # Display-only -- see this function's own docstring. Never read by
        # anything in why_reasons/story; a reviewer may still want to see
        # the overall blended number next to this card's own evidence-vs-
        # price read.
        "tpe_score_display_only": tpe_score,
        "market_value_score_display_only": market_value_score,
        "confidence_band": confidence_band,
        "review_status": review_status,
    }
