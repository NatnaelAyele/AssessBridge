/* Persistent candidate feedback. Recurring saves/progress never create notifications. */
window.candidateFeedback = (() => {
  const manager = AssessBridgeFeedback.create({ notificationHost: '#candidate-notifications' });
  const scope = manager.scope('candidate-workflow');
  const tones = { saved: 'success', ready: 'success', saving: 'loading', pending: 'loading', error: 'error' };
  function show(id, text, tone = 'error', options = {}) {
    const target = document.getElementById(id);
    if (!target) return;
    if (!text) { scope.clear(id); target.hidden = true; return; }
    target.hidden = false;
    const element = scope.show({ id, target, text, tone, announce: options.announce !== false, action: options.action });
    if (options.compact) element.classList.add('ab-feedback--compact');
    return element;
  }
  function status(id, text, state) {
    return show(id, text, tones[state] || 'neutral', { compact: true, announce: false });
  }
  function clearExamWarnings() {
    for (const id of ['connection-status', 'exam-error', 'exam-monitoring-warning', 'device-error',
      'recovery-error', 'start-error', 'consent-error', 'identity-error', 'network-check-status',
      'save-status', 'recording-status']) show(id, '');
    const warning = document.getElementById('exam-device-warning');
    if (warning) warning.hidden = true;
  }
  function serverError(response, body, fallback) {
    const detail = typeof body?.detail === 'string' ? body.detail : '';
    if (response.status === 401) return 'Your session ended. Sign in and enter your exam credentials again.';
    if (response.status === 403) return detail.includes('Safe Exam Browser')
      ? 'Open the SEB link from your exam invitation.' : 'Your exam access is unavailable. Sign in and check your exam credentials.';
    if (response.status === 428) return 'Complete the recovery checks before continuing.';
    if (response.status === 409) return 'The exam state changed. Reconnect to check your attempt before continuing.';
    if (response.status === 422) return 'Check the entered information and try again.';
    return fallback;
  }
  return { show, status, clearExamWarnings, serverError, confirm: options => scope.confirm(options) };
})();
