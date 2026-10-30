"""
Deliverable 6 — the three illustrative example cards, run through the
real classifier against the real fixture's pool, plus a field-by-field
grounding check against the real citable-fields list (Deliverable 3).

INVENTED PLAYERS, INVENTED STAT VALUES, do not canonize, not real cards
-- see this package's classification_rubric.md / final report for the
same caveat. The POOL they are ranked against is real (2025_wk10.csv,
745 players with real odds), so the rank comparison is against a real
population; only the three rows themselves are synthetic.

STAGE 1b REWRITE: Stage 1's version of this file deliberately included
one ungroundable claim per card, to demonstrate what the grounding check
catches. This revision replaces each ungroundable claim with a real,
groundable one -- re-verified against the real, CURRENT
SEARCHED_CARD_TOP_LEVEL_CITABLE_FIELDS (read fresh from
searched_card_citable_fields.py, not trusted from the prior run's
memory -- see grounding_audit.md, Task 3, for the fuller field-by-field
audit this rewrite drew on), not just carried over from the old list.

STAGE 1c TREND-WORD GROUNDING AUDIT: being backed by a real, citable
field (Stage 1b's bar) is necessary but not sufficient -- a claim can
cite a real field and still overclaim what that field supports, if the
claim's own WORDING implies a before/after comparison (e.g. "cooled
off," "down," "climbing," "starting to show up") that the field itself
doesn't encode. Every one of the 9 claims below was re-checked, this
revision, for exactly that: does the claim use a trend/comparison word,
and if so, does its backing field actually carry a delta (fields with
"trend"/"movement" in their name do; a bare trailing-window SUM like
i10_touches_trail3/gl_touches_trail3 does not -- see grounding_audit.md).
Two of the 9 failed this check (Card 1's former "touches inside the ten
have cooled off" and Card 3's former "goal-line touches are starting to
show up," both backed by trail3 sums with no comparison baked in, and
both confirmed to have no trail6/prior-window alternative anywhere in
this repo to cite instead) and were rewritten below to state only the
magnitude their real field supports, with no directional word. The
other 7 either used no trend word at all, or used one whose backing
field genuinely encodes a delta (anything with "trend"/"movement" in its
name) -- see the final report's Task 3 for the full claim-by-claim
finding, not just the two that changed.

Every claim below is now backed by a real field AND, as of this
revision, by wording that field can actually support; the table this
module prints names the exact field for each one.

Run: cd nfl/searched_card && ../.venv/bin/python -m pytest test_example_cards.py -v -s
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evidence_price_classifier import classify_evidence_vs_price  # noqa: E402
from searched_card_citable_fields import SEARCHED_CARD_TOP_LEVEL_CITABLE_FIELDS  # noqa: E402

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "data" / "stub_weeks" / "2025_wk10.csv"


def _load_real_pool() -> list:
    with open(FIXTURE_PATH) as f:
        rows = list(csv.DictReader(f))
    pool = []
    for r in rows:
        if not r["consensus_price_american"]:
            continue
        pool.append({
            "td_opportunity": float(r["td_opportunity"]), "role_momentum": float(r["role_momentum"]),
            "situation": float(r["situation"]), "evidence_quality": float(r["evidence_quality"]),
            "consensus_price_american": float(r["consensus_price_american"]),
        })
    return pool


# ---------------------------------------------------------------------------
# Each example card's claims, mapped to the real field backing it --
# checked against SEARCHED_CARD_TOP_LEVEL_CITABLE_FIELDS programmatically
# below, not just asserted in prose. Stats/price are UNCHANGED from Stage
# 1 (so the real classification below is the same real result); only the
# CLAIMS changed.
# ---------------------------------------------------------------------------

CARD_1 = {
    "price": -150.0,
    "title": "The Name Is Doing the Work",
    "stats": {"td_opportunity": 41.0, "role_momentum": 34.0, "situation": 37.0, "evidence_quality": 72.0},
    "claims": [
        ("snap share is down vs. a prior stretch", "snap_share_trend_pct_role"),
        # REPLACED (Stage 1c, trend-word grounding audit) -- "cooled off" is
        # a trend/comparison word (implies a before/after change), but
        # i10_touches_trail3 is a bare trailing-3-game SUM with no
        # comparison or prior-window baked into the number itself (see
        # grounding_audit.md's own finding on this exact field, and
        # Stage 1c's grounding_audit.md addendum below -- confirmed no
        # trail6/prior-window variant of this field exists anywhere in this
        # repo to cite instead). Rewritten to state only the magnitude the
        # field actually supports, with no directional word.
        ("touches inside the ten have been thin", "i10_touches_trail3"),
        ("matchup is not generous", "defensive_matchup_vulnerability"),
    ],
}

CARD_2 = {
    "price": 175.0,
    "title": "The Workload Explains the Attention",
    "stats": {"td_opportunity": 68.0, "role_momentum": 60.0, "situation": 71.0, "evidence_quality": 75.0},
    "claims": [
        # REPLACED (Stage 1b) -- Stage 1's "11 inside-the-10 touches over 5
        # games" failed on two independent grounds (a trail3 window stated
        # as "5 games," and a specific game count the prompt's own hard rule
        # bans). Rephrased generically, with no game count and no invented
        # window length, while staying backed by the same real field.
        ("a real pile of touches inside the ten", "i10_touches_trail3"),
        ("a stable role", "touch_share_trend_pct_role"),
        ("a generous opponent", "recent_tds_allowed_pct"),
    ],
}

CARD_3 = {
    "price": 650.0,
    "title": "A Different Role Is Emerging",
    "stats": {"td_opportunity": 55.0, "role_momentum": 58.0, "situation": 63.0, "evidence_quality": 61.0},
    "claims": [
        # REPLACED (Stage 1b) -- Stage 1's "depth chart lists him as the
        # secondary option" had no backing field (depth_rank, the snapshot,
        # is not citable -- only its MOVEMENT delta is; see grounding_
        # audit.md claim type #1). Rephrased as a movement claim, which is
        # both real evidence this writer can cite AND a better fit for "a
        # different role is EMERGING" than a static snapshot would have
        # been in the first place.
        ("he's been climbing the depth chart", "depth_chart_movement_pct"),
        # REPLACED (Stage 1b) -- Stage 1's "snaps are up over 3 games" was
        # right on the field and the number, wrong on stating a game count
        # at all (same hard rule as Card 2). Rephrased with no count.
        ("snaps are trending up", "snap_share_trend_pct_role"),
        # REPLACED (Stage 1b) -- Stage 1's "new red-zone targets" had no
        # backing field (rz_target_touches is target-only and not citable,
        # and there is no trailing-window version of it at all -- see
        # grounding_audit.md claim type #5). Swapped for the real, citable
        # goal-line touch count, which is close enough to "red-zone" in the
        # sense a reader would mean it, and is real, trailing, and citable.
        # REPLACED AGAIN (Stage 1c, trend-word grounding audit) -- the
        # Stage 1b wording above ("starting to show up") is itself a
        # trend/comparison phrase (implies these touches are new / weren't
        # there before), but gl_touches_trail3 is the same kind of bare
        # trailing-3-game SUM as i10_touches_trail3 above, with no
        # comparison or prior-window baked in -- confirmed no trail6/
        # prior-window field exists for goal-line touches either (see
        # grounding_audit.md, which already names this exact gap for
        # red-zone touch counts generally). Rewritten to state only the
        # magnitude, no directional word -- "starting to show up" implied a
        # change this field cannot support; "a real ... touch count" states
        # only that the number is real and non-trivial, the same no-
        # directional-word discipline Card 2's "a real pile of touches
        # inside the ten" claim already uses for the identical field type.
        ("a real goal-line touch count", "gl_touches_trail3"),
    ],
}


def _is_groundable(field_name) -> bool:
    return field_name is not None and field_name in SEARCHED_CARD_TOP_LEVEL_CITABLE_FIELDS


def _print_card(card: dict, result: dict) -> None:
    print(f"\n{card['title']!r} ({card['price']:+.0f}): label={result['label']} evidence_vs_price={result['evidence_vs_price']} gap={result['gap']}")
    for claim_text, field in card["claims"]:
        print(f"  claim: {claim_text!r:50} field={field!r:30} groundable={_is_groundable(field)}")


def test_card_1_every_claim_is_groundable():
    pool = _load_real_pool()
    row = dict(CARD_1["stats"], consensus_price_american=CARD_1["price"])
    result = classify_evidence_vs_price(row, pool + [row])
    _print_card(CARD_1, result)
    # Weak-on-all-three evidence at a short price -- price ranks far above
    # the football evidence -> raises_questions, same real outcome as
    # Stage 1 (stats/price unchanged).
    assert result["label"] == "raises_questions"
    ungroundable = [c for c, f in CARD_1["claims"] if not _is_groundable(f)]
    assert ungroundable == [], f"Card 1 has ungroundable claims: {ungroundable}"
    assert len(CARD_1["claims"]) == 3


def test_card_2_every_claim_is_groundable():
    pool = _load_real_pool()
    row = dict(CARD_2["stats"], consensus_price_american=CARD_2["price"])
    result = classify_evidence_vs_price(row, pool + [row])
    _print_card(CARD_2, result)
    assert result["label"] in ("supports_expectation", "raises_questions")
    ungroundable = [c for c, f in CARD_2["claims"] if not _is_groundable(f)]
    assert ungroundable == [], f"Card 2 has ungroundable claims: {ungroundable}"
    assert len(CARD_2["claims"]) == 3


def test_card_3_every_claim_is_groundable():
    pool = _load_real_pool()
    row = dict(CARD_3["stats"], consensus_price_american=CARD_3["price"])
    result = classify_evidence_vs_price(row, pool + [row])
    _print_card(CARD_3, result)
    assert result["label"] in ("supports_expectation", "raises_questions", "insufficient_evidence")
    ungroundable = [c for c, f in CARD_3["claims"] if not _is_groundable(f)]
    assert ungroundable == [], f"Card 3 has ungroundable claims: {ungroundable}"
    assert len(CARD_3["claims"]) == 3


def test_every_claim_across_all_three_cards_is_groundable():
    """The aggregate assertion the rewrite was for: zero ungroundable
    claims anywhere across all three example cards, re-checked against
    the real, freshly-imported field list (not a cached copy)."""
    all_claims = CARD_1["claims"] + CARD_2["claims"] + CARD_3["claims"]
    ungroundable = [c for c, f in all_claims if not _is_groundable(f)]
    assert ungroundable == []
    assert len(all_claims) == 9

    fields_used = sorted({f for _, f in all_claims})
    print(f"\nReal fields backing the 9 claims across all 3 cards: {fields_used}")
    for f in fields_used:
        assert f in SEARCHED_CARD_TOP_LEVEL_CITABLE_FIELDS
