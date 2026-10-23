"""
Defensive Trends step 1b: dynamic window wording, and thin-maturity
honesty in both the prose and the evidence classification.

Standalone script, same shape as the other tests here. No network.

    python3 test_defensive_trends_window_maturity.py
"""
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import defensive_trends as dt  # noqa: E402

results = []


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    results.append(bool(cond))
    return bool(cond)


def row(window=1.0, maturity="thin", completeness=100.0, games=3,
        last3=0.7, season_avg=0.5, delta=-6.2):
    """Modelled on the live 2026 Week 5 Defensive rows: games_played 3,
    completeness 100, confidence 100 -> maturity thin, window 1."""
    return pd.Series({
        "defteam": "CHI", "position_group": "TE", "season": 2026, "week": 5,
        "defensive_matchup_vulnerability": 38.0, "defensive_matchup_completeness": completeness,
        "recent_tds_allowed_pct": 41.0, "conversion_rate_allowed_pct": 44.0,
        "allowed_rz_tds_last3": last3, "allowed_rz_tds_season_avg": season_avg,
        "allowed_rz_touches_last3": 2.0, "allowed_rz_touches_season_avg": 2.4,
        "_delta": delta, "_trend_window": window, "_methodology_maturity": maturity,
        "_games_played": games,
    })


def thin_comp(r):
    return r["defensive_matchup_completeness"] < dt.CONFIG["thin_completeness"]


print("\n-- item 1: the prose states the window actually used")
r1 = row(window=1.0)
_, story1, agrees1 = dt._headline_and_story(r1, "growing-resistance", thin_comp(r1))
check("window 1: the trend sentence says 'last 1 game'", "last 1 game" in story1)
check("window 1: it no longer claims 'last 3 games'", "last 3 games" not in story1)
r3 = row(window=3.0, maturity="confirmed", games=10)
_, story3, _ = dt._headline_and_story(r3, "growing-resistance", thin_comp(r3))
check("window 3: unchanged, still says 'last 3 games'", "last 3 games" in story3)

print("\n-- item 1: the 'three weeks running' claim follows the window")
# td_agrees needs the raw TD numbers to move WITH the direction.
rv1 = row(window=1.0, last3=0.9, season_avg=0.5, delta=6.2)
_, sv1, agrees_v1 = dt._headline_and_story(rv1, "growing-vulnerability", thin_comp(rv1))
check("vulnerability + agreeing TD numbers sets td_agrees", agrees_v1 is True)
check("window 1: does NOT say 'Three weeks running'", "Three weeks running" not in sv1)
check("window 1: says so in one honest sentence instead",
      "a single game so far" in sv1)
rv3 = row(window=3.0, maturity="confirmed", games=10, last3=0.9, season_avg=0.5, delta=6.2)
_, sv3, agrees_v3 = dt._headline_and_story(rv3, "growing-vulnerability", thin_comp(rv3))
check("window 3: 'Three weeks running, not one loud Sunday.' is preserved",
      agrees_v3 and "Three weeks running, not one loud Sunday." in sv3)
check("window 3: the real 3-game TD average keeps its own 'last three' wording",
      "over the last three" in sv3)

print("\n-- item 1: a row with no window does not crash or invent one")
rn = row(window=float("nan"), maturity=None, games=1)
_, sn, _ = dt._headline_and_story(rn, "growing-resistance", thin_comp(rn))
check("NaN window renders a neutral phrase, no 'nan' in customer text",
      "nan" not in sn.lower() and "recent games" in sn)
check("_games_phrase is singular at 1, plural at 3",
      dt._games_phrase(1) == "1 game" and dt._games_phrase(3) == "3 games")

print("\n-- item 2: what counts as a still-developing read")
check("thin completeness alone -> developing",
      dt._is_developing_read(row(maturity="confirmed", completeness=40.0), True) is True)
check("thin MATURITY alone, completeness 100 -> developing (the live case)",
      dt._is_developing_read(row(maturity="thin", completeness=100.0), False) is True)
check("developing maturity, completeness 100 -> NOT developing (unchanged)",
      dt._is_developing_read(row(maturity="developing", completeness=100.0), False) is False)
check("confirmed maturity, completeness 100 -> NOT developing (unchanged)",
      dt._is_developing_read(row(maturity="confirmed", completeness=100.0), False) is False)
check("no maturity at all is not treated as thin",
      dt._is_developing_read(row(maturity=None, completeness=100.0), False) is False)

print("\n-- item 2: the prose no longer calls a thin read 'established'")
rt = row(maturity="thin", completeness=100.0)
items = dt._what_changed_for_row(rt, "growing-resistance", False, thin_comp(rt), dt.CONFIG)
sample = [i for i in items if i["label"] == "Sample size"][0]["observation"]
check("thin maturity: sample-size line says 'still a developing read'",
      "still a developing read" in sample)
check("thin maturity: it does NOT say 'established, well-populated'",
      "established" not in sample)
rc = row(maturity="confirmed", completeness=100.0, games=10, window=3.0)
sample_c = [i for i in dt._what_changed_for_row(rc, "growing-resistance", False, thin_comp(rc), dt.CONFIG)
            if i["label"] == "Sample size"][0]["observation"]
check("confirmed maturity: 'an established, well-populated read' is unchanged",
      "an established, well-populated read" in sample_c)

print("\n-- item 2: a thin read also picks up the story's own hedge")
_, st, _ = dt._headline_and_story(rt, "growing-resistance", thin_comp(rt))
check("thin maturity, completeness 100: the still-developing hedge now fires",
      "still-developing sample this season" in st)
_, sc, _ = dt._headline_and_story(rc, "growing-resistance", thin_comp(rc))
check("confirmed maturity: no hedge, unchanged", "still-developing sample" not in sc)

print("\n-- item 2: the classification ceiling")
C = dt.CONFIG
check("thin + 100/100 -> moderate, not strong",
      dt._evidence_classification_for_row(100.0, 100.0, C, "thin") == "moderate")
check("confirmed + 100/100 -> strong, unchanged",
      dt._evidence_classification_for_row(100.0, 100.0, C, "confirmed") == "strong")
check("developing + 100/100 -> strong, unchanged",
      dt._evidence_classification_for_row(100.0, 100.0, C, "developing") == "strong")
check("maturity omitted (default) -> strong, back-compatible",
      dt._evidence_classification_for_row(100.0, 100.0, C) == "strong")
check("the ceiling only lowers: thin + 50/50 is still limited",
      dt._evidence_classification_for_row(50.0, 50.0, C, "thin") == "limited")
check("thin at the moderate band is unaffected",
      dt._evidence_classification_for_row(65.0, 65.0, C, "thin") == "moderate")
check("exactly at the strong threshold, thin is capped",
      dt._evidence_classification_for_row(80.0, 80.0, C, "thin") == "moderate")
check("exactly at the strong threshold, confirmed still clears",
      dt._evidence_classification_for_row(80.0, 80.0, C, "confirmed") == "strong")

print("\n-- the maturity schedule itself is untouched")
sched = dt._methodology_for_games_played(pd.Series([1, 2, 3, 4, 9]), C)
check("games_played 3 is thin with a 1-game window",
      sched["_methodology_maturity"].iloc[2] == "thin" and sched["_trend_window"].iloc[2] == 1.0)
check("games_played 4 is developing with a 3-game window",
      sched["_methodology_maturity"].iloc[3] == "developing" and sched["_trend_window"].iloc[3] == 3.0)

print()
if all(results):
    print(f"All {len(results)} checks passed.")
else:
    print(f"{len(results) - sum(results)} of {len(results)} checks FAILED — see above.")
    raise SystemExit(1)
