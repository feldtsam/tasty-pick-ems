// ============================================================
// TASTY PICK EMS — Dashboard Renderers
// ============================================================

// ── HERO PICK ─────────────────────────────────────────────
function renderHero() {
  const el = document.getElementById('hero-section');
  if (!el) return;

  const p = DATA.hrCandidates.find(c => c.featured) || DATA.hrCandidates[0];

  el.innerHTML = `
    <div class="hero-pick">
      <div class="hero-label">⚡ Today's Featured Play</div>
      <div class="hero-inner">
        <div class="hero-left">
          <div class="hero-name">${p.name}</div>
          <div class="hero-meta">${p.position} · ${p.team} · Bats ${p.bats}</div>
          <div class="hero-hook">${p.tiktokHook.replace(/ #\S+/g, '').replace(/[🔥⚡💪😤🐻‍❄️]/g, '').trim()}</div>
          <div class="hero-reasons">
            ${p.reasons.map(r => `<div class="hero-reason">${r}</div>`).join('')}
          </div>
          <div class="hero-actions">
            <button class="grab-btn" onclick="copyHeroHook(this)">📲 Copy TikTok Hook</button>
            <button class="outline-btn" onclick="showPage('prompt-library')">🖼 Get Image Prompt</button>
          </div>
        </div>
        <div class="hero-right">
          <div class="hero-odds">${p.odds}</div>
          <div class="hero-odds-label">HR Prop</div>
          <div class="hero-stat-box">
            <div class="hero-stat-val">${p.heroStat}</div>
            <div class="hero-stat-label">Hero Stat</div>
          </div>
        </div>
      </div>
    </div>
  `;

  window._heroHook = p.tiktokHook;
}

function copyHeroHook(btn) {
  navigator.clipboard.writeText(window._heroHook || '').then(() => {
    btn.classList.add('copied');
    btn.textContent = '✓ Copied!';
    setTimeout(() => {
      btn.classList.remove('copied');
      btn.innerHTML = '📲 Copy TikTok Hook';
    }, 2000);
  });
}

// ── QUICK BAR ─────────────────────────────────────────────
function renderQuickBar() {
  const el = document.getElementById('quick-bar');
  if (!el) return;

  const topPark = DATA.ballparks[0];
  const topPitcher = DATA.pitchers[0];
  const picks = DATA.hrCandidates.length;
  const angles = DATA.sneakyAngles.length;

  el.innerHTML = `
    <div class="quick-pill">
      <span class="pill-icon">🏟️</span>
      <strong>${topPark.park.split(' ')[0]}</strong>
      <span class="pill-stat">${topPark.hrFactor}x</span>
    </div>
    <div class="quick-pill">
      <span class="pill-icon">🎯</span>
      <strong>${topPitcher.name.split(' ')[1]}</strong>
      <span class="pill-stat">${topPitcher.hrPer9} HR/9</span>
    </div>
    <div class="quick-pill">
      <span class="pill-icon">💰</span>
      <strong>${picks} Plays</strong>
      <span class="pill-stat">+300+</span>
    </div>
    <div class="quick-pill">
      <span class="pill-icon">👀</span>
      <strong>${angles} Angles</strong>
      today
    </div>
    <div class="quick-pill">
      <span class="pill-icon">📲</span>
      <strong>${DATA.socialAngles.length} Captions</strong>
      ready
    </div>
  `;
}

// ── PARK LIST (ranked) ─────────────────────────────────────
function renderBallparks() {
  const el = document.getElementById('ballparks-grid');
  if (!el) return;

  el.innerHTML = `<ol class="park-list">
    ${DATA.ballparks.map((p, i) => `
      <li class="park-row">
        <div class="park-rank">#${i + 1}</div>
        <div class="park-info">
          <div class="park-name-row">
            <span class="park-name-text">${p.park}</span>
            <span class="park-team-chip">${p.team}</span>
          </div>
          <div class="park-wind-note">${p.wind}</div>
          <div class="park-insight-text">${p.insight}</div>
        </div>
        <div>
          <div class="park-factor-badge">${p.hrFactor}x</div>
          <div class="park-factor-label">HR Factor</div>
        </div>
      </li>
    `).join('')}
  </ol>`;
}

// ── PITCHER LIST (compact) ─────────────────────────────────
function renderPitchers() {
  const el = document.getElementById('pitchers-grid');
  if (!el) return;

  el.innerHTML = `<div class="pitcher-list">
    ${DATA.pitchers.map(p => {
      const tierClass = p.tier === 'Primary Target' ? 'tier-primary' : 'tier-secondary';
      return `
        <div class="pitcher-compact">
          <div class="pitcher-compact-header">
            <span class="pitcher-tier ${tierClass}">${p.tier}</span>
            <span class="pitcher-name-text">${p.name}</span>
            <span class="pitcher-matchup">${p.handedness} · vs ${p.opponent}</span>
          </div>
          <div class="pitcher-stats-row">
            <div class="pitcher-stat-item">
              <div class="pitcher-stat-val">${p.era}</div>
              <div class="pitcher-stat-lbl">ERA</div>
            </div>
            <div class="pitcher-stat-item">
              <div class="pitcher-stat-val">${p.hrPer9}</div>
              <div class="pitcher-stat-lbl">HR / 9</div>
            </div>
          </div>
          <div class="pitcher-insight">${p.insight}</div>
          <button class="outline-btn mt-8" onclick="copyText(this, ${JSON.stringify(p.tiktokHook)})">📲 Copy Content Angle</button>
        </div>
      `;
    }).join('')}
  </div>`;
}

// ── HR CANDIDATE CAROUSEL ──────────────────────────────────
function renderPlayerCards(containerId, players) {
  const el = document.getElementById(containerId);
  if (!el) return;

  el.innerHTML = `<div class="card-carousel">
    ${players.map((p, i) => {
      const tagClass = { 'Top Pick': 'tag-top', 'Value': 'tag-value', 'Sneaky': 'tag-sneaky', 'Long Shot': 'tag-long' }[p.tag] || 'tag-value';
      return `
        <div class="card-wrap">
          <div class="player-card">
            <div class="card-brand-bar">
              <span class="card-logo-text">TASTY PICK EMS</span>
              <span class="card-tag ${tagClass}">${p.tag}</span>
            </div>
            <div class="card-image-area">
              <div class="card-image-placeholder">
                <div class="player-silhouette">⚾</div>
                <div class="team-label-bg">${p.team}</div>
              </div>
            </div>
            <div class="card-body">
              <div class="card-player-name">${p.name}</div>
              <div class="card-player-meta">${p.position} · ${p.team} · Bats ${p.bats}</div>
              <div class="card-odds">${p.odds}</div>
              <div class="card-odds-label">HR Prop Odds · ${p.recentForm}</div>
              <div class="card-hero-stat">
                <div class="card-hero-stat-value">${p.heroStat}</div>
              </div>
              <div class="card-divider"></div>
              <div class="card-reasons">
                ${p.reasons.map(r => `
                  <div class="card-reason">
                    <div class="card-reason-dot"></div>
                    <div class="card-reason-text">${r}</div>
                  </div>
                `).join('')}
              </div>
            </div>
          </div>
          <button class="card-grab-btn" onclick="copyText(this, ${JSON.stringify(p.tiktokHook)})">📲 Grab TikTok Hook</button>
        </div>
      `;
    }).join('')}
  </div>`;
}

// ── SNEAKY ANGLES ──────────────────────────────────────────
function renderSneakyAngles() {
  const el = document.getElementById('angles-grid');
  if (!el) return;

  el.innerHTML = `<div class="angles-list">
    ${DATA.sneakyAngles.map(a => `
      <div class="angle-card">
        <div class="angle-card-top">
          <span class="angle-tag">${a.tag}</span>
          <span class="angle-odds-chip">${a.odds}</span>
        </div>
        <div class="angle-headline">${a.headline}</div>
        <div class="angle-tiktok-hook">"${a.tiktokHook.replace(/ #\S+/g, '').trim()}"</div>
        <div class="angle-detail">${a.detail}</div>
        <div class="angle-actions">
          <button class="grab-btn" onclick="copyText(this, ${JSON.stringify(a.tiktokHook)})">📲 Copy Hook</button>
        </div>
      </div>
    `).join('')}
  </div>`;
}

// ── SOCIAL / CONTENT ANGLES ────────────────────────────────
function renderSocialAngles() {
  const el = document.getElementById('social-grid');
  if (!el) return;

  el.innerHTML = `<div class="social-list">
    ${DATA.socialAngles.map(a => `
      <div class="social-card">
        <div class="social-card-header">
          <div class="social-hook-text">${a.hook}</div>
          <span class="social-platform-chip">${a.platform}</span>
        </div>
        <div class="social-card-body">
          <div class="social-caption-box">${a.caption}</div>
          <div class="social-idea-label">Content Idea</div>
          <div class="social-idea-row">${a.contentIdea}</div>
          <div class="social-actions">
            <button class="grab-btn" onclick="copyText(this, ${JSON.stringify(a.caption)})">📋 Copy Caption</button>
          </div>
        </div>
      </div>
    `).join('')}
  </div>`;
}

// ── COPY HELPER ────────────────────────────────────────────
function copyText(btn, text) {
  navigator.clipboard.writeText(text).then(() => {
    const original = btn.innerHTML;
    btn.classList.add('copied');
    btn.textContent = '✓ Copied!';
    setTimeout(() => {
      btn.classList.remove('copied');
      btn.innerHTML = original;
    }, 2000);
  });
}

// ── INIT ───────────────────────────────────────────────────
function initDashboard() {
  renderHero();
  renderQuickBar();
  renderBallparks();
  renderPitchers();
  renderPlayerCards('hr-candidates-grid', DATA.hrCandidates);
  renderSneakyAngles();
  renderSocialAngles();

  const dateEl = document.getElementById('dashboard-date');
  if (dateEl) {
    dateEl.textContent = new Date().toLocaleDateString('en-US', {
      weekday: 'long', month: 'long', day: 'numeric', year: 'numeric'
    });
  }
}
