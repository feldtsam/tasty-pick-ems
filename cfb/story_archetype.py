"""
CFB Visual Resolver, Phase 1 — story_archetype.py.

The Story Archetype Resolver: maps one real scored player-week row (plus
which of the 8 real shelves it landed on — curate_cfb_shelves.add_shelf_
convergence's own `shelves` list) to a fixed illustration archetype for
that card, a team-color tint pair, and any subordinate shelf-context
motifs. Mirrors nfl/story_archetype.py's role and its split from the art/
rendering layer (nfl-archetype-prompts.ts / nfl-visual.ts) — this module
never generates art, never picks an asset file, never renders anything.
Pure functions, no I/O, no DB writes, same "takes scoring output as
input" contract as cfb/scoring.py.

Phase 1 only (per spec): resolver structure. Art prompts, generation, and
pipeline wiring are later phases, exactly like NFL's own build order
(story_archetype.py shipped standalone before nfl-archetype-prompts.ts
and before ad3eb21 wired resolve_archetype() into NFL's live curation
pipeline).

THE PIPELINE — Sport → Shelf → Story Archetype → Team Identity →
Context:
  1. Shelf: caller already knows which of the 8 real shelves (assign_
     cfb_shelves) this player-week landed on.
  2. Story Archetype: resolve_cfb_archetype() below.
  3. Team Identity: get_cfb_tint_profile() below.
  4. Context: resolve_cfb_shelf_context() below.

ARCHETYPE RESOLUTION IS PER-PLAYER, NOT PER-SHELF-PLACEMENT — a
deliberate difference from how resolve_cfb_archetype() is invoked here
vs. how NFL's resolve_archetype(shelf, row) takes a single shelf. CFB's
own design decision (this task's own spec) is what forces this: "when a
card has both a story archetype and a shelf-context motif [e.g. a player
on both Goal-Line Favorites and SEC TD Watch], the story archetype wins
— shelf-context becomes a subordinate background motif." That only
makes sense as ONE resolution per player-week that looks at ALL of a
player's shelf memberships together, not one independent resolution per
shelf a player happens to appear on (which would leave no way to decide
which of two simultaneous archetypes "wins" when a player is genuinely
on two behavior shelves, or would silently produce two different card
identities for the same player in the same week). So both functions
below take the row's full `shelves` list (curate_cfb_shelves.add_shelf_
convergence's own output, or an equivalent caller-supplied list for
direct testing) rather than a single shelf name.

PART 1 — THE OPEN ITEM, RESOLVED: defensive_matchup_vulnerability gets
its own 5th archetype, THE MISMATCH, not left out of archetype
resolution. Recommendation, with the tradeoff named rather than picked
silently (per this task's own instruction):

  Reasoning FOR adding it: (a) it is a fully validated, live signal —
  same standing as the other 3 — and NFL's own story_archetype.py
  already treats the IDENTICAL underlying concept (defensive_matchup_
  vulnerability) as archetype-worthy via its own MISMATCH archetype, so
  this isn't a new narrative invented from nothing, it's a direct,
  low-risk precedent already proven out in the sibling sport. (b)
  Architecturally free: population-shelf archetype resolution (see
  _resolve_independent below) was already going to be signal-driven and
  independent of which specific shelf triggered it, for the other 3
  archetypes — Top 25 / conference shelves never had a shelf of their
  own reinforcing WHICH of td_opportunity / role_momentum / target_
  magnets a given card's story comes from either. Adding a 4th candidate
  signal to that same independent-resolution pool is one more entry in
  an existing table, not new resolver shape. (c) The alternative (option
  b, leaving it out) means one of CFB's 4 headline evidence pillars is
  never visually representable at all — a real, likely-to-resurface gap
  once someone asks why defensive_matchup_vulnerability never shows up
  in the art despite being scored, live, and load-bearing in the
  Universal TPE composite every shelf already ranks by.

  The tradeoff, named honestly: unlike Goal-Line/Workhorse/Target Magnet,
  no shelf headlines "The Mismatch" the way those three do — a card can
  carry this story with no on-screen shelf context explaining "why," and
  there's no near-term shelf planned to close that gap (no "Weak Defense
  Watch" shelf exists or is scoped). This is the SAME class of thing
  already true for any population-shelf resolution today (a player's
  Top-25-shelf card can already carry a Workhorse story despite not being
  on the Workhorses shelf) — so it's not a NEW kind of risk, but it does
  mean Mismatch inherits that risk MORE than the other three, since it
  has literally no shelf of its own on any path, ever. Flagging this
  explicitly rather than treating it as free.

  Ships as archetype key "MISMATCH", governing signal defensive_matchup_
  vulnerability, gate = defensive_matchup_completeness > 0 (see
  _mismatch_eligible's own comment for why completeness > 0, not a
  boolean, is the right gate shape for this specific signal).

THE FLOOR — 55.0. Same underlying reasoning NFL's story_archetype.py
used (a fixed distance above the KNOWN neutral-fill sentinel, not a
number tuned to any one week's own sparsity), CONFIRMED transferable:
cfb/normalize.py's fill_neutral has the exact same real 50.0 default
CFB-side (checked directly, not assumed) — every CFB pillar here is,
like NFL's, a percentile-ranked 0-100 score that degrades to exactly
50.0 when a real reference population is missing. A floor set as a fixed
distance above that known sentinel keeps working regardless of how
populated the real reference distribution is at any given point in the
season, for the same reason it does on the NFL side.

Full honesty on ONE real difference from NFL's own floor derivation,
worth naming rather than glossing over: NFL's 55.0 was a REUSE of an
already-vetted cross-module number (content_writer/nfl_writer_common.
NFL_REGULAR_ROW_CONFIDENCE_BAND_THRESHOLDS' own "developing_angle ->
strong_setup" seam, derived from real historical tpe_score population
analysis). CFB has no equivalent pre-existing confidence-band constant
anywhere in this codebase to borrow the same way — grepped clean. So
55.0 here is the SAME anti-sentinel REASONING applied fresh, not a
reused, independently-pre-vetted number. It is grounded in something
real, though: cfb/scripts/shelf_sanity.py's own 2025 Weeks 1-6 pull
(the exact real dataset this Phase 5 build was validated against) shows
real, ungated per-signal scores spanning the FULL range even within an
already-non-thin population — e.g. Goal-Line Favorites' own top 7
ungated td_opportunity scores that week ran from 68.1 down to 26.2, well
below 50 — confirming a plain non-thin real score can legitimately land
below the neutral sentinel itself. 55.0 asks for genuinely
above-median, not merely non-thin, before a signal is allowed to drive a
card's whole visual identity — a deliberately stricter bar than
"cleared its own pillar's internal qualification gate," which is all
`_x_eligible()` below actually checks. Flag back if real Week-7+ data
suggests this floor is sitting in the wrong place once more weeks of a
real season are available to check it against (the same posture NFL's
own floor was left in — confirmed necessary via real data, not assumed
permanent).

TIE-BREAKING (population-shelf resolution only — behavior-shelf locks
have no tie to break, see resolve_cfb_archetype's own docstring): all 4
candidate signals are independently-scaled percentiles (each ranked
against its own real reference population), so a literal tie at the same
rounded score is coincidental, not a meaningful signal collision — an
arbitrary but DETERMINISTIC rule is all that's needed. Ties go to
whichever candidate is earliest in _ARCHETYPE_SPECS' own fixed order:
GOAL_LINE, WORKHORSE, TARGET_MAGNET, MISMATCH — the first 3 in the same
priority CFB_SHELF_ORDER already uses for its own real shelves (they at
least have an on-screen shelf backing them), MISMATCH last since it has
none (see Part 1's own tradeoff note above). Not NFL's own tie-break
rule (which favors the calling shelf's own "primary signal family" —
that concept doesn't exist here, since this module is never called with
a single governing shelf the way NFL's is) — a fresh, CFB-appropriate
tie-break, not a blind copy.

TEAM IDENTITY (tint) — reuses NFL's exact getNFLTintProfile() mechanism
(nfl-visual.ts), ported to Python: same HSL clamp band (lightness
[0.22, 0.74], saturation <= 0.70), same reasoning (a near-black or
near-white raw team color needs a floor/ceiling so it stays visible
against a dark card ground without blowing out; several real team colors
sit near-maximum raw saturation and need a cap so they don't fight an
archetype's own already-tuned art palette). The one real, deliberate
difference: CFBD's `/teams/fbs` supplies real, live color/alternateColor
hex per school (get_cfb_tint_profile is fed cfb.ids.team_color_map(),
confirmed 100% real coverage across all 136 FBS teams and stable
2019->2025 in a prior investigation) rather than NFL's own hand-
maintained 32-team NFL_TEAM_PALETTE constant — CFB has no equivalent
table to hand-author, and doesn't need one. Same null-on-unrecognized-
team decision as NFL, for the same reason (see get_cfb_tint_profile's own
docstring) even though real coverage makes that path rare in practice.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Part 1 — the 5 archetypes (4 signal-driven + the fallback)
# ---------------------------------------------------------------------------

ARCHETYPES = ("GOAL_LINE", "WORKHORSE", "TARGET_MAGNET", "MISMATCH")
FALLBACK_ARCHETYPE = "SATURDAY_POSTER"

FLOOR = 55.0

# {behavior_shelf_name: archetype it locks} -- same 3 behavior shelves
# curate_cfb_shelves.PLAYER_BEHAVIOR_SHELVES defines, duplicated here
# rather than imported (a trivial 3-entry table) to keep story_archetype.py
# free of any dependency on cfb/api/ -- the reverse direction (api code
# depending on this module in a later pipeline-wiring phase) is the one
# that's actually expected to happen, matching NFL's own story_archetype.py
# -> curate_home_shelves.py wiring direction.
_BEHAVIOR_SHELF_ARCHETYPE = {
    "goal_line_favorites": "GOAL_LINE",
    "workhorses": "WORKHORSE",
    "target_magnets": "TARGET_MAGNET",
}

# Priority order when a row is (unusually) on more than one behavior
# shelf at once -- same order as curate_cfb_shelves.PLAYER_BEHAVIOR_SHELVES
# / CFB_SHELF_ORDER's own walk order.
_BEHAVIOR_SHELF_PRIORITY = ("goal_line_favorites", "workhorses", "target_magnets")

# Same real conference-shelf spelling as curate_cfb_shelves.
# CONFERENCE_TD_WATCH_SHELVES, duplicated here for the same cross-module
# reason as _BEHAVIOR_SHELF_ARCHETYPE above.
_CONFERENCE_TD_WATCH_SHELVES = {
    "sec_td_watch": "SEC",
    "big_ten_td_watch": "Big Ten",
    "big12_td_watch": "Big 12",
    "acc_td_watch": "ACC",
}


def _real(value) -> float | None:
    """None for missing/NaN, the real float value otherwise -- handles a
    plain dict or pandas Series value transparently, same convention as
    every other honest-None helper in this codebase (cfb/redzone.py's
    _int_or_none/_str_or_none, nfl/story_archetype.py's own _real)."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN != NaN


def _goal_line_eligible(row: dict) -> bool:
    """td_opportunity_gated is an explicit boolean (score_td_opportunity_cfb
    forces it to exactly neutral-50 when the pillar-wide thin-sample gate
    trips) -- a gated row is never eligible regardless of its (fake-
    neutral) score."""
    return row.get("td_opportunity_gated") is not True


def _workhorse_eligible(row: dict) -> bool:
    """role_momentum_cfb has no explicit _gated boolean the way td_
    opportunity does -- completeness == 0 is the same honest proxy assign_
    cfb_shelves already uses for the Workhorses shelf itself (only
    possible when both trend inputs were NaN)."""
    return (_real(row.get("role_momentum_completeness")) or 0) > 0


def _target_magnet_eligible(row: dict) -> bool:
    """target_magnets_gated mirrors td_opportunity_gated exactly (score_
    target_magnets_cfb's own thin-sample gate) -- same explicit-boolean
    shape, same treatment."""
    return row.get("target_magnets_gated") is not True


def _mismatch_eligible(row: dict) -> bool:
    """defensive_matchup_vulnerability has neither an explicit _gated
    boolean (unlike td_opportunity/target_magnets) nor a discrete 0-or-100
    completeness (unlike role_momentum) -- score_defensive_matchup_cfb's
    own defensive_matchup_completeness is a continuous mean-of-4-inputs
    value, so completeness > 0 only excludes the fully-degenerate case
    (every recency-weighted input AND the conversion-rate input were all
    NaN at once). Anything short of that degenerate case is trusted to
    FLOOR itself -- a row backed mostly by fill_neutral's own 50.0
    sentinel on most of its 4 inputs will land close to 50 in the blended
    score anyway and simply fail the floor check, without needing a
    second, stricter gate invented here."""
    return (_real(row.get("defensive_matchup_completeness")) or 0) > 0


# {archetype: (governing_signal_column, eligibility_fn)} -- eligibility_fn
# already encodes that archetype's own real gate; iteration order IS the
# tie-break priority (see module docstring).
_ARCHETYPE_SPECS: dict[str, tuple[str, "callable"]] = {
    "GOAL_LINE": ("td_opportunity", _goal_line_eligible),
    "WORKHORSE": ("role_momentum", _workhorse_eligible),
    "TARGET_MAGNET": ("target_magnets", _target_magnet_eligible),
    "MISMATCH": ("defensive_matchup_vulnerability", _mismatch_eligible),
}


def _resolve_independent(row: dict) -> dict:
    """Population-shelf (Top 25 / conference / Tasty Six) resolution:
    whichever eligible signal has the highest real score wins, provided it
    clears FLOOR. See module docstring for the floor's own reasoning and
    the tie-break rule."""
    candidates = []  # (score, archetype, signal_col)
    for archetype, (signal_col, eligible_fn) in _ARCHETYPE_SPECS.items():
        if not eligible_fn(row):
            continue
        score = _real(row.get(signal_col))
        if score is None or score < FLOOR:
            continue
        candidates.append((score, archetype, signal_col))

    if not candidates:
        return {"archetype": FALLBACK_ARCHETYPE, "governing_signal": None, "confidence": None}

    best_score = max(c[0] for c in candidates)
    tied = [c for c in candidates if c[0] == best_score]
    if len(tied) > 1:
        order = list(_ARCHETYPE_SPECS.keys())
        tied.sort(key=lambda c: order.index(c[1]))

    score, archetype, signal_col = tied[0]
    return {"archetype": archetype, "governing_signal": signal_col, "confidence": round(score, 1)}


def resolve_cfb_archetype(row: dict, shelves: list | None = None) -> dict:
    """
    Resolves one player-week row to its story archetype.

    `row`: one real scored weekly row (dict or pandas Series — converted
    via .to_dict() transparently, same "either works" convention as
    nfl/story_archetype.py's own resolve_archetype).

    `shelves`: the row's own real shelf memberships this week
    (curate_cfb_shelves.add_shelf_convergence's `shelves` column — a
    list of the 8 real shelf names). Falls back to row["shelves"] when
    not passed explicitly, so a caller already holding an add_shelf_
    convergence-enriched row doesn't need to pass it twice; still
    overridable for direct/synthetic testing.

    Sport -> Shelf -> Story Archetype: on ANY behavior shelf (Goal-Line
    Favorites / Workhorses / Target Magnets — checked in that priority
    order, see _BEHAVIOR_SHELF_PRIORITY), the archetype is LOCKED to that
    shelf's own governing signal — no ambiguity, the shelf already tells
    you the story, exactly as spec'd. A row on more than one behavior
    shelf at once (unusual, not structurally prevented) resolves to
    whichever comes first in that priority order. A row on NO behavior
    shelf (Top 25 / a conference shelf / Tasty Six only, or no shelf at
    all) is resolved INDEPENDENTLY via _resolve_independent — same
    evidence-first principle NFL's own resolver uses, falling back to
    SATURDAY_POSTER when nothing clears FLOOR.

    Returns {"archetype": one of ARCHETYPES or FALLBACK_ARCHETYPE,
    "governing_signal": the winning signal's own column name (None for
    the fallback), "confidence": the winning signal's own real 0-100
    score rounded to 1 decimal (None for the fallback), "locked_by_shelf":
    the specific behavior shelf name that locked this resolution, or None
    when it was resolved independently}.
    """
    if hasattr(row, "to_dict"):
        row = row.to_dict()
    shelves = shelves if shelves is not None else (row.get("shelves") or [])

    locked_shelf = next((s for s in _BEHAVIOR_SHELF_PRIORITY if s in shelves), None)
    if locked_shelf is not None:
        archetype = _BEHAVIOR_SHELF_ARCHETYPE[locked_shelf]
        signal_col = _ARCHETYPE_SPECS[archetype][0]
        confidence = _real(row.get(signal_col))
        return {
            "archetype": archetype,
            "governing_signal": signal_col,
            "confidence": round(confidence, 1) if confidence is not None else None,
            "locked_by_shelf": locked_shelf,
        }

    resolved = _resolve_independent(row)
    resolved["locked_by_shelf"] = None
    return resolved


# ---------------------------------------------------------------------------
# Shelf-context motif — subordinate layer, never overrides the archetype
# resolved above (see module docstring's design-decision restatement).
# ---------------------------------------------------------------------------

# Documented ART-DIRECTION TENDENCIES for the next phase's prompt-writing,
# NOT literal per-card rules enforced anywhere in this module — resolve_
# cfb_shelf_context only ever returns the conference NAME; a later phase
# decides how (or whether) to lean on the tendency text below for any
# individual card.
CONFERENCE_ART_TENDENCIES = {
    "SEC": "Saturday night under the lights — warm sodium-vapor stadium glow, thick humid air, a packed dark bowl.",
    "Big Ten": "Colder and older — stone/brick monumental architecture, visible breath, a flatter grey daylight.",
    "Big 12": "Expansive sky — wide open plains horizon, long low-angle sunset light, more air than architecture.",
    "ACC": "East Coast/Southeast, no single unifying look — the most varied of the four; keep this one flexible rather than reaching for one setting.",
}


def resolve_cfb_shelf_context(shelves: list, team_id, ap_ranks: dict, team_conference: dict) -> list:
    """
    Subordinate background motifs for a player-week's real shelf
    memberships — never used to pick the archetype itself (resolve_cfb_
    archetype above), only layered behind it.

    `shelves`: same list resolve_cfb_archetype takes (add_shelf_
    convergence's `shelves` column, or a caller-supplied equivalent).
    `team_id`: the row's own team_id.
    `ap_ranks`: cfb.ids.fetch_ap_top25's own output ({team_id: {rank,
    school, conference}}) for the week being rendered.
    `team_conference`: cfb.ids.team_conference_map's own output
    ({team_id: conference}).

    Returns a list of motif dicts (possibly more than one — a player can
    legitimately be on Top 25 AND a conference shelf at once):
      * {"motif": "TOP25_RANK", "rank": <int>} when "top25_td_watch" is in
        `shelves` and a real rank is resolvable for `team_id` this week
        (translucent large ranking number + broadcast/stadium-spotlight
        atmosphere, per spec — the actual compositing is a later phase).
      * {"motif": "CONFERENCE_TENDENCY", "conference": <name>} for each
        real conference shelf `team_id` is actually on (never inferred
        from team_conference alone — only a shelf the row is REALLY
        assigned to produces a motif, so this stays in sync with assign_
        cfb_shelves' own eligibility logic rather than recomputing it).

    A row on no population shelf (behavior shelves only, or no shelf at
    all) returns [] — there's no shelf-context to layer in that case,
    not an error.
    """
    context = []

    if "top25_td_watch" in shelves:
        info = ap_ranks.get(team_id)
        rank = info.get("rank") if info else None
        if rank is not None:
            context.append({"motif": "TOP25_RANK", "rank": int(rank)})

    for shelf_name, conf_name in _CONFERENCE_TD_WATCH_SHELVES.items():
        if shelf_name in shelves:
            context.append({"motif": "CONFERENCE_TENDENCY", "conference": conf_name})

    return context


# ---------------------------------------------------------------------------
# Team identity (tint) — HSL-clamped, ported from NFL's own getNFLTintProfile()
# (tastypickems/src/lib/nfl-visual.ts). Kept in this module rather than a
# separate file since, unlike NFL's split, nothing here depends on any
# frontend asset/rendering code — it's a pure hex-in/hex-out function.
# ---------------------------------------------------------------------------

_MIN_LIGHTNESS = 0.22  # a near-black raw team color tinted with no floor reads as no tint at all against a dark card ground.
_MAX_LIGHTNESS = 0.74  # keeps a pale/near-white raw color from blowing out against the same dark ground.
_MAX_SATURATION = 0.70  # several real school colors sit near-maximum raw saturation; uncapped, it would fight an archetype's own already-tuned palette.


def _hex_to_hsl(hex_str: str) -> tuple[float, float, float]:
    h = hex_str.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    mx, mn = max(r, g, b), min(r, g, b)
    l = (mx + mn) / 2
    if mx == mn:
        return 0.0, 0.0, l
    d = mx - mn
    s = d / (2 - mx - mn) if l > 0.5 else d / (mx + mn)
    if mx == r:
        hh = (g - b) / d + (6 if g < b else 0)
    elif mx == g:
        hh = (b - r) / d + 2
    else:
        hh = (r - g) / d + 4
    return hh * 60, s, l


def _hue_to_rgb(p: float, q: float, t: float) -> float:
    if t < 0:
        t += 1
    if t > 1:
        t -= 1
    if t < 1 / 6:
        return p + (q - p) * 6 * t
    if t < 1 / 2:
        return q
    if t < 2 / 3:
        return p + (q - p) * (2 / 3 - t) * 6
    return p


def _hsl_to_hex(h: float, s: float, l: float) -> str:
    if s == 0:
        v = round(l * 255)
        return f"#{v:02x}{v:02x}{v:02x}"
    q = l * (1 + s) if l < 0.5 else l + s - l * s
    p = 2 * l - q
    hk = h / 360
    rgb = (_hue_to_rgb(p, q, hk + 1 / 3), _hue_to_rgb(p, q, hk), _hue_to_rgb(p, q, hk - 1 / 3))
    return "#" + "".join(f"{round(c * 255):02x}" for c in rgb)


def _tame_for_overlay(hex_str: str) -> str:
    h, s, l = _hex_to_hsl(hex_str)
    s = min(s, _MAX_SATURATION)
    l = min(max(l, _MIN_LIGHTNESS), _MAX_LIGHTNESS)
    return _hsl_to_hex(h, s, l)


def get_cfb_tint_profile(team_id, team_colors: dict) -> dict | None:
    """
    Resolves a team_id to a safety-clamped {"primary", "secondary"} tint
    pair, or None when unresolved (missing team_id, or team_id absent
    from `team_colors`) — the same "null, not a fabricated fallback pair"
    decision NFL's getNFLTintProfile() makes on an unrecognized team, for
    the same reason: team tint here is an ADDITIVE enhancement on top of
    an archetype's own already-finished art, not the card's only source
    of color, so "no additional tint" is an honest answer. In practice
    this path is far rarer for CFB than NFL — team_colors (cfb.ids.
    team_color_map) is fed by CFBD's real color/alternateColor fields,
    confirmed 100% populated across all 136 real FBS teams (see this
    module's docstring) — but the null path is kept for the same
    robustness reason (a bad/missing team_id, a non-FBS opponent, a
    future season where that could genuinely change).

    `team_colors`: cfb.ids.team_color_map(season)'s own output
    ({team_id: (color_hex, alternate_color_hex)}).
    """
    if team_id is None:
        return None
    pair = team_colors.get(int(team_id))
    if not pair:
        return None
    primary, secondary = pair
    return {"primary": _tame_for_overlay(primary), "secondary": _tame_for_overlay(secondary)}
