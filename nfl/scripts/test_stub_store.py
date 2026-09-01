"""
Tests scripts/stub_store.py — the nfl_stub_weeks persistence helpers
(Phase A of the NFL weekly-automation plan).

Covers the pure shape/unpack round trip, the reconciled-row filter, the
DB-managed-column drop, NaN handling, the signed-POST wrappers (with
requests.post monkeypatched — no network), the mark_reconciled payload
shape, and the headline fidelity check: curate_nfl_shelves() produces
byte-identical output from a table-backed frame vs. the original CSV.

Run: python3 nfl/scripts/test_stub_store.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

import numpy as np
import pandas as pd

import stub_store
from stub_store import (
    STUB_WEEKS_TYPED_COLUMNS,
    mark_stub_week_reconciled,
    read_stub_week_rows,
    rows_to_stub_frame,
    shape_stub_rows,
    stub_week_snapshot,
    write_stub_rows,
)

STUB_CSV = Path(__file__).resolve().parent.parent / "data" / "stub_weeks" / "2026_wk1.csv"


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    return condition


def _toy_frame():
    """A tiny frame with the shape build_stub_week() produces: the typed
    columns, a couple of extra columns, and a real NaN."""
    return pd.DataFrame(
        [
            {
                "player_id": "00-0000001", "season": 2026, "week": 3, "posteam": "KC",
                "position_group": "WR", "td_opportunity": 71.2, "role_momentum": 55.0,
                "situation": 60.1, "market_value_score": np.nan, "tpe_score": 64.4,
                "player_name": "Toy Receiver", "rz_touches": 4, "some_pct": np.nan,
            },
            {
                "player_id": "00-0000002", "season": 2026, "week": 3, "posteam": "KC",
                "position_group": "RB", "td_opportunity": 40.0, "role_momentum": np.nan,
                "situation": 12.0, "market_value_score": 33.0, "tpe_score": 29.1,
                "player_name": "Toy Back", "rz_touches": 9, "some_pct": 0.5,
            },
        ]
    )


class _FakeResp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.text = body if isinstance(body, str) else json.dumps(body)


if __name__ == "__main__":
    results = []

    # ------------------------------------------------------------------
    # shape_stub_rows / rows_to_stub_frame — pure round trip
    # ------------------------------------------------------------------
    df = _toy_frame()
    rows = shape_stub_rows(df)

    results.append(check(
        "shape_stub_rows: one row per input row, each with exactly the typed columns + 'extra'",
        len(rows) == 2
        and all(set(r) == set(STUB_WEEKS_TYPED_COLUMNS) | {"extra"} for r in rows),
    ))
    results.append(check(
        "shape_stub_rows: non-typed columns land in extra, typed ones do not",
        rows[0]["extra"].get("player_name") == "Toy Receiver"
        and "td_opportunity" not in rows[0]["extra"]
        and "player_id" not in rows[0]["extra"],
    ))
    results.append(check(
        "shape_stub_rows: NaN serializes to None (to_json round trip), not a float nan",
        rows[0]["market_value_score"] is None and rows[0]["extra"]["some_pct"] is None,
    ))

    # simulate the read route echoing these rows back with the DB-managed
    # columns it adds at the top level (select("*"))
    echoed = []
    for r in rows:
        e = dict(r)
        e.update({"id": "uuid-x", "created_at": "2026-09-01T00:00:00Z",
                  "updated_at": "2026-09-01T00:00:00Z", "reconciled": False})
        echoed.append(e)
    back = rows_to_stub_frame(echoed)

    results.append(check(
        "rows_to_stub_frame: exact column parity with the original frame (no id/created_at/reconciled leak)",
        set(back.columns) == set(df.columns),
    ))
    results.append(check(
        "rows_to_stub_frame: values survive the round trip (typed + extra)",
        back.loc[back["player_id"] == "00-0000002", "tpe_score"].iloc[0] == 29.1
        and back.loc[back["player_id"] == "00-0000002", "some_pct"].iloc[0] == 0.5,
    ))
    results.append(check(
        "rows_to_stub_frame: a reconciled row is filtered out",
        len(rows_to_stub_frame([{**echoed[0], "reconciled": True}, echoed[1]])) == 1,
    ))
    results.append(check(
        "rows_to_stub_frame: all-reconciled (or empty) -> zero-row frame with the typed columns present",
        list(rows_to_stub_frame([]).columns) == STUB_WEEKS_TYPED_COLUMNS
        and len(rows_to_stub_frame([{**echoed[0], "reconciled": True}])) == 0,
    ))

    # ------------------------------------------------------------------
    # write_stub_rows / read_stub_week_rows / mark_stub_week_reconciled
    # — signed POST wrappers, requests.post monkeypatched
    # ------------------------------------------------------------------
    import lovable_forward

    captured = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        captured["url"] = url
        captured["body"] = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else data
        captured["sig"] = headers.get("X-Signature")
        return _FakeResp(200, {"ok": True, "received": 2, "upserted": 2, "stub_weeks": echoed})

    orig_post = lovable_forward.requests.post
    lovable_forward.requests.post = fake_post
    try:
        w = write_stub_rows(rows, "test-secret", write_url="https://example.test/write")
        results.append(check(
            "write_stub_rows: signs the exact serialized body and reports success",
            w["success"] is True
            and captured["sig"].startswith("sha256=")
            and captured["sig"] == lovable_forward.compute_signature("test-secret", captured["body"]),
        ))

        r = read_stub_week_rows(2026, 3, "test-secret", read_url="https://example.test/read")
        results.append(check(
            "read_stub_week_rows: unwraps {ok, rows} from the response body",
            r["ok"] is True and len(r["rows"]) == 2,
        ))
        results.append(check(
            "read_stub_week_rows: request body is exactly {season, week}",
            json.loads(captured["body"]) == {"season": 2026, "week": 3},
        ))

        mark_stub_week_reconciled(2026, 3, "test-secret", write_url="https://example.test/write")
        results.append(check(
            "mark_stub_week_reconciled: sends {\"mark_reconciled\": {season, week}}",
            json.loads(captured["body"]) == {"mark_reconciled": {"season": 2026, "week": 3}},
        ))

        # stub_week_snapshot: table read -> reconstructed frame
        snap = stub_week_snapshot(2026, 3, "test-secret", read_url="https://example.test/read")
        results.append(check(
            "stub_week_snapshot: returns the reconstructed frame (2 rows, original columns)",
            len(snap) == 2 and set(snap.columns) == set(df.columns),
        ))

        # a failed read must raise, not silently return empty
        def failing_post(url, data=None, headers=None, timeout=None):
            return _FakeResp(500, {"ok": False, "error": "boom"})

        lovable_forward.requests.post = failing_post
        raised = False
        try:
            stub_week_snapshot(2026, 3, "test-secret", read_url="https://example.test/read")
        except RuntimeError:
            raised = True
        results.append(check("stub_week_snapshot: a transport/query failure raises RuntimeError (not empty)", raised))
    finally:
        lovable_forward.requests.post = orig_post

    # ------------------------------------------------------------------
    # FIDELITY: curate_nfl_shelves() output is byte-identical whether the
    # week comes from the committed CSV or from a table round trip.
    # ------------------------------------------------------------------
    if not STUB_CSV.exists():
        print(f"\n(skipped the CSV fidelity check — {STUB_CSV.name} not present)")
    else:
        from curate_home_shelves import curate_nfl_shelves

        real = pd.read_csv(STUB_CSV)
        real_rows = shape_stub_rows(real)
        for rr in real_rows:
            rr.update({"id": "x", "created_at": "t", "updated_at": "t", "reconciled": False})
        table_backed = rows_to_stub_frame(real_rows)

        def _norm(x):
            return json.loads(json.dumps(x, default=str, sort_keys=True))

        csv_out = curate_nfl_shelves(real.copy(), 2026, 1)
        tbl_out = curate_nfl_shelves(table_backed.copy(), 2026, 1)
        results.append(check(
            f"curate_nfl_shelves: content_draft_rows identical CSV vs table-backed "
            f"({len(csv_out['content_draft_rows'])} rows)",
            _norm(csv_out["content_draft_rows"]) == _norm(tbl_out["content_draft_rows"]),
        ))
        results.append(check(
            "curate_nfl_shelves: shelf_signal_history_rows identical CSV vs table-backed",
            _norm(csv_out["shelf_signal_history_rows"]) == _norm(tbl_out["shelf_signal_history_rows"]),
        ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED — see above.")
        raise SystemExit(1)
