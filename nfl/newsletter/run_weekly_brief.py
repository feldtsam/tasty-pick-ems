"""
run_weekly_brief.py -- the production caller for the Weekly Pick Ems
Report draft (the thing run_weekly_editor_agent.py's own docstring says
it is NOT: "this exists to answer one question ... not to be the real
Thursday caller").

One command, one week, real stories:

    python3 run_weekly_brief.py --season 2026 --week 7

What it does, in order:
  1. READ. Pulls the week's real Intelligence stories through the
     existing signed read route (intelligence_write.read_intelligence_
     stories), for the same two weeks and the same family/week rule the
     prep step uses: market_intelligence from the UPCOMING week, every
     other family from week-1 (prep_interrogation_eps.expected_week_
     for_family is imported, not re-implemented, so the two steps can
     never drift apart).
  2. SELECT. Keeps only ENRICHED stories -- rows that already carry both
     a non-empty `interrogation` and a non-empty `eps` from a prior
     `prep_interrogation_eps.py --write`. A story with eps null is
     dropped on the prompt's own instruction ("If a Story Object has
     eps: null (upstream scoring failed), do not include it as a
     candidate at all -- an excluded story, not a guessed score").
     Then maps each row into the Editor Agent's input contract.
  3. REFUSE, loudly, rather than produce a misleading draft: no enriched
     stories for that week, or input that looks like Calibration Fixture
     V2 (see FIXTURE_MARKERS) -- the one failure mode that would quietly
     draft an issue about players who do not exist.
  4. DRAFT. Calls run_weekly_editor_agent() completely unchanged, which
     runs the structural contract and then the Evidence Validator.
  5. WRITE OUTPUTS. Always, pass or fail: a JSON record and a plain
     markdown draft a non-technical reader can read like an article,
     under outputs/brief_<season>w<week>_<timestamp>.{json,md}.

DRY BY DEFAULT. Nothing reaches the database without --persist, and
--persist only ever creates or updates a DRAFT: it refuses outright if
an issue for that (season, week) already exists with a publish_status
other than 'draft'. Neither this script nor anything it calls can set
publish_status or published_at -- see newsletter-write.ts's own
"DRAFT ONLY" contract and persist_weekly_brief.shape_newsletter_rows(),
which builds the issue row as {id, season, week} and nothing else.

A hard Evidence Validator failure is NOT retried and NOT rewritten: the
draft and the failure report are both written to outputs/ so a human can
read what the Editor tried to say, and the process exits 1.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
NFL = HERE.parent
for p in (NFL, NFL / "api", NFL / "content_writer"):
    sys.path.insert(0, str(p))

from prep_interrogation_eps import (  # noqa: E402  -- one source of truth for the family/week rule
    PRICE_PER_MTOK,
    expected_week_for_family,
)
from run_weekly_editor_agent import (  # noqa: E402
    MAX_TOKENS,
    PROMPT_PATH,
    build_candidate_pool,
    run_weekly_editor_agent,
)
from weekly_brief_evidence import EvidenceValidationFailed  # noqa: E402
from weekly_brief_shape import SECTION_PRIORITY  # noqa: E402

OUTPUTS_DIR = HERE / "outputs"

# Default ceiling for ONE drafting run. The Editor Agent makes one call
# plus at most one capped retry, so this budgets for two full calls with
# room to spare; the Oct 8 prep step's own $3.00 ceiling covered 81
# calls. Raise it with --max-cost if a week's candidate pool is unusually
# large.
DEFAULT_MAX_COST_USD = 1.00

# Rough offline token estimate, used ONLY for the pre-flight ceiling
# check (no API call is made to count tokens). 4 characters per token is
# the usual English approximation; the margin below covers it being
# optimistic.
CHARS_PER_TOKEN = 4.0
ESTIMATE_SAFETY_MARGIN = 1.25

# Reader-facing section names, verbatim from the Weekly Editor Agent
# prompt's own Step 3 list, so the markdown draft calls each section what
# the prompt that produced it calls it.
SECTION_LABELS = {
    "from_the_desk": "From the Desk of Mr. Pick Ems",
    "big_one": "The Big One",
    "what_changed": "What Changed",
    "market_knows_something": "The Market Knows Something",
    "who_it_affects": "Who It Affects",
    "tasty_connection": "The Tasty Connection",
    "watchlist": "The Watchlist",
}

# Calibration Fixture V2's own inhabitants. None of these is a real NFL
# player, and a real week can never legitimately contain them, so their
# presence means someone pointed this at fixture data.
FIXTURE_MARKERS = (
    "marcus thibodeaux",
    "dorsey keane",
    "devin okonkwo",
    "synthetic-fixture",
    "synthetic-player",
)


# ---------------------------------------------------------------------------
# Pure functions: selection, guards, rendering. No I/O, no network.
# ---------------------------------------------------------------------------
def entity_label(row: dict) -> str:
    """
    Display name for a story's subject, using this repo's own existing
    convention for exactly this (prep_interrogation_eps.py builds its
    report rows the same way): the player's name when the entity is a
    player, otherwise the team.
    """
    ent = row.get("entity") or {}
    if not isinstance(ent, dict):
        return str(ent)
    return ent.get("player_name") or ent.get("team") or ent.get("entity_key") or ""


def is_enriched(row: dict) -> bool:
    """A story prep has already interrogated AND scored."""
    it = row.get("interrogation")
    eps = row.get("eps")
    if not isinstance(it, dict) or not it:
        return False
    if not isinstance(eps, dict) or not eps:
        return False
    # An eps blob with no dimensions is a failed score, not a score.
    return bool(eps.get("dimensions"))


def to_story_object(row: dict) -> dict:
    """
    One stored nfl_intelligence_stories row -> one Editor Agent input
    Story Object.

    The target shape is build_candidate_pool()'s ten fields exactly.
    Eight map straight across from columns of the same name. The two
    that do not:

      intelligence_story_id <- row["id"]. The read route returns the
        DB-generated primary key as `id` (intelligence_write.read_
        intelligence_stories's docstring records this as confirmed
        against a real round-tripped row, "id, not story_id").
      entity <- entity_label(row). The fixture carries `entity` as a
        display STRING ("RB Dorsey Keane"); the column is jsonb. Flattened
        with the repo's existing player_name-or-team convention.

      player_id <- entity.player_id, null for a team-level story
        (defensive_trends and coaching_trends are team entities and
        legitimately have none).

    interrogation and eps are passed through untouched: build_candidate_
    pool()'s docstring records that their stored shapes already match
    interrogate_story()'s and compute_eps()'s return shapes field for
    field, so no remapping is correct here, not laziness.
    """
    ent = row.get("entity") if isinstance(row.get("entity"), dict) else {}
    return {
        "intelligence_story_id": row.get("id"),
        "player_id": ent.get("player_id"),
        "intelligence_family": row.get("intelligence_family"),
        "entity": entity_label(row),
        "headline": row.get("headline"),
        "story": row.get("story"),
        "time_window": row.get("time_window"),
        "sample_size": row.get("sample_size"),
        "interrogation": row.get("interrogation"),
        "eps": row.get("eps"),
    }


def composite(story: dict) -> float:
    eps = story.get("eps") or {}
    try:
        return float(eps.get("composite_score") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def select_qualified(rows: list[dict], season: int, upcoming_week: int, max_stories: int) -> dict:
    """
    Stored rows -> the candidate pool handed to the Editor Agent.

    Three filters, in order, each counted in the returned report so a
    thin week is explainable rather than mysterious:
      1. the row is for the week this family should be read from
         (expected_week_for_family: market = upcoming, others = week-1),
      2. the row is enriched (interrogation AND eps with dimensions),
      3. ranked by eps.composite_score, highest first, capped at
         max_stories.

    The cap rarely binds in practice: only the ~20 stories the prep step
    shortlisted ever get an interrogation in the first place. It exists
    so an unexpectedly large enriched pool cannot silently turn one
    drafting call into an enormous one.
    """
    wrong_week, not_enriched, kept = [], [], []
    for row in rows:
        family = row.get("intelligence_family")
        if row.get("season") != season or row.get("week") != expected_week_for_family(family, upcoming_week):
            wrong_week.append(row)
            continue
        if not is_enriched(row):
            not_enriched.append({
                "id": row.get("id"), "family": family, "entity": entity_label(row),
                "has_interrogation": isinstance(row.get("interrogation"), dict) and bool(row.get("interrogation")),
                "has_eps": isinstance(row.get("eps"), dict) and bool(row.get("eps")),
            })
            continue
        kept.append(row)

    kept.sort(key=lambda r: composite({"eps": r.get("eps")}), reverse=True)
    capped = kept[:max_stories]
    stories = [to_story_object(r) for r in capped]
    by_family: dict[str, int] = {}
    for s in stories:
        by_family[s["intelligence_family"]] = by_family.get(s["intelligence_family"], 0) + 1

    return {
        "stories": stories,
        "considered": len(rows),
        "excluded_wrong_week": len(wrong_week),
        "excluded_not_enriched": not_enriched,
        "cap_applied": len(kept) - len(capped),
        "by_family": by_family,
        "gates": {
            "big_one_eligible": sum(1 for s in stories if ((s["eps"] or {}).get("gates") or {}).get("big_one_eligible")),
            "watchlist_eligible": sum(1 for s in stories if ((s["eps"] or {}).get("gates") or {}).get("watchlist_eligible")),
        },
    }


def fixture_contamination(stories: list[dict]) -> list[str]:
    """
    Every reason this pool looks like Calibration Fixture V2 rather than
    a real week. Empty list means clean.
    """
    found = []
    for s in stories:
        haystack = " ".join(str(s.get(k) or "") for k in ("intelligence_story_id", "player_id", "entity", "headline", "story")).lower()
        for marker in FIXTURE_MARKERS:
            if marker in haystack:
                found.append(f"{s.get('intelligence_story_id')}: contains fixture marker {marker!r}")
    return found


def estimate_cost_usd(stories: list[dict], prompt_text: str, max_tokens: int = MAX_TOKENS, calls: int = 2) -> float:
    """
    Offline worst-case cost estimate for a drafting run: `calls` full
    calls (the Editor's one call plus its one capped retry), each paying
    for the whole system prompt and candidate pool as input and the full
    max_tokens as output. No API call is made to count tokens; see
    CHARS_PER_TOKEN. Deliberately pessimistic -- the ceiling should stop
    a run that MIGHT be too expensive, not one that turned out to be.
    """
    pool_chars = len(json.dumps(build_candidate_pool(stories)))
    input_tokens = (pool_chars + len(prompt_text)) / CHARS_PER_TOKEN * ESTIMATE_SAFETY_MARGIN
    per_call = (input_tokens * PRICE_PER_MTOK["input"] + max_tokens * PRICE_PER_MTOK["output"]) / 1e6
    return round(per_call * calls, 4)


def render_markdown(issue: dict, meta: dict, evidence: dict | None) -> str:
    """
    The readable draft: the issue as an article, then the machinery.

    Sections appear in SECTION_PRIORITY order (the editorial order the
    persist step and the reading page both use), not the order the model
    happened to emit them in.
    """
    lines: list[str] = []
    lines.append("# The Pick Ems Report")
    lines.append("")
    lines.append(f"**DRAFT -- not published.** Week {meta['week']}, {meta['season']}. "
                 f"Drafted {meta['run_at']}.")
    lines.append("")
    lines.append("---")
    lines.append("")

    sections = {s.get("section_type"): s for s in issue.get("sections", [])}
    for section_type in SECTION_PRIORITY:
        section = sections.get(section_type)
        if not section:
            continue
        stories = section.get("stories") or []
        crossrefs = section.get("cross_references") or []
        if not stories and not crossrefs:
            continue
        lines.append(f"## {SECTION_LABELS.get(section_type, section_type)}")
        lines.append("")
        for story in stories:
            headline = (story.get("headline") or "").strip()
            if headline:
                lines.append(f"### {headline}")
                lines.append("")
            for paragraph in (story.get("body") or "").split("\n\n"):
                paragraph = paragraph.strip()
                if paragraph:
                    lines.append(paragraph)
                    lines.append("")
        for ref in crossrefs:
            text = (ref.get("text") or "").strip()
            if text:
                lines.append(f"> {text}")
                lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Review notes (not part of the article)")
    lines.append("")
    # The Editor sometimes emits this field with escaped newlines inside
    # the JSON string; unescape them so the reviewer reads paragraphs
    # rather than one wall of text with literal \n in it.
    notes = (issue.get("notes_for_human_reviewer") or "").replace("\\n", "\n").strip()
    lines.append("**Editor's notes for the reviewer:**")
    lines.append("")
    lines.append(notes or "_(none)_")
    lines.append("")

    lines.append("### Story placement and EPS")
    lines.append("")
    # Gate booleans are not on the Editor's own eps_scores (persist freezes
    # them on later), so they are resolved from the candidate pool this
    # draft was written from -- the same upstream gates, read not recomputed.
    gates_by_id = {
        s.get("intelligence_story_id"): ((s.get("eps") or {}).get("gates") or {})
        for s in ((meta.get("selection") or {}).get("stories") or [])
    }
    lines.append("| Section | Headline | EPS total | Big One eligible | Watchlist eligible | Sources |")
    lines.append("|---|---|---|---|---|---|")
    for section_type in SECTION_PRIORITY:
        section = sections.get(section_type)
        if not section:
            continue
        for story in section.get("stories") or []:
            scores = story.get("eps_scores") or {}
            ids = story.get("intelligence_story_ids") or []
            gates = gates_by_id.get(ids[0]) if ids else {}
            gates = gates or {}
            lines.append(
                f"| {SECTION_LABELS.get(section_type, section_type)} "
                f"| {(story.get('headline') or '').replace('|', '/')} "
                f"| {scores.get('eps_total', '-')} "
                f"| {gates.get('big_one_eligible', '-')} "
                f"| {gates.get('watchlist_eligible', '-')} "
                f"| {len(ids)} |"
            )
    lines.append("")

    lines.append("### Evidence Validator")
    lines.append("")
    if evidence is None:
        lines.append("Not run (the Editor Agent did not return an issue).")
    else:
        lines.append(f"**{'PASSED' if evidence.get('passed') else 'HARD FAIL -- this draft is blocked'}** "
                     f"-- {evidence.get('summary', '')}")
        lines.append("")
        lines.append(f"Findings needing a human read: {evidence.get('needs_review_count', 0)}")
        lines.append("")
        for fail in evidence.get("hard_fails") or []:
            lines.append(f"- **{fail.get('where')}** / {fail.get('check')}: "
                         f"{(fail.get('claim_text') or '')!r} -- {fail.get('detail')}")
        if evidence.get("hard_fails"):
            lines.append("")

    lines.append("### Structural repairs applied")
    lines.append("")
    validation = issue.get("_validation") or {}
    repairs = validation.get("repairs") or []
    lines.append(f"Retry fired: {validation.get('retry_fired')}. Repairs: {len(repairs)}.")
    for repair in repairs:
        lines.append(f"- {repair.get('where')}: {repair.get('action')}")
    lines.append("")

    lines.append("### Candidate pool this draft was written from")
    lines.append("")
    sel = meta.get("selection") or {}
    lines.append(f"- Stories offered to the Editor: {len(sel.get('stories') or [])} "
                 f"({json.dumps(sel.get('by_family') or {})})")
    lines.append(f"- Rows read: {sel.get('considered')}; wrong week for their family: {sel.get('excluded_wrong_week')}; "
                 f"not enriched: {len(sel.get('excluded_not_enriched') or [])}; dropped by the cap: {sel.get('cap_applied')}")
    lines.append(f"- Gate-eligible in the pool: Big One {((sel.get('gates') or {}).get('big_one_eligible'))}, "
                 f"Watchlist {((sel.get('gates') or {}).get('watchlist_eligible'))}")
    lines.append("")
    return "\n".join(lines)


def issue_week_label(season: int, week: int) -> str:
    return f"{season} NFL Week {week}"


# ---------------------------------------------------------------------------
# I/O: the signed reads, the draft call, the outputs, the persist guard.
# ---------------------------------------------------------------------------
def read_week_rows(season: int, upcoming_week: int, secret: str) -> list[dict]:
    """
    Both weeks the families are drawn from, through the existing signed
    read route. Same two-week read as prep_interrogation_eps.load_pool,
    and the same hard failure on a bad read: a partial pool would
    silently produce a thin issue.
    """
    from intelligence_write import read_intelligence_stories

    rows: list[dict] = []
    for week in sorted({upcoming_week, upcoming_week - 1}):
        result = read_intelligence_stories(season, week, secret)
        if not result["ok"]:
            raise RuntimeError(f"intelligence read failed for season={season} week={week}: {result['error']!r}")
        rows.extend(result["rows"])
    return rows


def read_existing_issue(season: int, week: int, secret: str, read_url: str | None = None) -> dict:
    """
    The current newsletter_issue row for (season, week), through the
    existing signed newsletter-read route, or None when no issue exists
    yet. Returns {"ok", "error", "issue"}; absence is not an error (the
    route returns a null issue by design).
    """
    from lovable_forward import forward_to_lovable

    url = read_url or os.environ.get("NFL_NEWSLETTER_READ_URL") or DEFAULT_NEWSLETTER_READ_URL
    result = forward_to_lovable({"season": season, "week": week}, secret, url)
    if not result["success"]:
        return {"ok": False, "error": result["error"], "issue": None}
    try:
        body = json.loads(result["response_body"])
    except (json.JSONDecodeError, TypeError):
        return {"ok": False, "error": f"non-JSON response body: {result['response_body']!r}", "issue": None}
    return {"ok": True, "error": None, "issue": body.get("issue")}


DEFAULT_NEWSLETTER_READ_URL = "https://tastypickems.com/api/public/newsletter-read"


def persist_guard(existing_issue: dict | None) -> tuple[bool, str]:
    """
    (may_persist, reason). A draft may be replaced. Anything a human has
    already moved forward may not: --persist must never be able to
    rewrite the stories under an approved or published issue, and this
    script must never be the thing that changes publish_status.
    """
    if not existing_issue:
        return True, "no issue exists for this week yet; a new draft will be created"
    status = (existing_issue.get("publish_status") or "").strip() or "draft"
    if status != "draft":
        return False, (
            f"an issue for this week already exists with publish_status={status!r} "
            f"(published_at={existing_issue.get('published_at')!r}). Refusing: --persist only ever "
            f"creates or updates a draft. Move it back to 'draft' by hand if you really mean to replace it."
        )
    return True, "an existing draft will be replaced"


def write_outputs(stem: str, record: dict, markdown: str, outputs_dir: Path) -> tuple[Path, Path]:
    outputs_dir.mkdir(parents=True, exist_ok=True)
    json_path = outputs_dir / f"{stem}.json"
    md_path = outputs_dir / f"{stem}.md"
    json_path.write_text(json.dumps(record, indent=2, default=str))
    md_path.write_text(markdown)
    return json_path, md_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Draft the weekly Pick Ems Report from real enriched Intelligence stories. Dry run unless --persist.",
    )
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--week", type=int, required=True,
                    help="the UPCOMING week, the same meaning prep_interrogation_eps.py uses "
                         "(market_intelligence comes from this week, every other family from week-1)")
    ap.add_argument("--persist", action="store_true",
                    help="save the draft through the signed newsletter-write route (default: write outputs only)")
    ap.add_argument("--max-cost", type=float, default=DEFAULT_MAX_COST_USD,
                    help=f"refuse to start a drafting run whose worst-case estimate exceeds this (default {DEFAULT_MAX_COST_USD})")
    ap.add_argument("--max-stories", type=int, default=20,
                    help="cap on candidate Story Objects handed to the Editor Agent (default 20)")
    ap.add_argument("--outputs-dir", default=str(OUTPUTS_DIR))
    ap.add_argument("--pool-json",
                    help="read story rows from this JSON file instead of the signed read route (diagnostics; "
                         "refused with --persist, exactly as prep refuses the same combination)")
    ap.add_argument("--editor-output-json",
                    help="skip the Editor Agent and use this previously saved draft JSON (its 'issue' key, or the "
                         "issue itself). Lets a reviewed draft be persisted without paying for a second drafting run.")
    args = ap.parse_args(argv)

    if args.pool_json and args.persist:
        print("--pool-json cannot be combined with --persist: a write must be based on a live read", file=sys.stderr)
        return 2

    secret = os.environ.get("NFL_PIPELINE_WEBHOOK_SECRET")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    needs_secret = not args.pool_json or args.persist
    needs_api_key = not args.editor_output_json
    if needs_secret and not secret:
        print("NFL_PIPELINE_WEBHOOK_SECRET must be set", file=sys.stderr)
        return 2
    if needs_api_key and not api_key:
        print("ANTHROPIC_API_KEY must be set (or pass --editor-output-json to skip the Editor Agent)", file=sys.stderr)
        return 2

    run_at = datetime.now(timezone.utc)
    stem = f"brief_{args.season}w{args.week}_{run_at.strftime('%Y%m%dT%H%M%S')}"
    outputs_dir = Path(args.outputs_dir)

    # 1. READ
    if args.pool_json:
        rows = json.loads(Path(args.pool_json).read_text())
        source = f"file:{args.pool_json}"
    else:
        rows = read_week_rows(args.season, args.week, secret)
        source = "signed intelligence read route"

    # 2. SELECT
    selection = select_qualified(rows, args.season, args.week, args.max_stories)
    stories = selection["stories"]
    print(f"[brief] read {selection['considered']} rows from {source}; "
          f"{len(stories)} enriched candidates {json.dumps(selection['by_family'])}; "
          f"not enriched {len(selection['excluded_not_enriched'])}, wrong week {selection['excluded_wrong_week']}, "
          f"dropped by cap {selection['cap_applied']}", flush=True)

    # 3. REFUSE
    if not stories:
        print(
            f"\nREFUSING TO DRAFT: no enriched stories for season {args.season} week {args.week}.\n"
            f"  Rows read: {selection['considered']}. With an interrogation and an EPS score: 0.\n"
            f"  The Editor Agent can only work from stories that already carry both, which is what\n"
            f"  `python3 prep_interrogation_eps.py --season {args.season} --week {args.week} --write`\n"
            f"  produces. Run that first, then run this again.",
            file=sys.stderr,
        )
        return 3

    contamination = fixture_contamination(stories)
    if contamination:
        print(
            "\nREFUSING TO DRAFT: this candidate pool looks like Calibration Fixture V2, not a real week.\n"
            + "".join(f"  - {c}\n" for c in contamination[:10])
            + "  These are synthetic players invented for prompt calibration. Drafting from them would produce\n"
            "  an issue about people who do not exist. Check --season/--week and --pool-json.",
            file=sys.stderr,
        )
        return 4

    # 4. COST CEILING (pre-flight; no API call is made to measure it)
    estimate = estimate_cost_usd(stories, PROMPT_PATH.read_text())
    if not args.editor_output_json and estimate > args.max_cost:
        print(
            f"\nREFUSING TO DRAFT: worst-case cost estimate ${estimate:.2f} exceeds the ${args.max_cost:.2f} ceiling\n"
            f"  ({len(stories)} candidate stories; the estimate budgets for the Editor's call plus its one retry).\n"
            f"  Raise it with --max-cost, or lower --max-stories.",
            file=sys.stderr,
        )
        return 5
    print(f"[brief] worst-case cost estimate ${estimate:.2f} (ceiling ${args.max_cost:.2f})", flush=True)

    meta = {
        "season": args.season, "week": args.week, "run_at": run_at.isoformat(),
        "source": source, "selection": selection, "cost_estimate_usd": estimate,
        "mode": "persist" if args.persist else "dry",
    }

    # 5. DRAFT
    evidence = None
    blocked = False
    if args.editor_output_json:
        loaded = json.loads(Path(args.editor_output_json).read_text())
        issue = loaded.get("issue", loaded)
        evidence = issue.get("_evidence_validation")
        print(f"[brief] using the saved draft in {args.editor_output_json}; the Editor Agent was not called", flush=True)
    else:
        try:
            issue = run_weekly_editor_agent(
                stories, api_key,
                issue_week=issue_week_label(args.season, args.week),
            )
            evidence = issue.get("_evidence_validation")
        except EvidenceValidationFailed as failure:
            issue = failure.issue
            evidence = failure.report
            blocked = True

    record = {"meta": meta, "issue": issue, "evidence_validation": evidence, "blocked": blocked}
    markdown = render_markdown(issue, meta, evidence)
    json_path, md_path = write_outputs(stem, record, markdown, outputs_dir)
    print(f"[brief] wrote {md_path}", flush=True)
    print(f"[brief] wrote {json_path}", flush=True)

    if blocked:
        print(
            f"\nBLOCKED by the Evidence Validator: {evidence.get('summary')}\n"
            f"  The draft was still written, so you can read what it tried to say: {md_path}\n"
            f"  Nothing was persisted. No retry and no rewrite was attempted, by design.",
            file=sys.stderr,
        )
        return 1

    # 6. PERSIST (never approves, never publishes)
    if not args.persist:
        print("[brief] dry run: nothing was written to the database. Add --persist to save this as a draft.", flush=True)
        return 0

    existing = read_existing_issue(args.season, args.week, secret)
    if not existing["ok"]:
        print(f"\nREFUSING TO PERSIST: could not read the current issue state ({existing['error']!r}).\n"
              f"  Not writing blind over an issue whose publish_status is unknown.", file=sys.stderr)
        return 6
    may, reason = persist_guard(existing["issue"])
    print(f"[brief] persist guard: {reason}", flush=True)
    if not may:
        print(f"\nREFUSING TO PERSIST: {reason}", file=sys.stderr)
        return 7

    # persist_weekly_brief(), not run_and_persist(): run_and_persist calls
    # the Editor Agent itself, and this script has already done that. It is
    # the same write path -- run_and_persist's own second half.
    from persist_weekly_brief import persist_weekly_brief

    stories_by_id = {s["intelligence_story_id"]: s for s in stories}
    # Reuse the existing issue's own id. A fresh uuid here would become
    # ON CONFLICT (season, week) DO UPDATE SET id = <new>, which the
    # newsletter_story foreign key refuses -- see shape_newsletter_rows().
    existing_id = (existing["issue"] or {}).get("id")
    persisted = persist_weekly_brief(issue, stories_by_id, args.season, args.week, secret,
                                     existing_issue_id=existing_id)
    if existing_id:
        print(f"[brief] re-persisting into the existing draft issue {existing_id}", flush=True)
    record["persisted"] = persisted
    write_outputs(stem, record, markdown, outputs_dir)

    write_result = persisted.get("write_result") or {}
    ok = write_result.get("success")
    print(f"[brief] persist {'succeeded' if ok else 'FAILED'}: "
          f"status={write_result.get('status_code')} {str(write_result.get('response_body'))[:300]}", flush=True)
    if not ok:
        return 8
    print(f"[brief] saved as a DRAFT issue for {args.season} week {args.week}. "
          f"publish_status is untouched; nothing is visible to readers until a human publishes it.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
