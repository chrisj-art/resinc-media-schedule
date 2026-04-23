// resinc-autosave.js
// Automatically pushes schedule data to GitHub whenever localStorage changes
// The GH_PAT is stored in localStorage (set once by the user in app settings)

(function() {
  const REPO = 'chrisj-art/resinc-media-schedule';
  const FILE = 'resinc_events.json';
  const PAT_KEY = 'resinc_gh_pat';
  let saveTimer = null;

  function getScheduleData() {
    return {
      events: JSON.parse(localStorage.getItem('resinc_media_events') || '[]'),
      projects: JSON.parse(localStorage.getItem('resinc_media_projects') || '[]'),
      savedAt: new Date().toISOString()
    };
  }

  async function pushToGitHub() {
    const pat = localStorage.getItem(PAT_KEY);
    if (!pat) return; // Silent - no PAT configured yet

    const data = getScheduleData();
    const content = btoa(unescape(encodeURIComponent(JSON.stringify(data, null, 2))));

    try {
      // Get current file SHA (if exists)
      let sha = null;
      const check = await fetch('https://api.github.com/repos/' + REPO + '/contents/' + FILE, {
        headers: { 'Authorization': 'token ' + pat }
      });
      if (check.ok) {
        const existing = await check.json();
        sha = existing.sha;
      }

      // Push update
      const body = { message: 'Auto-save: ' + new Date().toISOString(), content };
      if (sha) body.sha = sha;

      const resp = await fetch('https://api.github.com/repos/' + REPO + '/contents/' + FILE, {
        method: 'PUT',
        headers: {
          'Authorization': 'token ' + pat,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(body)
      });

      if (resp.ok) {
        console.log('[RESINC] Schedule auto-saved to GitHub');
      } else {
        const err = await resp.json();
        console.warn('[RESINC] Auto-save failed:', err.message);
      }
    } catch (e) {
      console.warn('[RESINC] Auto-save error:', e.message);
    }
  }

  // Debounced save: wait 3s after last change before pushing
  function scheduleSave() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(pushToGitHub, 3000);
  }

  // Intercept localStorage.setItem to detect changes
  const _setItem = localStorage.setItem.bind(localStorage);
  localStorage.setItem = function(key, value) {
    _setItem(key, value);
    if (key.startsWith('resinc_media_')) {
      scheduleSave();
    }
  };

  console.log('[RESINC] Auto-save ready. Set resinc_gh_pat in localStorage to enable.');
})();
