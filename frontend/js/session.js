/* Shared account-session expiry handling. Exam credentials have their own lifecycle. */
(() => {
  'use strict';
  const url = new URL(location.href);
  const path = url.pathname.replace(/\.html$/, '');
  if (path === '/' || path === '/index') {
    if (url.searchParams.get('session') === 'expired') {
      url.searchParams.delete('session');
      history.replaceState(null, '', url.pathname + url.search + url.hash);
      const host = document.querySelector('#account-notifications');
      const feedback = AssessBridgeFeedback.create({ notificationHost: host });
      feedback.scope('session-ended').notify({
        id: 'expired', text: 'Your session has ended. Please sign in again.',
        tone: 'error', dismissible: true,
      });
    }
    return;
  }

  const protectedPage = ['/company-admin', '/employer', '/auditor', '/candidate', '/exam-room'].includes(path);
  if (!protectedPage && path !== '/exam-entry') return;
  const originalFetch = window.fetch.bind(window);
  let authenticated = protectedPage, leaving = false, epoch = 0;
  let deadlineTimer = null, checking = null;

  function clearDeadline() { clearTimeout(deadlineTimer); deadlineTimer = null; }
  function ended() {
    if (leaving) return;
    leaving = true;
    authenticated = false;
    clearDeadline();
    // The marked 401 response already cleared both HttpOnly session cookies.
    location.replace('/?session=expired');
  }
  function rememberDeadline(response) {
    const expires = Date.parse(response.headers.get('X-AssessBridge-Session-Expires-At') || '');
    if (!Number.isFinite(expires)) return;
    const serverNow = Date.parse(response.headers.get('Date') || '');
    const delay = Math.max(250, expires - (Number.isFinite(serverNow) ? serverNow : Date.now()));
    clearDeadline();
    // Recheck with the server: another tab or Start exam may have renewed the session.
    deadlineTimer = setTimeout(check, Math.min(delay, 2147483647));
  }
  async function check() {
    if (!authenticated || leaving || checking) return checking;
    checking = (async () => {
      try { await window.fetch('/api/auth/me', { credentials: 'same-origin', cache: 'no-store' }); }
      catch { /* Connectivity loss is not evidence of an expired account session. */ }
    })();
    try { await checking; } finally { checking = null; }
  }

  window.fetch = async (input, options) => {
    const requestUrl = new URL(typeof input === 'string' || input instanceof URL ? input : input.url, location.href);
    const ownApi = requestUrl.origin === location.origin && requestUrl.pathname.startsWith('/api/');
    const requestEpoch = epoch;
    const response = await originalFetch(input, options);
    if (!ownApi || leaving) return response;
    if (requestUrl.pathname === '/api/auth/login' && response.ok) {
      epoch++;
      authenticated = true;
      void check();
      return response;
    }
    if (requestUrl.pathname === '/api/auth/logout' && response.ok) {
      epoch++;
      authenticated = false;
      clearDeadline();
      return response;
    }
    if (requestEpoch !== epoch) return response;
    if (response.status === 401 && response.headers.get('X-AssessBridge-Session-Ended') === '1') {
      if (authenticated) ended();
      return response;
    }
    if (response.ok && requestUrl.pathname === '/api/auth/me') authenticated = true;
    if (response.ok) rememberDeadline(response);
    return response;
  };

  setInterval(() => { if (!document.hidden) void check(); }, 20000);
  window.addEventListener('focus', () => { void check(); });
  window.addEventListener('pageshow', () => { void check(); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) void check(); });
  void check();
})();
