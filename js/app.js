// ============================================================
// TASTY PICK EMS — App Router & Init
// ============================================================

let _liveDataInitialized = false;

function showPage(pageId) {
  // Hide all pages
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));

  // Show target page
  document.getElementById(pageId).classList.add('active');
  document.querySelector(`[data-page="${pageId}"]`).classList.add('active');

  // Save state
  sessionStorage.setItem('tpe-page', pageId);

  // Initialize Live Data page on first visit (triggers fetch)
  if (pageId === 'live-data' && !_liveDataInitialized) {
    _liveDataInitialized = true;
    initLiveData();
  }
}

document.addEventListener('DOMContentLoaded', () => {
  // Init static pages
  initDashboard();
  initPromptLibrary();

  // Wire nav buttons
  document.querySelectorAll('.nav-btn[data-page]').forEach(btn => {
    btn.addEventListener('click', () => showPage(btn.dataset.page));
  });

  // Restore last page or default to dashboard
  const saved = sessionStorage.getItem('tpe-page') || 'dashboard';
  showPage(saved);
});
