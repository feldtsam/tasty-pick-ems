"""
CFB Visual Resolver, Phase 1 — story_archetype.py.

The Story Archetype Resolver: maps one real scored player-week row, for
ONE specific shelf placement, to a fixed illustration archetype for that
card, plus a team-color tint pair. Mirrors nfl/story_archetype.py's role
and its split from the art/rendering layer (nfl-archetype-prompts.ts /
nfl-visual.ts) — this module never generates art, never picks an asset
file, never renders anything. Pure functions, no I/O, no DB writes, same
"takes scoring output as input" contract as cfb/scoring.py.

Phase 1 only (per spec): resolver structure. Art prompts, generation, and
pipeline wiring are later phases, exactly like NFL's own build order
(story_archetype.py shipped standalone before nfl-archetype-prompts.ts
and before ad3eb21 wired resolve_archetype() into NFL's live curation
pipeline).

REWORK (this task, supersedes the original 80b0f66 build): the priority
between shelf identity and per-player signal has REVERSED. Originally,
signal-driven story always won and shelf placement (Top 25 rank,
conference) was a subordinate background motif that never overrode it.
Now, shelf identity wins outright for any shelf that has its own
dedicated archetype — the conference or the Top 25 ranking IS the story
for that placement, full stop, and the player's own td_opportunity /
role_momentum / target_magnets / defensive_matchup_vulnerability scores
are never consulted for that placement. Independent signal-driven
resolution survives only as the path for a placement with NO dedicated
shelf identity (Tasty Six, or any future context without one).

THE PIPELINE — Sport → Shelf → Story Archetype → Team Identity:
  1. Shelf: caller already knows which shelf placement is being resolved.
  2. Story Archetype: resolve_cfb_archetype() below.
  3. Team Identity: get_cfb_tint_profile() below.
There is no separate "Context" step any more — see the removed-function
note below.

ARCHITECTURE QUESTION, ANSWERED: resolution is now PER-SHELF-PLACEMENT,
not per-player-week. The original build made it per-player-week
specifically because the old priority rule needed exactly one winner
across all of a player's shelves (signal-driven story always won,
regardless of shelf). That reasoning no longer holds: a player on BOTH
Goal-Line Favorites and SEC TD Watch now has TWO EQUALLY VALID,
mutually-exclusive locked stories (GOAL_LINE and SEC) with no principled
way to pick one as "more true" than the other — neither is a fallback or
a subordinate motif of the other any more. The only coherent answer is
that the same player's card genuinely looks different depending on WHICH
shelf it's being rendered on this week. So resolve_cfb_archetype() now
takes a single `shelf` (matching NFL's own resolve_archetype(shelf, row)
shape almost exactly, now that shelf identity has real narrative
primacy here too) rather than the row's full `shelves` list — call it
once per placement a caller wants to render, not once per player-week.
A caller that wants every archetype a player could show (e.g. to render
every shelf they landed on) calls this once per shelf in `row["shelves"]`
in a loop; nothing in this module does that aggregation for them.

REMOVED: resolve_cfb_shelf_context() and its TOP25_RANK / CONFERENCE_
TENDENCY motif types are gone entirely, superseded by the locked-
archetype behavior above — a shelf that used to contribute a subordinate
background motif now just IS the archetype outright. Also confirmed
this session: the Top 25 ranking NUMBER is explicitly not rendered on
any card — THE_RANKED's own art (next phase) uses abstract/decorative
numeral shapes, not a real dynamic number pulled from that week's AP
poll. Nothing in this module builds or references dynamic-number
rendering.

OUT OF SCOPE: GOING_NUCLEAR is not built or referenced anywhere in this
module — deferred, per this task's explicit instruction.

THE 10 ARCHETYPE VALUES: GOAL_LINE, WORKHORSE, TARGET_MAGNET, MISMATCH,
SEC, BIG_TEN, BIG_12, ACC, THE_RANKED (all 9 in the ARCHETYPES tuple
below) plus the fallback SATURDAY_POSTER (kept as its own FALLBACK_
ARCHETYPE constant, same convention as the original build and as NFL's
own GENERIC — a distinct "nothing won" value, not one more entry in the
same tuple as the real archetypes).

  * Behavioral shelves (goal_line_favorites / workhorses / target_
    magnets): UNCHANGED from the original build — locks to that shelf's
    own governing signal (GOAL_LINE / WORKHORSE / TARGET_MAGNET), and
    still reports that signal's own real score as `confidence` (there IS
    a real per-player number backing these three, it's just no longer in
    competition with anything — the shelf placement already guarantees
    it wins).
  * Identity shelves (sec_td_watch / big_ten_td_watch / big12_td_watch /
    acc_td_watch): NEW — locks to that conference's own dedicated
    archetype (SEC / BIG_TEN / BIG_12 / ACC), completely ignoring the
    player's own signal scores. `governing_signal` and `confidence` are
    both None here — deliberately, not an oversight: there is no real
    per-player number that produced this archetype (any player on the
    shelf gets the same archetype regardless of their own scores), so
    reporting a number would misrepresent what actually drove the
    resolution. Same honest-None principle nfl/story_archetype.py uses
    for GENERIC's own confidence.
  * Editorial shelf (top25_td_watch): NEW — same override logic, locks
    to THE_RANKED. Same None/None reporting shape as the identity
    shelves, same reasoning.
  * No dedicated shelf identity (shelf is None, "tasty_six", or any
    other value not in the 8 real shelves): resolved INDEPENDENTLY from
    signal — see _resolve_independent below. GOAL_LINE / WORKHORSE /
    TARGET_MAGNET are still reachable this way too (e.g. a Tasty-Six-only
    placement with no locked shelf backing it), but MISMATCH is ONLY
    ever reachable through this path — it has no shelf of its own on any
    path, a tradeoff named explicitly in Part 1 below.

PART 1 — THE OPEN ITEM FROM THE ORIGINAL BUILD, UNCHANGED BY THIS
REWORK: defensive_matchup_vulnerability still gets its own archetype,
MISMATCH, rather than being left out of archetype resolution entirely.
Recommendation, with the tradeoff named rather than picked silently (per
the original task's own instruction — this reasoning stood on its own
merits before this rework and still does):

  Reasoning FOR adding it: (a) it is a fully validated, live signal —
  same standing as the other 3 — and NFL's own story_archetype.py
  already treats the IDENTICAL underlying concept (defensive_matchup_
  vulnerability) as archetype-worthy via its own MISMATCH archetype, so
  this isn't a new narrative invented from nothing, it's a direct,
  low-risk precedent already proven out in the sibling sport. (b) The
  alternative (leaving it out) means one of CFB's 4 headline evidence
  pillars is never visually representable at all — a real,
  likely-to-resurface gap once someone asks why defensive_matchup_
  vulnerability never shows up in the art despite being scored, live,
  and load-bearing in the Universal TPE composite every shelf already
  ranks by.

  The tradeoff, named honestly, UPDATED for this rework (this got
  meaningfully worse under the new priority rule, worth re-flagging, not
  just carrying forward unchanged): under the original priority rule,
  every population-shelf placement was signal-driven, so Mismatch's "no
  shelf of its own" gap was the SAME KIND of gap Goal-Line/Workhorse/
  Target Magnet already had off their own shelves. Under THIS rule, Top
  25 and the 4 conference shelves are now ALL locked to their own
  dedicated identity archetypes — meaning Mismatch is now the ONLY one
  of the 5 real archetypes that can NEVER be reached from ANY of the 8
  real shelves, on any placement, ever. It is reachable only from a
  shelf-less context (Tasty Six today; nothing else is scoped). That is
  a materially narrower path to the screen than this recommendation
  originally accounted for — flagging it again here rather than treating
  the original Part 1 answer as still fully accurate as-is.

  Ships as archetype key "MISMATCH", governing signal defensive_matchup_
  vulnerability, gate = defensive_matchup_completeness > 0 (see
  _mismatch_eligible's own comment for why completeness > 0, not a
  boolean, is the right gate shape for this specific signal).

THE FLOOR — 55.0, UNCHANGED by this rework (still only relevant to the
independent, no-locked-shelf path). Same underlying reasoning NFL's
story_archetype.py used (a fixed distance above the KNOWN neutral-fill
sentinel, not a number tuned to any one week's own sparsity), CONFIRMED
transferable: cfb/normalize.py's fill_neutral has the exact same real
50.0 default CFB-side (checked directly, not assumed) — every CFB pillar
here is, like NFL's, a percentile-ranked 0-100 score that degrades to
exactly 50.0 when a real reference population is missing. A floor set as
a fixed distance above that known sentinel keeps working regardless of
how populated the real reference distribution is at any given point in
the season, for the same reason it does on the NFL side.

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

TIE-BREAKING — UNCHANGED, and now relevant ONLY to the independent,
no-locked-shelf path (a locked shelf has nothing to tie against; it
always wins outright). All 4 candidate signals are independently-scaled
percentiles (each ranked against its own real reference population), so
a literal tie at the same rounded score is coincidental, not a
meaningful signal collision — an arbitrary but DETERMINISTIC rule is all
that's needed. Ties go to whichever candidate is earliest in
_ARCHETYPE_SPECS' own fixed order: GOAL_LINE, WORKHORSE, TARGET_MAGNET,
MISMATCH — MISMATCH last since, per Part 1 above, it's the one archetype
with no shelf of its own on any path. Not NFL's own tie-break rule (which
favors the calling shelf's own "primary signal family" — that concept
doesn't exist here). A fresh, CFB-appropriate tie-break, not a blind
copy.

TEAM IDENTITY (tint) — UNCHANGED by this rework. Reuses NFL's exact getNFLTintProfile() mechanism
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
# The 10 archetype values (9 real + the fallback), and the shelf-lock
# tables that give 8 of the 9 a dedicated shelf. See module docstring for
# the full reasoning behind each category.
# ---------------------------------------------------------------------------

ARCHETYPES = (
    "GOAL_LINE", "WORKHORSE", "TARGET_MAGNET", "MISMATCH",
    "SEC", "BIG_TEN", "BIG_12", "ACC", "THE_RANKED",
)
FALLBACK_ARCHETYPE = "SATURDAY_POSTER"

FLOOR = 55.0

# {behavior_shelf_name: archetype it locks} -- same 3 behavior shelves
# curate_cfb_shelves.PLAYER_BEHAVIOR_SHELVES defines, duplicated here
# rather than imported (a trivial table) to keep story_archetype.py free
# of any dependency on cfb/api/ -- the reverse direction (api code
# depending on this module in a later pipeline-wiring phase) is the one
# that's actually expected to happen, matching NFL's own story_archetype.py
# -> curate_home_shelves.py wiring direction.
_BEHAVIOR_SHELF_ARCHETYPE = {
    "goal_line_favorites": "GOAL_LINE",
    "workhorses": "WORKHORSE",
    "target_magnets": "TARGET_MAGNET",
}

# {conference_shelf_name: archetype it locks} -- same real shelf spelling
# as curate_cfb_shelves.CONFERENCE_TD_WATCH_SHELVES, but mapped to this
# module's own archetype keys rather than the human-readable conference
# name (that display name has no consumer left in this module now that
# resolve_cfb_shelf_context is gone -- see module docstring).
_CONFERENCE_SHELF_ARCHETYPE = {
    "sec_td_watch": "SEC",
    "big_ten_td_watch": "BIG_TEN",
    "big12_td_watch": "BIG_12",
    "acc_td_watch": "ACC",
}

# {editorial_shelf_name: archetype it locks} -- just the one shelf today;
# kept as its own table (rather than folded into _CONFERENCE_SHELF_
# ARCHETYPE) since it's a conceptually distinct category (editorial
# ranking, not team identity) even though both lock the same way.
_EDITORIAL_SHELF_ARCHETYPE = {
    "top25_td_watch": "THE_RANKED",
}

# Every real shelf that locks an archetype by identity alone, regardless
# of category -- union of the three tables above. Under this rework all
# three categories take priority over independent signal resolution
# equally; only the REPORTED governing_signal/confidence shape differs
# (behavior locks still report the player's own real score; identity/
# editorial locks report None/None -- see resolve_cfb_archetype's own
# docstring for why).
_LOCKED_SHELF_ARCHETYPE = {
    **_BEHAVIOR_SHELF_ARCHETYPE,
    **_CONFERENCE_SHELF_ARCHETYPE,
    **_EDITORIAL_SHELF_ARCHETYPE,
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


def resolve_cfb_archetype(shelf: str | None, row: dict) -> dict:
    """
    Resolves ONE shelf placement of one player-week row to its story
    archetype. Call once per placement a caller wants to render — a
    player on multiple shelves this week gets one call per shelf, and can
    genuinely get a different archetype back each time (see module
    docstring's "architecture question" section for why this is
    per-placement rather than per-player-week).

    `shelf`: the specific shelf this card is being rendered for — one of
    the 8 real shelf names (curate_cfb_shelves.CFB_SHELF_ORDER), or
    anything else (None, "tasty_six", an unrecognized string) for a
    placement with no dedicated shelf identity.

    `row`: one real scored weekly row (dict or pandas Series — converted
    via .to_dict() transparently, same "either works" convention as
    nfl/story_archetype.py's own resolve_archetype).

    Sport -> Shelf -> Story Archetype:
      * `shelf` is a behavior shelf (goal_line_favorites / workhorses /
        target_magnets): locks to that shelf's own governing signal.
        `governing_signal`/`confidence` report the player's own real
        score for that signal — there IS a real number behind these
        three, it's just no longer competing with anything.
      * `shelf` is an identity shelf (a conference *_td_watch) or the
        editorial shelf (top25_td_watch): locks to that shelf's own
        dedicated archetype (SEC/BIG_TEN/BIG_12/ACC/THE_RANKED),
        completely ignoring the player's own signal scores.
        `governing_signal`/`confidence` are both None — deliberately:
        no per-player number actually produced this archetype, so
        reporting one would misrepresent what happened.
      * `shelf` is anything else (no dedicated shelf identity): resolved
        INDEPENDENTLY via _resolve_independent — whichever of the 4
        signals is highest wins, provided it clears FLOOR, falling back
        to SATURDAY_POSTER when nothing does.

    Returns {"archetype": one of ARCHETYPES or FALLBACK_ARCHETYPE,
    "governing_signal": the winning signal's own column name, or None
    (fallback, or an identity/editorial lock), "confidence": the winning
    signal's own real 0-100 score rounded to 1 decimal, or None (same two
    cases), "locked_by_shelf": the specific shelf name that locked this
    resolution (behavior OR identity OR editorial), or None when it was
    resolved independently}.
    """
    if hasattr(row, "to_dict"):
        row = row.to_dict()

    archetype = _LOCKED_SHELF_ARCHETYPE.get(shelf)
    if archetype is not None:
        if shelf in _BEHAVIOR_SHELF_ARCHETYPE:
            signal_col = _ARCHETYPE_SPECS[archetype][0]
            confidence = _real(row.get(signal_col))
            return {
                "archetype": archetype,
                "governing_signal": signal_col,
                "confidence": round(confidence, 1) if confidence is not None else None,
                "locked_by_shelf": shelf,
            }
        # Identity (conference) and editorial (Top 25) locks: the shelf
        # IS the story -- no per-player signal drives it at all, so
        # there's honestly no governing_signal/confidence to report
        # (see this function's own docstring).
        return {
            "archetype": archetype,
            "governing_signal": None,
            "confidence": None,
            "locked_by_shelf": shelf,
        }

    resolved = _resolve_independent(row)
    resolved["locked_by_shelf"] = None
    return resolved


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
