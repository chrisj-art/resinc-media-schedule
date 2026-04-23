// resinc-autosave.js
// Reads events + projects from Supabase and pushes to GitHub
// whenever Supabase data changes (via Realtime) or on page load.

(function() {
  const REPO      = 'chrisj-art/resinc-media-schedule';
  const FILE      = 'resinc_events.json';
  const PAT_KEY   = 'resinc_gh_pat';
  const SB_URL    = 'https://kosqyettdnibrxskwgfn.supabase.co';
  const SB_KEY    = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imtvc3F5ZXR0ZG5pYnJ4c2t3Z2ZuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzY4MTI3NDIsImV4cCI6MjA5MjM4ODc0Mn0.JccP4W0dVw-kcbKlGOwWzwsNwPEb8rBVujN6mQliuMQ';

  let saveTimer = null;

  // ---- Fetch all data from Supabase ----------------------------------------

  async function fetchFromSupabase(table, params) {
    const url = SB_URL + '/rest/v1/' + table + '?select=*' + (params || '');
    const resp = await fetch(url, {
      headers: { 'apikey': SB_KEY, 'Authorization': 'Bearer ' + SB_KEY }
    });
    if (!resp.ok) throw new Error('Supabase fetch failed: ' + table);
    return resp.json();
  }

  async function getScheduleData() {
    const [events, projects] = await Promise.all([
      fetchFromSupabase('events'),
      fetchFromSupabase('projects'),
    ]);
    return { events, projects, savedAt: new Date().toISOString() };
  }

  // ---- Push to GitHub -------------------------------------------------------

  async function pushToGitHub() {
    const pat = localStorage.getItem(PAT_KEY) || '__RESINC_GH_PAT__';

    let data;
    try {
      data = await getScheduleData();
    } catch(e) {
      console.warn('[RESINC] Could not read Supabase data:', e.message);
      return;
    }

    const content = btoa(unescape(encodeURIComponent(JSON.stringify(data, null, 2))));

    try {
      let sha = null;
      const check = await fetch('https://api.github.com/repos/' + REPO + '/contents/' + FILE, {
        headers: { 'Authorization': 'token ' + pat }
      });
      if (check.ok) sha = (await check.json()).sha;

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
        console.log('[RESINC] Schedule auto-saved to GitHub (' + data.events.length + ' events, ' + data.projects.length + ' projects)');
      } else {
        const err = await resp.json();
        console.warn('[RESINC] Auto-save failed:', err.message);
      }
    } catch(e) {
      console.warn('[RESINC] Auto-save error:', e.message);
    }
  }

  function scheduleSave() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(pushToGitHub, 3000);
  }

  // ---- Watch for Supabase Realtime changes ---------------------------------
  // Wait for the Supabase client (sb) to be available, then subscribe

  function waitForSupabase(attempts) {
    if (attempts <= 0) {
      console.warn('[RESINC] Supabase client not found - falling back to localStorage watch');
      watchLocalStorage();
      return;
    }
    if (window.sb && typeof window.sb.channel === 'function') {
      setupRealtimeWatch();
    } else {
      setTimeout(() => waitForSupabase(attempts - 1), 500);
    }
  }

  function setupRealtimeWatch() {
    window.sb
      .channel('resinc-autosave')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'events' }, () => scheduleSave())
      .on('postgres_changes', { event: '*', schema: 'public', table: 'projects' }, () => scheduleSave())
      .subscribe();
    console.log('[RESINC] Auto-save watching Supabase Realtime (events + projects)');
    // Also do an initial push on load
    scheduleSave();
  }

  // ---- Fallback: watch localStorage (legacy) --------------------------------

  function watchLocalStorage() {
    const _setItem = localStorage.setItem.bind(localStorage);
    localStorage.setItem = function(key, value) {
      _setItem(key, value);
      if (key.startsWith('resinc_media_')) scheduleSave();
    };
    console.log('[RESINC] Auto-save watching localStorage (fallback)');
  }

  // ---- Boot ----------------------------------------------------------------

  console.log('[RESINC] Auto-save ready. Set resinc_gh_pat in localStorage to enable.');
  waitForSupabase(20); // Try for up to 10 seconds

})();
