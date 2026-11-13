/* Shared validation for account and invitation forms. No authentication decisions live here. */
window.AssessBridgeAccountFeedback = (() => {
  const manager = AssessBridgeFeedback.create({ notificationHost: '#account-notifications' });
  const present = input => Boolean(input.value.trim());
  const email = input => present(input) && input.validity.valid && input.value.trim().length <= 254
    && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(input.value.trim());
  function validate(scope, rules) {
    let first;
    for (const [input, test, text] of rules) {
      const id = `field-${input.id}`;
      if (test(input)) scope.clear(id);
      else {
        scope.fieldError({ id, input, target: input.closest('.field'), text, validate: test });
        first ||= input;
      }
    }
    first?.focus();
    return !first;
  }
  function notice(scope, target, text, tone = 'error', action) {
    return scope.show({ id: 'status', target, text, tone, blocking: tone === 'error', action });
  }
  async function detail(response) {
    const body = await response.json().catch(() => ({}));
    return typeof body.detail === 'string' ? body.detail : '';
  }
  return { manager, present, email, validate, notice, detail };
})();
