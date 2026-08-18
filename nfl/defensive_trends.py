"""
Defensive Trends — the third of four NFL Intelligence families (Market
Intelligence and Role Changes are built; Coaching Trends is not).
Reuses intelligence_schema.build_story unchanged, same shared contract
the first two families established.

CORE QUESTION: "Which defenses are becoming vulnerable or resistant to
touchdowns, by position and situation?" — the inverse framing of the two
offense-centric families so far: entity is a defense (team, position
group), not a player.

DATA CHECK BEFORE BUILDING (real, not assumed): scoring.score_situation's
defensive_matchup_vulnerability needed NO new pillar math — confirmed
directly (real player_redzone_weekly.csv data) that it is already a
single, well-defined value per (defteam, position_group, season, week):
0 of 4,158 real groups have more than one distinct value, even though
1-5 offensive player rows share each group (average 1.7). It is a
genuine defense-level reading today, just redundantly duplicated across
however many offensive players faced that defense that week — no
aggregation was needed to get a defense-level SNAPSHOT out of it.

THE REAL GAP, one layer down (same shape as Role Changes' gap):
situation_completeness blends TWO unrelated contextual modifiers
(7 defensive-matchup inputs + 1 environment/weather reading) into one
number — a misleading confidence signal for a story that's only about
defensive matchup (a dome/wind reading has no bearing on whether a
defense is genuinely vulnerable). Fixed with one small, purely additive
extension to scoring.score_situation: a new defensive_matchup_
completeness column, splitting out just the 7 dm-related fallback
flags already being tracked internally. situation_completeness's own
computation and every existing caller are unchanged.

THE REAL NEW AGGREGATION, correctly identified by investigating rather
than assuming: defensive_matchup_vulnerability is a per-WEEK snapshot,
and this family's own name and core question are about TREND — "is
this defense BECOMING more vulnerable," not just "how vulnerable is it
right now." Nothing in this codebase computes a week-over-week trend of
this score before this module — that's the one genuinely new piece of
logic here, and it's a new AGGREGATION (rolling the already-validated
score across weeks via redzone.add_rolling_windows, the same generic,
already-proven helper every other pillar's trend components use), not
new pillar math.

UNLIKE MARKET INTELLIGENCE'S V1 (deliberately snapshot-only, since no
real price-history data exists anywhere in this project), Defensive
Trends is NOT snapshot-only — the trailing windows already exist
(allowed_rz_tds_last1/last3/last5/season_avg), so a genuine movement
signal is buildable and IS built here, not deferred.

SAMPLE-SIZE / EARLY-SEASON HONESTY, connecting directly to the n=1-game
overconfidence fix, and CONFIRMED THIS FAMILY CAN REINTRODUCE IT IN A
NEW FORM if not handled: recent_tds_allowed_pct blends FOUR windows
(last1/last3/last5/season_avg) with NO shrinkage — unlike conversion_
rate_allowed_pct's three bands, which are shrinkage-adjusted via
_shrink_rate specifically to guard against small samples.  Checked
directly against real data (2025 NYJ run defense, Week 2): with a
single game of history, allowed_rz_tds_last1/last3/last5/season_avg are
all EXACTLY 1.0 — the same one game echoed through all four windows,
scored as if it were convergent multi-window evidence, producing
recent_tds_allowed_pct=60.5 from a single data point. This is the same
underlying mechanism as the original n=1-game bug (thin history
collapsing multiple nominally-independent windows onto each other),
just showing up as REDUNDANT ECHO producing an artificially EXTREME
reading, not a forced-zero delta. NOT fixed at the source (scoring.py's
defensive_matchup_vulnerability formula) — that would ripple through
every player's Situation score for a concern that's really specific to
this story layer, too large a blast radius for a targeted honesty need.
Instead: the week-over-week TREND this module computes is masked to NaN
whenever games_played <= window (the exact same games_played-based
mask _trend_delta already uses, reimplemented here, not imported —
matches how role_changes.py and shelves.py each keep their own local
copy rather than reaching into scoring.py's private helpers), which is
a STRONGER guarantee than sample-size language-hedging alone: a thin
defense-week literally cannot produce a trend delta, so it can never
appear in this family's output at all, not just appear with softened
language.

ENTITY GRANULARITY: (team, position_group), confirmed against real data
above — NOT a single team-wide score. "Broncos run defense vs. RBs" and
"Broncos pass defense vs. WRs" are genuinely different readings with
genuinely different trends (confirmed: the two rarely move together).

RELATED_PLAYERS — a new relatedness DIRECTION, investigated rather than
copied from either prior family: defense -> offensive players, not
player -> teammates. The real offensive players currently facing this
exact defense at this exact position group this week (weekly's own
posteam/defteam/position_group/season/week — no schedule lookup
needed, since aggregate_redzone_allowed's rows only exist for games
that were actually played), ranked by td_opportunity — the players
genuinely positioned to exploit (or be limited by) the identified
trend, not a generic team roster dump.
"""
import pandas as pd

from intelligence_schema import build_story
from redzone import add_rolling_windows

CONFIG = {
    # Minimum |delta| (vulnerability-score points, recent-3-games vs
    # season-to-date) to call a defense's trend "growing-vulnerability"
    # or "growing-resistance" rather than skipping it as not material.
    # Checked against real data, not guessed: real deltas across the
    # full backfill range up to ~40.5, with 20.0 clearing only 5.9%
    # (176/3,006) of real non-thin defense-weeks — meaningfully
    # selective, same "starting hypothesis, tunable" treatment every
    # other threshold in this codebase gets.
    "trend_threshold": 20.0,
    # Trend window — matches _trend_delta's precedent (3), not a fresh
    # number. Also the games_played boundary the trend mask uses.
    "trend_window": 3,
    # Below this defensive_matchup_completeness, headline language
    # hedges even though the trend mask already passed (a defense can
    # clear the games_played gate while still resting on a thin
    # qualified reference population for percentile ranking).
    "thin_completeness": 60.0,
    "related_players_limit": 5,
}


def _defense_weekly(weekly: pd.DataFrame) -> pd.DataFrame:
    """
    Dedupe the offense-fanned weekly table down to one row per (defteam,
    position_group, season, week) — confirmed safe directly (see module
    docstring): defensive_matchup_vulnerability and defensive_matchup_
    completeness are already identical across every offensive player row
    sharing that group, so keep="first" loses no information.
    """
    cols = [
        "defteam", "position_group", "season", "week",
        "defensive_matchup_vulnerability", "defensive_matchup_completeness",
        "recent_tds_allowed_pct", "conversion_rate_allowed_pct",
        "allowed_rz_tds_last3", "allowed_rz_tds_season_avg",
        "allowed_rz_touches_last3", "allowed_rz_touches_season_avg",
    ]
    dw = (
        weekly.drop_duplicates(subset=["defteam", "position_group", "season", "week"])[cols]
        .sort_values(["defteam", "position_group", "season", "week"])
        .reset_index(drop=True)
    )
    dw = add_rolling_windows(
        dw, metrics=["defensive_matchup_vulnerability"], group_cols=["defteam", "position_group", "season"]
    )
    return dw


def _trend_delta(defense_weekly: pd.DataFrame, window: int) -> pd.Series:
    """
    Same shape as scoring._trend_delta, reimplemented locally rather
    than imported (matches shelves.py/role_changes.py precedent of not
    reaching into another module's private helpers) — recent-window
    mean of the SCORE itself minus its own season-to-date average,
    masked to NaN whenever games_played <= window so a defense with too
    little history can't produce a forced/redundant-echo trend.
    """
    games_played = defense_weekly.groupby(["defteam", "position_group", "season"]).cumcount()
    delta = (
        defense_weekly[f"defensive_matchup_vulnerability_last{window}"]
        - defense_weekly["defensive_matchup_vulnerability_season_avg"]
    )
    return delta.where(games_played > window)


def _related_players(weekly: pd.DataFrame, season, week, defteam, position_group, config: dict) -> list:
    """
    The real offensive players actually facing this defense at this
    position group this week, ranked by td_opportunity — who's
    genuinely positioned to exploit (or be limited by) this trend, not
    a generic roster dump.
    """
    pool = weekly[
        (weekly["season"] == season) & (weekly["week"] == week)
        & (weekly["defteam"] == defteam) & (weekly["position_group"] == position_group)
    ]
    pool = pool.sort_values("td_opportunity", ascending=False, na_position="last").head(config["related_players_limit"])
    return [
        {
            "player_id": r["player_id"], "player_name": r["player_name"], "team": r["posteam"],
            "relationship": "faces_this_defense_this_week", "td_opportunity": r.get("td_opportunity"),
        }
        for _, r in pool.iterrows()
    ]


def _headline_and_story(row: pd.Series, direction: str, thin_completeness: bool) -> tuple:
    """
    Story first, per this project's established storytelling hierarchy.
    Prefers citing real raw red-zone-TD-allowed counts, but ONLY when
    they genuinely agree in direction with the overall trend being
    claimed — checked directly, not assumed. defensive_matchup_
    vulnerability blends red-zone/inside-10/goal-line TD rates AND a
    separately shrinkage-adjusted conversion rate; confirmed against
    real data (2025 Week 15, CAR WR defense) that the SCORE's own
    3-game trend can move a different direction than red-zone TDs alone
    over that exact same window (score trending resistant off a very
    low Weeks 9-13 base, while red-zone TDs specifically had already
    ticked back up by Week 15) — the same class of bug already fixed
    twice elsewhere this session (a sub-score/blend moving one way
    doesn't guarantee the one specific raw number chosen to cite backs
    it up). Falls back to an honest, still-real but non-numeric claim
    when they disagree, rather than citing a contradicting number.
    """
    team, pos = row["defteam"], row["position_group"]
    last3, season_avg = row["allowed_rz_tds_last3"], row["allowed_rz_tds_season_avg"]

    # Compared at DISPLAY precision (1 decimal), not raw float precision:
    # two values that round to the same display text (e.g. 0.667 vs 0.733,
    # both "0.7") would otherwise produce a self-contradicting sentence
    # ("0.7 ... down from a 0.7 season average") even though the raw
    # numbers technically differ — confirmed as a real case (2022 Week 14
    # NYJ RB defense, 2025 Week 11 DAL WR defense), not hypothetical.
    td_agrees = False
    if pd.notna(last3) and pd.notna(season_avg):
        last3_r, season_avg_r = round(last3, 1), round(season_avg, 1)
        if direction == "growing-vulnerability" and last3_r > season_avg_r:
            td_agrees = True
        elif direction == "growing-resistance" and last3_r < season_avg_r:
            td_agrees = True

    if direction == "growing-vulnerability":
        headline = f"{team}'s defense is getting worse against {pos}s."
        if td_agrees:
            story = (
                f"Opponents have scored {last3:.1f} red-zone TDs per game against {team}'s {pos} defense over the "
                f"last 3 games, up from a {season_avg:.1f} season average — a real, recent decline, not just a "
                f"single bad week."
            )
        else:
            story = (
                f"{team}'s {pos} defense has trended clearly more vulnerable over its last 3 games, per the "
                f"model's combined red-zone/inside-10/goal-line and conversion-rate read — not (yet) showing up "
                f"as a single standout raw red-zone-TD number, but a real shift in the overall profile."
            )
    else:
        headline = f"{team}'s defense is tightening up against {pos}s."
        if td_agrees:
            story = (
                f"Opponents have scored just {last3:.1f} red-zone TDs per game against {team}'s {pos} defense over "
                f"the last 3 games, down from a {season_avg:.1f} season average — a real, recent improvement."
            )
        else:
            story = (
                f"{team}'s {pos} defense has trended clearly more resistant over its last 3 games, per the "
                f"model's combined red-zone/inside-10/goal-line and conversion-rate read — not (yet) showing up "
                f"as a single standout raw red-zone-TD number, but a real shift in the overall profile."
            )
    if thin_completeness:
        story += " Based on a still-developing sample this season — worth confirming as more games are played."

    return headline, story, td_agrees


def build_defensive_trends_stories(weekly: pd.DataFrame, season: int, week: int, config: dict = CONFIG) -> list:
    """
    weekly: the full multi-week player_redzone_weekly table (scoring.
    score_situation's own output, any number of seasons/weeks) — needs
    the full season's history to compute the trend, same reasoning as
    role_changes.py's games_played. season/week: the target week to
    generate stories for. One story per (defteam, position_group) whose
    trend clears config["trend_threshold"] as of that week.
    """
    dw = _defense_weekly(weekly)
    dw["_games_played"] = dw.groupby(["defteam", "position_group", "season"]).cumcount() + 1
    dw["_delta"] = _trend_delta(dw, config["trend_window"])

    pool = dw[
        (dw["season"] == season) & (dw["week"] == week) & dw["_delta"].notna()
        & (dw["_delta"].abs() >= config["trend_threshold"])
    ].copy()

    stories = []
    for _, row in pool.iterrows():
        direction = "growing-vulnerability" if row["_delta"] > 0 else "growing-resistance"
        thin_completeness = row["defensive_matchup_completeness"] < config["thin_completeness"]
        headline, story_text, td_agrees = _headline_and_story(row, direction, thin_completeness)

        evidence = [
            f"defensive_matchup_vulnerability {row['defensive_matchup_vulnerability']:.0f}/100, "
            f"moved {row['_delta']:+.1f} points over the last {config['trend_window']} games vs. season-to-date",
            f"recent_tds_allowed_pct {row['recent_tds_allowed_pct']:.0f}/100, conversion_rate_allowed_pct "
            f"{row['conversion_rate_allowed_pct']:.0f}/100 (this week's own component readings)",
            f"{int(row['_games_played'])} game(s) of data this season "
            f"({'a thin, still-developing sample' if thin_completeness else 'an established, well-populated read'})",
        ]
        if td_agrees:
            evidence.insert(1, (
                f"Allowed {row['allowed_rz_tds_last3']:.1f} red-zone TDs/game over the last 3 games "
                f"(season average {row['allowed_rz_tds_season_avg']:.1f})"
            ))

        stories.append(build_story(
            intelligence_family="defensive_trends",
            entity={"type": "defense", "team": row["defteam"], "position_group": row["position_group"]},
            headline=headline,
            story=story_text,
            primary_signal={"name": "defensive_matchup_vulnerability", "value": float(row["defensive_matchup_vulnerability"])},
            supporting_evidence=evidence,
            trend_direction=direction,
            trend_strength=float(min(abs(row["_delta"]), 100.0)),
            sample_size=int(row["_games_played"]),
            completeness=float(row["defensive_matchup_completeness"]),
            confidence=float(row["defensive_matchup_completeness"]),
            time_window=f"Season {season}, last {config['trend_window']} games through Week {week} vs. season-to-date",
            related_players=_related_players(weekly, season, week, row["defteam"], row["position_group"], config),
        ))

    return stories
