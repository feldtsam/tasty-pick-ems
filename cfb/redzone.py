"""
Shared red zone / inside-10 / goal-line aggregation for CFBD /plays/stats.

The CFB counterpart of nfl/redzone.py's `_touches` / `_band_agg` /
`aggregate_redzone_game` / `aggregate_redzone_allowed`. Same band
definitions, same touch/TD counting logic, same "one ingest feeds two
tables" structure — only the input row shape differs (CFBD /plays/stats
rows instead of nflverse play-by-play).

Band definitions (spec §8, identical to NFL):
    RED_ZONE  yardsToGoal <= 20
    INSIDE_10 yardsToGoal <= 10
    GOAL_LINE yardsToGoal <= 5

Touch definition (spec §8): a touch is a `Rush` stat row (touch_type
"rush") or a `Target` stat row (touch_type "target"). `Reception` is
deliberately NOT a touch — Target captures the opportunity whether the
pass was caught or not, matching the pillar's intent.

TD attribution (spec §8): a touch scored iff a `Touchdown` stat row exists
on the SAME playId with the SAME athleteId. Joining on both keys credits
only the actual scorer (not, e.g., the QB on a passing TD, nor a teammate
who recovered a fumble in the end zone).

Outputs are plain lists of dict rows shaped for the two Lovable write
routes:
    aggregate_redzone_game_cfb     -> cfb_player_redzone_weekly rows
    aggregate_redzone_allowed_cfb  -> cfb_defense_redzone_allowed_weekly rows
Each returns (rows, diagnostics). Rolling windows / cumulative / season
totals are NOT computed here — the later scoring task derives them over
the full season's stored rows, exactly as NFL's add_rolling_windows does
(Step 1 Q2).
"""
from __future__ import annotations

import pandas as pd

from ids import team_id_map_from_games
from plays_stats import STAT_RECEPTION, STAT_RUSH, STAT_TARGET

RED_ZONE = 20
INSIDE_10 = 10
GOAL_LINE = 5

_BANDS = (("rz", RED_ZONE), ("i10", INSIDE_10), ("gl", GOAL_LINE))

# Touch = Rush + Target + Reception (spec §8a). CFBD's `Target` is only
# the receiver on an INCOMPLETE pass and `Reception` only on a COMPLETED
# one, so a pass "target" (opportunity regardless of completion) is the
# two combined. rush -> "rush"; target/reception -> "target_*" for the
# rush/target split, and both count toward total touches.
_TOUCH_TYPE = {STAT_RUSH: "rush", STAT_TARGET: "target", STAT_RECEPTION: "reception"}
_PASS_TOUCH_TYPES = ("target", "reception")
# only a completed touch (rush or reception) can be the scorer on a TD
# play — a `target` is by definition an incompletion.
_SCOREABLE_TOUCH_TYPES = ("rush", "reception")


# --------------------------------------------------------------------------
# shared touch / band-count primitives
# --------------------------------------------------------------------------
def _touches(play_stats: list[dict], td_play_ids: set[str]) -> pd.DataFrame:
    """
    One row per touch (a `Rush`, `Target`, or `Reception` stat row with a
    real athleteId), tagged with which player/offense/defense touched the
    ball, the yard line, and whether that player scored on the play.

    A touch scored iff its `playId` is an offensive-TD play (from /plays,
    passed in as `td_play_ids`, spec §8a) AND the touch is a rush or a
    reception — never a bare `target`, which is an incompletion.

    Columns: game_id season week team opponent play_id athlete_id
             athlete_name yards_to_goal touch_type own_touchdown
    """
    recs: list[dict] = []
    for r in play_stats:
        st = r.get("statType")
        touch_type = _TOUCH_TYPE.get(st)
        if touch_type is None:
            continue
        aid = r.get("athleteId")
        if aid is None:
            continue
        ytg = r.get("yardsToGoal")
        if ytg is None:
            continue
        pid = r.get("playId")
        pid_s = None if pid is None else str(pid)
        scored = int(
            pid_s is not None
            and pid_s in td_play_ids
            and touch_type in _SCOREABLE_TOUCH_TYPES
        )
        recs.append(
            {
                "game_id": r.get("gameId"),
                "season": r.get("season"),
                "week": r.get("week"),
                "team": r.get("team"),
                "opponent": r.get("opponent"),
                "conference": r.get("conference"),
                "play_id": pid_s,
                "athlete_id": str(aid),
                "athlete_name": r.get("athleteName"),
                "yards_to_goal": ytg,
                "touch_type": touch_type,
                "own_touchdown": scored,
            }
        )

    cols = [
        "game_id", "season", "week", "team", "opponent", "conference",
        "play_id", "athlete_id", "athlete_name", "yards_to_goal",
        "touch_type", "own_touchdown",
    ]
    df = pd.DataFrame(recs, columns=cols)
    if not df.empty:
        df["yards_to_goal"] = pd.to_numeric(df["yards_to_goal"], errors="coerce")
        df = df[df["yards_to_goal"].notna()]
    return df


def _band_agg(touches: pd.DataFrame, label: str, group_keys: list[str], zone_max: int) -> pd.DataFrame:
    """
    Touch / rush-touch / target-touch / TD counts for one yard-line band,
    grouped by group_keys. Identical shape to nfl/redzone.py._band_agg.
    """
    empty_cols = group_keys + [
        f"{label}_touches", f"{label}_rush_touches",
        f"{label}_target_touches", f"{label}_tds",
    ]
    if touches.empty:
        return pd.DataFrame(columns=empty_cols)

    min_df = touches[touches["yards_to_goal"] <= zone_max]
    if min_df.empty:
        return pd.DataFrame(columns=empty_cols)

    return (
        min_df.groupby(group_keys, dropna=False)
        .agg(
            **{
                f"{label}_touches": ("touch_type", "count"),
                f"{label}_rush_touches": ("touch_type", lambda s: (s == "rush").sum()),
                f"{label}_target_touches": ("touch_type", lambda s: s.isin(_PASS_TOUCH_TYPES).sum()),
                f"{label}_tds": ("own_touchdown", "sum"),
            }
        )
        .reset_index()
    )


def _merge_bands(touches: pd.DataFrame, group_keys: list[str]) -> pd.DataFrame:
    out = None
    for label, zone_max in _BANDS:
        part = _band_agg(touches, label, group_keys, zone_max)
        out = part if out is None else out.merge(part, on=group_keys, how="outer")
    for c in out.columns:
        if c.endswith(("_touches", "_tds")):
            out[c] = out[c].fillna(0).astype(int)
    return out


def _band_totals(touches: pd.DataFrame) -> dict:
    """rz/i10/gl touch+td totals for an arbitrary already-filtered frame —
    used for the extra.qb_rz / extra.unclassified summary objects."""
    d: dict = {}
    for label, zone_max in _BANDS:
        band = touches[touches["yards_to_goal"] <= zone_max]
        d[f"{label}_touches"] = int(len(band))
        d[f"{label}_rush_touches"] = int((band["touch_type"] == "rush").sum())
        d[f"{label}_target_touches"] = int(band["touch_type"].isin(_PASS_TOUCH_TYPES).sum())
        d[f"{label}_tds"] = int(band["own_touchdown"].sum())
    return d


def _td_attribution_diagnostics(td_play_ids: set[str], touches: pd.DataFrame) -> dict:
    """
    How many offensive-TD plays (from /plays, spec §8a) matched a real
    rush/reception touch row. Post-§8a, `unmatched` should be ~0 — every
    rushing/passing TD has a `Rush` or `Reception` stat row on its play.
    A non-trivial `unmatched` count means the /plays -> /plays/stats join
    (or the offensive-TD play-type list) has drifted and needs a look.
    """
    if touches.empty:
        scoreable_play_ids: set[str] = set()
    else:
        scoreable = touches[touches["touch_type"].isin(_SCOREABLE_TOUCH_TYPES)]
        scoreable_play_ids = set(scoreable["play_id"].dropna().astype(str))
    matched = td_play_ids & scoreable_play_ids
    unmatched = td_play_ids - scoreable_play_ids
    return {
        "td_plays": len(td_play_ids),
        "matched_to_a_touch": len(matched),
        "unmatched": len(unmatched),
        "unmatched_sample": sorted(unmatched)[:10],
    }


def stat_type_distribution(play_stats: list[dict]) -> dict:
    """Count of every distinct `statType` string in the raw pull — how
    CFBD actually encodes plays, so a smoke run can confirm the touch /
    TD stat-type assumptions in spec §8 against real data."""
    out: dict = {}
    for r in play_stats:
        k = str(r.get("statType"))
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


# --------------------------------------------------------------------------
# Aggregation A — cfb_player_redzone_weekly  (TD Opportunity, §2)
# --------------------------------------------------------------------------
def aggregate_redzone_game_cfb(
    play_stats: list[dict],
    games: list[dict],
    raw_pos_lookup: dict[str, str],
    td_play_ids: set[str],
    *,
    season: int,
    week: int,
) -> tuple[list[dict], dict]:
    """
    One row per (player_id, season, week) with red-zone band touch/TD
    counts and the player's share of his offense's red-zone touches.

    `td_play_ids` — offensive-TD playIds from /plays (spec §8a); a touch
    on one of those plays that is a rush or reception scored.

    Rows are emitted for RB / WR / TE and for players not resolvable to
    one of those (position_group = NULL, `extra.unresolved` /
    `extra.position_raw` set — never dropped, Step 1 Q3). QB red-zone
    rushing is NOT emitted as typed rows (Step 1 Q4) — it is summarised
    per offense into `extra.qb_rz`, attached to that offense's skill rows
    and returned in diagnostics.
    """
    from roster import POSITION_GROUPS  # local import: avoids a module-load cycle in tests

    touches = _touches(play_stats, td_play_ids)
    diagnostics: dict = {
        "aggregation": "cfb_player_redzone_weekly",
        "season": season, "week": week,
        "touch_rows": int(len(touches)),
        "td_attribution": _td_attribution_diagnostics(td_play_ids, touches),
        "stat_type_distribution": stat_type_distribution(play_stats),
    }
    if touches.empty:
        diagnostics["note"] = "no Rush/Target touch rows in the input"
        return [], diagnostics

    name_map = team_id_map_from_games(games)

    touches = touches.copy()
    touches["raw_position"] = touches["athlete_id"].map(raw_pos_lookup)
    touches["position_group"] = touches["raw_position"].where(
        touches["raw_position"].isin(POSITION_GROUPS)
    )
    touches["is_qb"] = touches["raw_position"] == "QB"
    touches["team_id"] = touches["team"].map(name_map)
    touches["opponent_team_id"] = touches["opponent"].map(name_map)

    unresolved_ids = sorted(set(touches.loc[touches["raw_position"].isna(), "athlete_id"]))
    team_name_misses = sorted(set(touches.loc[touches["team_id"].isna(), "team"].dropna()))

    # ---- QB red-zone rushing summary, per offense (team_id) --------------
    qb = touches[touches["is_qb"]]
    qb_rz_by_team: dict[int, dict] = {}
    qb_rz_orphans: list[dict] = []
    for tid, grp in qb.groupby("team_id", dropna=True):
        totals = _band_totals(grp)
        totals["game_id"] = _single(grp["game_id"])
        totals["athlete_ids"] = sorted(set(grp["athlete_id"]))
        qb_rz_by_team[int(tid)] = totals

    # ---- typed rows: RB/WR/TE + NULL-position (non-QB) ------------------
    scored = touches[~touches["is_qb"]]
    keys = ["athlete_id"]
    bands = _merge_bands(scored, keys)

    # team red-zone touch totals per offense — the denominator for
    # rz_touch_share. Step 1 Q5 (confirmed): "sum of Rush + Target rows at
    # yardsToGoal <= 20 for that offense that game" — the FULL touch pool,
    # QB keepers and unclassified touches included. A skill player's share
    # is therefore his share of the offense's entire red-zone workload, not
    # just the non-QB slice (matters for option / mobile-QB offenses — the
    # honest number is the smaller one).
    team_rz = (
        touches[touches["yards_to_goal"] <= RED_ZONE]
        .groupby("team_id", dropna=False)
        .size()
        .to_dict()
    )

    # per-athlete context (team, opponent, game, name, position) — constant
    # within an athlete-week in practice; take the modal / first value and
    # note any genuine split in extra.
    ctx = (
        scored.sort_values("game_id")
        .groupby("athlete_id", dropna=False)
        .agg(
            team=("team", _first),
            team_id=("team_id", _first),
            opponent=("opponent", _first),
            opponent_team_id=("opponent_team_id", _first),
            game_id=("game_id", _first),
            player_name=("athlete_name", _first),
            position_group=("position_group", _first),
            raw_position=("raw_position", _first),
            conference=("conference", _first),
            n_games=("game_id", lambda s: s.nunique()),
        )
        .reset_index()
    )

    # inner join: `ctx` is built over every non-QB touch (incl. touches
    # outside the 20 — e.g. a 45-yard catch), `bands` only over athletes
    # with a real red-zone-band touch. Only the latter get a row —
    # matches nfl/redzone.py.aggregate_redzone_game, whose `rz` band is
    # its base frame.
    merged = ctx.merge(bands, on="athlete_id", how="inner")
    for c in merged.columns:
        if c.endswith(("_touches", "_tds")):
            merged[c] = merged[c].fillna(0).astype(int)

    rows: list[dict] = []
    for r in merged.to_dict("records"):
        team_id = _int_or_none(r["team_id"])
        trz = int(team_rz.get(r["team_id"], 0)) if r["team_id"] is not None else 0
        rz_touches = int(r["rz_touches"])
        share = round(rz_touches / trz, 3) if trz else None

        extra: dict = {
            "stat_type_row_counts": {
                "rush": int(r["rz_rush_touches"]),
                "target": int(r["rz_target_touches"]),
            },
            "conference": r.get("conference"),
        }
        if r.get("raw_position") is None:
            extra["unresolved"] = True
        elif r.get("position_group") is None:
            extra["position_raw"] = r.get("raw_position")
        if int(r.get("n_games", 1) or 1) > 1:
            extra["multi_game_week"] = int(r["n_games"])
        if team_id is not None and team_id in qb_rz_by_team:
            extra["qb_rz"] = qb_rz_by_team[team_id]

        rows.append(
            {
                "player_id": r["athlete_id"],
                "season": season,
                "week": week,
                "game_id": _int_or_none(r["game_id"]),
                "team_id": team_id,
                "team": _str_or_none(r["team"]),
                "opponent_team_id": _int_or_none(r["opponent_team_id"]),
                "opponent": _str_or_none(r["opponent"]),
                "player_name": _str_or_none(r["player_name"]),
                "position_group": _str_or_none(r["position_group"]),
                "rz_touches": rz_touches,
                "rz_rush_touches": int(r["rz_rush_touches"]),
                "rz_target_touches": int(r["rz_target_touches"]),
                "rz_tds": int(r["rz_tds"]),
                "i10_touches": int(r["i10_touches"]),
                "i10_rush_touches": int(r["i10_rush_touches"]),
                "i10_target_touches": int(r["i10_target_touches"]),
                "i10_tds": int(r["i10_tds"]),
                "gl_touches": int(r["gl_touches"]),
                "gl_rush_touches": int(r["gl_rush_touches"]),
                "gl_target_touches": int(r["gl_target_touches"]),
                "gl_tds": int(r["gl_tds"]),
                "team_rz_touches": trz or None,
                "rz_touch_share": share,
                "extra": extra,
            }
        )

    # orphan QB summaries — a team whose ONLY red-zone participants were
    # QBs, so extra.qb_rz has no skill row to ride on. Surfaced here so a
    # smoke run notices; not persisted in v1 (nothing consumes qb_rz yet).
    teams_with_rows = {row["team_id"] for row in rows if row["team_id"] is not None}
    for tid, totals in qb_rz_by_team.items():
        if tid not in teams_with_rows:
            qb_rz_orphans.append({"team_id": tid, **totals})

    rows.sort(key=lambda x: (-(x["rz_touches"]), x["player_id"]))

    diagnostics.update(
        {
            "rows": len(rows),
            "position_group_counts": _value_counts([x["position_group"] for x in rows]),
            "unresolved_athlete_ids": unresolved_ids[:50],
            "unresolved_athlete_count": len(unresolved_ids),
            "team_name_id_misses": team_name_misses,
            "qb_rz_teams": len(qb_rz_by_team),
            "qb_rz_orphans": qb_rz_orphans,
        }
    )
    return rows, diagnostics


# --------------------------------------------------------------------------
# Aggregation B — cfb_defense_redzone_allowed_weekly  (Situation, §3)
# --------------------------------------------------------------------------
def aggregate_redzone_allowed_cfb(
    play_stats: list[dict],
    games: list[dict],
    raw_pos_lookup: dict[str, str],
    td_play_ids: set[str],
    *,
    season: int,
    week: int,
) -> tuple[list[dict], dict]:
    """
    One row per (defense team_id, position_group, season, week) with
    red-zone band touch/TD counts ALLOWED to that position group.

    `td_play_ids` — offensive-TD playIds from /plays (spec §8a).

    Only RB / WR / TE rows are emitted (NFL parity — the inner join in
    nfl/redzone.py.aggregate_redzone_allowed). Touches with no resolvable
    RB/WR/TE position (QB scrambles + unrostered) are pooled into
    `extra.unclassified` on that defense's rows and into diagnostics
    (Step 1 Q3) rather than written as a NULL-position row.
    """
    from roster import POSITION_GROUPS

    touches = _touches(play_stats, td_play_ids)
    diagnostics: dict = {
        "aggregation": "cfb_defense_redzone_allowed_weekly",
        "season": season, "week": week,
        "touch_rows": int(len(touches)),
    }
    if touches.empty:
        diagnostics["note"] = "no Rush/Target touch rows in the input"
        return [], diagnostics

    name_map = team_id_map_from_games(games)
    touches = touches.copy()
    touches["raw_position"] = touches["athlete_id"].map(raw_pos_lookup)
    touches["position_group"] = touches["raw_position"].where(
        touches["raw_position"].isin(POSITION_GROUPS)
    )
    # the DEFENSE is the play row's `opponent`; the offense faced is `team`
    touches["def_team_id"] = touches["opponent"].map(name_map)
    touches["off_team_id"] = touches["team"].map(name_map)

    def_name_misses = sorted(set(touches.loc[touches["def_team_id"].isna(), "opponent"].dropna()))

    classified = touches[touches["position_group"].notna()]
    unclassified = touches[touches["position_group"].isna()]

    keys = ["def_team_id", "position_group"]
    bands = _merge_bands(classified, keys)

    ctx = (
        classified.sort_values("game_id")
        .groupby(keys, dropna=False)
        .agg(
            team=("opponent", _first),          # defense school string
            game_id=("game_id", _first),
            opponent=("team", _first),           # offense faced
            off_team_id=("off_team_id", _first),
            n_games=("game_id", lambda s: s.nunique()),
        )
        .reset_index()
    )
    # inner join, same reasoning as aggregate_redzone_game_cfb: a
    # (defense, position) pair with only out-of-red-zone touches allowed
    # gets no row (NFL parity).
    merged = ctx.merge(bands, on=keys, how="inner")
    for c in merged.columns:
        if c.endswith(("_touches", "_tds")):
            merged[c] = merged[c].fillna(0).astype(int)

    # per-defense unclassified (QB scramble + unrostered) RZ summary
    unclassified_by_def: dict[int, dict] = {}
    qb_allowed_by_def: dict[int, dict] = {}
    for tid, grp in unclassified.groupby("def_team_id", dropna=True):
        unclassified_by_def[int(tid)] = _band_totals(grp)
        qb_grp = grp[grp["raw_position"] == "QB"]
        if len(qb_grp):
            qb_allowed_by_def[int(tid)] = _band_totals(qb_grp)

    rows: list[dict] = []
    for r in merged.to_dict("records"):
        def_id = _int_or_none(r["def_team_id"])
        extra: dict = {
            "stat_type_row_counts": {
                "rush": int(r["rz_rush_touches"]),
                "target": int(r["rz_target_touches"]),
            }
        }
        if int(r.get("n_games", 1) or 1) > 1:
            extra["multi_game_week"] = int(r["n_games"])
        if def_id is not None and def_id in unclassified_by_def:
            extra["unclassified"] = unclassified_by_def[def_id]
        if def_id is not None and def_id in qb_allowed_by_def:
            extra["qb_rz_allowed"] = qb_allowed_by_def[def_id]

        rows.append(
            {
                "team_id": def_id,
                "team": _str_or_none(r["team"]),
                "position_group": _str_or_none(r["position_group"]),
                "season": season,
                "week": week,
                "game_id": _int_or_none(r["game_id"]),
                "opponent_team_id": _int_or_none(r["off_team_id"]),
                "opponent": _str_or_none(r["opponent"]),
                "rz_touches_allowed": int(r["rz_touches"]),
                "rz_rush_touches_allowed": int(r["rz_rush_touches"]),
                "rz_target_touches_allowed": int(r["rz_target_touches"]),
                "rz_tds_allowed": int(r["rz_tds"]),
                "i10_touches_allowed": int(r["i10_touches"]),
                "i10_rush_touches_allowed": int(r["i10_rush_touches"]),
                "i10_target_touches_allowed": int(r["i10_target_touches"]),
                "i10_tds_allowed": int(r["i10_tds"]),
                "gl_touches_allowed": int(r["gl_touches"]),
                "gl_rush_touches_allowed": int(r["gl_rush_touches"]),
                "gl_target_touches_allowed": int(r["gl_target_touches"]),
                "gl_tds_allowed": int(r["gl_tds"]),
                "extra": extra,
            }
        )

    rows.sort(key=lambda x: (str(x["team"]), str(x["position_group"])))

    diagnostics.update(
        {
            "rows": len(rows),
            "position_group_counts": _value_counts([x["position_group"] for x in rows]),
            "defenses": len({x["team_id"] for x in rows}),
            "defense_name_id_misses": def_name_misses,
            "unclassified_touch_rows": int(len(unclassified)),
        }
    )
    return rows, diagnostics


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _first(s: pd.Series):
    s = s.dropna()
    return s.iloc[0] if len(s) else None


def _single(s: pd.Series):
    vals = sorted(set(s.dropna()))
    return _int_or_none(vals[0]) if vals else None


def _int_or_none(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _str_or_none(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return str(v)


def _value_counts(values: list) -> dict:
    out: dict = {}
    for v in values:
        k = "NULL" if v is None else str(v)
        out[k] = out.get(k, 0) + 1
    return out
