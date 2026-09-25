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
import re

import pandas as pd

# Suffixes the two sources disagree about. Matched as whole tokens only, so
# a real surname is never eaten -- "Sr" here can only ever strip a trailing
# generational suffix, never the "Sr" inside a name.
_SUFFIXES = ("jr", "sr", "ii", "iii", "iv")
_SUFFIX_RE = re.compile(r"\b(?:" + "|".join(_SUFFIXES) + r")\b")
_PUNCT_RE = re.compile(r"[.']")
_WS_RE = re.compile(r"\s+")


def normalize_player_name(name) -> str:
    """
    A name reduced to what both sources agree on: lowercase, no periods or
    apostrophes, no generational suffix, single-spaced.

        "Deebo Samuel Sr."   -> "deebo samuel"
        "Travis Etienne Jr." -> "travis etienne"
        "Brian Thomas Jr"    -> "brian thomas"
        "C.J. Williams"      -> "cj williams"

    Deliberately NOT a fuzzy match. No edit distance, no phonetics, no
    first-name/nickname folding -- every one of those can merge two real
    players, and this runs unattended against live money. It removes
    exactly the punctuation and suffix differences observed between the
    Odds API and nflverse, and nothing else. Nicknames ("Hollywood Brown"
    for Marquise Brown) are a separate problem that normalisation cannot
    and should not solve.

    Non-strings and NaN return "" so they can never collide with a real
    name in a lookup.
    """
    if not isinstance(name, str):
        return ""
    out = _PUNCT_RE.sub("", name.lower())
    out = _SUFFIX_RE.sub(" ", out)
    return _WS_RE.sub(" ", out).strip()


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

    TWO PASSES. Exact string equality first, then a normalised retry for
    whatever the exact pass missed (see normalize_player_name). Exact
    stays first so an exact hit can never be overruled by a normalised
    one; normalisation only ever rescues rows that were already going to
    be unmatched.

    The normalised pass exists because the Odds API and nflverse disagree
    about suffixes in both directions, on real players every week: the
    feed sends "Deebo Samuel" where the roster has "Deebo Samuel Sr.",
    and "Travis Etienne Jr." where the roster has "Travis Etienne".
    "Brian Thomas Jr" vs "Brian Thomas Jr." differs by one period. Nine
    real RB/WR/TE with live prices were lost to this in week 3 alone.

    AMBIGUITY IS NOT RESOLVED, IT IS REPORTED. If a normalised name
    matches more than one roster player after the team filter, no row is
    picked — it is left unmatched as "ambiguous_match". Two different
    real players can normalise together (a "Josh Allen"/"Joshua Allen"
    pair on the same team would), and silently taking the first would
    attach a real price to the wrong player_id. An unmatched row costs
    one player's pricing for one poll; a wrongly matched one corrupts a
    player's history without any signal that it happened.

    Returns (matched, unmatched) — unmatched rows are never dropped
    silently. Each unmatched row gets a match_issue_type:
      "ambiguous_match"       - normalised to more than one roster player
                                 on the candidate teams. Deliberately not
                                 resolved; see above.
      "team_mismatch"         - a real RB/WR/TE, just not on any of the
                                 candidate teams (traded, a team-
                                 abbreviation resolution issue, or a
                                 genuine name collision) — worth a human
                                 look, more likely a real issue than
                                 expected coverage noise.
      "position_out_of_scope" - a real, matchable player (QB, OL, etc.),
                                 correctly excluded for being outside
                                 RB/WR/TE.
      "not_on_roster"         - no match, exact or normalised, anywhere in
                                 seasonal_rosters. RENAMED from
                                 "rookie_or_new", which named a cause the
                                 data cannot support: the bucket was
                                 carrying suffix mismatches, team
                                 defenses, a market outcome and genuine
                                 absences, and actual rookies were the
                                 least common of the four. "Not on this
                                 roster" is what is observed; why is not.
    """
    season_rosters = seasonal_rosters[seasonal_rosters["season"] == season]
    skill_position_rosters = season_rosters[season_rosters["position"].isin(["RB", "WR", "TE"])]
    roster_lookup = skill_position_rosters[["player_id", "player_name", "position", "team"]]

    all_names_ever = set(seasonal_rosters["player_name"].dropna())
    skill_position_names_ever = set(
        seasonal_rosters.loc[seasonal_rosters["position"].isin(["RB", "WR", "TE"]), "player_name"].dropna()
    )
    # Normalised twins of the three lookups above, built once. The
    # per-name dict maps a normalised name to EVERY roster row that
    # normalises to it, which is what makes the ambiguity check possible
    # -- a set would have thrown away the collision this needs to detect.
    norm_roster: dict = {}
    for r in roster_lookup.to_dict("records"):
        norm_roster.setdefault(normalize_player_name(r["player_name"]), []).append(r)
    norm_skill_names_ever = {normalize_player_name(n) for n in skill_position_names_ever}
    norm_all_names_ever = {normalize_player_name(n) for n in all_names_ever}

    matched_rows = []
    unmatched_rows = []
    for row in rows.to_dict("records"):
        name = row[name_col]
        teams = row[candidate_teams_col]

        # PASS 1 -- exact. Unchanged, and still first: an exact hit is
        # never overruled by a normalised one.
        candidates = roster_lookup[roster_lookup["player_name"] == name]
        in_scope = candidates[candidates["team"].isin(teams)]
        if len(in_scope) >= 1:
            m = in_scope.iloc[0]
            matched_rows.append(
                {**row, "player_id": m["player_id"], "team": m["team"], "position_group": m["position"]}
            )
            continue

        # PASS 2 -- normalised, over the same pool, same team filter.
        norm_name = normalize_player_name(name)
        norm_in_scope = [
            r for r in norm_roster.get(norm_name, []) if r["team"] in teams
        ] if norm_name else []
        distinct_ids = {r["player_id"] for r in norm_in_scope}

        if len(distinct_ids) == 1:
            m = norm_in_scope[0]
            matched_rows.append(
                {**row, "player_id": m["player_id"], "team": m["team"], "position_group": m["position"]}
            )
            continue
        if len(distinct_ids) > 1:
            # Two real players normalised together on the candidate teams.
            # Picking either one would attach a real price to a possibly
            # wrong player_id, and nothing downstream would ever surface
            # it. Left unmatched on purpose.
            unmatched_rows.append({**row, "match_issue_type": "ambiguous_match"})
            continue

        # Fallbacks, normalised-aware so a suffix difference cannot push a
        # real QB into "not_on_roster" the way it used to.
        if name in skill_position_names_ever or norm_name in norm_skill_names_ever:
            # A real RB/WR/TE, just not matchable to a candidate team
            # (traded, an abbreviation resolution issue, or a genuine name
            # collision) — a real issue worth a look, not coverage noise.
            unmatched_rows.append({**row, "match_issue_type": "team_mismatch"})
        elif name in all_names_ever or norm_name in norm_all_names_ever:
            # A real player (QB, OL, etc.) correctly out of scope for a
            # system built around RB/WR/TE red-zone touches — not a bug,
            # matches the scope every pillar in this project already has
            # (e.g. QBs are excluded from redzone._position_lookup the
            # same way).
            unmatched_rows.append({**row, "match_issue_type": "position_out_of_scope"})
        else:
            unmatched_rows.append({**row, "match_issue_type": "not_on_roster"})

    matched = pd.DataFrame(matched_rows)
    unmatched = pd.DataFrame(unmatched_rows)
    return matched, unmatched
