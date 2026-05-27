#!/usr/bin/env python3
# ============================================================
# generate_report.py
#
# Runs every morning at 6:30 AM via macOS launchd.
# Fetches live MLB, odds, and weather data, then writes a
# complete standalone HTML file to daily-reports/.
#
# Output:
#   daily-reports/YYYY-MM-DD.html   ← today's report
#   daily-reports/latest.html       ← always the most recent
#   daily-reports/generate.log      ← appended each run
# ============================================================

import os
import sys
import json
import logging
from datetime import date, datetime, timezone
from dataclasses import asdict

# ── Paths ──────────────────────────────────────────────────────
ROOT       = os.path.dirname(os.path.abspath(__file__))
REPORTS    = os.path.join(os.path.expanduser("~"), "Desktop", "tasty-pick-ems-reports")
LOG_FILE   = os.path.join(REPORTS, "generate.log")
CSS_FILE   = os.path.join(ROOT, "css", "style.css")

# Make sure the Desktop folder exists
os.makedirs(REPORTS, exist_ok=True)

sys.path.insert(0, ROOT)

# ── Logging ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("tasty-pick-ems")

# ── Load API modules ───────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

from src.lib.api.mlb     import fetch_todays_games
from src.lib.api.odds    import fetch_hr_props
from src.lib.api.weather import fetch_weather_for_venue, get_park_factor
from src.lib.scoring.hr_score import calculate_hr_score


# ── Main ───────────────────────────────────────────────────────

def main():
    today = date.today().strftime("%Y-%m-%d")
    log.info(f"=== Tasty Pick Ems daily report — {today} ===")

    # Fetch data (each call falls back to mock if API fails)
    log.info("Fetching MLB schedule...")
    games = fetch_todays_games()
    log.info(f"  → {len(games)} games")

    log.info("Fetching HR props...")
    props = [asdict(p) for p in fetch_hr_props()]
    log.info(f"  → {len(props)} props at +300 or longer")

    log.info("Fetching weather for outdoor parks...")
    game_packets = []
    for game in games:
        weather     = fetch_weather_for_venue(game.venue) if game.is_outdoor else None
        park_factor = get_park_factor(game.venue)
        game_props  = [p for p in props if p['team'] in (_team_abbrev(game.home_team), _team_abbrev(game.away_team))]
        best_odds   = min((p['odds'] for p in game_props), default=None)
        score       = calculate_hr_score(
            venue=game.venue,
            park_factor=park_factor,
            weather=weather,
            best_prop_odds=best_odds,
        )
        game_packets.append({
            "game":    asdict(game),
            "weather": asdict(weather) if weather else None,
            "props":   [asdict(p) for p in game_props],
            "score":   asdict(score),
        })

    # Sort by score descending
    game_packets.sort(key=lambda x: x["score"]["total"], reverse=True)

    # Generate HTML
    log.info("Generating HTML report...")
    html = build_html(today, game_packets, props)

    # Write dated file
    dated_path = os.path.join(REPORTS, f"{today}.html")
    with open(dated_path, "w", encoding="utf-8") as f:
        f.write(html)
    log.info(f"  → Written: {dated_path}")

    # Overwrite latest.html
    latest_path = os.path.join(REPORTS, "latest.html")
    with open(latest_path, "w", encoding="utf-8") as f:
        f.write(html)
    log.info(f"  → Updated: {latest_path}")

    # Regenerate the archive index
    write_archive_index()
    log.info("Done.")


# ── HTML builder ───────────────────────────────────────────────

def build_html(today: str, game_packets: list, all_props: list) -> str:
    """Assemble a complete, standalone HTML report for the day."""

    # Read the shared CSS and embed it so the file is fully self-contained
    try:
        with open(CSS_FILE, "r", encoding="utf-8") as f:
            css = f.read()
    except FileNotFoundError:
        css = ""

    date_label = datetime.strptime(today, "%Y-%m-%d").strftime("%A, %B %-d, %Y")
    generated  = datetime.now().strftime("%I:%M %p")

    # Build each section
    top_games_html  = _render_game_cards(game_packets[:6])   # top 6 by score
    all_props_html  = _render_props_strip(all_props)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Tasty Pick Ems — {date_label}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
  <link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Inter:wght@400;500;600;700;900&display=swap" rel="stylesheet"/>
  <style>{css}</style>
  <style>
    /* Report-specific overrides */
    body {{ padding-bottom: 60px; }}
    .report-header {{
      background: var(--card-bg);
      border-bottom: 2px solid var(--green);
      padding: 24px 20px 20px;
      margin-bottom: 0;
      position: sticky;
      top: 0;
      z-index: 50;
    }}
    .report-header-inner {{
      max-width: 1300px;
      margin: 0 auto;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      flex-wrap: wrap;
    }}
    .report-brand {{
      font-family: 'Bebas Neue', sans-serif;
      font-size: 22px;
      letter-spacing: 2px;
      color: var(--green);
      text-shadow: 0 0 10px rgba(57,255,20,0.4);
    }}
    .report-brand span {{ color: var(--white); text-shadow: none; }}
    .report-date-chip {{
      font-family: 'Bebas Neue', sans-serif;
      font-size: 16px;
      letter-spacing: 1px;
      color: var(--white);
    }}
    .report-generated {{
      font-size: 11px;
      color: var(--gray);
    }}
    .report-back {{
      font-size: 12px;
      font-weight: 700;
      color: var(--green);
      border: 1px solid var(--green);
      padding: 5px 12px;
      border-radius: 6px;
      background: var(--green-glow);
    }}
    .score-ring {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex-direction: column;
      width: 58px;
      height: 58px;
      border-radius: 50%;
      border: 2px solid currentColor;
      flex-shrink: 0;
    }}
    .score-ring-num  {{ font-family:'Bebas Neue',sans-serif; font-size:22px; line-height:1; }}
    .score-ring-lbl  {{ font-size:8px; font-weight:800; letter-spacing:.5px; text-transform:uppercase; opacity:.8; }}
  </style>
</head>
<body>

  <!-- Report header (sticky) -->
  <div class="report-header">
    <div class="report-header-inner">
      <div>
        <div class="report-brand">TASTY <span>PICK EMS</span></div>
        <div class="report-generated">Generated at {generated} · auto-run daily at 6:30 AM</div>
      </div>
      <div class="report-date-chip">{date_label}</div>
      <a class="report-back" href="index.html">← All Reports</a>
    </div>
  </div>

  <div class="container" style="padding-top:28px;">

    <!-- HR Props Strip -->
    <div class="section">
      <div class="section-header">
        <div class="section-title">💰 HR Props Today</div>
        <div class="section-meta">{len(all_props)} available at +300 or longer</div>
      </div>
      <div class="section-divider"></div>
      {all_props_html}
    </div>

    <!-- Games ranked by score -->
    <div class="section">
      <div class="section-header">
        <div class="section-title">🏟️ Today's Games</div>
        <div class="section-meta">Ranked by HR environment score</div>
      </div>
      <div class="section-angle">
        <strong>Content angle:</strong> Higher score = better HR environment. Lead with the top game.
      </div>
      <div class="section-divider"></div>
      <div class="games-list">
        {top_games_html}
      </div>
    </div>

  </div>

  <script>
    // Copy button helper (works in static HTML files)
    function copyText(btn, text) {{
      navigator.clipboard.writeText(text).then(() => {{
        const orig = btn.innerHTML;
        btn.textContent = '✓ Copied!';
        btn.style.background = 'var(--white)';
        setTimeout(() => {{ btn.innerHTML = orig; btn.style.background = ''; }}, 2000);
      }});
    }}
  </script>

</body>
</html>"""


# ── Section renderers ──────────────────────────────────────────

def _render_props_strip(props: list) -> str:
    if not props:
        return '<p style="color:var(--gray);font-size:13px;">No props available today.</p>'
    items = "".join(f"""
      <div class="prop-pill">
        <span class="prop-pill-name">{p['player_name']}</span>
        <span class="prop-pill-team">{p['team']}</span>
        <span class="prop-pill-odds">+{p['odds']}</span>
      </div>""" for p in sorted(props, key=lambda x: x['odds']))
    return f'<div class="props-strip">{items}</div>'


def _render_game_cards(packets: list) -> str:
    return "".join(_render_game_card(p) for p in packets)


def _render_game_card(packet: dict) -> str:
    game    = packet["game"]
    weather = packet["weather"]
    props   = packet["props"]
    score   = packet["score"]

    score_color = _score_color(score["total"])
    start_time  = _fmt_time(game["start_time"])

    home_p = game["home_pitcher"] or "TBD"
    away_p = game["away_pitcher"] or "TBD"

    # Weather block
    if weather:
        wind_cat   = weather["wind_category"]
        wind_icon  = "↑" if wind_cat == "out" else ("↓" if wind_cat == "in" else "→")
        cond_icons = {"Clear":"☀️","Mainly Clear":"🌤️","Partly Cloudy":"⛅",
                      "Overcast":"☁️","Rain":"🌧️","Drizzle":"🌦️","Thunderstorm":"⛈️","Snow":"❄️"}
        cond_icon  = cond_icons.get(weather["conditions"], "🌤️")
        weather_html = f"""
          <div class="live-weather">
            <div class="live-detail-label">Weather</div>
            <div class="live-weather-main">{cond_icon} {weather['conditions']} · {weather['temp_f']}°F</div>
            <div class="live-weather-wind">
              {wind_icon} {weather['wind_speed_mph']} mph {weather['wind_direction']}
              <span class="live-wind-cat live-wind-{wind_cat}">{wind_cat}</span>
            </div>
          </div>"""
    else:
        weather_html = """
          <div class="live-weather">
            <div class="live-detail-label">Weather</div>
            <div class="live-weather-main">🏠 Indoor / Roof</div>
            <div class="live-weather-wind" style="color:var(--gray)">Not a factor</div>
          </div>"""

    # Score bars
    bars = "".join(_score_bar(lbl, val, mx) for lbl, val, mx in [
        ("Park",    score["park_score"],    30),
        ("Weather", score["weather_score"], 25),
        ("Pitcher", score["pitcher_score"], 20),
        ("Odds",    score["odds_score"],    15),
        ("Power",   score["batter_score"],  10),
    ])

    # Props rows
    if props:
        prop_rows = "".join(f"""
          <div class="live-prop-row">
            <span class="live-prop-player">{p['player_name']}</span>
            <span class="live-prop-team">{p['team']}</span>
            <span class="live-prop-odds">+{p['odds']}</span>
            <span class="live-prop-book">{p['bookmaker']}</span>
          </div>""" for p in props)
        props_html = f"""
          <div class="live-game-props">
            <div class="live-detail-label">HR Props in This Game</div>
            {prop_rows}
          </div>"""
    else:
        props_html = ""

    hook_escaped = score["content_angle"].replace("'", "\\'")

    return f"""
    <div class="live-game-card">

      <div class="live-game-header">
        <div class="live-game-teams">
          <span class="live-away-team">{game['away_team']}</span>
          <span class="live-at">@</span>
          <span class="live-home-team">{game['home_team']}</span>
        </div>
        <div class="live-game-meta">
          <span class="live-venue">{game['venue']}</span>
          <span class="live-time">{start_time}</span>
        </div>
      </div>

      <div class="live-score-row">
        <div class="live-score-badge" style="border-color:{score_color};color:{score_color};">
          <span class="live-score-number">{score['total']}</span>
          <span class="live-score-label">{score['label']}</span>
        </div>
        <div class="live-score-breakdown">{bars}</div>
      </div>

      <div class="live-details-row">
        <div class="live-pitchers">
          <div class="live-detail-label">Probable Pitchers</div>
          <div class="live-pitcher-row">
            <span class="live-pitcher-team">{_abbrev(game['away_team'])}</span>
            <span class="live-pitcher-name">{away_p}</span>
          </div>
          <div class="live-pitcher-row">
            <span class="live-pitcher-team">{_abbrev(game['home_team'])}</span>
            <span class="live-pitcher-name">{home_p}</span>
          </div>
        </div>
        {weather_html}
      </div>

      {props_html}

      <div class="live-content-angle">
        <div class="live-angle-label">📲 Content Angle</div>
        <div class="live-angle-text">{score['content_angle']}</div>
        <button class="grab-btn" style="margin-top:10px;width:100%;"
          onclick="copyText(this,'{hook_escaped}')">📲 Copy Hook</button>
      </div>

    </div>"""


def _score_bar(label, value, max_val):
    pct = round((value / max_val) * 100)
    return f"""
      <div class="score-bar-row">
        <span class="score-bar-label">{label}</span>
        <div class="score-bar-track"><div class="score-bar-fill" style="width:{pct}%"></div></div>
        <span class="score-bar-value">{value}</span>
      </div>"""


# ── Archive index ──────────────────────────────────────────────

def write_archive_index():
    """Regenerate daily-reports/index.html listing every past report."""
    reports = sorted(
        [f for f in os.listdir(REPORTS) if f.endswith(".html") and f != "index.html" and f != "latest.html"],
        reverse=True,
    )

    rows = ""
    for fname in reports:
        day = fname.replace(".html", "")
        try:
            label = datetime.strptime(day, "%Y-%m-%d").strftime("%A, %B %-d, %Y")
        except ValueError:
            label = day
        rows += f"""
        <a class="archive-row" href="{fname}">
          <span class="archive-date">{label}</span>
          <span class="archive-arrow">→</span>
        </a>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Tasty Pick Ems — Daily Reports</title>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Inter:wght@400;500;600;700;900&display=swap" rel="stylesheet"/>
  <link rel="stylesheet" href="../css/style.css"/>
  <style>
    body {{ padding: 40px 20px; }}
    .archive-header {{ margin-bottom: 32px; }}
    .archive-title {{ font-family:'Bebas Neue',sans-serif; font-size:32px; letter-spacing:2px; color:var(--white); }}
    .archive-title span {{ color:var(--green); }}
    .archive-subtitle {{ font-size:13px; color:var(--gray); margin-top:6px; }}
    .archive-list {{ display:flex; flex-direction:column; gap:8px; max-width:600px; }}
    .archive-row {{
      display:flex; align-items:center; justify-content:space-between;
      background:var(--card-bg); border:1px solid var(--border); border-radius:10px;
      padding:16px 18px; transition:border-color 0.15s; text-decoration:none;
    }}
    .archive-row:hover {{ border-color:var(--green); }}
    .archive-date {{ font-size:15px; font-weight:600; color:var(--white); }}
    .archive-arrow {{ font-size:16px; color:var(--green); }}
    .latest-btn {{
      display:inline-block; margin-bottom:24px;
      background:var(--green); color:var(--black);
      font-size:13px; font-weight:900; letter-spacing:1px; text-transform:uppercase;
      padding:12px 24px; border-radius:8px; text-decoration:none;
    }}
  </style>
</head>
<body>
  <div class="archive-header">
    <div class="archive-title">TASTY <span>PICK EMS</span></div>
    <div class="archive-subtitle">Daily MLB HR Props Reports — generated every morning at 6:30 AM</div>
  </div>
  <a class="latest-btn" href="latest.html">⚡ Open Today's Report</a>
  <div class="archive-list">{rows}</div>
</body>
</html>"""

    index_path = os.path.join(REPORTS, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)
    log.info(f"  → Archive index updated: {index_path}")


# ── Utilities ──────────────────────────────────────────────────

def _score_color(score: int) -> str:
    if score >= 80: return "#39FF14"
    if score >= 65: return "#a8ff6e"
    if score >= 50: return "#FFD700"
    if score >= 35: return "#FF8C00"
    return "#FF4444"


def _fmt_time(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        from datetime import timezone as tz
        import time
        local_offset = -time.timezone / 3600
        from datetime import timedelta
        local_dt = dt + timedelta(hours=local_offset)
        return local_dt.strftime("%-I:%M %p")
    except Exception:
        return iso


def _abbrev(team_name: str) -> str:
    MAP = {
        "Yankees":"NYY","Mets":"NYM","Red Sox":"BOS","Cubs":"CHC",
        "White Sox":"CHW","Astros":"HOU","Athletics":"OAK","Dodgers":"LAD",
        "Angels":"LAA","Giants":"SF","Padres":"SD","Mariners":"SEA",
        "Rangers":"TEX","Phillies":"PHI","Braves":"ATL","Cardinals":"STL",
        "Reds":"CIN","Pirates":"PIT","Rockies":"COL","Diamondbacks":"ARI",
        "Brewers":"MIL","Twins":"MIN","Tigers":"DET","Guardians":"CLE",
        "Royals":"KC","Orioles":"BAL","Blue Jays":"TOR","Rays":"TB",
        "Marlins":"MIA","Nationals":"WSH",
    }
    key = next((k for k in MAP if k in team_name), None)
    return MAP[key] if key else team_name[:3].upper()


def _team_abbrev(team_name: str) -> str:
    return _abbrev(team_name)


if __name__ == "__main__":
    main()
