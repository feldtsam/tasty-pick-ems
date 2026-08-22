"""
NFL Intelligence — story lifecycle (Detected -> Strengthening -> Active ->
Weakening -> Archived), per the approved design (see the conversation this
was investigated and proposed in). Deliberately its OWN module, not folded
into intelligence_schema.py or any one family file: this is cross-family
orchestration logic that reads/updates persisted history, a genuinely
different concern from build_story()'s job of shaping ONE story object.

DELIBERATELY DOES NOT TOUCH intelligence_schema.py OR ANY STORY DICT a
family's build_*_stories() function returns. build_story()'s own required-
field check (STORY_FIELDS) raises on any unexpected key — adding a
"lifecycle_state" field directly onto a story object would mean either
extending STORY_FIELDS (a real schema change touching all four families,
including Market Intelligence, which explicitly does NOT get lifecycle —
see below) or bypassing build_story's own enforcement. Neither is
necessary: the approved design already puts lifecycle_state on its OWN
table (nfl_intelligence_story_history), joined to a story by identity, not
merged into the story object. Keeping this module entirely separate means
zero risk to the four families' own already-tested build_story() call
sites and existing test suites — confirmed unaffected, not just assumed
(see the conversation this was validated in).

MARKET INTELLIGENCE IS DELIBERATELY EXCLUDED, per the approved design —
lifecycle is deferred until real multi-book price-history data exists to
calibrate real thresholds against (V1 is snapshot-only; there is no real
week-over-week movement to compare yet — see market_intelligence.py's own
module docstring). Nothing in this module is wired to it, and nothing
here should be — a caller for Market Intelligence stories simply never
calls apply_lifecycle() at all; there's no "lifecycle_state: null" branch
to build here, because Market Intelligence's stories never enter this
module's world in the first place.

IDENTITY KEY, approved: (intelligence_family, entity_key, primary_signal_
name) — see entity_key_for()'s own docstring for why entity_key alone
isn't enough (the real Coaching Trends collision: three simultaneous real
stories per team share one entity, disambiguated only by primary_signal.
name).

THRESHOLDS ARE PROVISIONAL STARTING HYPOTHESES, same treatment every
other "hypothesis to tune" constant in this codebase gets (scoring.CONFIG,
shelves.py's completeness_threshold, curate_home_shelves.STICKINESS_
MARGIN) — grounded in real historical week-over-week delta distributions
(see FAMILY_SIGNAL_THRESHOLDS below for the real numbers and how each was
derived), not invented, but explicitly NOT final: real-world tuning once
live multi-week Intelligence data actually accumulates is expected, not
optional.
"""

import math

# ---------------------------------------------------------------------------
# Real per-family (per-signal) thresholds — the real 75th-percentile
# absolute week-over-week delta of each family's own real historical
# trend_strength-equivalent value, pulled directly from real data (not
# assumed uniform across families — investigated first, see the
# conversation this was derived in):
#
#   role_momentum (Role Changes):                  real p75 |delta| =  8.2
#     — player_redzone_weekly.csv, RB/WR/TE, all real historical seasons,
#       n=5,876 real week-over-week deltas.
#   defensive_matchup_vulnerability (Defensive Trends): real p75 |delta| = 21.7
#     — same real backfill, (defteam, position_group) grain, n=3,870.
#   redzone_run_tendency (Coaching Trends #1):      real p75 |delta| = 14.1
#   fourth_down_aggressiveness (Coaching Trends #2): real p75 |delta| = 14.3
#   pace_score (Coaching Trends #3):                real p75 |delta| = 15.4
#     — real 2022/2024/2025 play-by-play (nfl_data_py.import_pbp_data),
#       each of team_tendencies.py's own three real aggregation/scoring
#       functions run against it, n≈1,300-1,324 real week-over-week
#       deltas each.
#
# role_momentum's own real distribution (mean 6.9, p75 8.2) is
# structurally much less noisy week-to-week than defensive_matchup_
# vulnerability's (mean 15.5, p75 21.7) — confirmed directly, not
# assumed: a single SHARED raw-point threshold across families would
# have been close to meaningless for one of them (a "20-point move" is a
# rare, real signal for role_momentum but happens on over a quarter of
# real defensive_matchup_vulnerability weeks purely from normal noise).
# This is exactly why thresholds are keyed per (family, primary_signal_
# name) below, not one shared constant.
#
# Coaching Trends' three real signals landed close to each other (14.1/
# 14.3/15.4) despite being calibrated fully independently — a real,
# incidental finding (all three are built the same structural way inside
# team_tendencies.py: a weekly percentile, rolled via the same add_
# rolling_windows trend mechanism), not something assumed or forced.
# Kept as three separate real numbers anyway, not averaged into one,
# since the approved identity key already tracks these three completely
# independently — there's no reason to blur real, independently-derived
# calibration data back together for convenience.
# ---------------------------------------------------------------------------
FAMILY_SIGNAL_THRESHOLDS = {
    "role_momentum": 8.2,
    "defensive_matchup_vulnerability": 21.7,
    "redzone_run_tendency": 14.1,
    "fourth_down_aggressiveness": 14.3,
    "pace_score": 15.4,
}

# How many of the most recent real persisted readings form the baseline
# a current reading is compared against — approved: "the average of the
# last 2-3 real persisted readings". 3 chosen as the real cap (not a
# fixed 2 or 3 every time): a story with only 1-2 real prior readings
# still gets a real, honest baseline from however many it actually has
# (see _compute_lifecycle_state's own recent_values handling) — this
# constant is the CEILING, not a hard requirement.
BASELINE_WINDOW = 3

# Approved: "2 consecutive real appearances" confirms a directional move;
# "3 consecutive real appearances" graduates Detected -> Active;
# "2 consecutive missed real appearances" archives.
CONFIRM_STREAK = 2
ACTIVE_APPEARANCE_COUNT = 3
ARCHIVE_MISS_COUNT = 2


def _safe_float(value):
    """
    float(value), but a non-convertible value (None, a string, etc. —
    the same real malformation intelligence_sanity.sanity_check_story
    would flag) becomes NaN rather than raising. Used only for history-
    row audit fields (see apply_lifecycle) — this module always wants
    to WRITE the real, honest bad value (or NaN standing in for "not a
    real number at all") for the audit trail, never crash while trying
    to record that something was wrong.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def entity_key_for(entity: dict) -> str:
    """
    Normalizes a real story's entity dict into ONE stable string,
    per the approved identity key (intelligence_family, entity_key,
    primary_signal_name). player-entities (Market Intelligence -- never
    actually reaches this module, see module docstring -- and Role
    Changes) key on player_id; defense-entities (Defensive Trends) key
    on "{team}:{position_group}"; team-entities (Coaching Trends) key on
    team alone.

    primary_signal_name is NOT part of this function's own output — it's
    the caller's job to combine entity_key_for(story["entity"]) with
    story["primary_signal"]["name"] into the real full identity tuple.
    Kept separate because entity normalization and signal-name lookup
    are genuinely different concerns (an entity dict is looked up the
    same way regardless of which signal produced this particular story),
    not because of any technical constraint.
    """
    entity_type = entity["type"]
    if entity_type == "player":
        return entity["player_id"]
    if entity_type == "defense":
        return f"{entity['team']}:{entity['position_group']}"
    if entity_type == "team":
        return entity["team"]
    raise ValueError(f"entity_key_for: unrecognized entity type {entity_type!r} in {entity!r}")


def _compute_lifecycle_state(
    current_value: float, recent_values: list, prior_state, prior_pending_direction, prior_streak_count: int,
    prior_appearance_count: int, threshold: float,
) -> dict:
    """
    Pure state-transition function for an identity that DID appear in
    this week's real detection output — isolated from all I/O, same
    "directly unit-testable" discipline as _compute_sticky_assignment.

    recent_values: up to the last BASELINE_WINDOW real prior persisted
    trend_strength readings for this identity (any real count from 0 to
    BASELINE_WINDOW — a story with only 1 prior real reading still gets
    a real, honest baseline from that one value, not padded or assumed).
    Empty list -- genuinely no prior history at all -- means Detected,
    full stop; no comparison is attempted.

    REAL BUG FOUND AND FIXED DURING THIS TASK'S OWN VALIDATION:
    prior_pending_direction is a SEPARATE, hidden field from the
    VISIBLE lifecycle_state — exactly the same "pending shelf, distinct
    from home_shelf" shape stickiness already uses, for the identical
    reason. The first version of this function compared the new
    direction against the prior VISIBLE state instead — but a direction
    that's only 1-of-2 confirmations in doesn't change the visible
    state yet (it still shows Active/Detected, per the appearance-count
    fallback below), so comparing against prior_state made the streak
    reset to 1 every single week instead of ever reaching CONFIRM_
    STREAK=2 — confirmed directly: a real synthetic 2-week Strengthening
    sequence silently never fired under the old comparison. Comparing
    against the hidden pending_direction instead (never overwritten by
    the appearance-count fallback) fixes it.

    DIRECTIONAL CONFIRMATION OVERRIDES APPEARANCE-COUNT PERSISTENCE:
    once a real 2-consecutive-appearance directional move is confirmed
    (Strengthening or Weakening), that's the visible state, regardless
    of how many total appearances this identity has. Absent a confirmed
    directional move, the visible state falls back to pure appearance-
    count persistence: Detected until ACTIVE_APPEARANCE_COUNT real
    appearances, Active from then on. This means a story that WAS
    Strengthening and then genuinely levels off correctly settles into
    Active (not stuck labeled "Strengthening" forever, and not reverting
    to "Detected" — appearance_count is already well past the Active
    threshold by the time a directional move has had time to confirm at
    all).

    SANITY-FAILED READINGS (current_value is NaN/inf — the exact thing
    intelligence_sanity.sanity_check_story's finiteness check catches):
    per the approved design, this identity still gets a real appearance
    counted (appearance_count increments below) — a sanity-failed story
    is a real detection that happened, just one whose number can't be
    trusted, not a no-show. But an undefined number can't support a
    directional claim (no "Strengthening"/"Weakening" this week — there
    is nothing there to compare), so direction is forced to None here
    explicitly, rather than relying on the fact that a NaN comparison
    happens to evaluate False in Python either side of the threshold
    check below (true, but an accident of IEEE-754 semantics this
    codebase's own house style doesn't rely on unstated elsewhere,  and
    a future reader/maintainer could plausibly "fix" as a bug). The
    other half of this same protection — keeping the bad value OUT of
    recent_values so it can never poison a FUTURE week's baseline — is
    the caller's (apply_lifecycle's) job, since this function doesn't
    own that list.

    Returns {"lifecycle_state": str, "pending_direction": str|None,
    "streak_count": int, "appearance_count": int, "miss_count": 0} —
    miss_count always resets to 0 here; this function is only ever
    called for a real appearance.
    """
    if not recent_values:
        return {"lifecycle_state": "Detected", "pending_direction": None, "streak_count": 0, "appearance_count": 1, "miss_count": 0}

    appearance_count = prior_appearance_count + 1

    if not math.isfinite(current_value):
        direction = None
    else:
        baseline = sum(recent_values) / len(recent_values)
        delta = current_value - baseline
        if delta >= threshold:
            direction = "Strengthening"
        elif delta <= -threshold:
            direction = "Weakening"
        else:
            direction = None

    if direction is not None:
        streak_count = (prior_streak_count + 1) if prior_pending_direction == direction else 1
    else:
        streak_count = 0  # broken this week — same "reset, don't partial-credit" rule stickiness uses

    if direction is not None and streak_count >= CONFIRM_STREAK:
        state = direction
    else:
        state = "Active" if appearance_count >= ACTIVE_APPEARANCE_COUNT else "Detected"

    return {
        "lifecycle_state": state, "pending_direction": direction, "streak_count": streak_count,
        "appearance_count": appearance_count, "miss_count": 0,
    }


def _compute_lifecycle_state_for_miss(
    prior_state, prior_pending_direction, prior_streak_count: int, prior_miss_count: int, prior_appearance_count: int,
):
    """
    Pure function for an identity with real prior history that did NOT
    appear in this week's real detection output — a real gap (a bye
    week for a player-entity; a real data/qualification gap for a
    defense/team-entity). "Pause, don't reset", approved, same principle
    already built and tested for stickiness: a single miss carries the
    prior state/pending_direction/streak/appearance_count forward
    UNCHANGED except miss_count incrementing; only ARCHIVE_MISS_COUNT
    consecutive real misses actually archives.

    Returns None when prior_state is already "Archived" — the caller's
    signal to skip this identity entirely (no new history row at all),
    satisfying the approved "stop re-writing 'still archived' every
    subsequent run" bounded exception. This is the ONLY place that
    exception is enforced — by construction, not a special case bolted
    on elsewhere.
    """
    if prior_state == "Archived":
        return None

    new_miss_count = prior_miss_count + 1
    if new_miss_count >= ARCHIVE_MISS_COUNT:
        return {
            "lifecycle_state": "Archived", "pending_direction": None, "streak_count": 0,
            "appearance_count": 0, "miss_count": new_miss_count,
        }

    return {
        "lifecycle_state": prior_state, "pending_direction": prior_pending_direction, "streak_count": prior_streak_count,
        "appearance_count": prior_appearance_count, "miss_count": new_miss_count,
    }


def apply_lifecycle(stories: list, history: dict, family: str, season: int, week: int) -> dict:
    """
    Orchestrates one real curation run's worth of lifecycle updates for
    ONE lifecycle-eligible family (never Market Intelligence — see
    module docstring). Pure with respect to any real network/database
    I/O — `history` is a caller-maintained in-memory dict (this task is
    explicitly dry-run only; no real nfl_intelligence_story_history
    read/write endpoint exists yet — see the write-connection task this
    is deliberately deferred to).

    stories: this week's REAL story dicts from the family's own build_*_
    stories() function, completely unmodified — this function never
    mutates or reads back into them; lifecycle stays a fully separate
    concern, joined by identity only.

    history: {(family, entity_key, signal_name): {"lifecycle_state":...,
    "pending_direction":..., "streak_count":..., "appearance_count":...,
    "miss_count":..., "recent_values": [...]}} — pending_direction is a
    hidden field, separate from the visible lifecycle_state, tracking
    which direction (if any) is currently accumulating toward a 2-
    consecutive-week confirmation (see _compute_lifecycle_state's own
    docstring for why this has to be separate). The real persisted
    state as of the END of
    the PRIOR real run this family actually executed (already walked
    back through any real gap by the caller, same bounded-lookback
    pattern already built for stickiness — build_prior_state_with_
    walkback's own bulk, week-scoped, early-stopping design applies here
    unchanged in PRINCIPLE, though this task validates the state machine
    itself locally/in-memory across real sequential historical weeks,
    not against a live read endpoint that doesn't exist yet).

    Returns {"history_rows": [...], "updated_history": {...}}.
    history_rows is exactly what WOULD be written to nfl_intelligence_
    story_history this run (shaped, ready, NOT written anywhere) —
    covers every story present this week PLUS any identity that just
    transitioned into Archived via a miss (a real row with no fresh
    content, since nothing was detected for it this week — trend_
    strength/primary_signal_value are None for those). Identities
    already Archived in a PRIOR run are silently skipped entirely, per
    _compute_lifecycle_state_for_miss's own contract.
    """
    updated_history = dict(history)
    history_rows = []
    seen_identities = set()

    for story in stories:
        signal_name = story["primary_signal"]["name"]
        identity = (family, entity_key_for(story["entity"]), signal_name)
        seen_identities.add(identity)

        prior = history.get(identity)
        threshold = FAMILY_SIGNAL_THRESHOLDS[signal_name]
        try:
            current_value = float(story["trend_strength"])
        except (TypeError, ValueError):
            current_value = float("nan")  # same "unusable, not absent" treatment as a real NaN below

        if prior is None:
            result = _compute_lifecycle_state(current_value, [], None, None, 0, 0, threshold)
        else:
            result = _compute_lifecycle_state(
                current_value, prior["recent_values"], prior["lifecycle_state"], prior["pending_direction"],
                prior["streak_count"], prior["appearance_count"], threshold,
            )

        # A sanity-failed (NaN/inf) current_value is deliberately NOT
        # appended here — the one real fix this task's NaN-in-lifecycle
        # investigation required. _compute_lifecycle_state already keeps
        # a bad THIS-week reading from producing a false directional
        # claim (see its own docstring); this is the other half: keeping
        # it out of the rolling window so it can never corrupt a FUTURE
        # week's baseline/delta either. recent_values simply carries
        # forward unchanged this week — mechanically identical to "no
        # new information," not a fabricated substitute value.
        prior_recent_values = prior["recent_values"] if prior else []
        if math.isfinite(current_value):
            recent_values = (prior_recent_values + [current_value])[-BASELINE_WINDOW:]
        else:
            recent_values = prior_recent_values

        updated_history[identity] = {**result, "recent_values": recent_values}
        history_rows.append({
            "intelligence_family": family,
            "entity_key": identity[1],
            "primary_signal_name": signal_name,
            "season": season,
            "week": week,
            "trend_strength": current_value,
            "primary_signal_value": _safe_float(story["primary_signal"].get("value")),
            "lifecycle_state": result["lifecycle_state"],
            "streak_count": result["streak_count"],
            "miss_count": result["miss_count"],
        })

    # Real misses: any identity with prior real history that simply
    # didn't appear in this week's real story batch at all.
    for identity, prior in history.items():
        if identity in seen_identities or identity[0] != family:
            continue
        result = _compute_lifecycle_state_for_miss(
            prior["lifecycle_state"], prior["pending_direction"], prior["streak_count"], prior["miss_count"], prior["appearance_count"],
        )
        if result is None:
            continue  # already Archived in a prior run -- no new row, per the approved bounded exception
        updated_history[identity] = {**result, "recent_values": prior["recent_values"]}
        history_rows.append({
            "intelligence_family": family,
            "entity_key": identity[1],
            "primary_signal_name": identity[2],
            "season": season,
            "week": week,
            "trend_strength": None,
            "primary_signal_value": None,
            "lifecycle_state": result["lifecycle_state"],
            "streak_count": result["streak_count"],
            "miss_count": result["miss_count"],
        })

    return {"history_rows": history_rows, "updated_history": updated_history}
