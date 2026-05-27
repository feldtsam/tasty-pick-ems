// ============================================================
// js/liveData.js — Live Data Page
//
// Fetches from the Python backend (server.py) and renders:
//   - Today's games with weather + HR environment score
//   - Probable pitchers
//   - Available HR props
//   - Best TikTok content angle per game
//
// The backend must be running for this to work:
//   cd tasty-pick-ems && ./start.sh
// ============================================================

const API_BASE   = 'http://localhost:5001/api';
const REFRESH_MS = 5 * 60 * 1000; // Auto-refresh every 5 minutes

// ── Page state ─────────────────────────────────────────────────
let liveState = {
  loading: false,
  error:   null,
  data:    null,
};


// ── Init ───────────────────────────────────────────────────────

function initLiveData() {
  fetchLiveData();
  setInterval(fetchLiveData, REFRESH_MS);
}


// ── Data fetch ─────────────────────────────────────────────────

async function fetchLiveData() {
  if (liveState.loading) return;

  liveState.loading = true;
  liveState.error   = null;
  renderLivePage();  // Show loading state immediately

  try {
    const res = await fetch(`${API_BASE}/live`);

    if (!res.ok) {
      throw new Error(`Server returned ${res.status}: ${res.statusText}`);
    }

    liveState.data    = await res.json();
    liveState.error   = null;
    liveState.loading = false;
    renderLivePage();

  } catch (err) {
    // This usually means the backend isn't running
    liveState.loading = false;
    liveState.error   = err.message || 'Could not reach backend';
    renderLivePage();
  }
}


// ── Main render ────────────────────────────────────────────────

function renderLivePage() {
  const el = document.getElementById('live-data-content');
  if (!el) return;

  if (liveState.loading) {
    el.innerHTML = renderLoadingState();
    return;
  }

  if (liveState.error) {
    el.innerHTML = renderErrorState(liveState.error);
    return;
  }

  if (!liveState.data) {
    el.innerHTML = renderLoadingState();
    return;
  }

  const { games, all_props, using_mock, fetched_at, date } = liveState.data;

  el.innerHTML = `
    ${renderStatusBar(using_mock, fetched_at)}
    ${renderAllPropsStrip(all_props)}
    ${renderGamesList(games)}
  `;
}


// ── Status bar ─────────────────────────────────────────────────

function renderStatusBar(usingMock, fetchedAt) {
  const time = fetchedAt
    ? new Date(fetchedAt).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
    : '--:--';

  const apiChips = [
    { name: 'MLB',     mock: usingMock?.mlb     || false },
    { name: 'Odds',    mock: usingMock?.odds     || false },
    { name: 'Weather', mock: usingMock?.weather  || false },
  ];

  const chips = apiChips.map(({ name, mock }) => `
    <div class="api-chip ${mock ? 'api-mock' : 'api-live'}">
      <span class="api-chip-dot"></span>
      ${name}: ${mock ? 'Mock' : 'Live'}
    </div>
  `).join('');

  return `
    <div class="live-status-bar">
      <div class="live-api-chips">${chips}</div>
      <div class="live-refresh-time">Updated ${time} · auto-refreshes every 5min</div>
    </div>
  `;
}


// ── All props strip ────────────────────────────────────────────

function renderAllPropsStrip(props) {
  if (!props || props.length === 0) return '';

  const items = props.map(p => `
    <div class="prop-pill">
      <span class="prop-pill-name">${p.player_name}</span>
      <span class="prop-pill-team">${p.team}</span>
      <span class="prop-pill-odds">+${p.odds}</span>
    </div>
  `).join('');

  return `
    <div class="live-section">
      <div class="live-section-title">💰 HR Props Available Today <span class="live-section-sub">+${Math.min(...props.map(p=>p.odds))} or longer</span></div>
      <div class="props-strip">${items}</div>
    </div>
  `;
}


// ── Games list ─────────────────────────────────────────────────

function renderGamesList(games) {
  if (!games || games.length === 0) {
    return `<div class="live-empty">No games found for today.</div>`;
  }

  return `
    <div class="live-section">
      <div class="live-section-title">🏟️ Today's Games <span class="live-section-sub">ranked by HR environment score</span></div>
      <div class="games-list">
        ${games.map(renderGameCard).join('')}
      </div>
    </div>
  `;
}


function renderGameCard(packet) {
  const { game, weather, props, score } = packet;
  const scoreColor = _scoreColor(score.total);
  const startTime  = _formatTime(game.start_time);

  return `
    <div class="live-game-card">

      <!-- Game header -->
      <div class="live-game-header">
        <div class="live-game-teams">
          <span class="live-away-team">${game.away_team}</span>
          <span class="live-at">@</span>
          <span class="live-home-team">${game.home_team}</span>
        </div>
        <div class="live-game-meta">
          <span class="live-venue">${game.venue}</span>
          <span class="live-time">${startTime}</span>
        </div>
      </div>

      <!-- Score badge -->
      <div class="live-score-row">
        <div class="live-score-badge" style="border-color: ${scoreColor}; color: ${scoreColor};">
          <span class="live-score-number">${score.total}</span>
          <span class="live-score-label">${score.label}</span>
        </div>
        <div class="live-score-breakdown">
          ${renderScoreBar('Park',    score.park_score,    30)}
          ${renderScoreBar('Weather', score.weather_score, 25)}
          ${renderScoreBar('Pitcher', score.pitcher_score, 20)}
          ${renderScoreBar('Odds',    score.odds_score,    15)}
          ${renderScoreBar('Power',   score.batter_score,  10)}
        </div>
      </div>

      <!-- Pitchers + weather row -->
      <div class="live-details-row">
        ${renderPitchers(game)}
        ${weather ? renderWeather(weather) : renderIndoorBadge()}
      </div>

      <!-- Props for this game -->
      ${props.length > 0 ? renderGameProps(props) : ''}

      <!-- TikTok content angle -->
      <div class="live-content-angle">
        <div class="live-angle-label">📲 Content Angle</div>
        <div class="live-angle-text">${score.content_angle}</div>
        <button class="grab-btn" style="margin-top:10px; width:100%;"
          onclick="copyText(this, ${JSON.stringify(score.content_angle)})">
          📲 Copy Hook
        </button>
      </div>

    </div>
  `;
}


function renderScoreBar(label, value, max) {
  const pct = Math.round((value / max) * 100);
  return `
    <div class="score-bar-row">
      <span class="score-bar-label">${label}</span>
      <div class="score-bar-track">
        <div class="score-bar-fill" style="width:${pct}%"></div>
      </div>
      <span class="score-bar-value">${value}</span>
    </div>
  `;
}


function renderPitchers(game) {
  const home = game.home_pitcher || 'TBD';
  const away = game.away_pitcher || 'TBD';
  return `
    <div class="live-pitchers">
      <div class="live-detail-label">Probable Pitchers</div>
      <div class="live-pitcher-row">
        <span class="live-pitcher-team">${_abbrev(game.away_team)}</span>
        <span class="live-pitcher-name">${away}</span>
      </div>
      <div class="live-pitcher-row">
        <span class="live-pitcher-team">${_abbrev(game.home_team)}</span>
        <span class="live-pitcher-name">${home}</span>
      </div>
    </div>
  `;
}


function renderWeather(w) {
  const windIcon = w.wind_category === 'out' ? '↑' : w.wind_category === 'in' ? '↓' : '→';
  const condIcon = { 'Clear': '☀️', 'Sunny': '☀️', 'Clouds': '⛅', 'Rain': '🌧️',
                     'Thunderstorm': '⛈️', 'Snow': '❄️', 'Drizzle': '🌦️' }[w.conditions] || '🌤️';
  return `
    <div class="live-weather">
      <div class="live-detail-label">Weather</div>
      <div class="live-weather-main">
        ${condIcon} ${w.conditions} · ${w.temp_f}°F
      </div>
      <div class="live-weather-wind">
        ${windIcon} ${w.wind_speed_mph} mph ${w.wind_direction}
        <span class="live-wind-cat live-wind-${w.wind_category}">${w.wind_category}</span>
      </div>
    </div>
  `;
}


function renderIndoorBadge() {
  return `
    <div class="live-weather">
      <div class="live-detail-label">Weather</div>
      <div class="live-weather-main">🏠 Indoor / Retractable roof</div>
      <div class="live-weather-wind" style="color:var(--gray)">Weather not a factor</div>
    </div>
  `;
}


function renderGameProps(props) {
  const items = props.map(p => `
    <div class="live-prop-row">
      <span class="live-prop-player">${p.player_name}</span>
      <span class="live-prop-team">${p.team}</span>
      <span class="live-prop-odds">+${p.odds}</span>
      <span class="live-prop-book">${p.bookmaker}</span>
    </div>
  `).join('');

  return `
    <div class="live-game-props">
      <div class="live-detail-label">HR Props in This Game</div>
      ${items}
    </div>
  `;
}


// ── Loading + error states ─────────────────────────────────────

function renderLoadingState() {
  return `
    <div class="live-loading">
      <div class="live-spinner"></div>
      <div class="live-loading-text">Fetching live data from all sources...</div>
      <div class="live-loading-sub">MLB schedule · HR props · Ballpark weather</div>
    </div>
  `;
}


function renderErrorState(errorMsg) {
  return `
    <div class="live-error">
      <div class="live-error-icon">⚡</div>
      <div class="live-error-title">Backend Not Running</div>
      <div class="live-error-body">
        The Live Data page needs the backend server to be running.<br/>
        Open a Terminal window and run:
      </div>
      <div class="live-error-code">cd /Users/samfeldt/tasty-pick-ems && ./start.sh</div>
      <div class="live-error-detail">${errorMsg}</div>
      <button class="grab-btn" style="margin-top:16px;" onclick="fetchLiveData()">
        🔄 Try Again
      </button>
    </div>
  `;
}


// ── Utilities ─────────────────────────────────────────────────

function _scoreColor(score) {
  if (score >= 80) return '#39FF14';        // neon green — elite
  if (score >= 65) return '#a8ff6e';        // light green — strong
  if (score >= 50) return '#FFD700';        // gold — moderate
  if (score >= 35) return '#FF8C00';        // orange — weak
  return '#FF4444';                         // red — poor
}

function _formatTime(isoString) {
  if (!isoString) return '--:--';
  try {
    return new Date(isoString).toLocaleTimeString('en-US', {
      hour: 'numeric', minute: '2-digit', timeZoneName: 'short'
    });
  } catch {
    return isoString;
  }
}

function _abbrev(teamName) {
  // Quick map of common full names to abbreviations
  const map = {
    'Yankees': 'NYY', 'Mets': 'NYM', 'Red Sox': 'BOS', 'Cubs': 'CHC',
    'White Sox': 'CHW', 'Astros': 'HOU', 'Athletics': 'OAK',
    'Dodgers': 'LAD', 'Angels': 'LAA', 'Giants': 'SF', 'Padres': 'SD',
    'Mariners': 'SEA', 'Rangers': 'TEX', 'Phillies': 'PHI',
    'Braves': 'ATL', 'Cardinals': 'STL', 'Reds': 'CIN', 'Pirates': 'PIT',
    'Rockies': 'COL', 'Diamondbacks': 'ARI', 'Brewers': 'MIL',
    'Twins': 'MIN', 'Tigers': 'DET', 'Guardians': 'CLE', 'Royals': 'KC',
    'Orioles': 'BAL', 'Blue Jays': 'TOR', 'Rays': 'TB', 'Marlins': 'MIA',
    'Nationals': 'WSH',
  };
  const key = Object.keys(map).find(k => teamName.includes(k));
  return key ? map[key] : teamName.slice(0, 3).toUpperCase();
}
