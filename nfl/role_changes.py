"""
Role Changes — the second of four NFL Intelligence families (Market
Intelligence is built; Defensive Trends and Coaching Trends are not).
Reuses intelligence_schema.build_story, same shared contract Market
Intelligence established.

CORE QUESTION: "Whose offensive role is materially changing?" — who is
picking up real touches/snaps/depth-chart standing, and why (a visible
usage trend, a vacated opportunity from an injury ahead of them on the
chart, or both).

DATA CHECK BEFORE BUILDING (real, not assumed): scoring.score_role_
momentum needed ZERO changes — role_trend, external_opportunity, role_
momentum, role_momentum_completeness, and their three intermediate
percentile components (touch_share_trend_pct_role, snap_share_trend_pct_
role, depth_chart_movement_pct) are already exactly what this module
needs, unchanged. The one real gap was one layer down, in the raw data
redzone.add_injury_context attaches: ahead_injury_statuses (existing)
tells you SOMEONE ahead is hurt and how badly, but not WHO — insufficient
for a causal "his opportunity opened up because X got hurt" story. Fixed
with one small, purely additive extension to add_injury_context
(ahead_injured_teammates — the identical population as ahead_injury_
statuses, just carrying player_id/player_name/status instead of just
status), verified directly against real data (confirmed against Rachaad
White/Parker Washington's real 2025 Week 10 rows: Bucky Irving and Brian
Thomas Jr. respectively, both real, correct) and against the full
7,934-row historical backfill (0 regression cells on any pre-existing
column).

V1 SCOPE, DELIBERATELY: EXPANDING ROLES ONLY, NOT BIDIRECTIONAL.
role_momentum is structurally an "opportunity" metric, not a "usage
change" metric — every one of its inputs (touch_share_trend_pct_role,
snap_share_trend_pct_role, depth_chart_movement_pct, external_
opportunity) is built to surface players trending UP; a declining share
just percentile-ranks near the bottom, indistinguishable from a flat
one — role_momentum has no mechanism that specifically flags "this
player's role is shrinking" as its own signal (confirmed by reading
score_role_momentum directly, not assumed). A true bidirectional Role
Changes family (including players actively LOSING role) is a genuine
product gap, not built here — it would need new detection logic in
scoring.py, not just new story text, and is flagged as Valuable Later
rather than approximated.

RB/WR/TE ONLY, EXPLICITLY (not implicitly, via "no restriction") — same
lesson already learned once in shelves.py (Josh Allen/Bo Nix leaking onto
Red Zone Trends). depth_rank itself is undefined for QB by construction
(redzone._skill_position_depth_chart / _new_schema_depth_chart both
filter to RB/WR/TE before depth_rank ever exists), so a QB row would
carry NaN depth_rank and an always-empty ahead_injury_statuses — nothing
Role Changes could honestly build a role-change story from anyway.

NOT gated by the +300 ATTD floor (Intelligence, not Picks) and NOT split
into three position-specific families — investigated before deciding
(the RB/WR/TE Trends Picks-shelf structure was NOT assumed to carry
over): Intelligence is a narrative discovery feed, not a set of separate
ranked leaderboards competing for a fixed number of slots, so there is no
structural reason for RB/WR/TE to each need their own gated pool the way
three separate 6-card shelves do. One function spans all three positions,
same "spans the whole eligible pool" shape as Red Zone Trends (not RB/WR/
TE Trends' three-way split). position_group is still visible on entity
and shapes headline language.

MATERIALITY THRESHOLD, validated against real data not guessed:
role_momentum >= 50 (this module's most literal "not neutral" reading)
turns out to be nearly a no-op filter in the early season — checked
directly against real 2025 Week 2 RB/WR/TE rows, where 135 of 141
(95.7%) already clear 50. This is the SAME early-season phenomenon
shelves.py's own Week 2 finding surfaced from a different angle: with
almost no trailing history yet, role_trend's own components mask to NaN
and fall through to neutral-50, and role_momentum's hi/lo combination
mechanic (hi = max(role_trend, external_opportunity)) means a role_trend
sitting right at that neutral baseline alone is often enough to clear a
threshold of exactly 50. role_momentum_threshold=60 was checked the same
way and is meaningfully selective at every real week tested (Week 2: 7/
141 [5.0%], Week 10: 12/112 [10.7%], Week 17: 35/137 [25.5%]) — still a
starting hypothesis (same "tune later" treatment every other constant in
this codebase gets), just one confirmed against real data rather than
picked blind.

SAMPLE-SIZE / COMPLETENESS HONESTY, connecting directly to the n=1-game
overconfidence fix: sample_size here is games_played (the SAME games-
played count scoring._trend_delta already uses to mask forced-zero
deltas — recomputed identically here, not reinvented) rather than
market_value's n_books, since it's this family's own real "how much do
we actually know" count. Investigated whether completeness should
combine two independent axes the way Market Intelligence's geometric
mean does (market_value_completeness x book_coverage) — decided NOT to:
role_momentum_completeness ALREADY substantially captures games_played's
effect (role_trend's own trend-delta inputs mask to NaN, and therefore
route through completeness's own fallback tracking, at exactly the same
games_played <= window boundary sample_size reports here), so a second
geometric-mean axis here would mostly double-count the same underlying
signal rather than add a genuinely independent one — unlike n_books,
which measured something market_value_completeness had no way to see at
all. completeness = role_momentum_completeness directly; confidence
equals it (no second independent axis exists here either — same "both
fields always exist, not always different numbers" allowance the shared
schema's own docstring documents, Market Intelligence's V1 already uses
this same equality for the same reason). sample_size (games_played)
still independently drives HEADLINE hedging below, same as Market
Intelligence's n_books did — a real, low games_played count changes the
language itself, not just a number in a field nobody reads.

STORYTELLING HONESTY — the same class of bug already found and fixed
twice this session (shelves.py's goal-line-claim bug, market_
intelligence.py's unhedged market-neutral template): a specific claim
("his snap share is climbing") is only made after checking the REAL raw
numbers back it up (snap_share_last1/last3 vs snap_share_season_avg,
depth_rank vs depth_rank_prev), never inferred purely from role_trend
"winning" its internal hi/lo comparison — the same way proven_heat could
legitimately win Emerging Heat's comparison at zero real touches via
shrinkage, without that meaning real goal-line evidence existed. See
_role_trend_evidence.

RELATED_PLAYERS, deliberately NOT Market Intelligence's same-team-
teammates framing (investigated, decided differently, not copied): this
family's core relationship is CAUSAL, not "who's in the same market" —
  - opportunity-driven stories: the specific named teammate(s) whose
    injury opened the door (ahead_injured_teammates), a real causal link
    Market Intelligence has no equivalent of.
  - role-trend-driven stories (no specific injury driving it): the other
    players in the same position-group competition on the same team/week
    — who else could gain or lose snaps in this same shuffle. Still
    "related by the same underlying situation," just a different
    situation than a market comparison.
"""
import numpy as np
import pandas as pd

from intelligence_schema import build_story, parse_list_field

CONFIG = {
    # Minimum role_momentum to generate a story at all — see module
    # docstring for why 50 (the most literal "not neutral" reading) is
    # nearly a no-op early in the season, checked against real data, and
    # 60 was chosen instead. Starting hypothesis, tunable.
    "role_momentum_threshold": 60.0,
    # games_played below this is treated as a thin/early read for
    # headline-hedging purposes — matches _trend_delta's own shortest
    # window (3) exactly, not a fresh arbitrary number.
    "thin_games_played": 3,
    # A role_trend sub-component (touch/snap share trend, depth-chart
    # movement) must clear this percentile before its specific raw
    # numbers get cited as the headline's claim — below it, the honest
    # fallback is generic "the model's combined read," not a fabricated
    # specific claim. Same role this session's two storytelling-honesty
    # fixes already established the need for.
    "component_evidence_threshold": 55.0,
    "related_players_limit": 5,
    # evidence_classification (Universal Card v2) -- the REAL formula,
    # confirmed directly from Lovable's own trustIndicator() (same
    # thresholds already confirmed and shipped for Defensive Trends).
    "evidence_strong_threshold": 80.0,
    "evidence_moderate_threshold": 60.0,
}


def _games_played(weekly: pd.DataFrame) -> pd.Series:
    """
    Real prior-games-this-season count, 1-indexed for human-readable
    display (first game of the season = 1) — the exact same cumcount
    scoring._trend_delta uses to mask forced-zero deltas, recomputed
    identically here (0-indexed there, +1 here only for display), not a
    different definition invented for this module.
    """
    return weekly.groupby(["player_id", "season"]).cumcount() + 1


def _depth_rank_prev(weekly: pd.DataFrame) -> pd.Series:
    """Same shift(1)-within-(player_id, season) shape scoring.score_role_momentum's own depth_chart_delta uses."""
    return weekly.groupby(["player_id", "season"])["depth_rank"].transform(lambda s: s.shift(1))


def _role_trend_evidence(row: pd.Series, config: dict) -> tuple:
    """
    Which real, raw signal actually backs a role_trend-driven claim, if
    any — checked directly rather than inferred from role_trend having
    numerically won its hi/lo comparison with external_opportunity (the
    exact class of bug already fixed twice this session: a sub-score
    winning internally doesn't guarantee real evidence exists to cite).

    Ranks the three role_trend sub-components by their own percentile
    score and only returns a specific claim for whichever is highest AND
    clears component_evidence_threshold; otherwise returns a generic,
    honest fallback with no specific raw-number claim attached.

    Returns (kind, detail_text) where kind is one of "snap_share",
    "touch_share", "depth_chart", or "generic".
    """
    candidates = [
        ("snap_share", row.get("snap_share_trend_pct_role", np.nan)),
        ("touch_share", row.get("touch_share_trend_pct_role", np.nan)),
        ("depth_chart", row.get("depth_chart_movement_pct", np.nan)),
    ]
    candidates = [(k, v) for k, v in candidates if pd.notna(v)]
    if not candidates:
        return "generic", None
    kind, score = max(candidates, key=lambda kv: kv[1])
    if score < config["component_evidence_threshold"]:
        return "generic", None

    if kind == "snap_share":
        last3, season_avg = row.get("snap_share_last3"), row.get("snap_share_season_avg")
        if pd.notna(last3) and pd.notna(season_avg) and last3 > season_avg:
            return "snap_share", f"Snap share is up to {last3*100:.0f}% over his last 3 games, from a {season_avg*100:.0f}% season average"
        return "generic", None
    if kind == "touch_share":
        last3, season_avg = row.get("rz_touch_share_last3"), row.get("rz_touch_share_season_avg")
        if pd.notna(last3) and pd.notna(season_avg) and last3 > season_avg:
            return "touch_share", f"Red-zone touch share is up to {last3*100:.0f}% over his last 3 games, from a {season_avg*100:.0f}% season average"
        return "generic", None
    if kind == "depth_chart":
        rank, prev = row.get("depth_rank"), row.get("_depth_rank_prev")
        if pd.notna(rank) and pd.notna(prev) and rank < prev:
            return "depth_chart", f"Moved up the depth chart from #{int(prev)} to #{int(rank)} at {row['position_group']}"
        return "generic", None
    return "generic", None


def _related_players_opportunity(injured_teammates: list, team, config: dict) -> list:
    """The specific, named teammate(s) whose injury opened this opportunity — a real causal link, not a market-comparison list."""
    return [
        {
            "player_id": t.get("player_id"), "player_name": t.get("player_name"), "team": team,
            "relationship": "injured_ahead_on_depth_chart", "status": t.get("status"),
        }
        for t in injured_teammates[: config["related_players_limit"]]
    ]


def _related_players_competition(weekly: pd.DataFrame, season, week, posteam, position_group, player_id, config: dict) -> list:
    """Other players in the same position-group role competition this week — who else could gain/lose snaps in this same shuffle."""
    pool = weekly[
        (weekly["season"] == season) & (weekly["week"] == week) & (weekly["posteam"] == posteam)
        & (weekly["position_group"] == position_group) & (weekly["player_id"] != player_id)
    ]
    pool = pool.sort_values("depth_rank", na_position="last").head(config["related_players_limit"])
    return [
        {
            "player_id": r["player_id"], "player_name": r["player_name"], "team": posteam,
            "relationship": "same_position_group_competition", "depth_rank": r.get("depth_rank"),
        }
        for _, r in pool.iterrows()
    ]


def _headline_and_story(row: pd.Series, opportunity_driven: bool, evidence_kind: str, evidence_detail, thin: bool) -> tuple:
    """
    Story first, per this project's established storytelling hierarchy.
    Language hedges explicitly when thin=True (games_played below
    CONFIG's thin_games_played) — same sample-size-honesty requirement
    Market Intelligence's headline templates already enforce.
    """
    name, position = row["player_name"], row["position_group"]

    if opportunity_driven:
        injured = row["_injured_teammates_parsed"]
        who = injured[0]["player_name"] if injured else "a teammate ahead of him"
        status = injured[0]["status"] if injured else "injured"
        if thin:
            headline = f"An early opportunity has opened up for {name}."
            story = (
                f"With {who} {status.lower()}, {name} ({position}) is positioned to see a real bump in "
                f"opportunity — but it's still early in the season, so this is a fresh, unproven read."
            )
        else:
            headline = f"{who}'s injury has handed {name} a real opportunity."
            story = (
                f"With {who} {status.lower()}, {name} ({position}) moves into a clearly larger role — "
                f"this reading is backed by a full season's worth of usage data, not just an early guess."
            )
    else:
        if evidence_kind != "generic" and evidence_detail:
            headline = f"{name}'s role is genuinely expanding."
            story = f"{evidence_detail} — a real, visible usage trend, not just a model score moving."
        else:
            headline = f"The model sees {name}'s role trending up."
            story = (
                f"{name}'s combined role-trend read is elevated this week, but no single usage number "
                f"stands out clearly enough on its own to point to — worth watching, not yet a clear story."
            )
        if thin:
            story += f" Only {int(row['_games_played'])} game(s) of data so far this season — an early read."

    return headline, story


# ============================================================
# Universal Card v2 fields — same "attach after build_story(), not
# threaded through STORY_FIELDS" approach Defensive Trends already
# established (see that module's own block comment for the full
# reasoning: STORY_FIELDS stays a hard, closed 13-field contract shared
# unchanged by all four families).
# ============================================================

def _signal_direction_for_row() -> str:
    """
    REAL FINDING, confirmed against this module's own module docstring
    before hardcoding, not assumed: unlike Defensive Trends' genuinely
    bidirectional trend_direction, Role Changes is explicitly, by
    design, EXPANDING ROLES ONLY — "role_momentum has no mechanism that
    specifically flags 'this player's role is shrinking' as its own
    signal" (confirmed by reading score_role_momentum directly, per
    that same docstring). A row only ever reaches this family's own
    pool at all when role_momentum has already cleared a real
    materiality threshold for an EXPANDING role — there is no real
    "his role is shrinking" story in this family's current scope at
    all, and trend_direction (opportunity-driven vs role-trend-driven)
    is a WHY-classification of that expansion, not an up/down polarity
    the way Defensive's two values were.

    Given that, every real Role Changes story already represents
    favorable news for its own entity player — confirmed this is
    genuinely a real constant for this family (not a lazy fallback),
    the same way Defensive Trends' evidence_classification turned out
    to be a real, non-forced constant for a different reason. unfavorable/
    neutral/mixed have no real case to map to here today; if bidirectional
    Role Changes (declining roles) is ever built (flagged as Valuable
    Later in this module's own docstring), THAT is where unfavorable
    would first have a real story to attach to, not before.
    """
    return "favorable"


def _hero_metric_for_row(row: pd.Series, opportunity_driven: bool, evidence_kind: str, config: dict) -> dict | None:
    """
    NULLABLE, same principle as Defensive Trends: populated only when
    the SAME real evidence_kind gate that already decides whether a
    specific claim is safe to cite in the story text itself (_role_
    trend_evidence, or the opportunity-driven branch's own deliberate
    "generic" — see build_role_changes_stories) says a specific real
    number backs this story. Never a separate, looser check.

    Which of THREE real sub-metrics becomes the hero is genuinely
    per-story, not fixed per family — whichever one _role_trend_
    evidence found already winning and citable. depth_chart's real
    comparison window is prior-week-vs-this-week (a real shift(1)),
    genuinely different from snap/touch share's season-vs-last-3 — the
    period labels reflect that real difference, not a copy-paste of
    Defensive's own SEASON/LAST 3 labels onto a different real window.
    """
    if opportunity_driven or evidence_kind == "generic":
        return None
    if evidence_kind == "snap_share":
        before, after = float(row["snap_share_season_avg"]) * 100, float(row["snap_share_last3"]) * 100
        return {
            "label": "Snap Share", "before_value": round(before, 1), "after_value": round(after, 1),
            "unit": "%", "value_format": "percent", "delta_display_mode": "percentage_points",
            "delta_value": round(after - before, 1), "lower_is_better": False,
            "period_before_label": "SEASON", "period_after_label": "LAST 3",
        }
    if evidence_kind == "touch_share":
        before, after = float(row["rz_touch_share_season_avg"]) * 100, float(row["rz_touch_share_last3"]) * 100
        return {
            "label": "Red-Zone Touch Share", "before_value": round(before, 1), "after_value": round(after, 1),
            "unit": "%", "value_format": "percent", "delta_display_mode": "percentage_points",
            "delta_value": round(after - before, 1), "lower_is_better": False,
            "period_before_label": "SEASON", "period_after_label": "LAST 3",
        }
    if evidence_kind == "depth_chart":
        before, after = float(row["_depth_rank_prev"]), float(row["depth_rank"])
        return {
            "label": f"{row['position_group']} Depth Chart Rank", "before_value": before, "after_value": after,
            "unit": "rank", "value_format": "rank", "delta_display_mode": "absolute",
            "delta_value": after - before,
            # A lower rank number IS the improvement here (#1 beats #3) —
            # the real reason this metric needs its own inversion flag,
            # confirmed directly against real depth_rank semantics, not
            # assumed from the field name alone.
            "lower_is_better": True,
            "period_before_label": "PRIOR WEEK", "period_after_label": "NOW",
        }
    return None


def _what_changed_for_row(row: pd.Series, opportunity_driven: bool, evidence_kind: str, evidence_detail, thin: bool, config: dict) -> list:
    """
    Real editorial content — genuinely different from Defensive Trends'
    own equivalent in one respect worth being explicit about: _role_
    trend_evidence's own evidence_detail strings are ALREADY plain
    editorial language with no internal field names (confirmed directly
    — "Snap share is up to 62% over his last 3 games..." reads exactly
    like real what_changed copy already), unlike Defensive's raw
    supporting_evidence list (which cites literal internal field names
    like "defensive_matchup_vulnerability"). Reused directly here for
    that reason, not out of inconsistency with Defensive's own separate
    "never reuse the internal evidence list" decision — that decision
    was about THAT list's real content, not a blanket rule independent
    of what the content actually looks like.
    """
    if opportunity_driven:
        injured = row["_injured_teammates_parsed"]
        if injured:
            who, status = injured[0]["player_name"], injured[0]["status"]
            items = [{"label": "Opportunity opened up", "observation": f"{who} is {status.lower()}, clearing a path to more of this player's own touches and snaps."}]
        else:
            items = [{"label": "Opportunity opened up", "observation": "A real injury ahead of him on the depth chart has cleared a path to more touches and snaps."}]
    elif evidence_kind != "generic" and evidence_detail:
        items = [{"label": "Role expanding", "observation": evidence_detail}]
    else:
        items = [{"label": "Role trending up", "observation": "This player's combined role-trend read is elevated this week, though no single usage number stands out clearly yet."}]

    items.append({
        "label": "Sample size",
        "observation": f"Based on {int(row['_games_played'])} real game(s) this season" + (" — still an early read." if thin else ", a season-established read."),
    })
    if pd.notna(row.get("depth_rank")):
        items.append({
            "label": "Depth chart standing",
            "observation": f"Currently ranked #{int(row['depth_rank'])} at {row['position_group']} on the depth chart.",
        })
    return items[:3]


def _evidence_classification_for_row(completeness: float, confidence: float, config: dict) -> str:
    """Same real formula as Defensive Trends, confirmed directly from Lovable's own trustIndicator(): score = (confidence+completeness)/2, strong >= 80, moderate >= 60, else limited."""
    score = (confidence + completeness) / 2
    if score >= config["evidence_strong_threshold"]:
        return "strong"
    if score >= config["evidence_moderate_threshold"]:
        return "moderate"
    return "limited"


def build_role_changes_stories(weekly: pd.DataFrame, season: int, week: int, config: dict = CONFIG) -> list:
    """
    weekly: the full multi-week player_redzone_weekly table (scoring.
    score_role_momentum's own output, any number of seasons/weeks) —
    needs the full season's history, not just the target week, to
    compute games_played and depth_rank_prev the same way scoring.py's
    own trend-delta masking does. season/week: the target week to
    generate stories for. One story per qualifying RB/WR/TE row.
    """
    weekly = weekly.sort_values(["player_id", "season", "week"]).copy()
    weekly["_games_played"] = _games_played(weekly)
    weekly["_depth_rank_prev"] = _depth_rank_prev(weekly)
    weekly["ahead_injury_statuses"] = weekly["ahead_injury_statuses"].apply(parse_list_field)
    weekly["_injured_teammates_parsed"] = weekly["ahead_injured_teammates"].apply(parse_list_field)

    pool = weekly[
        (weekly["season"] == season) & (weekly["week"] == week)
        & (weekly["position_group"].isin(["RB", "WR", "TE"]))
        & (weekly["role_momentum"] >= config["role_momentum_threshold"])
    ].copy()

    stories = []
    for _, row in pool.iterrows():
        thin = row["_games_played"] < config["thin_games_played"]
        opportunity_driven = row["external_opportunity"] >= row["role_trend"] and bool(row["_injured_teammates_parsed"])

        if opportunity_driven:
            trend_direction = "opportunity-driven"
            evidence_kind, evidence_detail = "generic", None
            related_players = _related_players_opportunity(row["_injured_teammates_parsed"], row["posteam"], config)
        else:
            trend_direction = "role-trend-driven"
            evidence_kind, evidence_detail = _role_trend_evidence(row, config)
            related_players = _related_players_competition(
                weekly, season, week, row["posteam"], row["position_group"], row["player_id"], config
            )

        headline, story_text = _headline_and_story(row, opportunity_driven, evidence_kind, evidence_detail, thin)

        evidence = [
            f"role_momentum {row['role_momentum']:.0f}/100 (role_trend {row['role_trend']:.0f}, "
            f"external_opportunity {row['external_opportunity']:.0f})",
            f"{int(row['_games_played'])} game(s) played this season "
            f"({'a thin, early read' if thin else 'a season-established read'})",
        ]
        if opportunity_driven and row["_injured_teammates_parsed"]:
            evidence.append(
                "Ahead on the depth chart and injured: "
                + ", ".join(f"{t['player_name']} ({t['status']})" for t in row["_injured_teammates_parsed"])
            )
        elif evidence_kind != "generic" and evidence_detail:
            evidence.append(evidence_detail)
        if pd.notna(row.get("depth_rank")):
            evidence.append(f"Currently ranked #{int(row['depth_rank'])} at {row['position_group']} on the depth chart")

        time_window = f"Season {season}, through Week {week}"

        story = build_story(
            intelligence_family="role_changes",
            entity={
                "type": "player", "player_id": row["player_id"], "player_name": row["player_name"],
                "team": row["posteam"], "position_group": row["position_group"],
            },
            headline=headline,
            story=story_text,
            primary_signal={"name": "role_momentum", "value": float(row["role_momentum"])},
            supporting_evidence=evidence,
            trend_direction=trend_direction,
            trend_strength=float(row["role_momentum"]),
            sample_size=int(row["_games_played"]),
            completeness=float(row["role_momentum_completeness"]),
            confidence=float(row["role_momentum_completeness"]),
            time_window=time_window,
            related_players=related_players,
        )
        # Universal Card v2 fields -- attached after build_story(), not
        # part of its own hard STORY_FIELDS contract (see the block
        # comment above these helper functions for why).
        story["hero_metric"] = _hero_metric_for_row(row, opportunity_driven, evidence_kind, config)
        story["signal_direction"] = _signal_direction_for_row()
        story["what_changed"] = _what_changed_for_row(row, opportunity_driven, evidence_kind, evidence_detail, thin, config)
        story["evidence_classification"] = _evidence_classification_for_row(story["completeness"], story["confidence"], config)
        stories.append(story)

    return stories
