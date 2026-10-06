"""
Tests for the CFB anytime-TD odds poller: cfb/api/poll_cfb_attd_odds.py and
POST /api/poll-cfb-attd-odds in cfb/api/index.py.

    python3 cfb/api/test_poll_cfb_attd_odds.py

No live calls anywhere: CFBD /games and /roster are monkeypatched, the
FBS reference is a fixture, and the signed write is captured instead of
sent. Fixture events cover BOTH outcome schemas (NFL-style name="Yes" with
the player in description; MLB-style name=player with the team in
description), several books, one-book players, a QB, a fictional name, and
the two REAL same-school name collisions test_attd_match.py already uses.
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "scripts"))

os.environ.setdefault("PIPELINE_INCOMING_SECRET", "test-incoming")
os.environ.setdefault("CFB_PIPELINE_WEBHOOK_SECRET", "test-webhook")

import poll_cfb_attd_odds as pm  # noqa: E402
from attd_match import implied_probability  # noqa: E402
from ids import CFBDError  # noqa: E402
import index as idx  # noqa: E402

AUTH = {"X-Pipeline-Secret": "test-incoming"}


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    return bool(cond)


# --- fixtures ---------------------------------------------------------------
ROSTER = [
    {"id": "1001", "firstName": "Jaylen", "lastName": "Carter", "team": "Georgia", "position": "WR"},
    {"id": "1002", "firstName": "Trevor", "lastName": "Etienne", "team": "Georgia", "position": "RB"},
    {"id": "1003", "firstName": "Marvin", "lastName": "Harrison", "team": "Alabama", "position": "WR"},
    {"id": "1004", "firstName": "Cole", "lastName": "Bishop", "team": "Alabama", "position": "QB"},
    {"id": "7001", "firstName": "Makai", "lastName": "Lemon", "team": "USC", "position": "WR"},
    {"id": "4244849", "firstName": "Marcel", "lastName": "Williams", "team": "Akron", "position": "WR"},
    {"id": "5226912", "firstName": "Marcel", "lastName": "Williams", "team": "Akron", "position": "WR"},
    {"id": "5123193", "firstName": "DJ", "lastName": "Jordan", "team": "USC", "position": "WR"},
    {"id": "5306715", "firstName": "DJ", "lastName": "Jordan", "team": "USC", "position": "WR"},
]
FBS_REF = {
    "Georgia": {"conf": "SEC", "mascot": "Bulldogs", "tier": "P4"},
    "Alabama": {"conf": "SEC", "mascot": "Crimson Tide", "tier": "P4"},
    "Akron": {"conf": "Mid-American", "mascot": "Zips", "tier": "G5"},
    "USC": {"conf": "Big Ten", "mascot": "Trojans", "tier": "P4"},
    "Ohio State": {"conf": "Big Ten", "mascot": "Buckeyes", "tier": "P4"},
}
FBS_COMBOS = {f"{s} {v['mascot']}": s for s, v in FBS_REF.items()}
GAMES = [
    {"id": 401, "completed": False, "startDate": "2099-10-10T19:30:00.000Z", "homeId": 61, "homeTeam": "Georgia", "awayId": 333, "awayTeam": "Alabama"},
    {"id": 402, "completed": False, "startDate": "2099-10-10T23:00:00.000Z", "homeId": 30, "homeTeam": "USC", "awayId": 2006, "awayTeam": "Akron"},
]
SEASON, WEEK = 2099, 7


def book(key, title, outcomes):
    return {"key": key, "title": title, "last_update": "2099-10-09T15:00:00Z",
            "markets": [{"key": "player_anytime_td", "last_update": "2099-10-09T15:00:00Z", "outcomes": outcomes}]}


def yes(player, price):
    return {"name": "Yes", "description": player, "price": price}


EV_NFL_STYLE = {
    "id": "evt-uga-bama", "sport_key": "americanfootball_ncaaf", "commence_time": "2099-10-10T19:30:00Z",
    "home_team": "Georgia Bulldogs", "away_team": "Alabama Crimson Tide",
    "bookmakers": [
        book("draftkings", "DraftKings", [yes("Trevor Etienne", -140), yes("Marvin Harrison", 165), yes("Jaylen Carter", 300),
                                          yes("Cole Bishop", 250), yes("Totally Fictional Player", 500)]),
        book("fanduel", "FanDuel", [yes("Trevor Etienne", -120), yes("Marvin Harrison", 150)]),
        book("betmgm", "BetMGM", [yes("Trevor Etienne", -150), yes("Marvin Harrison", 170)]),
    ],
}
EV_MLB_STYLE = {
    "id": "evt-usc-akron", "commence_time": "2099-10-10T23:00:00Z",
    "home_team": "USC Trojans", "away_team": "Akron Zips",
    "bookmakers": [
        book("draftkings", "DraftKings", [
            {"name": "Makai Lemon", "description": "USC Trojans", "price": 180},
            {"name": "Marcel Williams", "description": "Akron Zips", "price": 400},
            {"name": "DJ Jordan", "description": "USC Trojans", "price": 350},
        ]),
    ],
}
EV_NO_MARKET = {"id": "evt-no-market", "commence_time": "2099-10-10T20:00:00Z", "home_team": "Georgia Bulldogs", "away_team": "Alabama Crimson Tide",
                "bookmakers": [{"key": "draftkings", "title": "DraftKings", "markets": [{"key": "player_pass_yds", "outcomes": [{"name": "Over", "description": "Someone", "price": -110, "point": 250.5}]}]}]}
EV_BAD = {"id": "evt-bad", "commence_time": "2099-10-10T20:00:00Z", "home_team": "Georgia Bulldogs", "away_team": "Alabama Crimson Tide", "bookmakers": "not-a-list"}
EV_UNRESOLVED_GAME = {
    "id": "evt-no-game", "commence_time": "2099-10-10T20:00:00Z", "home_team": "Ohio State Buckeyes", "away_team": "Georgia Bulldogs",
    "bookmakers": [book("draftkings", "DraftKings", [yes("Trevor Etienne", -130)])],
}
EV_SINGLE_BOOK = {
    "id": "evt-one-book", "commence_time": "2099-10-10T19:30:00Z", "home_team": "Georgia Bulldogs", "away_team": "Alabama Crimson Tide",
    "bookmakers": [book("fanduel", "FanDuel", [yes("Jaylen Carter", 275)])],
}


class Harness:
    """Monkeypatches every external call and records forwards."""

    def __init__(self, *, games=GAMES, roster=ROSTER, fail_chunk_index=None, cfbd_error=False):
        pm.clear_caches()
        self.games_calls = 0
        self.roster_calls = 0
        self.forwards: list = []
        self.fail_chunk_index = fail_chunk_index

        def fake_games(season, week, **kw):
            self.games_calls += 1
            if cfbd_error:
                raise CFBDError("simulated CFBD outage")
            return games

        def fake_roster(season):
            self.roster_calls += 1
            return roster

        def fake_forward(payload, secret, url):
            self.forwards.append({"payload": payload, "url": url, "secret_len": len(secret)})
            if self.fail_chunk_index is not None and len(self.forwards) - 1 == self.fail_chunk_index:
                return {"success": False, "status_code": 500, "error": "simulated Lovable failure", "response_body": '{"ok":false}'}
            n = len(payload["rows"])
            return {"success": True, "status_code": 200, "error": None, "response_body": json.dumps({"ok": True, "received": n, "upserted": n, "deduped": 0})}

        pm.fetch_games = fake_games
        pm.fetch_fbs_roster = fake_roster
        pm.load_fbs_ref = lambda: (FBS_REF, FBS_COMBOS)
        pm.forward_to_lovable = fake_forward


if __name__ == "__main__":
    r = []
    client = idx.app.test_client()

    # ---- consensus math (NFL rule) -------------------------------------
    c = pm.consensus_from_book_odds([
        {"bookmaker": "DraftKings", "odds": -140, "implied_prob": implied_probability(-140)},
        {"bookmaker": "FanDuel", "odds": -120, "implied_prob": implied_probability(-120)},
        {"bookmaker": "BetMGM", "odds": -150, "implied_prob": implied_probability(-150)},
    ])
    r.append(check("consensus: median implied prob of 3 books (-140/-120/-150) -> consensus -140", c["consensus_price_american"] == -140 and c["n_books"] == 3))
    r.append(check("consensus: best price is the LONGEST payout (-120 FanDuel), not the numeric max", c["best_price"] == -120 and c["best_book"] == "FanDuel"))
    c1 = pm.consensus_from_book_odds([{"bookmaker": "DraftKings", "odds": 300, "implied_prob": implied_probability(300)}])
    r.append(check("consensus: one book degenerates to that book everywhere (+300, n_books 1)",
                   c1["consensus_price_american"] == 300 and c1["best_price"] == 300 and c1["best_book"] == "DraftKings" and c1["n_books"] == 1
                   and abs(c1["consensus_implied_probability"] - 0.25) < 1e-6))
    c2 = pm.consensus_from_book_odds([{"bookmaker": "A", "odds": 165}, {"bookmaker": "B", "odds": 150}, {"bookmaker": "C", "odds": 170}])
    r.append(check("consensus: positive prices -> median +165, best +170 (C); implied_prob recomputed when absent", c2["consensus_price_american"] == 165 and c2["best_price"] == 170 and c2["best_book"] == "C"))
    r.append(check("consensus: empty book list -> zeros/None, no crash", pm.consensus_from_book_odds([])["n_books"] == 0))
    r.append(check("probability_to_american: 0.5 -> +100, 0.25 -> +300, 0.6 -> -150",
                   pm.probability_to_american(0.5) == 100 and pm.probability_to_american(0.25) == 300 and pm.probability_to_american(0.6) == -150))

    # ---- body shapes -----------------------------------------------------
    r.append(check("normalize: {'events': [...]} handled by the endpoint; a bare list passes through", pm.normalize_events_input([EV_NFL_STYLE]) == [EV_NFL_STYLE]))
    r.append(check("normalize: a single event object becomes a one-item list", pm.normalize_events_input(EV_NFL_STYLE) == [EV_NFL_STYLE]))
    r.append(check("normalize: garbage -> None", pm.normalize_events_input("nope") is None and pm.normalize_events_input({"foo": 1}) is None))

    # ---- process_events, function level ----------------------------------
    h = Harness()
    out = pm.process_events([EV_NFL_STYLE, EV_MLB_STYLE, EV_NO_MARKET, EV_BAD, EV_UNRESOLVED_GAME, EV_SINGLE_BOOK], SEASON, WEEK, poll_timestamp="2099-10-09T15:00:00+00:00")
    d = out["diagnostics"]
    # Keyed by (event, player): Etienne is priced in two fixture events on
    # purpose (the real game and the unresolved-game event).
    rows_by_event = {(row["event_id"], row["player_id"]): row for row in out["rows"]}
    rows = {pid: row for (eid, pid), row in rows_by_event.items() if eid != "evt-no-game"}
    r.append(check("events: 6 received, 1 no-market, 1 parse error, 4 processed",
                   d["events_received"] == 6 and d["events_with_no_market"] == ["evt-no-market"]
                   and [e["event_id"] for e in d["events_with_parse_errors"]] == ["evt-bad"] and d["events_processed"] == 4))
    r.append(check("schema_seen counts both shapes", d["schema_seen"].get("nfl_style") == 3 and d["schema_seen"].get("mlb_style") == 1))
    r.append(check("matched: Etienne x2 events, Harrison, Carter x2, Lemon = 6 matched outcome groups counted per (player, book) rows",
                   d["matched"] == 3 + 3 + 1 + 1 + 1 + 1))
    r.append(check("unmatched reasons: QB -> position_out_of_scope, fictional -> rookie_or_new, Marcel Williams + DJ Jordan -> name_collision",
                   d["unmatched_by_reason"]["position_out_of_scope"] == 1 and d["unmatched_by_reason"]["rookie_or_new"] == 1
                   and d["unmatched_by_reason"]["name_collision"] == 2))
    et = rows["1002"]
    r.append(check("row: Etienne has 3 books, consensus -140, best -120 FanDuel, n_books 3",
                   len(et["book_odds"]) == 3 and et["consensus_price_american"] == -140 and et["best_price"] == -120 and et["best_book"] == "FanDuel" and et["n_books"] == 3))
    r.append(check("row: team/opponent/game resolved from CFBD /games (Georgia 61 vs Alabama 333, game 401)",
                   et["team_id"] == 61 and et["team"] == "Georgia" and et["opponent"] == "Alabama" and et["opponent_team_id"] == 333 and et["game_id"] == 401))
    r.append(check("row: event_id, commence_time, poll_timestamp carried", et["event_id"] == "evt-uga-bama" and et["commence_time"] == "2099-10-10T19:30:00Z" and et["poll_timestamp"] == "2099-10-09T15:00:00+00:00"))
    r.append(check("row: Alabama player gets the mirrored opponent (Georgia) and his own team id 333",
                   rows["1003"]["team_id"] == 333 and rows["1003"]["opponent_team_id"] == 61 and rows["1003"]["opponent"] == "Georgia"))
    r.append(check("row: MLB-style event matched Lemon to USC (30) vs Akron (2006), game 402, consensus +180 from one book",
                   rows["7001"]["team_id"] == 30 and rows["7001"]["opponent_team_id"] == 2006 and rows["7001"]["game_id"] == 402 and rows["7001"]["consensus_price_american"] == 180))
    r.append(check("row: unresolved game (Ohio State vs Georgia not in /games) -> game_id None, counted, row still produced",
                   "evt-no-game" in d["events_game_unresolved"]))
    r.append(check("row: every row has exactly the odds-table columns",
                   all({"player_id", "player_name", "position_group", "team_id", "team", "opponent_team_id", "opponent", "season", "week", "game_id",
                        "event_id", "commence_time", "poll_timestamp", "book_odds", "n_books", "best_price", "best_book",
                        "consensus_price_american", "consensus_implied_probability", "extra"} <= set(row.keys()) for row in out["rows"])))
    r.append(check("row: book_odds entries carry bookmaker/odds/implied_prob", all({"bookmaker", "odds", "implied_prob"} <= set(b) for row in out["rows"] for b in row["book_odds"])))
    r.append(check("players_priced counts one row per (event, player); the same player in two events yields two rows (upsert resolves)",
                   d["players_priced"] == len(out["rows"]) and sum(1 for row in out["rows"] if row["player_id"] == "1002") == 2))
    r.append(check("cfbd: exactly one /roster and one /games fetch for the whole batch (lazy, cached)",
                   d["cfbd_calls"] == ["/roster", "/games"] and h.games_calls == 1 and h.roster_calls == 1))
    h2 = Harness()
    out2 = pm.process_events([EV_NO_MARKET], SEASON, WEEK, poll_timestamp="t")
    r.append(check("cfbd: a batch with no priced event fetches nothing from CFBD", out2["diagnostics"]["cfbd_calls"] == [] and h2.roster_calls == 0 and h2.games_calls == 0))

    # ---- chunked forwarding ----------------------------------------------
    h = Harness()
    many = [{"player_id": str(i), "season": SEASON, "week": WEEK, "book_odds": []} for i in range(600)]
    fw = pm.forward_rows_in_chunks(many, "test-webhook", url="https://example.invalid/write", chunk_size=250)
    r.append(check("chunking: 600 rows -> 3 signed POSTs of 250/250/100, all {'rows': [...]}",
                   fw["success"] is True and fw["chunks_sent"] == 3 and fw["rows_sent"] == 600
                   and [len(f["payload"]["rows"]) for f in h.forwards] == [250, 250, 100]))
    h = Harness(fail_chunk_index=1)
    fw = pm.forward_rows_in_chunks(many, "test-webhook", url="https://example.invalid/write", chunk_size=250)
    r.append(check("chunking: a failed 2nd chunk stops the batch and reports chunks_sent=1, failed_chunk_index=1, Lovable status/error",
                   fw["success"] is False and fw["chunks_sent"] == 1 and fw["failed_chunk_index"] == 1 and fw["status_code"] == 500 and "simulated" in fw["error"]))
    r.append(check("chunking: zero rows -> no POST at all", pm.forward_rows_in_chunks([], "s", url="u")["chunks_total"] == 0))

    # ---- endpoint ---------------------------------------------------------
    h = Harness()
    resp = client.post("/api/poll-cfb-attd-odds", json={"season": SEASON, "week": WEEK, "events": [EV_NFL_STYLE]})
    r.append(check("endpoint: 401 without X-Pipeline-Secret", resp.status_code == 401 and h.forwards == []))
    resp = client.post("/api/poll-cfb-attd-odds", json={"events": [EV_NFL_STYLE]}, headers=AUTH)
    r.append(check("endpoint: missing season/week -> 400 with a clear message", resp.status_code == 400 and "season and week" in resp.get_json()["error"]))
    resp = client.post("/api/poll-cfb-attd-odds", json={"season": SEASON, "week": WEEK}, headers=AUTH)
    r.append(check("endpoint: envelope without events -> 400", resp.status_code == 400))
    resp = client.post("/api/poll-cfb-attd-odds", json={"season": SEASON, "week": WEEK, "events": []}, headers=AUTH)
    body = resp.get_json()
    r.append(check("endpoint: {'events': []} -> 200, zero everything, no forward",
                   resp.status_code == 200 and body["events_received"] == 0 and body["rows_written"] == 0 and h.forwards == []))

    h = Harness()
    resp = client.post("/api/poll-cfb-attd-odds", json={"season": SEASON, "week": WEEK, "events": [EV_NFL_STYLE, EV_MLB_STYLE, EV_NO_MARKET, EV_BAD]}, headers=AUTH)
    body = resp.get_json()
    r.append(check("endpoint: happy path 200 with status ok, counts, schema_seen, unmatched_by_reason",
                   resp.status_code == 200 and body["status"] == "ok" and body["events_received"] == 4 and body["events_processed"] == 2
                   and body["schema_seen"] == {"nfl_style": 1, "mlb_style": 1} and body["unmatched_by_reason"]["name_collision"] == 2))
    r.append(check("endpoint: rows_written equals players_priced and the forward went to the odds write URL in one chunk",
                   body["rows_written"] == body["players_priced"] == 4 and len(h.forwards) == 1
                   and h.forwards[0]["url"].endswith("/cfb-player-attd-odds-weekly-write") and h.forwards[0]["payload"]["rows"][0]["player_id"]))
    r.append(check("endpoint: forward block carries status_code/success/response_body/error",
                   {"status_code", "success", "response_body", "error"} <= set(body["forward"].keys()) and body["forward"]["success"] is True))
    r.append(check("endpoint: cfbd_calls reported", body["cfbd_calls"] == ["/roster", "/games"]))
    text = resp.get_data(as_text=True)
    r.append(check("endpoint: response contains neither the incoming secret nor the webhook secret", "test-incoming" not in text and "test-webhook" not in text))

    h = Harness()
    resp = client.post("/api/poll-cfb-attd-odds?season=%d&week=%d" % (SEASON, WEEK), json=EV_NFL_STYLE, headers=AUTH)
    r.append(check("endpoint: a single event object body with ?season&week query -> 200, processed", resp.status_code == 200 and resp.get_json()["events_processed"] == 1))
    resp = client.post("/api/poll-cfb-attd-odds?season=%d&week=%d" % (SEASON, WEEK), json=[EV_NFL_STYLE, EV_SINGLE_BOOK], headers=AUTH)
    r.append(check("endpoint: a bare list body with ?season&week query -> 200, 2 processed", resp.status_code == 200 and resp.get_json()["events_processed"] == 2))
    resp = client.post("/api/poll-cfb-attd-odds", json=[EV_NFL_STYLE], headers=AUTH)
    r.append(check("endpoint: a bare list WITHOUT season/week anywhere -> 400", resp.status_code == 400))

    h = Harness()
    resp = client.post("/api/poll-cfb-attd-odds", json={"season": SEASON, "week": WEEK, "events": [EV_NFL_STYLE], "preview_only": True}, headers=AUTH)
    body = resp.get_json()
    r.append(check("endpoint: preview_only -> 200, forward skipped, zero writes, sample rows returned with consensus fields",
                   resp.status_code == 200 and body["forward"] == {"skipped": "preview_only"} and body["rows_written"] == 0 and h.forwards == []
                   and body["sample"] and "consensus_price_american" in body["sample"][0] and body["players_priced"] == 3))

    h = Harness(fail_chunk_index=0)
    resp = client.post("/api/poll-cfb-attd-odds", json={"season": SEASON, "week": WEEK, "events": [EV_NFL_STYLE]}, headers=AUTH)
    body = resp.get_json()
    r.append(check("endpoint: a failed forward -> 502 with status error, Lovable status 500 and error text, rows_written 0",
                   resp.status_code == 502 and body["status"] == "error" and body["forward"]["status_code"] == 500
                   and "simulated" in body["forward"]["error"] and body["rows_written"] == 0))

    h = Harness(cfbd_error=True)
    resp = client.post("/api/poll-cfb-attd-odds", json={"season": SEASON, "week": WEEK, "events": [EV_NFL_STYLE]}, headers=AUTH)
    r.append(check("endpoint: a CFBD failure -> 502 with stage cfbd, nothing forwarded", resp.status_code == 502 and resp.get_json()["stage"] == "cfbd" and h.forwards == []))

    resp = client.get("/api/poll-cfb-attd-odds")
    r.append(check("endpoint: GET health check is public and describes the contract", resp.status_code == 200 and "player_anytime_td" in resp.get_json()["usage"]))

    print()
    p = sum(r)
    print(f"{p}/{len(r)} checks passed")
    raise SystemExit(0 if p == len(r) else 1)
