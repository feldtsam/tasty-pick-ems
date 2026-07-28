"""
Pull real historical weather and starting-lineup batting order for each game
in our sample, from the free official MLB Stats API (statsapi.mlb.com,
no key required). This is the same source MLB.com itself uses.

Caches one raw JSON per game_pk to data/raw/game_feeds/ so re-runs don't
re-hit the API, then derives a (game_pk, batter) -> context table with:
  - batting_order_slot (1-9, starters only)
  - wind_speed_mph, wind_description (raw text, e.g. "Out To LF")
  - temp_f, condition (e.g. "Clear", "Cloudy", "Rain")
  - venue_name

Usage: python scripts/fetch_game_context.py data/processed/batter_games_2023-06-01_2023-06-30.parquet
"""
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests
from tqdm import tqdm

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw" / "game_feeds"
PROC_DIR = ROOT / "data" / "processed"
RAW_DIR.mkdir(parents=True, exist_ok=True)

FEED_URL = "https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"
MAX_WORKERS = 8  # a handful of concurrent requests to the free public API, not a hammering pace


def fetch_game_feed(game_pk: int) -> dict:
    cache_path = RAW_DIR / f"{game_pk}.json"
    if cache_path.exists():
        with open(cache_path) as f:
            return json.load(f)
    resp = requests.get(FEED_URL.format(game_pk=game_pk), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    with open(cache_path, "w") as f:
        json.dump(data, f)
    return data


def parse_wind(wind_str: str):
    # e.g. "4 mph, Out To LF" / "10 mph, In From CF" / "Calm"
    if not wind_str or wind_str.lower() == "calm":
        return 0.0, "Calm"
    m = re.match(r"(\d+)\s*mph,\s*(.+)", wind_str)
    if m:
        return float(m.group(1)), m.group(2).strip()
    return None, wind_str


def extract_context(game_pk: int, data: dict) -> list:
    game_data = data.get("gameData", {})
    weather = game_data.get("weather", {}) or {}
    venue_name = game_data.get("venue", {}).get("name")
    wind_speed, wind_desc = parse_wind(weather.get("wind", ""))

    rows = []
    boxscore = data.get("liveData", {}).get("boxscore", {})
    for side in ("home", "away"):
        players = boxscore.get("teams", {}).get(side, {}).get("players", {})
        for pid, p in players.items():
            batting_order = p.get("battingOrder")
            if not batting_order or not batting_order.endswith("00"):
                continue  # only starters, not mid-game substitutions
            rows.append({
                "game_pk": game_pk,
                "batter": p["person"]["id"],
                "batting_order_slot": int(batting_order[0]),
                "venue_name": venue_name,
                "temp_f": weather.get("temp"),
                "condition": weather.get("condition"),
                "wind_speed_mph": wind_speed,
                "wind_description": wind_desc,
            })
    return rows


if __name__ == "__main__":
    games_path = Path(sys.argv[1] if len(sys.argv) > 1 else "data/processed/batter_games_2023-06-01_2023-06-30.parquet")
    month_label = games_path.stem.replace("batter_games_", "")

    games = pd.read_parquet(games_path)
    unique_game_pks = [int(pk) for pk in games["game_pk"].unique().tolist()]
    print(f"Fetching game context for {len(unique_game_pks)} games ({MAX_WORKERS} workers)...")

    all_rows = []
    failed = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fetch_game_feed, pk): pk for pk in unique_game_pks}
        for future in tqdm(as_completed(futures), total=len(futures)):
            game_pk = futures[future]
            try:
                data = future.result()
                all_rows.extend(extract_context(game_pk, data))
            except Exception as e:
                failed.append((game_pk, str(e)))

    context = pd.DataFrame(all_rows)
    out_path = PROC_DIR / f"game_context_{month_label}.parquet"
    context.to_parquet(out_path, index=False)
    print(f"Saved {len(context)} batter-game context rows -> {out_path}")
    if failed:
        print(f"{len(failed)} games failed:", failed[:5])
