"""
STICKINESS_PERCENTILE_SCALE: the percentile-scaled stickiness comparison,
the flag that gates it, and the rules that must NOT change with it.

Standalone script, same shape as the other tests here. No network, no API.

    python3 test_stickiness_scale.py
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE / "api"), str(HERE / "content_writer"), str(HERE / "scripts")]

import stickiness_scale  # noqa: E402
from curate_home_shelves import (  # noqa: E402
    SHELF_ORDER, STICKINESS_MARGIN, TREND_SHELVES, _compute_sticky_assignment, _trend_percentiles,
)

results = []


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    results.append(bool(cond))
    return bool(cond)


def sticky(cand_sig, cur_sig, *, cand_pct=None, cur_pct=None, scale=False,
           cand="WR Trends", cur="ATTD +700+", pending=None, count=0):
    return _compute_sticky_assignment(
        cand, cand_sig, cur, cur_sig, pending, count,
        candidate_percentile=cand_pct, current_percentile=cur_pct, percentile_scale=scale,
    )


print("\n-- the real Week 7 shape: raw cannot move, percentile can")
# role_momentum 49.0 vs tpe_score 46.6 -- the measured means. Raw gap +2.4,
# nowhere near the 20-point margin; on each shelf's own scale the same player
# is 88th percentile on the trend shelf and 41st on the odds band.
raw = sticky(49.0, 46.6)
check("raw scale: a real trend candidate stays pinned, nothing pending",
      raw["home_shelf"] == "ATTD +700+" and raw["pending_shelf"] is None)
pct = sticky(49.0, 46.6, cand_pct=88.0, cur_pct=41.0, scale=True)
check("percentile scale: the same player becomes pending (run 1 of 2)",
      pct["home_shelf"] == "ATTD +700+" and pct["pending_shelf"] == "WR Trends"
      and pct["pending_run_count"] == 1)
pct2 = sticky(49.0, 46.6, cand_pct=88.0, cur_pct=41.0, scale=True,
              pending="WR Trends", count=1)
check("percentile scale: the move fires on the SECOND consecutive run",
      pct2["home_shelf"] == "WR Trends" and pct2["pending_shelf"] is None
      and pct2["pending_run_count"] == 0)

print("\n-- the two-consecutive-runs rule is unchanged on both paths")
for scale, cp, rp in ((False, None, None), (True, 90.0, 10.0)):
    lab = "percentile" if scale else "raw"
    one = sticky(80.0, 40.0, cand_pct=cp, cur_pct=rp, scale=scale)
    check(f"{lab}: a clearing margin on run 1 only sets pending, never moves",
          one["home_shelf"] == "ATTD +700+" and one["pending_run_count"] == 1)
    two = sticky(80.0, 40.0, cand_pct=cp, cur_pct=rp, scale=scale,
                 pending="WR Trends", count=1)
    check(f"{lab}: run 2 moves and clears the pending state",
          two["home_shelf"] == "WR Trends" and two["pending_run_count"] == 0)
    miss = sticky(45.0, 44.0, cand_pct=51.0, cur_pct=50.0, scale=scale,
                  pending="WR Trends", count=1)
    check(f"{lab}: a missed margin RESETS the streak rather than part-crediting",
          miss["home_shelf"] == "ATTD +700+" and miss["pending_shelf"] is None
          and miss["pending_run_count"] == 0)
    same = _compute_sticky_assignment(
        "WR Trends", 50.0, "WR Trends", 50.0, None, 0,
        candidate_percentile=cp, current_percentile=rp, percentile_scale=scale)
    check(f"{lab}: candidate == current short-circuits with nothing pending",
          same["home_shelf"] == "WR Trends" and same["pending_shelf"] is None)
    gone = _compute_sticky_assignment(
        "WR Trends", 50.0, "ATTD +700+", None, None, 0,
        candidate_percentile=cp, current_percentile=None, percentile_scale=scale)
    check(f"{lab}: a prior shelf the player no longer qualifies for moves immediately",
          gone["home_shelf"] == "WR Trends" and gone["pending_run_count"] == 0)

print("\n-- the margin value itself is untouched")
check("exactly at the margin clears, on the percentile scale",
      sticky(0, 0, cand_pct=70.0, cur_pct=70.0 - STICKINESS_MARGIN, scale=True)["pending_run_count"] == 1)
check("one hundredth under the margin does not",
      sticky(0, 0, cand_pct=70.0, cur_pct=70.0 - STICKINESS_MARGIN + 0.01,
             scale=True)["pending_shelf"] is None)

print("\n-- flag on, but a percentile is missing: fall back to raw, never guess")
fb = sticky(80.0, 40.0, cand_pct=None, cur_pct=10.0, scale=True)
check("missing candidate percentile -> raw comparison still applies",
      fb["pending_shelf"] == "WR Trends" and fb["pending_run_count"] == 1)
fb2 = sticky(45.0, 44.0, cand_pct=99.0, cur_pct=None, scale=True)
check("missing current percentile -> raw comparison, so no spurious move",
      fb2["pending_shelf"] is None)

print("\n-- _trend_percentiles default is unchanged")
pools = {s: __import__("pandas").DataFrame({"player_id": [f"p{i}" for i in range(3)],
                                            "role_momentum": [10.0, 50.0, 90.0],
                                            "td_opportunity": [10.0, 50.0, 90.0],
                                            "tpe_score": [10.0, 50.0, 90.0]}) for s in SHELF_ORDER}
default = _trend_percentiles(pools)
check("default covers exactly the four trend shelves",
      set(default) == set(TREND_SHELVES))
allshelf = _trend_percentiles(pools, shelves=tuple(SHELF_ORDER))
check("an explicit shelves list covers every shelf, odds bands included",
      set(allshelf) == set(SHELF_ORDER))
check("the trend shelves' own values are identical either way",
      all(default[s] == allshelf[s] for s in TREND_SHELVES))

print("\n-- the flag")
saved = os.environ.get("STICKINESS_PERCENTILE_SCALE")
try:
    for value, expected in [(None, False), ("true", True), ("TRUE", False), ("1", False),
                            ("yes", False), ("", False), (" true", False)]:
        stickiness_scale.reset_cache()
        if value is None:
            os.environ.pop("STICKINESS_PERCENTILE_SCALE", None)
        else:
            os.environ["STICKINESS_PERCENTILE_SCALE"] = value
        check(f"env {value!r} -> {expected}",
              stickiness_scale.percentile_stickiness_enabled() is expected)
    stickiness_scale.reset_cache()
    os.environ["STICKINESS_PERCENTILE_SCALE"] = "true"
    stickiness_scale.percentile_stickiness_enabled()
    os.environ["STICKINESS_PERCENTILE_SCALE"] = "false"
    check("the read is cached, so one run cannot straddle a change",
          stickiness_scale.percentile_stickiness_enabled() is True)
    check("an explicit boolean overrides the environment",
          stickiness_scale.resolve(False) is False)
    check("None defers to the environment", stickiness_scale.resolve(None) is True)
finally:
    stickiness_scale.reset_cache()
    if saved is None:
        os.environ.pop("STICKINESS_PERCENTILE_SCALE", None)
    else:
        os.environ["STICKINESS_PERCENTILE_SCALE"] = saved

print()
if all(results):
    print(f"All {len(results)} checks passed.")
else:
    print(f"{len(results) - sum(results)} of {len(results)} checks FAILED — see above.")
    raise SystemExit(1)
