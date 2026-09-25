"""Print the exact facts behind a signal at a given tick.

Used to explain replay results without guessing. Read-only.

    python3 sutton/diagnose.py 6152892 2026-09-08T02:00Z
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from checks import CONFIG, _scoped, classify_execution, evaluate  # noqa: E402
from replay import load_fixture  # noqa: E402


def main() -> None:
    scenario_id = int(sys.argv[1])
    now = datetime.fromisoformat(sys.argv[2].replace("Z", "+00:00"))

    payload = load_fixture()
    scenario = next(s for s in payload["scenarios"] if s["scenario_id"] == scenario_id)

    out = evaluate({**payload, "scenarios": [scenario]}, state={}, now=now)
    print(f"=== {scenario['name']} ({scenario_id}) at {sys.argv[2]}")
    print(f"status: {out['status']}")
    for sig in out["signals"]:
        print(f"\n  {sig['signal_id']}  tier={sig['tier']}  reason={sig.get('reason')}")
        print(f"  facts: {json.dumps(sig['facts'], indent=4)}")

    scoped = _scoped(scenario, now, CONFIG)
    print(f"\nlast config change: {scoped['last_change']}")
    pool = [
        e
        for e in scoped["post_edit_executions"]
        if e.get("run_type") == "auto" and classify_execution(e) == "success"
    ]
    print(f"post-edit successful auto runs: {len(pool)}")
    print("last 12 durations (ms):")
    for e in pool[-12:]:
        print(f"    {e['_at'].strftime('%m-%d %H:%M')}  {e['duration_ms']:>8}")

    manual = [e for e in scoped["all_executions"] if e.get("run_type") == "manual"]
    print(f"\nall manual runs ({len(manual)}):")
    for e in manual:
        windows = scoped["windows"]
        inside = any(s <= e["_at"] < en for s, en in windows)
        print(
            f"    {e['_at'].strftime('%m-%d %H:%M')}  status={e['status']}  "
            f"{'IN testing window (excluded)' if inside else 'counts for L2'}"
        )


if __name__ == "__main__":
    main()
