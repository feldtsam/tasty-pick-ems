// ============================================================
// TASTY PICK EMS — Player Card Builder
// Returns HTML string for a vertical 9:16 player card.
// ============================================================

function getTagClass(tag) {
  const map = {
    "Top Pick": "tag-top",
    "Value":    "tag-value",
    "Sneaky":   "tag-sneaky",
    "Long Shot": "tag-long"
  };
  return map[tag] || "tag-value";
}

function buildPlayerCard(player) {
  const tagClass = getTagClass(player.tag);
  const today = new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });

  const reasonsHTML = player.reasons.map(r => `
    <div class="card-reason">
      <div class="card-reason-dot"></div>
      <div class="card-reason-text">${r}</div>
    </div>
  `).join('');

  return `
    <div class="player-card">

      <!-- Branding bar -->
      <div class="card-brand-bar">
        <span class="card-logo-text">TASTY PICK EMS</span>
        <span class="card-tag ${tagClass}">${player.tag}</span>
      </div>

      <!-- Image area -->
      <div class="card-image-area">
        <div class="card-image-placeholder">
          <div class="player-silhouette">⚾</div>
          <div class="team-label">${player.team}</div>
        </div>
      </div>

      <!-- Body -->
      <div class="card-body">

        <!-- Name + meta -->
        <div class="card-player-name">${player.name}</div>
        <div class="card-player-meta">${player.position} · ${player.team} · Bats ${player.bats}</div>

        <!-- Odds -->
        <div class="card-odds-row">
          <div>
            <div class="card-odds">${player.odds}</div>
            <div class="card-odds-label">HR Prop Odds</div>
          </div>
        </div>

        <!-- Hero stat -->
        <div class="card-hero-stat">
          <div class="card-hero-stat-value">${player.heroStat}</div>
          <div class="card-hero-stat-label">${player.statLine} · ${player.recentForm}</div>
        </div>

        <!-- Divider -->
        <div class="card-divider"></div>

        <!-- Reasons -->
        <div class="card-reasons">
          ${reasonsHTML}
        </div>

        <!-- Footer -->
        <div class="card-footer">
          <span class="card-date">${today}</span>
          <span class="card-action">Pick It →</span>
        </div>

      </div>
    </div>
  `;
}

// Renders a grid of player cards into a container element
function renderPlayerCards(containerId, players) {
  const el = document.getElementById(containerId);
  if (!el) return;
  el.innerHTML = players.map(buildPlayerCard).join('');
}
