"""Build the Sutton replay fixture from real Make execution history.

HOW THE PAGES WERE FETCHED. Make's history API returns at most 50 rows per
request, newest first, with no truncation signal (SPEC.md "Data realities"
#1). The Make MCP tool is driven by the agent, not importable from Python,
so pagination was performed as a series of `executions_list` calls over
fixed, contiguous `[from, to]` windows sized to stay under the 50-row cap.
Every one of those raw responses is recorded verbatim in the Claude Code
session transcript, and this script reconstructs the fixture from them. That
keeps the build reproducible and auditable without re-hitting the API.

PIPELINE
  1. extract  raw pages from the session transcript  -> tests/fixtures/raw/
  2. verify   window coverage + no page hit the 50-row cap
  3. normalize into the SPEC.md input schema
  4. REDACT every free-text field (redact.py)
  5. write    tests/fixtures/make_history_<range>.json

Step 4 is not optional and not last-minute: raw/ holds unredacted secrets and
is gitignored; the redacted fixture is the only artifact that gets committed.

Usage:
    python3 sutton/build_fixture.py --extract    # transcript -> raw/
    python3 sutton/build_fixture.py --build      # raw/ -> fixture
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from normalize import (  # noqa: E402
    derive_ended_at as _derive_ended_at,
    is_execution as _is_execution,
    normalize_event as _normalize_event,
    normalize_execution as _normalize_execution,
)
from redact import redact_free_text  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(HERE, "tests", "fixtures", "raw")
FIXTURE_PATH = os.path.join(
    HERE, "tests", "fixtures", "make_history_2026-09-07_to_09-24.json"
)

WATCHED = {
    6186710: "NFL Picks Daily Generation",
    6241867: "NFL Grade Bookmarks Live",
    6152892: "NFL ATTD Price History Poller",
    6079077: "NFL Split 2",
}

WINDOW_FROM_MS = 1788739200000  # 2026-09-07T00:00:00Z
WINDOW_TO_MS = 1790294399000  # 2026-09-24T23:59:59Z
PAGE_CAP = 50


def _ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- step 1: extract ------------------------------------------------------


def _transcript_path() -> str:
    session = os.environ.get("SUTTON_TRANSCRIPT")
    if session:
        return session
    base = os.path.expanduser("~/.claude/projects/-Users-samfeldt-Claude-Code")
    candidates = sorted(
        glob.glob(os.path.join(base, "*.jsonl")), key=os.path.getmtime, reverse=True
    )
    if not candidates:
        raise SystemExit("no session transcript found; set SUTTON_TRANSCRIPT")
    return candidates[0]


def extract() -> None:
    path = _transcript_path()
    os.makedirs(RAW_DIR, exist_ok=True)

    calls: dict[str, dict] = {}
    written = 0
    scenarios_written = False

    for line in open(path, encoding="utf-8"):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        msg = rec.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                name = str(block.get("name", ""))
                if "executions_list" in name or "scenarios_list" in name:
                    calls[block["id"]] = {"name": name, "input": block.get("input") or {}}
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in calls:
                call = calls[block["tool_use_id"]]
                payload = block.get("content")
                text = (
                    "".join(p.get("text", "") for p in payload if isinstance(p, dict))
                    if isinstance(payload, list)
                    else str(payload)
                )
                try:
                    rows = json.loads(text)
                except Exception:
                    continue
                if "scenarios_list" in call["name"]:
                    if isinstance(rows, list) and rows and not scenarios_written:
                        with open(
                            os.path.join(RAW_DIR, "scenarios_list.json"), "w", encoding="utf-8"
                        ) as fh:
                            json.dump(rows, fh, indent=2)
                        scenarios_written = True
                    continue

                inp = call["input"]
                sid = inp.get("scenarioId")
                if sid not in WATCHED or inp.get("status") is not None:
                    continue  # status-filtered probes are partial views; skip
                frm = inp.get("from", 0)
                to = inp.get("to", 0)
                name = f"{sid}__{frm}__{to}.json"
                with open(os.path.join(RAW_DIR, name), "w", encoding="utf-8") as fh:
                    json.dump(
                        {"scenario_id": sid, "from": frm, "to": to, "rows": rows}, fh, indent=2
                    )
                written += 1

    print(f"transcript: {path}")
    print(f"raw pages written: {written}  (scenarios_list: {scenarios_written})")


# --- step 2: verify -------------------------------------------------------


def _load_pages(scenario_id: int) -> list[dict]:
    pages = []
    for path in sorted(glob.glob(os.path.join(RAW_DIR, f"{scenario_id}__*.json"))):
        with open(path, encoding="utf-8") as fh:
            page = json.load(fh)
        page["_path"] = os.path.basename(path)
        pages.append(page)
    return pages


def verify_coverage(scenario_id: int, pages: list[dict]) -> list[str]:
    """Assert the query windows cover the target range with no gap.

    A page that came back with exactly PAGE_CAP rows was silently clipped by
    Make (there is no truncation flag), so it cannot be used to prove
    coverage of its own window. Its rows are still real and still merged --
    a clipped page is a subset, never wrong data -- but coverage has to be
    established by narrower pages that came back under the cap.
    """
    problems = []
    if not pages:
        return [f"{scenario_id}: no raw pages"]

    clean = [p for p in pages if len(p["rows"]) < PAGE_CAP]
    clipped = [p for p in pages if len(p["rows"]) >= PAGE_CAP]
    for page in clipped:
        print(
            f"  note: {scenario_id} page {page['_path']} hit the {PAGE_CAP}-row cap; "
            f"rows kept, window not counted toward coverage"
        )
    if not clean:
        return [f"{scenario_id}: every page hit the {PAGE_CAP}-row cap"]

    spans = sorted((p["from"], p["to"]) for p in clean)
    covered_to = WINDOW_FROM_MS
    for start, end in spans:
        # `from`/`to` are inclusive, so a window starting at the previous
        # window's `to` + 1ms is contiguous, not a gap.
        if start > covered_to + 1:
            problems.append(
                f"{scenario_id}: coverage gap {_ms_to_iso(covered_to)} -> {_ms_to_iso(start)}"
            )
            covered_to = max(covered_to, end)
        else:
            covered_to = max(covered_to, end)
    if covered_to < WINDOW_TO_MS:
        problems.append(
            f"{scenario_id}: window ends early at {_ms_to_iso(covered_to)}, "
            f"need {_ms_to_iso(WINDOW_TO_MS)}"
        )
    return problems


# --- step 3: normalize ----------------------------------------------------
#
# The implementations live in normalize.py, which api/index.py also imports.
# They used to be duplicated here; a drift between the two would mean the rules
# were validated against one normalization and run in production against
# another.


def build() -> None:
    with open(os.path.join(RAW_DIR, "scenarios_list.json"), encoding="utf-8") as fh:
        scenarios_list = json.load(fh)
    current = {s["id"]: s for s in scenarios_list}

    all_problems: list[str] = []
    scenarios_out = []

    for sid, fallback_name in WATCHED.items():
        pages = _load_pages(sid)
        all_problems += verify_coverage(sid, pages)

        by_key: dict[str, dict] = {}
        for page in pages:
            for row in page["rows"]:
                key = row.get("imtId") or f"{row.get('type')}:{row.get('timestamp')}"
                by_key[key] = row

        executions, events = [], []
        for row in by_key.values():
            if _is_execution(row):
                executions.append(_normalize_execution(row))
            else:
                events.append(_normalize_event(row))

        executions.sort(key=lambda e: e["started_at"] or "")
        events.sort(key=lambda e: e["at"] or "")

        meta = current.get(sid, {})
        scenarios_out.append(
            {
                "scenario_id": sid,
                # SPEC.md "Data realities" #8: names come from the CURRENT
                # scenarios_list, never from historical execution rows
                # (6241867 was "Integration HTTP" before Sep 11).
                "name": (meta.get("name") or fallback_name).strip(),
                "is_active": bool(meta.get("isActive", True)),
                "is_paused": bool(meta.get("isPaused", False)),
                "executions": executions,
                "events": events,
            }
        )

    if all_problems:
        print("COMPLETENESS PROBLEMS:")
        for p in all_problems:
            print("  -", p)
        raise SystemExit(1)

    payload = {
        "collected_at": _ms_to_iso(WINDOW_TO_MS),
        "mode": "collect",
        "window": {"from": _ms_to_iso(WINDOW_FROM_MS), "to": _ms_to_iso(WINDOW_TO_MS)},
        "source": "Make executions_list, paged; see sutton/build_fixture.py",
        "redacted": True,
        "scenarios": scenarios_out,
    }

    # STEP 4 -- nothing reaches disk before this line.
    payload = redact_free_text(payload)

    os.makedirs(os.path.dirname(FIXTURE_PATH), exist_ok=True)
    with open(FIXTURE_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=False)

    print(f"fixture written: {FIXTURE_PATH}")
    for s in payload["scenarios"]:
        print(
            f"  {s['scenario_id']}  {s['name']:<32} "
            f"executions={len(s['executions']):>4}  events={len(s['events']):>3}"
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extract", action="store_true")
    ap.add_argument("--build", action="store_true")
    args = ap.parse_args()
    if args.extract:
        extract()
    if args.build:
        build()
    if not (args.extract or args.build):
        ap.print_help()


if __name__ == "__main__":
    main()
