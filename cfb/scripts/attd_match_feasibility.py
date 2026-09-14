"""
CFB ATTD player-matching feasibility investigation (2026-09-13).

Read-only. No scoring, no storage schema, no live poller -- just the
feasibility question: given a plain player-name string off an odds
board (First Last, no team on the outcome for NFL's confirmed shape --
see poll_ncaaf_prop_coverage.py's own docstring for why CFB's real
outcome shape is NOT yet confirmed), how reliably could it be resolved
to a real CFBD athleteId, restricted to the game's two real candidate
schools?

WHY THIS RUNS AGAINST ROSTER SELF-CONSISTENCY, NOT REAL ODDS NAMES:
every real CFB game probed on 2026-09-13 (all 57 upcoming games, an
exhaustive, zero-cost sweep -- see poll_ncaaf_prop_coverage.py) was 4+
days from kickoff with ZERO live player_anytime_td coverage, matching
that module's own long-documented "posts inside ~2 days of kickoff"
pattern. There was no real odds-sourced player name available to test
against on the day this investigation ran. Every check below instead
asks whether a REAL CFBD roster full name, matched back against its own
real school's roster, resolves uniquely -- this isolates the roster-
side ambiguity risk (the actual hard part of the problem: name
collisions, transfers, multi-name schools) using 100% real data, without
needing a live odds board. It does NOT rule out a DIFFERENT failure mode
this investigation couldn't test: The Odds API formatting a name
differently than CFBD does (a nickname, a suffix, a hyphenation) -- that
needs a real captured odds name to check, and none existed today. Re-run
once poll_ncaaf_prop_coverage.py's own attd_outcomes capture (this same
investigation's Part 1) returns real rows, and extend this script to
match against those real names directly rather than against roster
self-consistency.

Usage:
    CFBD_API_KEY=<key> python3 cfb/scripts/attd_match_feasibility.py [season]
    # default season 2025
"""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ids import cfbd_get
from plays_stats import completed_games, fetch_games, fetch_week_play_stats

# ATTD is offered on any player who can plausibly score a TD -- broader
# than this project's own RB/WR/TE scoring scope (which excludes QBs
# throughout cfb/scoring.py and cfb/redzone.py). Scoped here to QB/RB/
# WR/TE deliberately, matching what a real sportsbook board would
# realistically carry (a rushing/passing QB is a real ATTD candidate;
# OL/DL/DB are not, short of a fluke).
ATTD_ELIGIBLE_POSITIONS = ("QB", "RB", "WR", "TE")


def _full_name(row: dict) -> str:
    return f"{(row.get('firstName') or '').strip()} {(row.get('lastName') or '').strip()}".strip()


def national_name_collisions(roster: list[dict]) -> dict:
    """Any position, any school -- how often does a full name collide
    across 2+ real distinct athleteIds nationally, with no team
    constraint applied at all (the worst case a matcher would face if it
    ignored team entirely)."""
    name_to_ids: dict[str, set] = defaultdict(set)
    name_to_teams: dict[str, set] = defaultdict(set)
    for r in roster:
        name = _full_name(r)
        if not name:
            continue
        name_to_ids[name].add(r["id"])
        name_to_teams[name].add(r.get("team"))
    collisions = {n: ids for n, ids in name_to_ids.items() if len(ids) > 1}
    return {
        "total_distinct_names": len(name_to_ids),
        "collision_count": len(collisions),
        "collision_rate": len(collisions) / len(name_to_ids) if name_to_ids else 0.0,
        "top_collisions": sorted(
            ({"name": n, "n_players": len(ids), "teams": sorted(name_to_teams[n])} for n, ids in collisions.items()),
            key=lambda d: -d["n_players"],
        )[:10],
    }


def skill_position_self_match(roster: list[dict]) -> dict:
    """The real feasibility number: restrict to QB/RB/WR/TE (the real
    ATTD-eligible population) and ask whether (name, own real school)
    resolves to exactly one real player -- exactly what team-constrained
    matching against a real game's home/away pair would do. Same
    3-way-classification SPIRIT as nfl/roster_match.py's match_player_
    names, adapted: here every row IS a real, in-scope, on-team player by
    construction (it's a self-consistency test, not a real unmatched-row
    classification), so the only real failure mode measurable this way
    is "ambiguous within own school" -- a genuine same-team, same-name
    collision, the one category true real ATTD names could also hit."""
    skill_rows = [r for r in roster if r.get("position") in ATTD_ELIGIBLE_POSITIONS]
    by_name: dict[str, list] = defaultdict(list)
    for r in skill_rows:
        by_name[_full_name(r)].append(r)

    by_position: dict[str, list] = defaultdict(lambda: [0, 0])
    same_school_dupes = []
    unique = 0
    for r in skill_rows:
        name = _full_name(r)
        same_school = [x for x in by_name[name] if x.get("team") == r.get("team")]
        pos_stat = by_position[r["position"]]
        pos_stat[1] += 1
        if len(same_school) == 1:
            unique += 1
            pos_stat[0] += 1

    seen_schools = set()
    for name, rows in by_name.items():
        by_school: dict[str, list] = defaultdict(list)
        for r in rows:
            by_school[r.get("team")].append(r)
        for school, rs in by_school.items():
            if len(rs) > 1 and (name, school) not in seen_schools:
                seen_schools.add((name, school))
                same_school_dupes.append({"name": name, "school": school, "athlete_ids": [x["id"] for x in rs]})

    n = len(skill_rows)
    return {
        "n_skill_position_rows": n,
        "unique_match_rate": unique / n if n else 0.0,
        "same_school_collisions": same_school_dupes,
        "by_position": {pos: {"matched": m, "total": t, "rate": m / t if t else 0.0} for pos, (m, t) in by_position.items()},
    }


def roster_vs_real_game_team(season: int, roster: list[dict], week: int = 1) -> dict:
    """Does /roster?year=<season>'s declared team for a real athlete
    agree with the team they ACTUALLY appeared under in that season's
    real week-`week` play-by-play? A mismatch here would mean the
    roster's season-level team assignment is stale relative to games
    already played -- the CFB analogue of a forward-looking-roster-vs-
    backfilled-stats bug, tested directly rather than assumed."""
    roster_team = {r["id"]: r.get("team") for r in roster if r.get("id")}
    games = fetch_games(season, week, season_type="regular")
    completed = completed_games(games)
    play_stats, _diag = fetch_week_play_stats(completed, season_type="regular")

    actual_team: dict[str, set] = defaultdict(set)
    for p in play_stats:
        aid, team = p.get("athleteId"), p.get("team")
        if aid and team:
            actual_team[aid].add(team)

    mismatches = []
    for aid, teams in actual_team.items():
        rteam = roster_team.get(aid)
        if rteam is not None and rteam not in teams:
            mismatches.append({"athlete_id": aid, "roster_team": rteam, "real_game_teams": sorted(teams)})

    n = len(actual_team)
    return {
        "week_checked": week,
        "athletes_checked": n,
        "mismatch_count": len(mismatches),
        "mismatch_rate": len(mismatches) / n if n else 0.0,
        "mismatches": mismatches[:15],
    }


def cross_season_transfer_volume(season: int) -> dict:
    """Real transfer-portal churn: athleteIds present in BOTH `season-1`
    and `season`'s rosters whose declared team differs -- a real,
    ID-stable measurement of transfer volume, not a name-based guess."""
    prev = cfbd_get("/roster", {"year": season - 1, "classification": "fbs"})
    cur = cfbd_get("/roster", {"year": season, "classification": "fbs"})
    team_prev = {r["id"]: r.get("team") for r in prev if r.get("id")}
    team_cur = {r["id"]: r.get("team") for r in cur if r.get("id")}
    name_cur = {r["id"]: _full_name(r) for r in cur}

    both = set(team_prev) & set(team_cur)
    transfers = [
        {"athlete_id": aid, "name": name_cur.get(aid), "from": team_prev[aid], "to": team_cur[aid]}
        for aid in both if team_prev[aid] != team_cur[aid]
    ]
    return {
        "returning_athletes_both_seasons": len(both),
        "transfer_count": len(transfers),
        "transfer_rate": len(transfers) / len(both) if both else 0.0,
        "sample": transfers[:15],
    }


def in_season_team_change(actual_team_wk1: dict, actual_team_wk_later: dict) -> dict:
    """Real athletes appearing in both weeks whose ACTUAL in-game team
    differs -- a genuine mid-season move (rare, unlike the offseason
    portal above), detected directly from real play-by-play."""
    both = set(actual_team_wk1) & set(actual_team_wk_later)
    changes = [
        {"athlete_id": aid, "wk1_team": sorted(actual_team_wk1[aid]), "later_team": sorted(actual_team_wk_later[aid])}
        for aid in both if not (actual_team_wk1[aid] & actual_team_wk_later[aid])
    ]
    return {"athletes_in_both_weeks": len(both), "in_season_changes": len(changes), "changes": changes}


if __name__ == "__main__":
    season = int(sys.argv[1]) if len(sys.argv) > 1 else 2025

    print(f"Fetching real {season} FBS roster ...")
    roster = cfbd_get("/roster", {"year": season, "classification": "fbs"})
    print(f"  {len(roster)} rows")

    print("\n" + "=" * 78)
    print("A) NATIONAL NAME COLLISIONS (any position, all FBS schools)")
    print("=" * 78)
    a = national_name_collisions(roster)
    print(f"  distinct full names: {a['total_distinct_names']}")
    print(f"  collide across 2+ real players: {a['collision_count']} ({100*a['collision_rate']:.2f}%)")
    for c in a["top_collisions"][:5]:
        print(f"    {c['name']!r}: {c['n_players']} players -- {c['teams']}")

    print("\n" + "=" * 78)
    print("B) QB/RB/WR/TE SELF-CONSISTENCY MATCH TEST (name + own real school)")
    print("=" * 78)
    b = skill_position_self_match(roster)
    print(f"  rows: {b['n_skill_position_rows']}  unique-match rate: {100*b['unique_match_rate']:.2f}%")
    for pos, d in b["by_position"].items():
        print(f"    {pos}: {d['matched']}/{d['total']} ({100*d['rate']:.2f}%)")
    print(f"  real same-school name collisions: {len(b['same_school_collisions'])}")
    for d in b["same_school_collisions"][:5]:
        print(f"    {d['name']!r} at {d['school']}: athleteIds {d['athlete_ids']}")

    print("\n" + "=" * 78)
    print(f"C) ROSTER(year={season}) TEAM vs REAL WEEK-1 IN-GAME TEAM")
    print("=" * 78)
    c = roster_vs_real_game_team(season, roster, week=1)
    print(f"  athletes checked: {c['athletes_checked']}")
    print(f"  mismatches: {c['mismatch_count']} ({100*c['mismatch_rate']:.2f}%)")
    for m in c["mismatches"][:5]:
        print(f"    id={m['athlete_id']} roster={m['roster_team']!r} real_game={m['real_game_teams']}")

    print("\n" + "=" * 78)
    print(f"D) CROSS-SEASON TRANSFER VOLUME ({season - 1} -> {season} roster team)")
    print("=" * 78)
    d = cross_season_transfer_volume(season)
    print(f"  returning athletes (in both rosters): {d['returning_athletes_both_seasons']}")
    print(f"  real transfers (team changed): {d['transfer_count']} ({100*d['transfer_rate']:.2f}%)")
    for t in d["sample"][:5]:
        print(f"    {t['name']!r}: {t['from']} -> {t['to']}")

    print("\n" + "=" * 78)
    print("E) IN-SEASON TEAM CHANGE (real game team, week 1 vs week 6)")
    print("=" * 78)
    games1 = fetch_games(season, 1, season_type="regular")
    ps1, _ = fetch_week_play_stats(completed_games(games1), season_type="regular")
    games6 = fetch_games(season, 6, season_type="regular")
    ps6, _ = fetch_week_play_stats(completed_games(games6), season_type="regular")
    at1: dict[str, set] = defaultdict(set)
    for p in ps1:
        if p.get("athleteId") and p.get("team"):
            at1[p["athleteId"]].add(p["team"])
    at6: dict[str, set] = defaultdict(set)
    for p in ps6:
        if p.get("athleteId") and p.get("team"):
            at6[p["athleteId"]].add(p["team"])
    e = in_season_team_change(at1, at6)
    print(f"  athletes appearing in both weeks: {e['athletes_in_both_weeks']}")
    print(f"  real in-season team changes: {e['in_season_changes']}")
