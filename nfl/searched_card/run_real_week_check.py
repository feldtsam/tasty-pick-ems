#!/usr/bin/env python3
"""
Searched Card writer — offline real-week check (Stage 1d, Task 2).

STAGE 1 DELIVERABLE, design-and-tests only. See searched_card_flags.py --
nothing here is wired into any real pipeline path, and running this script
does not flip SEARCHED_CARD_WRITER_ENABLED or touch any other package file.

WHAT THIS IS: a local, offline sanity check of evidence_price_classifier.
classify_evidence_vs_price against one real week's REAL exported data, once
a human has run real_week_export.sql (this same directory, Task 1) against
the real tastypickems database and saved the result as a CSV. This script
never queries a database and never makes a network call of any kind -- it
reads exactly the one local file path given on the command line.

ZERO IMPORTS FROM THE REST OF nfl/, AND NO OTHER IMPORT FROM THIS PACKAGE
EITHER, ON PURPOSE -- the only thing imported from this repo at all is
classify_evidence_vs_price itself, imported directly from
evidence_price_classifier.py (this same directory). Everything else used
here is the Python standard library. This matches evidence_price_
classifier.py's own "zero imports from the rest of nfl/" discipline (see
that module's docstring for why), extended one step further here: even
searched_card_citable_fields.py's field list is not imported, so this
script's own column handling has to stand on its own, generic logic rather
than on a hardcoded or imported field list that could silently drift out of
sync with the real export.

STRUCTURE (for Task 3's benefit): load_pool_from_csv() below is a separate,
importable, pure function with no side effects (no prints, no randomness,
no classifier calls) -- it turns a CSV path into a list[dict] pool, nothing
more. Task 3's test imports and calls this function directly, against a
tiny synthetic CSV, without running the rest of this script or needing a
real exported CSV. Everything else in this file (the CLI driver: run(),
main(), the print helpers) is kept separate from that parsing logic for
exactly that reason.

HOW NUMERIC PARSING WORKS: every CSV cell is either a known-always-text
identity field (player_id, player_name, posteam, position_group -- kept as a
string, or None if blank, never float-cast even if it happens to look
numeric) or else attempted as float(); a value that fails to parse as a
float (any other real text field, e.g. roof) is kept as the raw string, and
a blank/missing cell becomes None either way. This generic approach
deliberately avoids hardcoding or importing the full
SEARCHED_CARD_TOP_LEVEL_CITABLE_FIELDS list (see "ZERO IMPORTS" above) --
whatever columns real_week_export.sql actually produced (today's 39 citable
fields, tpe_score/market_value_score, consensus_price_american/
consensus_implied_probability, and identity) are handled correctly without
this script needing its own separate, driftable copy of that column list.

RANDOM SAMPLING: `random.seed(RANDOM_SEED)` is called once, immediately
before the one random.sample() call this script makes, so the "random 10"
non-favorite players chosen for inspection are the SAME 10 every time this
script is run against the same CSV -- a fixed seed (42, documented here, not
hidden), not a silently-different sample run to run.

Usage:
    python run_real_week_check.py /path/to/exported_week.csv
"""
import argparse
import csv
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evidence_price_classifier import classify_evidence_vs_price  # noqa: E402 -- the ONLY import from this repo

RANDOM_SEED = 42  # fixed seed -- see module docstring's "RANDOM SAMPLING" note
SAMPLE_SIZE = 10

# Identity fields that must never be float-cast, even if a particular
# player_id/name/posteam/position string happens to look numeric (e.g. a
# purely-digit player_id would otherwise silently become a float).
# STAGE 1e: "team" -> "posteam" -- matches real_week_export.sql's own
# Stage 1e fix (was aliased "AS team", now exported under its real,
# SEARCHED_CARD_TOP_LEVEL_CITABLE_FIELDS-matching name, "posteam").
_ALWAYS_TEXT_FIELDS = {"player_id", "player_name", "posteam", "position_group"}


def load_pool_from_csv(path) -> list:
    """
    Pure, importable CSV -> pool parsing, no side effects (no printing, no
    randomness, no classifier calls) -- see module docstring's "STRUCTURE"
    note for why this is split out from the CLI driver below.

    Reads `path` with csv.DictReader (same library/style as this package's
    own test_example_cards.py) and returns one dict per row, suitable
    directly as the `pool` argument to evidence_price_classifier.
    classify_evidence_vs_price (and, for any row that itself has every
    REQUIRED_ROW_FIELDS value present, as the `row` argument too).

    Per field:
      - blank/missing cell -> None, for every field, identity included.
      - a field in _ALWAYS_TEXT_FIELDS -> kept as the raw string (never
        float-cast), so a numeric-looking player_id/name/posteam/position is
        never silently turned into a float.
      - everything else -> float(value) if that succeeds, else the raw
        string unchanged. This means a genuinely missing/blank
        consensus_price_american becomes None (handled, not an error) and
        is excluded from classify_evidence_vs_price's own price-ranking
        population, exactly the way that function already excludes any pool
        member missing consensus_price_american -- see that function's own
        docstring. A text field this script doesn't know about by name
        (e.g. roof: "dome") simply fails the float() attempt and is kept as
        the real string, with no special-casing needed for it here. A
        numeric-looking player_id/name/posteam/position is never silently
        turned into a float, because those four are always in
        _ALWAYS_TEXT_FIELDS regardless of what they look like.

    Never raises on a missing/blank numeric cell -- only a genuinely
    malformed CSV (not a dict-shaped row at all) would raise, the same way
    csv.DictReader itself would.
    """
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))

    pool = []
    for raw_row in rows:
        row = {}
        for key, value in raw_row.items():
            if value is None or value.strip() == "":
                row[key] = None
                continue
            if key in _ALWAYS_TEXT_FIELDS:
                row[key] = value
                continue
            try:
                row[key] = float(value)
            except ValueError:
                row[key] = value
        pool.append(row)
    return pool


def _identity(row: dict) -> str:
    """Whatever identity field the real export actually provided -- prefers
    player_name, falls back to player_id, falls back to a placeholder if
    somehow neither is present (should not happen against a real export,
    but this script never crashes on display formatting alone)."""
    name = row.get("player_name")
    pid = row.get("player_id")
    if name and pid:
        return f"{name} ({pid})"
    if name:
        return str(name)
    if pid:
        return str(pid)
    return "<unknown player>"


def _print_classification(row: dict, result: dict) -> None:
    odds = row.get("consensus_price_american")
    odds_str = f"{odds:+.0f}" if isinstance(odds, float) else str(odds)
    print(
        f"  {_identity(row):40} odds={odds_str:>7}  "
        f"implied_prob%={result['implied_probability_pct']:>6}  "
        f"evidence_rank%={result['evidence_rank_pct']:>6}  "
        f"price_rank%={result['price_rank_pct']:>6}  gap={result['gap']:>6}  "
        f"label={result['label']:<22} evidence_vs_price={result['evidence_vs_price']}"
    )


def _classify_and_report(rows: list, pool: list, label_counts: dict, fav_label_counts: dict = None) -> None:
    for row in rows:
        try:
            result = classify_evidence_vs_price(row, pool)
        except ValueError as exc:
            print(f"  SKIPPED ({exc}): {_identity(row)}")
            continue
        _print_classification(row, result)
        label_counts[result["label"]] = label_counts.get(result["label"], 0) + 1
        if fav_label_counts is not None:
            fav_label_counts[result["label"]] = fav_label_counts.get(result["label"], 0) + 1


def run(csv_path: str) -> None:
    pool = load_pool_from_csv(csv_path)
    total = len(pool)
    priced = [r for r in pool if r.get("consensus_price_american") is not None]

    print(f"Pool size (total rows): {total}")
    print(f"Players with a usable price: {len(priced)}")

    favorites = [r for r in priced if r["consensus_price_american"] < 0]
    others = [r for r in priced if r["consensus_price_american"] >= 0]

    random.seed(RANDOM_SEED)  # fixed seed -- see module docstring's "RANDOM SAMPLING" note
    sample = random.sample(others, min(SAMPLE_SIZE, len(others)))

    print(f"\nFavorites (negative odds, real): {len(favorites)}")
    print(f"Random sample from the rest (seed={RANDOM_SEED}): {len(sample)} of {len(others)}")

    label_counts: dict = {}
    fav_label_counts: dict = {}

    print("\n--- Favorites (negative consensus_price_american) ---")
    _classify_and_report(favorites, pool, label_counts, fav_label_counts)

    print(f"\n--- Random sample of {len(sample)} (seed={RANDOM_SEED}) ---")
    _classify_and_report(sample, pool, label_counts)

    print("\nLabel counts (favorites + sample):")
    for label in sorted(label_counts):
        print(f"  {label}: {label_counts[label]}")

    print("\nLabel counts (favorites only):")
    if fav_label_counts:
        for label in sorted(fav_label_counts):
            print(f"  {label}: {fav_label_counts[label]}")
    else:
        print("  (no favorites classified)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Offline real-week check for evidence_price_classifier.classify_evidence_vs_price. "
            "Reads a CSV exported with real_week_export.sql; makes no network or DB call."
        )
    )
    parser.add_argument("csv_path", help="Path to the exported CSV (see real_week_export.sql).")
    args = parser.parse_args()
    run(args.csv_path)


if __name__ == "__main__":
    main()
