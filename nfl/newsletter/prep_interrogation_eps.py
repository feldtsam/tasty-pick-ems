"""
prep_interrogation_eps.py -- the manually triggered "interrogation
candidates" step for the Weekly Brief (2026-10-07).

What it does, in order:
  1. PRE-FILTER (deterministic, no API calls). Reads the current week's
     stories across the four Intelligence families through the existing
     signed read route, drops stale rows, ranks the rest by the
     deterministic base evidence strength (eps.compute_evidence_strength
     with interrogation=None -- the same number EPS would start from),
     plus a small evidence_classification bonus, with a mild diminishing-
     returns penalty on each additional story from the same family, and
     selects up to `max_candidates` above a score floor. The output is
     labelled "interrogation candidates": it is a shortlist for editorial
     consideration and says nothing about Big One or Watchlist status,
     which only compute_eps() decides.
  2. EXECUTION. For each candidate: reuse the stored interrogation when
     it is still valid (see interrogation_reuse_decision), otherwise run
     story_interrogation.interrogate_story() (one extra attempt on a None
     result, then recorded as failed for this run); then ALWAYS run
     eps.compute_eps(), so gates reflect current data. prior_history and
     market_data are built exactly the way api/curate_home_shelves.py
     builds them for shelf cards: Table 1 season-evidence rows +
     reconciled weeks -> player_season_history.build_player_history_
     package; this week's nfl_price_history rows -> curate_home_shelves.
     _market_data_for_candidate. Modest concurrency, a hard --max-cost
     ceiling measured from the real token usage of every call.
  3. REPORT. A compact summary printed, and saved as JSON and Markdown
     under nfl/newsletter/outputs/prep_<timestamp>.{json,md}.

Default mode is DRY RUN: everything is computed and reported, nothing
is written. --write sends the updated rows (the read row with its
interrogation/eps replaced, nothing else touched) through the existing
signed write path, intelligence_write.write_intelligence_rows -> the
nfl-intelligence-write route, which upserts on the stories' unique key.
There is no direct database access anywhere in this module.

Nothing here changes gate thresholds, EPS weights, or the Editor Agent
prompt. Not wired into any route or Make scenario.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
NFL = HERE.parent
for p in (NFL, NFL / "api", NFL / "scripts", NFL / "content_writer", NFL / "content_writer" / "voice"):
    sys.path.insert(0, str(p))

import card_writer_common as cwc  # noqa: E402  (the shared HTTP call every model call goes through)
from eps import compute_eps, compute_evidence_strength  # noqa: E402
from story_interrogation import interrogate_story  # noqa: E402

FAMILIES = ("market_intelligence", "role_changes", "defensive_trends", "coaching_trends")
# Market stories are stamped with the UPCOMING week; the other three with
# the most recent RECONCILED week (upcoming - 1). A family whose rows at
# its expected week are missing is reported as stale, never back-filled
# from an older week.
UPCOMING_WEEK_FAMILIES = ("market_intelligence",)

PREP_CONFIG = {
    "max_candidates": 20,
    # Floor on the ADJUSTED ranking score (after the family penalty). A
    # "limited" story's base evidence is capped at 35 by eps.compute_
    # evidence_strength, so 30 lets strong limited stories compete while
    # keeping anything that is weak on its own merits out of the list.
    "min_score": 30.0,
    # evidence_classification bonus: a small nudge, not a tier system.
    "classification_bonus": {"strong": 10.0, "moderate": 5.0, "limited": 0.0},
    # Diminishing returns per additional story from the same family: the
    # n-th story already selected from a family costs the next one from
    # that family penalty[n] points (capped at the last value). Mild on
    # purpose: a family whose stories are clearly better still dominates.
    "family_penalty": (0.0, 3.0, 6.0, 9.0, 12.0, 15.0),
    "near_miss_count": 10,
    # Tie-break (item 1, 2026-10-08): a deterministic secondary key from
    # fields already on the story, normalized within family against FIXED
    # scales (below), averaged, and multiplied by tiebreak_cap. The cap
    # must stay strictly below the smallest gap between classification
    # bonuses (5) so a tie-break can never lift a story over a higher
    # classification -- test_prep_interrogation_eps.py proves that from
    # these constants, not from a hardcoded number.
    "tiebreak_cap": 4.0,
    "concurrency": 4,
    "max_cost_usd": 4.0,
    # Rough per-story cost used only to decide whether starting ONE MORE
    # story could cross the ceiling; replaced by the running average once
    # real calls have been made. The Oct 6 dry run averaged $0.10.
    "estimated_cost_per_story_usd": 0.15,
    # A Market story is recomputed when its row was rewritten this long
    # after its interrogation was produced -- a Market Refresh, not this
    # step's own write (which lands within seconds of computed_at).
    "market_refresh_tolerance_minutes": 15,
    # Fields that define a story's content for the reuse fingerprint.
    "fingerprint_fields": (
        "headline", "story", "primary_signal", "supporting_evidence", "hero_metric", "what_changed",
        "sample_size", "confidence", "completeness", "trend_strength", "time_window", "related_players",
    ),
}

# Fixed per-family scales for the tie-break. Each metric maps to 0-1 as
# value / scale, clamped; the scales are documented family constants, not
# pool statistics, so a story's tie-break never moves because other
# stories changed. Why these numbers:
#   market_intelligence: the deviation (hero_metric.delta_value, percentage
#     points vs. the peer tier; primary_signal.value as fallback) saturates
#     at 32 pp = 4x the family's own "extreme" threshold
#     (magnitude_extreme_pp = 8). 2x was tried first against the saved
#     Oct 6 pool and half the top 20 saturated (deviations there run 5 to
#     44 pp), which left the order to the story id again; 4x spreads the
#     real range without letting one outlier gap dominate. Books
#     (sample_size) saturate at 8, the most books seen quoting a single
#     player in this market so far (the poller's US region returns about
#     that many); full_coverage_books (3) is the family's "solid" floor,
#     too low to separate 3 from 8.
#   defensive_trends / coaching_trends: trend_strength saturates at 40 =
#     2x the families' trend_threshold (20, the materiality floor);
#     sample_size (games) saturates at 6 = 2x the 3-game thin threshold.
#   role_changes: role_momentum (trend_strength) runs 60-100 above the
#     family's own 60 threshold, so (value - 60) / 40; games as above.
# A metric that is missing on a story is simply left out of the average.
TIEBREAK_SCALES = {
    "market_intelligence": {"magnitude": 32.0, "sample": 8.0},
    "defensive_trends": {"magnitude": 40.0, "sample": 6.0},
    "coaching_trends": {"magnitude": 40.0, "sample": 6.0},
    "role_changes": {"magnitude": 40.0, "magnitude_offset": 60.0, "sample": 6.0},
}

# Anthropic list prices per million tokens for the model every call here
# uses (card_writer_common.MODEL_NAME). Used only for the cost ceiling and
# the report; a billing-page figure is the authority.
PRICE_PER_MTOK = {"input": 3.0, "cache_write": 3.75, "cache_read": 0.30, "output": 15.0}


# ---------------------------------------------------------------------------
# Cost meter: wraps the one HTTP call every model call goes through.
# ---------------------------------------------------------------------------
class CostMeter:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls: list[dict] = []
        self._real_post = None

    def install(self):
        if self._real_post is not None:
            return
        self._real_post = cwc.requests.post
        meter = self

        def counting_post(*args, **kwargs):
            resp = meter._real_post(*args, **kwargs)
            try:
                usage = resp.json().get("usage") or {}
            except Exception:  # noqa: BLE001 -- a non-JSON body is a failed call; nothing to meter
                usage = {}
            meter.record(usage, status=resp.status_code)
            return resp

        cwc.requests.post = counting_post

    def uninstall(self):
        if self._real_post is not None:
            cwc.requests.post = self._real_post
            self._real_post = None

    def record(self, usage: dict, status=200):
        with self.lock:
            self.calls.append({
                "status": status,
                "input_tokens": int(usage.get("input_tokens") or 0),
                "cache_creation_input_tokens": int(usage.get("cache_creation_input_tokens") or 0),
                "cache_read_input_tokens": int(usage.get("cache_read_input_tokens") or 0),
                "output_tokens": int(usage.get("output_tokens") or 0),
            })

    @staticmethod
    def cost_of(calls: list[dict]) -> float:
        return round(sum(
            c["input_tokens"] * PRICE_PER_MTOK["input"]
            + c["cache_creation_input_tokens"] * PRICE_PER_MTOK["cache_write"]
            + c["cache_read_input_tokens"] * PRICE_PER_MTOK["cache_read"]
            + c["output_tokens"] * PRICE_PER_MTOK["output"]
            for c in calls
        ) / 1e6, 4)

    def cost(self) -> float:
        with self.lock:
            return self.cost_of(self.calls)

    def snapshot(self) -> int:
        with self.lock:
            return len(self.calls)

    def totals(self) -> dict:
        with self.lock:
            calls = list(self.calls)
        return {
            "calls": len(calls),
            "input_tokens": sum(c["input_tokens"] for c in calls),
            "cache_creation_input_tokens": sum(c["cache_creation_input_tokens"] for c in calls),
            "cache_read_input_tokens": sum(c["cache_read_input_tokens"] for c in calls),
            "output_tokens": sum(c["output_tokens"] for c in calls),
            "estimated_cost_usd": self.cost_of(calls),
        }


# ---------------------------------------------------------------------------
# Per-thread capture of the interrogation/EPS modules' own log lines, so a
# failure can be attributed to the story that produced it even with four
# workers interleaving on stdout. Lines still reach the real stdout.
# ---------------------------------------------------------------------------
class ThreadLogCapture:
    def __init__(self):
        self._local = threading.local()
        self._real = None

    def install(self):
        if self._real is None:
            self._real = sys.stdout
            sys.stdout = self

    def uninstall(self):
        if self._real is not None:
            sys.stdout = self._real
            self._real = None

    def write(self, text):
        buf = getattr(self._local, "buf", None)
        if buf is not None:
            buf.append(text)
        (self._real or sys.__stdout__).write(text)

    def flush(self):
        (self._real or sys.__stdout__).flush()

    def start(self):
        self._local.buf = []

    def stop(self) -> str:
        buf = getattr(self._local, "buf", None) or []
        self._local.buf = None
        return "".join(buf)


_CAPTURE = ThreadLogCapture()

_FAILURE_MARKERS = (
    ("violation persisted after retry", "language/consistency check persisted after retry"),
    ("malformed shape persisted", "malformed response shape persisted after retry"),
    ("retry response also malformed", "malformed response shape persisted after retry"),
    ("API call failed", "API call failed"),
)


def failure_reason_from_log(log_text: str, prefix: str) -> str | None:
    """The most specific reason the module's own log gives for a None
    result: the check name from a '<check> violation persisted' line, or a
    malformed-shape / API-failure line. None when the log shows no failure."""
    lines = [ln for ln in (log_text or "").splitlines() if ln.startswith(prefix)]
    for ln in reversed(lines):
        for marker, label in _FAILURE_MARKERS:
            if marker in ln:
                if marker == "violation persisted after retry":
                    check = ln.split(prefix, 1)[1].split(" violation persisted", 1)[0].strip()
                    return f"{check} check persisted after retry"
                return label
    return None


# ---------------------------------------------------------------------------
# 1. Pre-filter
# ---------------------------------------------------------------------------
def expected_week_for_family(family: str, upcoming_week: int) -> int:
    return upcoming_week if family in UPCOMING_WEEK_FAMILIES else upcoming_week - 1


def story_key(story: dict) -> str:
    return story.get("id") or story.get("story_id") or "|".join(str(story.get(k)) for k in (
        "intelligence_family", "entity_key", "primary_signal_name", "season", "week"))


def _magnitude_for(story: dict) -> float | None:
    fam = story.get("intelligence_family")
    if fam == "market_intelligence":
        hero = story.get("hero_metric") or {}
        v = hero.get("delta_value")
        if v is None:
            v = (story.get("primary_signal") or {}).get("value")
        return abs(float(v)) if v is not None else None
    v = story.get("trend_strength")
    return float(v) if v is not None else None


def tiebreak_score(story: dict, config: dict = PREP_CONFIG) -> dict:
    """
    tiebreak = tiebreak_cap x mean(clamp(metric / scale, 0, 1) over the
    metrics present), i.e. 0 .. tiebreak_cap. Metrics: a family-specific
    magnitude and the sample size, each against TIEBREAK_SCALES. Returns
    {"tiebreak", "tiebreak_inputs"}; tiebreak is 0 when nothing is present.
    """
    fam = story.get("intelligence_family")
    scales = TIEBREAK_SCALES.get(fam)
    if not scales:
        return {"tiebreak": 0.0, "tiebreak_inputs": {}}
    parts, inputs = [], {}
    mag = _magnitude_for(story)
    if mag is not None:
        norm = max(0.0, min(1.0, (mag - scales.get("magnitude_offset", 0.0)) / scales["magnitude"]))
        parts.append(norm); inputs["magnitude"] = {"value": round(mag, 2), "scale": scales["magnitude"], "normalized": round(norm, 3)}
    size = story.get("sample_size")
    if size is not None:
        norm = max(0.0, min(1.0, float(size) / scales["sample"]))
        parts.append(norm); inputs["sample"] = {"value": size, "scale": scales["sample"], "normalized": round(norm, 3)}
    tb = round(config["tiebreak_cap"] * (sum(parts) / len(parts)), 2) if parts else 0.0
    return {"tiebreak": tb, "tiebreak_inputs": inputs}


def base_score(story: dict, config: dict = PREP_CONFIG) -> dict:
    """The deterministic pre-interrogation ranking inputs for one story:
    base evidence strength + classification bonus + the capped tie-break."""
    evidence = compute_evidence_strength(story, None)["score"]
    bonus = config["classification_bonus"].get(story.get("evidence_classification"), 0.0)
    tb = tiebreak_score(story, config)
    return {"base_evidence_strength": evidence, "classification_bonus": bonus, **tb,
            "raw_score": round(evidence + bonus + tb["tiebreak"], 2)}


def prefilter_candidates(stories: list[dict], season: int, upcoming_week: int, config: dict = PREP_CONFIG) -> dict:
    """
    Deterministic, no API calls. Returns {
      "considered": int, "stale_excluded": [...], "hidden_excluded": int,
      "candidates": [...], "near_misses": [...], "family_counts": {...},
      "families_without_current_stories": [...], "config": {...}
    }. candidates/near_misses carry story_key, family, entity, headline,
    raw_score, family_penalty, adjusted_score, selection_rank.
    """
    stale, hidden, eligible = [], 0, []
    for s in stories:
        fam = s.get("intelligence_family")
        if fam not in FAMILIES:
            continue
        if s.get("is_visible") is False or s.get("sanity_check_passed") is False:
            hidden += 1
            continue
        exp = expected_week_for_family(fam, upcoming_week)
        if int(s.get("season") or 0) != int(season) or int(s.get("week") or 0) != exp:
            stale.append({"story_key": story_key(s), "family": fam, "season": s.get("season"), "week": s.get("week"), "expected_week": exp})
            continue
        sc = base_score(s, config)
        eligible.append({
            "story_key": story_key(s), "family": fam,
            "entity": (s.get("entity") or {}).get("player_name") or (s.get("entity") or {}).get("team"),
            "headline": s.get("headline"), "evidence_classification": s.get("evidence_classification"),
            "interrogation_present": isinstance(s.get("interrogation"), dict) and bool(s.get("interrogation")),
            **sc,
        })
    present_families = {e["family"] for e in eligible}
    # Greedy selection with the family penalty recomputed each round.
    penalties = config["family_penalty"]
    remaining = sorted(eligible, key=lambda e: (-e["raw_score"], e["family"], str(e["story_key"])))
    selected: list[dict] = []
    counts = {f: 0 for f in FAMILIES}

    def adjusted(e):
        pen = penalties[min(counts[e["family"]], len(penalties) - 1)]
        return round(e["raw_score"] - pen, 2), pen

    while remaining and len(selected) < config["max_candidates"]:
        scored = sorted(((adjusted(e), e) for e in remaining), key=lambda t: (-t[0][0], t[1]["family"], str(t[1]["story_key"])))
        (adj, pen), best = scored[0]
        if adj < config["min_score"]:
            break
        best = dict(best, family_penalty=pen, adjusted_score=adj, selection_rank=len(selected) + 1)
        selected.append(best)
        counts[best["family"]] += 1
        remaining = [e for e in remaining if e["story_key"] != best["story_key"]]
    near = []
    for e in remaining:
        adj, pen = adjusted(e)
        near.append(dict(e, family_penalty=pen, adjusted_score=adj))
    near.sort(key=lambda e: (-e["adjusted_score"], e["family"], str(e["story_key"])))
    return {
        "label": "interrogation candidates",
        "considered": len(stories),
        "stale_excluded": stale,
        "hidden_excluded": hidden,
        "eligible": len(eligible),
        "candidates": selected,
        "near_misses": near[:config["near_miss_count"]],
        "family_counts": {f: counts[f] for f in FAMILIES},
        "families_without_current_stories": [f for f in FAMILIES if f not in present_families],
        "config": {k: config[k] for k in ("max_candidates", "min_score", "classification_bonus", "family_penalty", "near_miss_count", "tiebreak_cap")},
    }


# ---------------------------------------------------------------------------
# 2. Reuse logic
# ---------------------------------------------------------------------------
def content_fingerprint(story: dict, config: dict = PREP_CONFIG) -> str:
    payload = {k: story.get(k) for k in config["fingerprint_fields"]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def _parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def interrogation_reuse_decision(story: dict, force: bool = False, config: dict = PREP_CONFIG) -> tuple[str, str]:
    """
    ("reuse" | "compute", reason). Reuse only when a non-null interrogation
    is stored, its provenance fingerprint matches the story's current
    content, and -- for Market stories -- the row was not rewritten more
    than market_refresh_tolerance_minutes after the interrogation was
    produced (a Market Refresh; this step's own write lands within
    seconds). --force recomputes everything.
    """
    if force:
        return "compute", "forced"
    it = story.get("interrogation")
    if not isinstance(it, dict) or not it:
        return "compute", "no stored interrogation (null or previously failed)"
    prov = it.get("provenance") or {}
    if prov.get("content_fingerprint") != content_fingerprint(story, config):
        return "compute", "story content changed since the stored interrogation"
    if story.get("intelligence_family") in UPCOMING_WEEK_FAMILIES:
        computed_at = _parse_ts(prov.get("computed_at"))
        updated_at = _parse_ts(story.get("updated_at"))
        if computed_at is None:
            return "compute", "stored interrogation has no computed_at (not produced by this step)"
        if updated_at and updated_at > computed_at + timedelta(minutes=config["market_refresh_tolerance_minutes"]):
            return "compute", "Market Refresh after the stored interrogation"
    return "reuse", "fingerprint unchanged"


def stamp_provenance(interrogation: dict, story: dict, now: datetime, config: dict = PREP_CONFIG) -> dict:
    out = dict(interrogation)
    out["provenance"] = {
        "content_fingerprint": content_fingerprint(story, config),
        "computed_at": now.isoformat(),
        "produced_by": "prep_interrogation_eps",
    }
    return out


# ---------------------------------------------------------------------------
# 2. Inputs the Oct 6 dry run used, from their production read paths.
# ---------------------------------------------------------------------------
def load_pool(season: int, upcoming_week: int, secret: str) -> dict:
    """Current-week stories for all four families via the existing signed
    read route: the upcoming week (Market) and the reconciled week (the
    other three). Returns {"rows": [...], "reads": [...]}."""
    from intelligence_write import read_intelligence_stories
    rows, reads = [], []
    for week in sorted({upcoming_week, upcoming_week - 1}):
        r = read_intelligence_stories(season, week, secret)
        reads.append({"season": season, "week": week, "ok": r["ok"], "rows": len(r["rows"]), "error": r["error"]})
        if not r["ok"]:
            raise RuntimeError(f"intelligence read failed for season={season} week={week}: {r['error']!r}")
        rows.extend(r["rows"])
    return {"rows": rows, "reads": reads}


def load_context(season: int, upcoming_week: int, secret: str) -> dict:
    """prior_history inputs (Table 1 season evidence + reconciled weeks)
    and market_data inputs (this week's price history), read through the
    same routes api/curate_home_shelves.py / api/index.py use for shelf
    cards. Each part degrades to None with a reason instead of raising:
    interrogate_story() accepts null for both and says so in its record."""
    from market_value import read_price_history
    from player_season_history import read_player_season_evidence_rows_for_season, reconciled_weeks_from_redzone_weekly_rows
    from reconcile_week import read_player_redzone_weekly_rows
    ctx = {"season": season, "as_of_week": None, "reconciled_weeks": [], "rows_by_player": {}, "price_rows_by_player": {}, "notes": []}
    rz = read_player_redzone_weekly_rows(season, secret)
    if rz["ok"]:
        ctx["reconciled_weeks"] = reconciled_weeks_from_redzone_weekly_rows(rz["rows"])
        ctx["as_of_week"] = max(ctx["reconciled_weeks"]) if ctx["reconciled_weeks"] else None
    else:
        ctx["notes"].append(f"redzone weekly read failed: {rz['error']!r}; prior_history will be null")
    if ctx["as_of_week"] is not None:
        ev = read_player_season_evidence_rows_for_season(season, secret)
        if ev["ok"]:
            for row in ev["rows"]:
                ctx["rows_by_player"].setdefault(row["player_id"], []).append(row)
        else:
            ctx["notes"].append(f"season evidence read failed: {ev['error']!r}; prior_history will be null")
    ph = read_price_history(season, upcoming_week, secret)
    if ph["ok"]:
        for row in ph["rows"]:
            if row.get("player_id"):
                ctx["price_rows_by_player"].setdefault(row["player_id"], []).append(row)
    else:
        ctx["notes"].append(f"price history read failed: {ph['error']!r}; market_data will be null")
    return ctx


def inputs_for_story(story: dict, ctx: dict) -> tuple[dict | None, dict | None]:
    from curate_home_shelves import _market_data_for_candidate
    from player_season_history import build_player_history_package
    ent = story.get("entity") or {}
    pid = ent.get("player_id") if ent.get("type") == "player" else None
    if not pid:
        return None, None
    prior = None
    if ctx.get("as_of_week") is not None and ctx.get("rows_by_player"):
        prior = build_player_history_package(pid, ctx["season"], ctx["as_of_week"], ctx["rows_by_player"].get(pid, []), ctx["reconciled_weeks"])
    market = _market_data_for_candidate(ctx.get("price_rows_by_player", {}).get(pid, []))
    return prior, market


# ---------------------------------------------------------------------------
# 2. Execution
# ---------------------------------------------------------------------------
def process_story(story: dict, ctx: dict, api_key: str, *, force: bool, now: datetime,
                  interrogate=interrogate_story, eps_fn=compute_eps, config: dict = PREP_CONFIG) -> dict:
    """One story: reuse-or-interrogate (one extra attempt on None), then
    always compute_eps. Pure apart from the model calls; returns the
    per-story record the report and the write step both read."""
    decision, reason = interrogation_reuse_decision(story, force, config)
    prior_history, market_data = inputs_for_story(story, ctx)
    rec = {
        "story_key": story_key(story), "family": story.get("intelligence_family"),
        "entity": (story.get("entity") or {}).get("player_name") or (story.get("entity") or {}).get("team"),
        "headline": story.get("headline"), "interrogation_decision": decision, "interrogation_reason": reason,
        "prior_history_provided": prior_history is not None, "market_data_provided": market_data is not None,
        "interrogation_attempts": 0, "interrogation_status": None, "eps_status": None, "error": None,
    }
    rec["interrogation_failure_reasons"] = []
    rec["eps_failure_reason"] = None
    interrogation = story.get("interrogation") if decision == "reuse" else None
    if decision == "reuse":
        rec["interrogation_status"] = "reused"
    else:
        for attempt in (1, 2):
            rec["interrogation_attempts"] = attempt
            _CAPTURE.start()
            try:
                interrogation = interrogate(story, api_key, prior_history=prior_history, market_data=market_data)
            except Exception as e:  # noqa: BLE001 -- one story's failure must never sink the run
                interrogation = None
                rec["error"] = f"interrogation: {e!r}"[:300]
            log = _CAPTURE.stop()
            if interrogation:
                break
            rec["interrogation_failure_reasons"].append(
                failure_reason_from_log(log, "[story_interrogation]") or (rec["error"] or "returned None (no reason logged)"))
        if interrogation:
            interrogation = stamp_provenance(interrogation, story, now, config)
            rec["interrogation_status"] = "completed"
        else:
            rec["interrogation_status"] = "failed"
            reasons = rec["interrogation_failure_reasons"]
            rec["same_reason_both_attempts"] = len(reasons) == 2 and reasons[0] == reasons[1]
    _CAPTURE.start()
    try:
        eps = eps_fn(story, interrogation, prior_history, api_key)
    except Exception as e:  # noqa: BLE001
        eps = None
        rec["error"] = (rec["error"] or "") + f" eps: {e!r}"[:300]
    log = _CAPTURE.stop()
    if eps:
        eps = dict(eps, computed_at=now.isoformat())
        rec["eps_status"] = "completed"
    else:
        rec["eps_status"] = "failed"
        rec["eps_failure_reason"] = failure_reason_from_log(log, "[eps]") or (rec["error"] or "returned None (no reason logged)")
    # A fresh evaluation exists only when BOTH halves succeeded this run
    # (a reused interrogation counts: it was validated as current).
    rec["evaluated_this_run"] = rec["interrogation_status"] in ("completed", "reused") and rec["eps_status"] == "completed"
    rec["interrogation"] = interrogation
    rec["eps"] = eps
    rec["signal_verdict"] = (interrogation or {}).get("signal_verdict")
    return rec


def run_execution(candidates: list[dict], stories_by_key: dict, ctx: dict, api_key: str, *, force: bool,
                  max_cost_usd: float, concurrency: int, meter: CostMeter, interrogate=interrogate_story,
                  eps_fn=compute_eps, config: dict = PREP_CONFIG, now: datetime | None = None) -> dict:
    """Runs process_story over the candidates with `concurrency` workers.
    Before each story starts, checks that the cost so far plus one more
    story's expected cost stays under max_cost_usd; when it would not,
    stops starting new stories (in-flight ones finish) and reports."""
    now = now or datetime.now(timezone.utc)
    queue = list(candidates)
    results: list[dict] = []
    skipped: list[dict] = []
    lock = threading.Lock()
    stop = {"hit": False, "at_cost": None, "after": None}
    started = {"n": 0, "api_stories": 0}

    def expected_next_cost():
        n = started["api_stories"]
        spent = meter.cost()
        return spent / n if n else config["estimated_cost_per_story_usd"]

    def worker():
        while True:
            with lock:
                if not queue:
                    return
                cand = queue[0]
                story = stories_by_key[cand["story_key"]]
                # Every story makes at least the EPS call, so every start
                # counts against the ceiling (a reused interrogation only
                # makes the story cheaper, which the running average sees).
                projected = meter.cost() + expected_next_cost()
                if projected > max_cost_usd:
                    if not stop["hit"]:
                        stop.update({"hit": True, "at_cost": round(meter.cost(), 4), "after": len(results)})
                    skipped.extend(queue)
                    queue.clear()
                    return
                queue.pop(0)
                started["n"] += 1
                started["api_stories"] += 1
            rec = process_story(story, ctx, api_key, force=force, now=now, interrogate=interrogate, eps_fn=eps_fn, config=config)
            rec["selection_rank"] = cand.get("selection_rank")
            with lock:
                results.append(rec)

    _CAPTURE.install()
    try:
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
            for _ in range(max(1, concurrency)):
                ex.submit(worker)
    finally:
        _CAPTURE.uninstall()
    results.sort(key=lambda r: r.get("selection_rank") or 0)
    return {"results": results, "cost_ceiling_hit": stop["hit"], "cost_at_stop": stop["at_cost"],
            "skipped_for_cost": [{"story_key": s["story_key"], "entity": s.get("entity"), "family": s.get("family")} for s in skipped],
            "max_cost_usd": max_cost_usd}


# ---------------------------------------------------------------------------
# Write (only with --write) through the existing signed path.
# ---------------------------------------------------------------------------
_DB_ONLY_FIELDS = ("id", "created_at", "updated_at")


def rows_for_write(results: list[dict], stories_by_key: dict) -> list[dict]:
    """The read row, unchanged except interrogation/eps, for every story
    EVALUATED THIS RUN (interrogation completed or reused AND eps
    completed). A story whose interrogation or EPS failed is not written
    at all: its row keeps its previous database state exactly, including
    any older interrogation and eps. DB-generated fields are dropped; the
    route upserts on the stories' unique key."""
    rows = []
    for r in results:
        if not r.get("evaluated_this_run"):
            continue
        row = {k: v for k, v in stories_by_key[r["story_key"]].items() if k not in _DB_ONLY_FIELDS}
        row["interrogation"] = r.get("interrogation")
        row["eps"] = r.get("eps")
        rows.append(row)
    return rows


def write_results(results: list[dict], stories_by_key: dict, secret: str, write_fn=None) -> dict:
    from intelligence_write import write_intelligence_rows
    write_fn = write_fn or write_intelligence_rows
    rows = rows_for_write(results, stories_by_key)
    if not rows:
        return {"attempted": False, "rows": 0, "success": None, "status_code": None, "error": None}
    out = write_fn(rows, [], secret)
    return {"attempted": True, "rows": len(rows), "success": out.get("success"), "status_code": out.get("status_code"), "error": out.get("error")}


# ---------------------------------------------------------------------------
# 3. Summary
# ---------------------------------------------------------------------------
def _reason_counts(results: list[dict], key: str) -> dict:
    counts: dict[str, int] = {}
    for r in results:
        gates = ((r.get("eps") or {}).get("gates") or {})
        reason = gates.get(key)
        if not reason:
            continue
        for part in str(reason).split("; "):
            counts[part] = counts.get(part, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def stale_evaluations_in_pool(stories: list[dict], run_started_at: datetime, config: dict = PREP_CONFIG) -> list[dict]:
    """
    Report only. Every story in the pool whose STORED interrogation or
    eps predates this run or no longer matches the story's content --
    what could leak into the publication pool if a reader trusted the
    stored columns as current. Reasons: no provenance (not produced by
    this step, age unknown), computed_at older than this run, content
    fingerprint mismatch, eps computed_at older than this run.
    """
    out = []
    for s in stories:
        it, eps = s.get("interrogation"), s.get("eps")
        if not isinstance(it, dict) and not isinstance(eps, dict):
            continue
        reasons = []
        if isinstance(it, dict):
            prov = it.get("provenance") or {}
            if not prov:
                reasons.append("interrogation has no provenance (age unknown)")
            else:
                ts = _parse_ts(prov.get("computed_at"))
                if ts is None or ts < run_started_at:
                    reasons.append("interrogation computed before this run")
                if prov.get("content_fingerprint") != content_fingerprint(s, config):
                    reasons.append("story content changed since the interrogation")
        if isinstance(eps, dict):
            ts = _parse_ts(eps.get("computed_at"))
            if ts is None or ts < run_started_at:
                reasons.append("eps computed before this run (or undated)")
        if reasons:
            gates = (eps or {}).get("gates") or {}
            out.append({"story_key": story_key(s), "family": s.get("intelligence_family"),
                        "entity": (s.get("entity") or {}).get("player_name") or (s.get("entity") or {}).get("team"),
                        "stored_big_one_eligible": gates.get("big_one_eligible"), "stored_watchlist_eligible": gates.get("watchlist_eligible"),
                        "reasons": reasons})
    return out


def build_summary(prefilter: dict, execution: dict, meter: CostMeter, *, season: int, upcoming_week: int,
                  mode: str, write_result: dict | None, started_at: datetime, ctx_notes: list, reads: list,
                  stale_in_pool: list | None = None) -> dict:
    results = execution["results"]
    by_status = lambda s: sum(1 for r in results if r["interrogation_status"] == s)  # noqa: E731
    gates_rows = []
    for r in results:
        eps = r.get("eps") or {}
        g = eps.get("gates") or {}
        gates_rows.append({
            "story_key": r["story_key"], "family": r["family"], "entity": r["entity"], "headline": r["headline"],
            "composite": eps.get("composite_score"), "evidence": ((eps.get("dimensions") or {}).get("evidence_strength") or {}).get("score"),
            "signal_verdict": r.get("signal_verdict"), "big_one_eligible": g.get("big_one_eligible"), "watchlist_eligible": g.get("watchlist_eligible"),
            "big_one_blocked_reason": g.get("big_one_blocked_reason"), "watchlist_blocked_reason": g.get("watchlist_blocked_reason"),
            "interrogation_status": r["interrogation_status"], "eps_status": r["eps_status"],
            "evaluated_this_run": bool(r.get("evaluated_this_run")),
        })
    fresh = [g for g in gates_rows if g["evaluated_this_run"]]
    marker = "carries stale or no evaluation, excluded from this run's qualifiers"
    not_written_interrogation = [
        {"story_key": r["story_key"], "family": r["family"], "entity": r["entity"], "reasons": r.get("interrogation_failure_reasons"),
         "same_reason_both_attempts": r.get("same_reason_both_attempts"), "status": marker}
        for r in results if r["interrogation_status"] == "failed"]
    not_written_eps = [
        {"story_key": r["story_key"], "family": r["family"], "entity": r["entity"], "reason": r.get("eps_failure_reason"), "status": marker}
        for r in results if r["interrogation_status"] != "failed" and r["eps_status"] == "failed"]
    return {
        "label": "interrogation candidates",
        "run_at": started_at.isoformat(), "mode": mode, "season": season, "upcoming_week": upcoming_week,
        "reads": reads, "context_notes": ctx_notes,
        "stories_considered": prefilter["considered"], "stale_excluded": len(prefilter["stale_excluded"]), "hidden_excluded": prefilter["hidden_excluded"],
        "families_without_current_stories": prefilter["families_without_current_stories"],
        "candidates_selected": len(prefilter["candidates"]), "candidates_by_family": prefilter["family_counts"],
        "candidates": prefilter["candidates"], "near_misses": prefilter["near_misses"], "prefilter_config": prefilter["config"],
        "interrogations": {"completed": by_status("completed"), "reused": by_status("reused"), "failed": by_status("failed")},
        "eps": {"completed": sum(1 for r in results if r["eps_status"] == "completed"), "failed": sum(1 for r in results if r["eps_status"] == "failed")},
        "evaluated_this_run": len(fresh),
        "not_written_interrogation_failed": not_written_interrogation,
        "not_written_eps_failed": not_written_eps,
        # Qualifiers come ONLY from stories evaluated successfully in this run.
        "big_one_qualifiers": [g for g in fresh if g["big_one_eligible"]],
        "watchlist_qualifiers": [g for g in fresh if g["watchlist_eligible"]],
        "big_one_blocked_reasons": _reason_counts([r for r in results if r.get("evaluated_this_run")], "big_one_blocked_reason"),
        "watchlist_blocked_reasons": _reason_counts([r for r in results if r.get("evaluated_this_run")], "watchlist_blocked_reason"),
        "gates": gates_rows,
        "stale_evaluations_in_pool": stale_in_pool or [],
        "cost": {**meter.totals(), "max_cost_usd": execution["max_cost_usd"], "ceiling_hit": execution["cost_ceiling_hit"],
                 "cost_at_stop": execution["cost_at_stop"], "skipped_for_cost": execution["skipped_for_cost"]},
        "write": write_result,
        "elapsed_seconds": round((datetime.now(timezone.utc) - started_at).total_seconds(), 1),
    }


def summary_markdown(s: dict) -> str:
    def row(g):
        return f"| {g['family']} | {g['entity']} | {g['composite']} | {g['evidence']} | {g['signal_verdict']} |"
    lines = [
        f"# Interrogation candidates -- {s['season']} week {s['upcoming_week']} ({s['mode']})", "",
        f"Run at {s['run_at']}. Stories considered {s['stories_considered']}, stale excluded {s['stale_excluded']}, hidden {s['hidden_excluded']}, "
        f"families without current stories: {s['families_without_current_stories'] or 'none'}.", "",
        f"Candidates selected: {s['candidates_selected']} " + json.dumps(s["candidates_by_family"]), "",
        f"Interrogations: {s['interrogations']['completed']} completed, {s['interrogations']['reused']} reused, {s['interrogations']['failed']} failed. "
        f"EPS: {s['eps']['completed']} completed, {s['eps']['failed']} failed. Evaluated this run (both halves): {s['evaluated_this_run']}.", "",
        f"Cost: ${s['cost']['estimated_cost_usd']:.2f} of ${s['cost']['max_cost_usd']:.2f} ceiling"
        + (f" -- CEILING HIT after {len(s['gates'])} stories, {len(s['cost']['skipped_for_cost'])} skipped" if s["cost"]["ceiling_hit"] else "")
        + f"; {s['cost']['calls']} calls, in {s['cost']['input_tokens']}, cache write {s['cost']['cache_creation_input_tokens']}, "
          f"cache read {s['cost']['cache_read_input_tokens']}, out {s['cost']['output_tokens']}.", "",
        "## Candidates (rank, family, entity, raw incl. tie-break, penalty, adjusted)", "",
    ]
    for c in s["candidates"]:
        lines.append(f"{c['selection_rank']}. {c['family']} | {c['entity']} | {c['raw_score']} (tie {c.get('tiebreak', 0)}) | -{c['family_penalty']} | {c['adjusted_score']} | {c['evidence_classification']}")
    lines += ["", "## Near misses (next ranked, adjusted score)", ""]
    for c in s["near_misses"]:
        lines.append(f"- {c['family']} | {c['entity']} | raw {c['raw_score']} | adjusted {c['adjusted_score']} | {c['evidence_classification']}")
    lines += ["", "## Big One qualifiers", "", "| family | entity | composite | evidence | verdict |", "|---|---|---|---|---|"]
    lines += [row(g) for g in s["big_one_qualifiers"]] or ["| none | | | | |"]
    lines += ["", "## Watchlist qualifiers", "", "| family | entity | composite | evidence | verdict |", "|---|---|---|---|---|"]
    lines += [row(g) for g in s["watchlist_qualifiers"]] or ["| none | | | | |"]
    lines += ["", "## Blocked reasons (evaluated-this-run stories only)", "", "Big One: " + json.dumps(s["big_one_blocked_reasons"]), "", "Watchlist: " + json.dumps(s["watchlist_blocked_reasons"]), ""]
    lines += ["## Not written (interrogation failed)", ""]
    lines += [f"- {x['family']} | {x['entity']} | attempts: {x['reasons']} | same reason both attempts: {x['same_reason_both_attempts']} | {x['status']}" for x in s["not_written_interrogation_failed"]] or ["- none"]
    lines += ["", "## Not written (EPS failed)", ""]
    lines += [f"- {x['family']} | {x['entity']} | {x['reason']} | {x['status']}" for x in s["not_written_eps_failed"]] or ["- none"]
    lines += ["", f"## Stale or unverifiable stored evaluations in the pool ({len(s.get('stale_evaluations_in_pool') or [])})", ""]
    lines += [f"- {x['family']} | {x['entity']} | stored gates big_one={x['stored_big_one_eligible']} watchlist={x['stored_watchlist_eligible']} | {'; '.join(x['reasons'])}" for x in (s.get("stale_evaluations_in_pool") or [])[:50]] or ["- none"]
    lines += [""]
    lines += ["## Per story", "", "| rank | family | entity | interrogation | verdict | eps | composite | evidence | big one | watchlist |", "|---|---|---|---|---|---|---|---|---|---|"]
    for i, g in enumerate(s["gates"], 1):
        lines.append(f"| {i} | {g['family']} | {g['entity']} | {g['interrogation_status']} | {g['signal_verdict']} | {g['eps_status']} | {g['composite']} | {g['evidence']} | {g['big_one_eligible']} | {g['watchlist_eligible']} |")
    if s.get("write"):
        lines += ["", f"Write: {json.dumps(s['write'])}"]
    return "\n".join(lines) + "\n"


def save_summary(summary: dict, outputs_dir: Path = HERE / "outputs") -> dict:
    outputs_dir.mkdir(parents=True, exist_ok=True)
    stamp = summary["run_at"].replace(":", "").replace("-", "")[:15]
    jp = outputs_dir / f"prep_{stamp}.json"
    mp = outputs_dir / f"prep_{stamp}.md"
    jp.write_text(json.dumps(summary, indent=1, default=str))
    mp.write_text(summary_markdown(summary))
    return {"json": str(jp), "markdown": str(mp)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Interrogation candidates: pre-filter, interrogate, EPS. Dry run unless --write.")
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--week", type=int, required=True, help="the UPCOMING week (Market stories' week); the other families use week-1")
    ap.add_argument("--max-candidates", type=int, default=PREP_CONFIG["max_candidates"])
    ap.add_argument("--max-cost", type=float, default=PREP_CONFIG["max_cost_usd"])
    ap.add_argument("--concurrency", type=int, default=PREP_CONFIG["concurrency"])
    ap.add_argument("--force", action="store_true", help="recompute every interrogation, ignoring stored ones")
    ap.add_argument("--write", action="store_true", help="store results through the signed write route (default: dry run)")
    # Offline inputs, for diagnostics when the signed read routes are not
    # reachable from the machine running this (e.g. a local secret the
    # production routes reject). The pool file is a JSON list of story
    # rows in the read route's shape; the context file is load_context()'s
    # own dict. Neither changes what gets computed, only where the inputs
    # come from, and --write is refused with them: a write must be based
    # on rows read from the live table moments earlier.
    ap.add_argument("--pool-json", help="read the story pool from this JSON file instead of the signed read route")
    ap.add_argument("--context-json", help="read prior_history/market_data inputs from this JSON file instead of the signed read routes")
    args = ap.parse_args(argv)
    if args.write and (args.pool_json or args.context_json):
        print("--write cannot be combined with --pool-json/--context-json: a write must be based on a live read", file=sys.stderr)
        return 2

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    secret = os.environ.get("NFL_PIPELINE_WEBHOOK_SECRET")
    offline = bool(args.pool_json and args.context_json)
    if not api_key or (not secret and not offline):
        print("ANTHROPIC_API_KEY and NFL_PIPELINE_WEBHOOK_SECRET must be set (the secret may be omitted only with both --pool-json and --context-json)", file=sys.stderr)
        return 2
    config = dict(PREP_CONFIG, max_candidates=args.max_candidates)
    started = datetime.now(timezone.utc)
    mode = "write" if args.write else "dry-run"

    if args.pool_json:
        pool = {"rows": json.loads(Path(args.pool_json).read_text()), "reads": [{"source": f"file:{args.pool_json}"}]}
    else:
        pool = load_pool(args.season, args.week, secret)
    stories_by_key = {story_key(s): s for s in pool["rows"]}
    pre = prefilter_candidates(pool["rows"], args.season, args.week, config)
    print(f"[prep] {mode}: considered {pre['considered']} stories, {len(pre['candidates'])} interrogation candidates "
          f"{json.dumps(pre['family_counts'])}, stale {len(pre['stale_excluded'])}, families without current stories {pre['families_without_current_stories']}", flush=True)

    if args.context_json:
        ctx = json.loads(Path(args.context_json).read_text())
        ctx.setdefault("notes", []).append(f"context loaded from file:{args.context_json}")
    else:
        ctx = load_context(args.season, args.week, secret)
    for note in ctx["notes"]:
        print(f"[prep] context: {note}", flush=True)

    meter = CostMeter(); meter.install()
    try:
        execution = run_execution(pre["candidates"], stories_by_key, ctx, api_key, force=args.force, max_cost_usd=args.max_cost,
                                  concurrency=args.concurrency, meter=meter, config=config, now=started)
    finally:
        meter.uninstall()

    write_result = None
    if args.write:
        write_result = write_results(execution["results"], stories_by_key, secret)
        print(f"[prep] write: {json.dumps(write_result)}", flush=True)
    else:
        print(f"[prep] dry run: {len(rows_for_write(execution['results'], stories_by_key))} row(s) would be written with --write; nothing written", flush=True)

    stale = stale_evaluations_in_pool(pool["rows"], started, config)
    summary = build_summary(pre, execution, meter, season=args.season, upcoming_week=args.week, mode=mode, write_result=write_result,
                            started_at=started, ctx_notes=ctx["notes"], reads=pool["reads"], stale_in_pool=stale)
    paths = save_summary(summary)
    print(summary_markdown(summary))
    print(f"[prep] saved {paths['json']} and {paths['markdown']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
