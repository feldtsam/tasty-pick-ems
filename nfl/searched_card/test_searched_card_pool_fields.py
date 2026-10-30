"""
Stage 2 — proves SEARCHED_CARD_POOL_FIELDS actually keeps a real week's
full pool under the request-body cap, against the real local week-7, 2026
data when it's present on this machine.

The two real CSV exports this reads (~/Downloads/week7_stub_prices_2026_
wk7.csv, ~/Downloads/week7_completeness_2026_wk7.csv) are never copied
into this repo -- they're machine-local, hand-exported fixtures from an
earlier investigation. SKIPPED, not failed, when either file is absent,
so this test suite stays green on any other machine or in CI.

Run: cd nfl/searched_card && ../.venv/bin/python -m pytest test_searched_card_pool_fields.py -v
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from searched_card_pool_fields import SEARCHED_CARD_POOL_FIELDS  # noqa: E402
from run_real_week_check import load_pool_from_csv  # noqa: E402

STUB_PRICES_CSV = os.path.expanduser("~/Downloads/week7_stub_prices_2026_wk7.csv")
COMPLETENESS_CSV = os.path.expanduser("~/Downloads/week7_completeness_2026_wk7.csv")

# Deliberately the ORIGINAL 256 KB bar, not whatever nfl/api/index.py's own
# SEARCHED_CARD_MAX_BODY_BYTES happens to be set to right now (512 KB as of
# this revision) -- the point of SEARCHED_CARD_POOL_FIELDS is to leave real
# headroom under the route's cap, however that cap is configured; holding
# this test to the narrower, original number is a stronger proof than
# holding it to whatever the cap currently is.
REQUEST_BODY_TARGET_BYTES = 256 * 1024

_CSVS_PRESENT = os.path.exists(STUB_PRICES_CSV) and os.path.exists(COMPLETENESS_CSV)


@pytest.mark.skipif(
    not _CSVS_PRESENT,
    reason="real week-7 2026 local CSV exports not present on this machine -- never committed to the repo",
)
def test_real_week7_pool_trimmed_to_pool_fields_stays_under_256kb():
    stub_rows = load_pool_from_csv(STUB_PRICES_CSV)
    completeness_rows = load_pool_from_csv(COMPLETENESS_CSV)
    completeness_by_id = {r["player_id"]: r for r in completeness_rows}

    pool = []
    for row in stub_rows:
        merged = dict(row)
        comp = completeness_by_id.get(row["player_id"])
        merged["td_opportunity_completeness"] = comp["td_opportunity_completeness"] if comp else None
        merged["role_momentum_completeness"] = comp["role_momentum_completeness"] if comp else None
        pool.append({key: merged.get(key) for key in SEARCHED_CARD_POOL_FIELDS})

    assert len(pool) > 0, "real export loaded zero rows -- the CSV exists but is empty, investigate separately"

    serialized = json.dumps({"pool": pool}, sort_keys=True, separators=(",", ":"))
    size = len(serialized.encode("utf-8"))

    assert size < REQUEST_BODY_TARGET_BYTES, (
        f"real week-7 pool ({len(pool)} rows), trimmed to SEARCHED_CARD_POOL_FIELDS "
        f"({SEARCHED_CARD_POOL_FIELDS}), serialized to {size} bytes -- expected under "
        f"{REQUEST_BODY_TARGET_BYTES}"
    )
