"""
Stage 1d, Task 3 -- tests for run_real_week_check.load_pool_from_csv, the
pure CSV-parsing function run_real_week_check.py's own module docstring
calls out as separate specifically so it could be tested without running
the whole script end-to-end or needing a real exported CSV.

Every CSV used below is tiny and synthetic, written to a real temp file via
pytest's own tmp_path fixture (load_pool_from_csv takes a path, not a file
object or a string of CSV text, so a real file is needed) -- none of this
depends on real_week_export.sql ever having been run against a real
database.

Run: cd nfl/searched_card && ../.venv/bin/python -m pytest test_run_real_week_check.py -v
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evidence_price_classifier import classify_evidence_vs_price  # noqa: E402
from run_real_week_check import load_pool_from_csv  # noqa: E402

_FIELDNAMES = [
    "player_id", "player_name", "posteam", "position_group",
    "td_opportunity", "role_momentum", "situation", "evidence_quality",
    "consensus_price_american", "tpe_score", "market_value_score",
    "roof",
]


def _write_csv(tmp_path: Path, rows: list) -> Path:
    path = tmp_path / "synthetic_week.csv"
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def _base_row(**overrides) -> dict:
    row = {
        "player_id": "00-0099001",
        "player_name": "Synthetic Player",
        "posteam": "SYN",
        "position_group": "RB",
        "td_opportunity": "55.0",
        "role_momentum": "60.0",
        "situation": "58.0",
        "evidence_quality": "72.0",
        "consensus_price_american": "-150",
        "tpe_score": "61.5",
        "market_value_score": "64.0",
        "roof": "dome",
    }
    row.update(overrides)
    return row


def test_numeric_fields_are_parsed_to_float(tmp_path):
    """Every numeric column -- including the classifier's own
    REQUIRED_ROW_FIELDS -- must come back as a real float, not a string,
    so the pool can be fed straight to classify_evidence_vs_price."""
    path = _write_csv(tmp_path, [_base_row()])
    pool = load_pool_from_csv(path)

    assert len(pool) == 1
    row = pool[0]
    for field in ("td_opportunity", "role_momentum", "situation", "evidence_quality",
                  "consensus_price_american", "tpe_score", "market_value_score"):
        assert isinstance(row[field], float), f"{field} was not parsed to float: {row[field]!r}"
    assert row["consensus_price_american"] == -150.0
    assert row["evidence_quality"] == 72.0


def test_identity_fields_stay_as_strings_even_when_numeric_looking(tmp_path):
    """player_id/player_name/posteam/position_group must never be float-cast,
    even if a player_id happens to be all-digits -- a float player_id would
    silently corrupt the real identifier."""
    path = _write_csv(tmp_path, [_base_row(player_id="123456", player_name="7", posteam="99", position_group="1")])
    pool = load_pool_from_csv(path)

    row = pool[0]
    assert row["player_id"] == "123456" and isinstance(row["player_id"], str)
    assert row["player_name"] == "7" and isinstance(row["player_name"], str)
    assert row["posteam"] == "99" and isinstance(row["posteam"], str)
    assert row["position_group"] == "1" and isinstance(row["position_group"], str)


def test_non_numeric_text_field_is_kept_as_a_string(tmp_path):
    """A real text field this script has no special knowledge of (e.g.
    roof) must survive untouched -- the generic float()-or-keep-the-string
    fallback, not a crash or a silently-dropped value."""
    path = _write_csv(tmp_path, [_base_row(roof="outdoors")])
    pool = load_pool_from_csv(path)
    assert pool[0]["roof"] == "outdoors"


def test_missing_price_becomes_none_without_raising(tmp_path):
    """A blank consensus_price_american (the normal, expected state for a
    player with no live market yet, per pool_size_report.md's own
    'thin/empty... is not a failure' finding) must parse to None, not raise
    and not become an empty string or NaN-as-string."""
    path = _write_csv(tmp_path, [_base_row(consensus_price_american="")])
    pool = load_pool_from_csv(path)  # must not raise
    assert pool[0]["consensus_price_american"] is None


def test_missing_price_row_is_excluded_from_the_price_ranking_population_only(tmp_path):
    """The real behavior this needs to prove: a pool member with no price
    still participates in the football-evidence ranking, but is excluded
    from classify_evidence_vs_price's own price-ranking population --
    exactly the exclusion that function's own docstring documents, now
    proven against a pool built by THIS script's own parsing function, not
    a hand-built dict."""
    rows = [
        _base_row(player_id="00-0000001", consensus_price_american=""),
        _base_row(player_id="00-0000002", consensus_price_american="-120"),
        _base_row(player_id="00-0000003", consensus_price_american="140"),
    ]
    path = _write_csv(tmp_path, rows)
    pool = load_pool_from_csv(path)

    priced = [r for r in pool if r.get("consensus_price_american") is not None]
    assert len(pool) == 3
    assert len(priced) == 2  # the blank-price row is excluded from the priced count

    # Run the classifier against the full pool (price-less member included,
    # exactly as a real caller would hand it in) for one of the priced rows.
    target = pool[1]
    result = classify_evidence_vs_price(target, pool)
    assert result["price_pool_size"] == 2  # only the two real prices count toward the price population


def test_pool_runs_through_the_real_classifier_without_error(tmp_path):
    """The end-to-end proof Task 3 asked for: a pool built entirely by
    load_pool_from_csv (no hand-built dicts) must be directly usable by
    classify_evidence_vs_price, for every row, with no exception and a
    well-formed result dict."""
    rows = [_base_row(player_id=f"00-000{i:04d}", consensus_price_american=str(-110 + i * 15)) for i in range(12)]
    path = _write_csv(tmp_path, rows)
    pool = load_pool_from_csv(path)

    assert len(pool) == 12
    for row in pool:
        result = classify_evidence_vs_price(row, pool)
        assert result["label"] in ("supports_expectation", "diverges_from_expectation", "insufficient_evidence")
        assert "evidence_vs_price" in result
        assert "gap" in result
        assert "implied_probability_pct" in result


# STAGE 1e, Task 2 -- the exact 12 columns real_week_export_slim.sql
# produces, nothing more (no roof, no i10_touches_trail3, no any of the
# other 27 fields the full real_week_export.sql exports). This proves
# load_pool_from_csv (and classify_evidence_vs_price against its output)
# needs nothing beyond the classifier's own REQUIRED_ROW_FIELDS plus the
# two display-only fields -- it does not hardcode or require the full
# SEARCHED_CARD_TOP_LEVEL_CITABLE_FIELDS set, matching this script's own
# "zero imports from that list" design (see module docstring).
_SLIM_FIELDNAMES = [
    "player_id", "player_name", "posteam", "position_group",
    "td_opportunity", "role_momentum", "situation", "evidence_quality",
    "tpe_score", "market_value_score",
    "consensus_price_american", "consensus_implied_probability",
]


def _write_slim_csv(tmp_path: Path, rows: list) -> Path:
    path = tmp_path / "synthetic_slim_week.csv"
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_SLIM_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def _slim_base_row(**overrides) -> dict:
    row = {
        "player_id": "00-0099002",
        "player_name": "Slim Synthetic Player",
        "posteam": "SYN",
        "position_group": "WR",
        "td_opportunity": "48.0",
        "role_momentum": "52.0",
        "situation": "45.0",
        "evidence_quality": "68.0",
        "tpe_score": "55.0",
        "market_value_score": "40.0",
        "consensus_price_american": "250",
        "consensus_implied_probability": "0.286",
    }
    row.update(overrides)
    return row


def test_slim_csv_with_only_the_12_exported_columns_loads_and_classifies(tmp_path):
    """real_week_export_slim.sql (Stage 1e, Task 2) exports only 12
    columns -- none of the other 27 citable fields the full export
    includes. This must work end to end with no special-casing: every
    REQUIRED_ROW_FIELDS value is present among these 12, so the pool
    loads and classifies exactly like the full export's pool does."""
    rows = [_slim_base_row(player_id=f"00-000{i:04d}", consensus_price_american=str(-110 + i * 15)) for i in range(12)]
    path = _write_slim_csv(tmp_path, rows)
    pool = load_pool_from_csv(path)

    assert len(pool) == 12
    # None of the full export's other 27 citable fields are present at all
    # -- proves this isn't silently working because they're there but
    # unused, they're genuinely absent from the CSV header.
    assert "roof" not in pool[0]
    assert "i10_touches_trail3" not in pool[0]

    for row in pool:
        result = classify_evidence_vs_price(row, pool)
        assert result["label"] in ("supports_expectation", "diverges_from_expectation", "insufficient_evidence")
        assert "evidence_vs_price" in result

    # tpe_score/market_value_score still load correctly as floats even in
    # the slim export (display-only, per real_week_export_slim.sql's own
    # framing) -- same proof as the full-export test above, against the
    # slim column set specifically.
    assert isinstance(pool[0]["tpe_score"], float)
    assert isinstance(pool[0]["market_value_score"], float)
    assert isinstance(pool[0]["consensus_implied_probability"], float)


def test_display_only_fields_are_present_but_not_required_by_the_classifier(tmp_path):
    """tpe_score/market_value_score must load correctly (for display, per
    Task 1/2's own framing) without the classifier needing or reading
    them -- classify_evidence_vs_price's result must be identical whether
    or not these two display-only fields are even present on the row,
    matching test_circularity.py's own proof that the classifier never
    reads either one."""
    with_scores = _base_row(player_id="00-0000010")
    path_with = _write_csv(tmp_path, [with_scores])
    pool_with = load_pool_from_csv(path_with)
    row_with = pool_with[0]
    assert row_with["tpe_score"] == 61.5
    assert row_with["market_value_score"] == 64.0

    row_without = {k: v for k, v in row_with.items() if k not in ("tpe_score", "market_value_score")}
    result_with = classify_evidence_vs_price(row_with, pool_with)
    result_without = classify_evidence_vs_price(row_without, pool_with)
    assert result_with == result_without
