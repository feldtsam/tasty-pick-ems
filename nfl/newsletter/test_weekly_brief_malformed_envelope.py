"""
Tests the malformed-envelope handling and the revised reader-language
list.

The real failure, 2026-11-10 (brief_2026w9_20261110T022542): the Editor
Agent's retry returned the entire brief as JSON TEXT inside the
`sections` string instead of as structured fields. `sections` was a
15,210-character string, every consumer did `issue.get("sections", []) or
[]`, and a string is truthy and iterable -- so each pass walked it
CHARACTER BY CHARACTER and produced one malformed-section error per
character. 15,210 hard errors. Consequences:

  - a 416 KB markdown "draft" listing every character
  - a 1.6 MB console log (the exception message JSON-dumps the reports)
  - and had a third attempt existed, all 15,210 would have gone into its
    feedback

The content itself was fine; only the envelope was wrong. So the fix is
three-part: parse a stringified-but-valid sections array back (a
deterministic repair), bail out with ONE error for anything else, and cap
every display/transmission site so no future variant can flood them.

Also covers the reader-language list revision: tier / tiers / peer tier /
peer-tier removed (the app's own Market card is labelled "Implied
probability vs. peer tier", so it is reader vocabulary, and banning it
put the rule in conflict with the data), role-momentum / role momentum /
external opportunity / "i'm not given" / "i wasn't given" added.

No API call is made anywhere in this file.

Run: python3 nfl/newsletter/test_weekly_brief_malformed_envelope.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "api"))
sys.path.insert(0, str(HERE.parent / "content_writer"))

import run_weekly_brief as rwb  # noqa: E402
from run_weekly_editor_agent import (  # noqa: E402
    RETRY_ENVELOPE_INSTRUCTION,
    RETRY_INSTRUCTION,
    build_candidate_pool,
    build_retry_feedback,
)
from weekly_brief_shape import (  # noqa: E402
    ERROR_DISPLAY_LIMIT,
    READER_LANGUAGE_BANNED_PHRASES,
    cap_errors,
    check_reader_language,
    coerce_sections,
    complete_derivable_fields,
    enforce_one_treatment,
    finalize_weekly_brief,
)

FIXTURES = json.loads((HERE / "fixture_v2.json").read_text())
CANDIDATES = build_candidate_pool(FIXTURES)["candidate_story_objects"]
F1 = FIXTURES[0]["intelligence_story_id"]
REAL_BAD = HERE / "outputs" / "brief_2026w9_20261110T022542.raw.attempt2.json"


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    return bool(cond)


def good_sections():
    return [{
        "section_type": "big_one",
        "stories": [{
            "headline": "A lead", "body": "Body text.",
            "intelligence_story_ids": [F1], "player_ids": [], "pick_ids": [],
            "eps_scores": {k: 0 for k in (
                "significance", "evidence_strength", "betting_relevance",
                "novelty", "story_tension", "audience_relevance", "eps_total")},
        }],
        "cross_references": [],
    }]


def issue_with(sections):
    return {"issue_week": "t", "sections": sections,
            "watchlist_populated": False, "notes_for_human_reviewer": "n"}


def reader_issue(body, headline="h"):
    return {"sections": [{"section_type": "big_one", "cross_references": [],
                          "stories": [{"headline": headline, "body": body}]}]}


def phrases_hit(issue):
    out = set()
    for e in check_reader_language(issue):
        if "phrase " in e:
            out.add(e.split("phrase ", 1)[1].split(" (", 1)[0].strip("'\""))
    return out


def main():
    r = []

    # ==============================================================
    # 1a -- bail out with ONE error, never one per element
    # ==============================================================
    for bad, label in ((123, "an int"), (None, "None"), ({}, "a dict")):
        _, _, err = coerce_sections({"sections": bad})
        r.append(check(f"coerce_sections reports {label} as exactly one error", isinstance(err, str)))

    unparseable = issue_with("this is not json at all, just prose " * 500)
    _, repairs, hard = complete_derivable_fields(unparseable, CANDIDATES)
    r.append(check(
        "complete_derivable_fields: an unparseable string produces EXACTLY ONE error "
        f"(the real bug produced one per character — {len(unparseable['sections'])} here)",
        len(hard) == 1,
    ))
    r.append(check(
        "that one error says what is wrong and how to fix it",
        "not an array" in hard[0] and "structured fields" in hard[0],
    ))
    enforced, treat_repairs = enforce_one_treatment(unparseable)
    r.append(check("enforce_one_treatment: no crash, no repairs, nothing changed", treat_repairs == []))
    r.append(check(
        "check_reader_language: returns NOTHING rather than one error per character",
        check_reader_language(unparseable) == [],
    ))
    _, report = finalize_weekly_brief(unparseable, CANDIDATES)
    r.append(check(
        "finalize_weekly_brief end to end: 1 hard error, not thousands",
        len(report["hard_errors"]) == 1 and report["passed"] is False,
    ))

    # ==============================================================
    # 1c -- the deterministic repair
    # ==============================================================
    stringified = issue_with(json.dumps(good_sections()))
    sections, repair, err = coerce_sections(stringified)
    r.append(check(
        "a stringified-but-valid sections array is PARSED BACK, not rejected",
        err is None and isinstance(sections, list) and len(sections) == 1,
    ))
    r.append(check(
        "the repair is logged as parsed_stringified_sections",
        repair is not None and repair["action"] == "parsed_stringified_sections",
    ))
    issue_fixed, repairs, hard = complete_derivable_fields(stringified, CANDIDATES)
    r.append(check(
        "complete_derivable_fields applies the repair in place and then processes the content normally",
        hard == [] and isinstance(issue_fixed["sections"], list)
        and any(x["action"] == "parsed_stringified_sections" for x in repairs),
    ))
    _, rep_full = finalize_weekly_brief(stringified, CANDIDATES)
    r.append(check(
        "a repaired brief PASSES the contract — the content was never the problem",
        rep_full["passed"] is True,
    ))
    _, _, err_partial = coerce_sections({"sections": '[{"section_type": "big_one"'})
    r.append(check(
        "a string that does NOT parse stays a single error — no partial salvage",
        isinstance(err_partial, str),
    ))
    _, _, err_notlist = coerce_sections({"sections": '{"section_type": "big_one"}'})
    r.append(check(
        "valid JSON that is not a LIST of objects is still an error",
        isinstance(err_notlist, str),
    ))

    # ==============================================================
    # 1b -- capping
    # ==============================================================
    many = [f"err {i}" for i in range(100)]
    capped = cap_errors(many)
    r.append(check(
        f"cap_errors shows the first {ERROR_DISPLAY_LIMIT} then counts the rest",
        len(capped) == ERROR_DISPLAY_LIMIT + 1 and capped[-1] == f"...and {100 - ERROR_DISPLAY_LIMIT} more",
    ))
    r.append(check("cap_errors leaves a short list untouched", cap_errors(["a", "b"]) == ["a", "b"]))
    r.append(check("cap_errors never mutates its input", len(many) == 100))

    fb = build_retry_feedback([{"hard_errors": many, "shape_errors": []}])
    r.append(check(
        "the retry feedback is capped — the model is never sent thousands of errors",
        fb.count("- hard_error:") == ERROR_DISPLAY_LIMIT and "(...and 75 more)" in fb,
    ))
    r.append(check(
        "the cap sentinel is rendered as a count, not as a 26th error",
        "- hard_error: ...and" not in fb,
    ))

    md = rwb.render_markdown(
        issue_with(["x"] * 100),
        {"season": 2026, "week": 9, "run_at": "t", "cost_estimate_usd": 0.0, "mode": "dry",
         "selection": {"by_family": {}, "rows_read": 0, "wrong_week": 0,
                       "not_enriched": 0, "dropped_by_cap": 0}},
        None)
    r.append(check(
        "the .md Malformed-sections block is capped (the real one rendered 416 KB)",
        md.count("- entry ") == ERROR_DISPLAY_LIMIT and f"...and {100 - ERROR_DISPLAY_LIMIT} more" in md,
    ))

    # ==============================================================
    # 1d -- the envelope instruction
    # ==============================================================
    r.append(check(
        "the retry feedback ends with the envelope instruction, after RETRY_INSTRUCTION",
        fb.rstrip().endswith(RETRY_ENVELOPE_INSTRUCTION)
        and fb.index(RETRY_INSTRUCTION) < fb.index(RETRY_ENVELOPE_INSTRUCTION),
    ))
    r.append(check(
        "it is exactly the specified sentence",
        RETRY_ENVELOPE_INSTRUCTION ==
        "Return the brief through the tool as structured fields, not as JSON text inside a string.",
    ))

    # ==============================================================
    # THE REAL 15,210-CHARACTER CASE
    # ==============================================================
    if REAL_BAD.exists():
        real = json.loads(REAL_BAD.read_text())["issue"]
        raw_len = len(real["sections"])
        r.append(check(
            f"the saved file really is the pathological case (sections is a {raw_len}-char string)",
            isinstance(real["sections"], str) and raw_len > 15000,
        ))
        issue_out, repairs, hard = complete_derivable_fields(real, CANDIDATES)
        per_char = [h for h in hard if "is str, not an object" in h]
        r.append(check(
            f"NO per-character errors on the real file (was {raw_len}, now {len(per_char)})",
            per_char == [],
        ))
        r.append(check(
            "it either repairs cleanly or gives exactly one error — never thousands",
            (any(x["action"] == "parsed_stringified_sections" for x in repairs) and len(hard) == 0)
            or len(hard) == 1,
        ))
        # Documented, not asserted as desirable: this particular string is
        # the sections array PLUS the trailing watchlist_populated /
        # notes_for_human_reviewer fields crammed in after it, so
        # json.loads raises "Extra data" at char 11659. Per the spec the
        # repair only covers a string that parses cleanly into a list, so
        # this case correctly falls through to the single error. A
        # raw_decode-based variant could recover it; deliberately not
        # done here.
        r.append(check(
            "this real string is array-plus-trailing-data, so it is NOT cleanly parseable "
            "and correctly takes the single-error path",
            not any(x["action"] == "parsed_stringified_sections" for x in repairs) and len(hard) == 1,
        ))
        _, real_report = finalize_weekly_brief(real, CANDIDATES)
        r.append(check(
            f"finalize on the real file yields {len(real_report['hard_errors'])} hard error(s), not 15,210",
            len(real_report["hard_errors"]) <= 2,
        ))
        repaired = any(x["action"] == "parsed_stringified_sections" for x in repairs)
        print(f"       -> real file outcome: {'REPAIRED' if repaired else 'single error'}; "
              f"hard_errors={len(hard)}, sections now "
              f"{len(issue_out['sections']) if isinstance(issue_out['sections'], list) else 'str'}")
    else:
        print(f"[SKIP] real malformed file not present at {REAL_BAD}")

    # ==============================================================
    # PART 2 -- the revised reader-language list
    # ==============================================================
    r.append(check(
        "tier / tiers / peer tier / peer-tier are NO LONGER banned",
        not {"tier", "tiers", "peer tier", "peer-tier"} & set(READER_LANGUAGE_BANNED_PHRASES),
    ))
    r.append(check(
        "role-momentum / role momentum / external opportunity / i'm not given / i wasn't given ARE banned",
        {"role-momentum", "role momentum", "external opportunity",
         "i'm not given", "i wasn't given"} <= set(READER_LANGUAGE_BANNED_PHRASES),
    ))
    r.append(check(
        "the rest of the original list is kept",
        {"peer group", "comparison group", "comparison class", "benchmark", "methodology",
         "role score", "role-change signal", "maxed out", "snapshot", "timestamp",
         "weeks of data", "usage numbers for", "dimension", "EPS"}
        <= set(READER_LANGUAGE_BANNED_PHRASES),
    ))
    r.append(check(
        "'peer tier' and 'tiers' now pass — it is what the app's own Market card calls it",
        phrases_hit(reader_issue("His peer tier says one thing; tiers are only as good as their members.")) == set(),
    ))
    r.append(check(
        "role momentum, in both spellings, is caught",
        phrases_hit(reader_issue("the role-momentum reading")) == {"role-momentum"}
        and "role momentum" in phrases_hit(reader_issue("the role momentum reading")),
    ))
    r.append(check(
        "external opportunity is caught",
        "external opportunity" in phrases_hit(reader_issue("external opportunity is maxed")),
    ))
    r.append(check(
        "\"I'm not given\" is caught with a straight apostrophe AND a curly one",
        "i'm not given" in phrases_hit(reader_issue("I'm not given their usage numbers"))
        and "i'm not given" in phrases_hit(reader_issue("I’m not given their numbers")),
    ))
    r.append(check(
        "\"I wasn't given\" is caught too",
        "i wasn't given" in phrases_hit(reader_issue("I wasn't given the peer usage")),
    ))
    r.append(check(
        "still no false positives on 'evidence', 'measure', 'signal' alone",
        phrases_hit(reader_issue(
            "The evidence is clear. On that specific measure, a legitimate opportunity signal.")) == set(),
    ))

    print()
    if all(r):
        print(f"All {len(r)} checks passed.")
    else:
        print(f"{len(r) - sum(r)} of {len(r)} checks FAILED — see above.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
