"""
Shared story-object schema for NFL Intelligence — Market Intelligence
(this task), then Role Changes, Defensive Trends, and Coaching Trends
(not built yet). This module exists specifically so those three don't
each invent their own shape later; Market Intelligence goes first
BECAUSE it's near-pure reuse of already-validated pillar work, which
makes it the cheapest place to get this schema right before three more
families build on top of it.

Same house style as every other module in nfl/ — a plain dict
constructor function, not a dataclass/TypedDict/class. Nothing in this
codebase's domain logic (redzone.py, scoring.py, market_value.py,
shelves.py) uses classes; introducing one here for schema validation
alone isn't a compelling enough reason to break that consistency.
STORY_FIELDS + build_story()'s own required-field check is this
project's version of a "real type" — enforced at construction time,
not just documented and hoped for.

FIELD SEMANTICS (family-agnostic; Market Intelligence's specific
interpretation of each is documented in market_intelligence.py):

  intelligence_family   short slug identifying which family produced
                         this story (e.g. "market") — not a display
                         label.
  entity                dict, {"type": ..., ...} — the subject of the
                         story. Market Intelligence's entity is always
                         a player; Defensive Trends' will presumably be
                         a defense, Coaching Trends' a coordinator/team.
                         Deliberately NOT hardcoded to a player shape.
  headline               short, human-readable claim — Story first, per
                         this project's established storytelling
                         hierarchy (see shelves.py).
  story                  one or two sentences of narrative context
                         beneath the headline — still prose, not yet
                         the raw numbers.
  primary_signal         {"name": str, "value": float} — the ONE number
                         this story is actually about. A name+value
                         pair rather than a bare float specifically so
                         a reader (or a future cross-family view) can
                         tell what's being measured without already
                         knowing which family produced it.
  supporting_evidence     list[str] — the concrete facts backing the
                         headline/story, Numbers last per the
                         storytelling hierarchy.
  trend_direction        str — family-defined vocabulary (Market
                         Intelligence V1 uses market standing, not
                         movement — see market_intelligence.py for why).
  trend_strength          float, 0-100 — magnitude of trend_direction's
                         reading.
  sample_size             int — how much real underlying data this
                         story rests on (Market Intelligence: n_books).
                         Whatever a family's real "how much do we
                         actually know" count is, not a fixed meaning
                         across families.
  completeness            float, 0-100 — how much of this story's own
                         inputs are real vs. fallback/assumed.
  confidence              float, 0-100 — overall trustworthiness. Not
                         required to be a different NUMBER than
                         completeness for every family (Market
                         Intelligence's V1 has no second independent
                         axis to combine, so the two are currently
                         equal there — see market_intelligence.py) —
                         but both fields always exist, so a family that
                         DOES have a genuine second axis (e.g. cross-
                         signal convergence) has somewhere to put it
                         without a schema change.
  time_window             str, human-readable — what period of data
                         this story actually reflects. Deliberately a
                         free-text description, not a strict (start,
                         end) pair — Market Intelligence V1 covers a
                         single snapshot, not a real window (see
                         market_intelligence.py); forcing a two-
                         timestamp shape here would misrepresent that.
  related_players        list[dict] — other players relevant to this
                         story (Market Intelligence: same-game market
                         participants). Empty list, not None, when
                         there are none.
"""

STORY_FIELDS = (
    "intelligence_family",
    "entity",
    "headline",
    "story",
    "primary_signal",
    "supporting_evidence",
    "trend_direction",
    "trend_strength",
    "sample_size",
    "completeness",
    "confidence",
    "time_window",
    "related_players",
)


def build_story(**fields) -> dict:
    """
    Construct one story object. Requires every field in STORY_FIELDS —
    raises immediately on a missing or unexpected key, rather than
    silently returning a partial/stubbed story a caller might not
    notice is missing something. This is what makes the schema a real
    shared contract instead of a convention every family has to
    remember on its own.
    """
    missing = set(STORY_FIELDS) - set(fields)
    if missing:
        raise ValueError(f"build_story missing required field(s): {sorted(missing)}")
    extra = set(fields) - set(STORY_FIELDS)
    if extra:
        raise ValueError(f"build_story got unexpected field(s) not in the shared schema: {sorted(extra)}")
    return {k: fields[k] for k in STORY_FIELDS}
