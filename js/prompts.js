// ============================================================
// TASTY PICK EMS — Prompt Library
// ============================================================

function renderPromptLibrary() {
  const container = document.getElementById('prompt-library-grid');
  if (!container) return;

  container.innerHTML = DATA.prompts.map((p, i) => `
    <div class="prompt-card">
      <div class="prompt-card-header">
        <div>
          <div class="prompt-player-name">${p.player}</div>
          <div class="prompt-team">${p.team}</div>
        </div>
        <div class="prompt-odds-badge">${p.odds}</div>
      </div>

      <div class="prompt-card-body">

        <!-- Image Prompt -->
        <div>
          <div class="prompt-label">🖼 Image Generation Prompt</div>
          <div class="prompt-text">${p.imagePrompt}</div>
          <button class="copy-btn mt-8" onclick="copyPromptText(this, ${i}, 'image')">
            📋 Copy Image Prompt
          </button>
        </div>

        <!-- Caption Template -->
        <div>
          <div class="prompt-label">📝 Caption Template</div>
          <div class="prompt-caption-text" id="caption-${i}">${p.captionTemplate}</div>
          <button class="copy-btn mt-8" onclick="copyPromptText(this, ${i}, 'caption')">
            📋 Copy Caption
          </button>
        </div>

        <!-- Style Note -->
        <div>
          <div class="prompt-label">🎨 Style Note</div>
          <div class="style-note">${p.styleNote}</div>
        </div>

      </div>
    </div>
  `).join('');
}

function copyPromptText(btn, index, type) {
  const p = DATA.prompts[index];
  const text = type === 'image' ? p.imagePrompt : p.captionTemplate;
  navigator.clipboard.writeText(text).then(() => {
    btn.classList.add('copied');
    btn.textContent = '✓ Copied!';
    setTimeout(() => {
      btn.classList.remove('copied');
      btn.innerHTML = type === 'image' ? '📋 Copy Image Prompt' : '📋 Copy Caption';
    }, 2000);
  });
}

function initPromptLibrary() {
  renderPromptLibrary();
}
