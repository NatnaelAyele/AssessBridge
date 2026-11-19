/* Shared visibility control for every password input, including inserted forms. */
(() => {
  'use strict';
  let sequence = 0;
  const controls = new Map();
  function enhance(input) {
    if (controls.has(input)) return;
    input.dataset.passwordInput = 'true';
    if (!input.id) {
      do { input.id = `ab-password-${++sequence}`; } while (document.getElementById(input.id) !== input);
    }
    const wrapper = document.createElement('span');
    wrapper.className = 'ab-password-control';
    input.before(wrapper); wrapper.append(input);
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'ab-password-toggle';
    button.setAttribute('aria-controls', input.id);
    button.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/><path class="ab-password-slash" d="m3 3 18 18"/></svg>';
    const sync = () => {
      const visible = input.type !== 'password';
      button.setAttribute('aria-label', visible ? 'Hide password' : 'Show password');
      button.setAttribute('aria-pressed', String(visible));
      button.classList.toggle('is-visible', visible);
      button.disabled = input.disabled;
    };
    button.addEventListener('click', event => {
      event.preventDefault();
      if (input.disabled) return;
      const start = input.selectionStart, end = input.selectionEnd;
      input.type = input.type === 'password' ? 'text' : 'password';
      sync();
      if (start !== null) input.setSelectionRange(start, end);
    });
    wrapper.append(button); controls.set(input, sync); sync();
  }
  function scan(root) {
    if (root.matches?.('input[type="password"]')) enhance(root);
    root.querySelectorAll?.('input[type="password"]').forEach(enhance);
  }
  scan(document);
  new MutationObserver(records => {
    for (const record of records) {
      if (record.type === 'childList') record.addedNodes.forEach(scan);
      else if (controls.has(record.target)) controls.get(record.target)();
      else scan(record.target);
    }
    for (const input of controls.keys()) if (!input.isConnected) controls.delete(input);
  }).observe(document.documentElement, { childList: true, subtree: true,
    attributes: true, attributeFilter: ['type', 'disabled'] });
  function conceal(root) {
    for (const [input, sync] of controls) {
      if (root.contains(input)) { input.type = 'password'; sync(); }
    }
  }
  document.addEventListener('reset', event => conceal(event.target));
  window.addEventListener('pagehide', () => conceal(document));
})();
