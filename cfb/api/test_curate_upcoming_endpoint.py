"""
Endpoint tests for the upcoming-week mode of
POST /api/curate-and-write-cfb-shelves (cfb/api/index.py).

    python3 cfb/api/test_curate_upcoming_endpoint.py

Flask test client, every external call monkeypatched: the four signed
season reads return the synthetic 2099 fixture from
test_curate_upcoming_week.py, CFBD lookups return fixture dicts, /games
returns the fixture week, and the signed write (_forward) is captured
instead of sent. No network, no live CFBD, no live writes.
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

os.environ.setdefault("PIPELINE_INCOMING_SECRET", "test-incoming")
os.environ.setdefault("CFB_PIPELINE_WEBHOOK_SECRET", "test-webhook")

import pandas as pd  # noqa: E402

import index as idx  # noqa: E402  -- cfb/api/index.py
from test_curate_upcoming_week import (  # noqa: E402
    AP_RANKS, CONFERENCES, EXPECTED_KICKOFF_8001, FBS_IDS, SEASON, UPCOMING_GAMES, UPCOMING_WEEK,
    build_season, empty_roles,
)
from curate_cfb_shelves import CFB_SHELF_SCORE_COLUMNS  # noqa: E402

AUTH = {"X-Pipeline-Secret": "test-incoming"}


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    return bool(cond)


class Harness:
    """Installs the monkeypatches and records every outbound call."""

    def __init__(self, players: pd.DataFrame, allowed: pd.DataFrame, games: list):
        self.forward_calls: list = []
        self.games_calls: list = []
        self.cfbd_error_on_games = False
        idx.fbs_team_ids = lambda season: FBS_IDS
        idx.fetch_ap_top25 = lambda season, week: AP_RANKS
        idx.team_conference_map = lambda season: CONFERENCES
        idx.cfb_player_redzone_weekly_snapshot = lambda season, secret: players.copy()
        idx.cfb_defense_redzone_allowed_weekly_snapshot = lambda season, secret: allowed.copy()
        idx.cfb_player_role_weekly_snapshot = lambda season, secret: empty_roles()
        idx.cfb_player_receiving_weekly_snapshot = lambda season, secret: pd.DataFrame(columns=[
            "player_id", "player_name", "position_group", "team_id", "team", "opponent_team_id", "opponent",
            "season", "week", "game_id", "targets", "receptions", "team_targets", "target_share",
        ])

        def fake_fetch_games(season, week, *, season_type="regular", classification="fbs"):
            self.games_calls.append({"season": season, "week": week, "season_type": season_type})
            if self.cfbd_error_on_games:
                raise idx.CFBDError("simulated CFBD outage")
            return games

        idx.fetch_games = fake_fetch_games

        def fake_forward(rows, secret, url_env, default_url):
            self.forward_calls.append({"rows": rows, "url_env": url_env, "default_url": default_url})
            return {"success": True, "status_code": 200, "error": None, "response_body": '{"ok":true}', "rows": len(rows), "url": default_url}

        idx._forward = fake_forward


if __name__ == "__main__":
    r = []
    client = idx.app.test_client()
    players, allowed = build_season(UPCOMING_WEEK - 1)
    h = Harness(players, allowed, UPCOMING_GAMES)

    # ---- auth gate unchanged ---------------------------------------------
    resp = client.post("/api/curate-and-write-cfb-shelves", json={"season": SEASON, "week": 7})
    r.append(check("401 without X-Pipeline-Secret (normal mode)", resp.status_code == 401))
    resp = client.post("/api/curate-and-write-cfb-shelves", json={"season": SEASON, "week": 7, "upcoming": True})
    r.append(check("401 without X-Pipeline-Secret (upcoming mode)", resp.status_code == 401))

    # ---- normal mode: byte-for-byte the old behavior ----------------------
    h.forward_calls.clear(); h.games_calls.clear()
    resp = client.post("/api/curate-and-write-cfb-shelves", json={"season": SEASON, "week": 7}, headers=AUTH)
    body = resp.get_json()
    r.append(check("normal mode: 200 with status ok", resp.status_code == 200 and body["status"] == "ok"))
    r.append(check("normal mode: response has no `mode` or upcoming-only keys",
                   "mode" not in body and "player_skeletons" not in body and "rows_with_kickoff" not in body))
    r.append(check("normal mode: fetch_games is never called", h.games_calls == []))
    r.append(check("normal mode: exactly one signed write, to the shelf-scores write URL",
                   len(h.forward_calls) == 1 and h.forward_calls[0]["url_env"] == "LOVABLE_CFB_PLAYER_SHELF_SCORES_WRITE_URL"))
    r.append(check("normal mode: forwarded rows carry exactly the typed columns + extra (no kickoff_utc)",
                   all(set(row.keys()) == set(CFB_SHELF_SCORE_COLUMNS + ["extra"]) for row in h.forward_calls[0]["rows"])))
    r.append(check("normal mode: a season_type of postseason is ignored, as before (not rejected)",
                   client.post("/api/curate-and-write-cfb-shelves",
                               json={"season": SEASON, "week": 7, "season_type": "postseason"}, headers=AUTH).status_code == 200))

    # ---- upcoming: postseason rejected -----------------------------------
    h.forward_calls.clear(); h.games_calls.clear()
    resp = client.post("/api/curate-and-write-cfb-shelves",
                       json={"season": SEASON, "week": 1, "upcoming": True, "season_type": "postseason"}, headers=AUTH)
    r.append(check("upcoming: season_type=postseason -> 400 with stage validate, before any read or CFBD call",
                   resp.status_code == 400 and resp.get_json()["stage"] == "validate" and h.games_calls == [] and h.forward_calls == []))

    # ---- upcoming: CFBD /games failure -----------------------------------
    h.cfbd_error_on_games = True
    resp = client.post("/api/curate-and-write-cfb-shelves", json={"season": SEASON, "week": UPCOMING_WEEK, "upcoming": True}, headers=AUTH)
    r.append(check("upcoming: a CFBD /games failure -> 502 with stage games, nothing written",
                   resp.status_code == 502 and resp.get_json()["stage"] == "games" and h.forward_calls == []))
    h.cfbd_error_on_games = False

    # ---- upcoming: refuses when the target week has real rows -------------
    players8, allowed8 = build_season(UPCOMING_WEEK)
    h8 = Harness(players8, allowed8, UPCOMING_GAMES)
    resp = client.post("/api/curate-and-write-cfb-shelves", json={"season": SEASON, "week": UPCOMING_WEEK, "upcoming": True}, headers=AUTH)
    r.append(check("upcoming: target week already ingested -> 409 with stage upcoming_conflict, nothing written",
                   resp.status_code == 409 and resp.get_json()["stage"] == "upcoming_conflict" and h8.forward_calls == []))
    r.append(check("upcoming: the normal mode still works for that same played week",
                   client.post("/api/curate-and-write-cfb-shelves", json={"season": SEASON, "week": UPCOMING_WEEK}, headers=AUTH).status_code == 200))

    # ---- upcoming: happy path ---------------------------------------------
    h = Harness(players, allowed, UPCOMING_GAMES)
    resp = client.post("/api/curate-and-write-cfb-shelves", json={"season": SEASON, "week": UPCOMING_WEEK, "upcoming": True}, headers=AUTH)
    body = resp.get_json()
    r.append(check("upcoming: 200 with status ok and mode upcoming", resp.status_code == 200 and body["status"] == "ok" and body["mode"] == "upcoming"))
    r.append(check("upcoming: /games fetched exactly once, for the target week, season_type regular",
                   h.games_calls == [{"season": SEASON, "week": UPCOMING_WEEK, "season_type": "regular"}]))
    r.append(check("upcoming: response counts -- 4 games in week, 1 skipped completed, 12 player + 12 defense skeletons, cfbd_calls 4",
                   body["games_in_week"] == 4 and body["games_skipped_completed"] == 1
                   and body["player_skeletons"] == 12 and body["defense_skeletons"] == 12 and body["cfbd_calls"] == 4))
    r.append(check("upcoming: week_rows == player skeletons (12) and placement_rows > 0",
                   body["week_rows"] == 12 and body["placement_rows"] > 0))
    r.append(check("upcoming: exactly one signed write, to the shelf-scores write URL only (never a raw-table write URL)",
                   len(h.forward_calls) == 1 and h.forward_calls[0]["url_env"] == "LOVABLE_CFB_PLAYER_SHELF_SCORES_WRITE_URL"
                   and "redzone" not in h.forward_calls[0]["default_url"]))
    rows = h.forward_calls[0]["rows"]
    with_kick = [row for row in rows if "kickoff_utc" in row]
    without_kick = [row for row in rows if "kickoff_utc" not in row]
    r.append(check("upcoming: forwarded rows for game 8001 carry kickoff_utc; rows for the TBD game 8004 omit it",
                   with_kick and all(row["game_id"] == 8001 and row["kickoff_utc"] == EXPECTED_KICKOFF_8001 for row in with_kick)
                   and without_kick and all(row["game_id"] == 8004 for row in without_kick)))
    r.append(check("upcoming: no forwarded row ever sends kickoff_utc as null", all(row.get("kickoff_utc", "absent") is not None for row in rows)))
    r.append(check("upcoming: rows_with_kickoff matches the forwarded rows carrying kickoff_utc",
                   body["rows_with_kickoff"] == len(with_kick) and len(with_kick) > 0))
    r.append(check("upcoming: forwarded rows keep the normal typed columns (shelf, archetype, game_id, scores) plus extra",
                   all(set(CFB_SHELF_SCORE_COLUMNS + ["extra"]) <= set(row.keys()) for row in rows)
                   and all(row["game_id"] in (8001, 8004) for row in rows)))
    r.append(check("upcoming: no forwarded row carries a raw current-game count (skeleton counts are not placement columns)",
                   all("rz_touches" not in row for row in rows)))

    # ---- upcoming: preview_only writes nothing ----------------------------
    h = Harness(players, allowed, UPCOMING_GAMES)
    resp = client.post("/api/curate-and-write-cfb-shelves",
                       json={"season": SEASON, "week": UPCOMING_WEEK, "upcoming": True, "preview_only": True}, headers=AUTH)
    body = resp.get_json()
    r.append(check("upcoming + preview_only: 200, mode upcoming, forward skipped, zero writes",
                   resp.status_code == 200 and body["mode"] == "upcoming"
                   and body["forward"] == {"skipped": "preview_only"} and h.forward_calls == []))
    r.append(check("upcoming + preview_only: sample rows still show kickoff_utc for game 8001",
                   any(s.get("kickoff_utc") == EXPECTED_KICKOFF_8001 for s in body["sample"]) or body["rows_with_kickoff"] > 0))

    # ---- upcoming with no rows for the season at all -----------------------
    hempty = Harness(players.iloc[0:0], allowed.iloc[0:0], UPCOMING_GAMES)
    resp = client.post("/api/curate-and-write-cfb-shelves", json={"season": SEASON, "week": UPCOMING_WEEK, "upcoming": True}, headers=AUTH)
    r.append(check("upcoming with an empty season -> the existing 404 (nobody to stub), nothing written",
                   resp.status_code == 404 and hempty.forward_calls == []))

    print()
    p = sum(r)
    print(f"{p}/{len(r)} checks passed")
    raise SystemExit(0 if p == len(r) else 1)
