"""
Shared RB/WR/TE name-matching against seasonal_rosters, with a 3-way
unmatched classification. Used by market_value.py (matching The Odds
API's player names — no team field on the outcome, so home/away are the
candidate teams) and redzone.py (matching the 2025+ depth-chart schema's
player names for the ~1% of rows missing gsis_id — the row already
carries its own team, so there's exactly one candidate team). Same
matching logic either way; the candidate-team set is the caller's job to
attach, not this function's, since where it comes from differs per
caller.
"""
import pandas as pd


def match_player_names(
    rows: pd.DataFrame,
    seasonal_rosters: pd.DataFrame,
    season: int,
    name_col: str,
    candidate_teams_col: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Match each row's [name_col] to a (player_id, position_group, team)
    via seasonal_rosters, restricted to RB/WR/TE and to whichever teams
    are listed in that row's [candidate_teams_col] (a set/list of team
    abbreviations per row).

    Uses a plain per-row loop rather than a vectorized merge — this only
    ever processes small per-call row counts (tens to low hundreds), so
    the performance cost is negligible next to how much easier the
    row-wise team-constrained matching logic is to verify correct versus
    the equivalent merge-and-filter.

    Returns (matched, unmatched) — unmatched rows are never dropped
    silently. Each unmatched row gets a heuristic match_issue_type:
      "rookie_or_new"         - no exact name match anywhere in
                                 seasonal_rosters (any position, any team,
                                 any season) — most likely a player with
                                 no backfilled season history yet (e.g. a
                                 rookie who hasn't played a tracked
                                 season). Can't be proven without external
                                 data; this is the best available
                                 heuristic, not a certainty.
      "position_out_of_scope" - a real, matchable player (QB, OL, etc.),
                                 correctly excluded for being outside
                                 RB/WR/TE.
      "team_mismatch"         - a real RB/WR/TE, just not on any of the
                                 candidate teams (traded, a team-
                                 abbreviation resolution issue, or a
                                 genuine name collision) — worth a human
                                 look, more likely a real issue than
                                 expected coverage noise.
    """
    season_rosters = seasonal_rosters[seasonal_rosters["season"] == season]
    skill_position_rosters = season_rosters[season_rosters["position"].isin(["RB", "WR", "TE"])]
    roster_lookup = skill_position_rosters[["player_id", "player_name", "position", "team"]]

    all_names_ever = set(seasonal_rosters["player_name"].dropna())
    skill_position_names_ever = set(
        seasonal_rosters.loc[seasonal_rosters["position"].isin(["RB", "WR", "TE"]), "player_name"].dropna()
    )

    matched_rows = []
    unmatched_rows = []
    for row in rows.to_dict("records"):
        name = row[name_col]
        candidates = roster_lookup[roster_lookup["player_name"] == name]
        in_scope = candidates[candidates["team"].isin(row[candidate_teams_col])]

        if len(in_scope) >= 1:
            m = in_scope.iloc[0]
            matched_rows.append(
                {**row, "player_id": m["player_id"], "team": m["team"], "position_group": m["position"]}
            )
        elif name in skill_position_names_ever:
            # A real RB/WR/TE, just not exact-matchable to a candidate
            # team (traded, an abbreviation resolution issue, or a
            # genuine name collision) — a real issue worth a look, not
            # expected coverage noise.
            unmatched_rows.append({**row, "match_issue_type": "team_mismatch"})
        elif name in all_names_ever:
            # A real player (QB, OL, etc.) correctly out of scope for a
            # system built around RB/WR/TE red-zone touches — not a bug,
            # matches the scope every pillar in this project already has
            # (e.g. QBs are excluded from redzone._position_lookup the
            # same way).
            unmatched_rows.append({**row, "match_issue_type": "position_out_of_scope"})
        else:
            unmatched_rows.append({**row, "match_issue_type": "rookie_or_new"})

    matched = pd.DataFrame(matched_rows)
    unmatched = pd.DataFrame(unmatched_rows)
    return matched, unmatched
