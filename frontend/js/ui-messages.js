/* Reusable, scoped feedback. Page migrations choose when to show each component. */
(() => {
  'use strict';
  let sequence = 0;
  const uid = () => `ab-feedback-${++sequence}`;
  const tones = new Set(['success', 'error', 'warning', 'info', 'neutral', 'loading']);
  const node = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const targetElement = value => {
    const element = typeof value === 'string' ? document.querySelector(value) : value;
    if (!(element instanceof HTMLElement)) throw new TypeError('Feedback requires an existing host element');
    return element;
  };
  const icon = (tone, close = false) => {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('focusable', 'false');
    svg.setAttribute('fill', 'none'); svg.setAttribute('stroke', 'currentColor'); svg.setAttribute('stroke-width', '2');
    svg.setAttribute('stroke-linecap', 'round'); svg.setAttribute('stroke-linejoin', 'round');
    svg.classList.add('ab-feedback__icon');
    const paths = close ? ['M6 6l12 12M18 6L6 18'] : tone === 'success'
      ? ['M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0', 'm7 12 3 3 7-7']
      : ['error', 'warning'].includes(tone) ? ['M12 3 2 21h20L12 3Z', 'M12 9v4m0 4h.01']
      : tone === 'loading' ? ['M12 2a10 10 0 1 0 10 10']
      : ['M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0', 'M12 11v6m0-10h.01'];
    for (const d of paths) { const path = document.createElementNS(svg.namespaceURI, 'path'); path.setAttribute('d', d); svg.append(path); }
    if (tone === 'loading' && !close) svg.classList.add('ab-feedback__spinner');
    return svg;
  };
  function liveRegion(host) {
    const polite = node('div', 'ab-feedback-live'), alert = node('div', 'ab-feedback-live');
    polite.setAttribute('role', 'status'); polite.setAttribute('aria-live', 'polite');
    alert.setAttribute('role', 'alert'); alert.setAttribute('aria-live', 'assertive');
    for (const element of [polite, alert]) { element.setAttribute('aria-atomic', 'true'); host.append(element); }
    let pending = [], timer = null, disposed = false;
    const flush = () => {
      timer = null;
      const next = pending.shift();
      if (!next || disposed) return;
      if (next.current()) {
        const element = next.urgent ? alert : polite;
        element.textContent = '';
        timer = setTimeout(() => {
          if (!disposed && next.current()) element.textContent = next.text;
          timer = setTimeout(flush, 150);
        }, 30);
      } else timer = setTimeout(flush, 0);
    };
    return {
      say(text, urgent, current) { if (disposed) return; pending.push({ text, urgent, current }); if (timer === null) flush(); },
      destroy() { disposed = true; clearTimeout(timer); pending = []; polite.remove(); alert.remove(); },
    };
  }

  function create({ notificationHost } = {}) {
    const host = targetElement(notificationHost);
    const rail = node('div', 'ab-feedback-region'); rail.setAttribute('aria-label', 'Notifications'); rail.setAttribute('role', 'region');
    // Portal outside page cards and scrolling containers.
    const dock = node('div', 'ab-feedback-dock');
    const attention = node('button', 'ab-feedback-attention'); attention.type = 'button'; attention.hidden = true;
    const attentionText = node('span'), attentionAction = node('strong', '', 'View message');
    attention.append(icon('error'), attentionText, attentionAction);
    dock.append(attention, rail); document.body.append(dock);
    const announcer = liveRegion(host), scopes = new Map(), records = new Set(), inputs = new WeakMap();
    const visible = [], queue = [];
    let destroyed = false, attentionRecord = null, frame = null;
    function refreshAttention() {
      frame = null;
      const modal = !!document.querySelector('dialog[open]');
      dock.hidden = modal;
      for (const record of visible) { if (modal) stopTimer(record); else resumeTimer(record); }
      const outside = [...records].filter(record => {
        if (record.removed || !record.scope.active || record.kind === 'temporary'
            || record.options.announce === false || !['error', 'warning'].includes(record.options.tone)
            || record.element.classList.contains('ab-feedback--compact') || !record.element.getClientRects().length) return false;
        const r = record.element.getBoundingClientRect();
        return r.bottom <= 0 || r.top >= window.innerHeight || r.right <= 0 || r.left >= window.innerWidth;
      });
      attentionRecord = outside.at(-1) || null;
      attention.hidden = !attentionRecord;
      if (attentionRecord) {
        attentionText.textContent = outside.length > 1 ? `${outside.length} messages need attention.` : attentionRecord.options.text;
        attention.setAttribute('aria-controls', attentionRecord.element.id);
      } else attention.removeAttribute('aria-controls');
    }
    function scheduleAttention() { if (!destroyed && frame === null) frame = requestAnimationFrame(refreshAttention); }
    const observer = new IntersectionObserver(scheduleAttention);
    const modalObserver = new MutationObserver(scheduleAttention);
    modalObserver.observe(document.body, { subtree: true, attributes: true, attributeFilter: ['open'] });
    document.addEventListener('scroll', scheduleAttention, true);
    window.addEventListener('resize', scheduleAttention);
    attention.addEventListener('click', () => {
      const record = attentionRecord;
      if (!record || record.removed) return;
      record.element.scrollIntoView({ block: 'center', behavior: 'instant' });
      const focus = record.input || record.element.querySelector('button:not([hidden]), a') || record.element;
      if (focus === record.element) focus.tabIndex = -1;
      focus.focus({ preventScroll: true }); scheduleAttention();
    });
    function paused(record) { return record.hovered || record.element.contains(document.activeElement) || document.hidden || !!document.querySelector('dialog[open]'); }
    function stopTimer(record) {
      if (record.timer !== null) {
        clearTimeout(record.timer); record.timer = null;
        record.remaining = Math.max(0, record.remaining-(performance.now()-record.started));
      }
    }
    function resumeTimer(record) {
      if (record.kind !== 'temporary' || !record.visible || record.removed || paused(record) || record.timer !== null) return;
      record.started = performance.now();
      record.timer = setTimeout(() => remove(record), record.remaining);
    }
    function speak(record) {
      if (record.options.announce === false) return;
      const text = [record.options.title, record.options.text].filter(Boolean).join(' ');
      const signature = `${record.options.tone}:${text}`;
      if (signature === record.announced) return;
      record.announced = signature;
      announcer.say(text, record.options.blocking || record.options.tone === 'error', () => !record.removed && record.scope.active && record.announced === signature);
    }
    function drain() {
      while (visible.length < 2 && queue.length) {
        const record = queue.shift();
        if (record.removed || !record.scope.active) continue;
        record.visible = true; visible.push(record); rail.append(record.element); speak(record); resumeTimer(record);
      }
    }
    function remove(record, restoreFocus = false) {
      if (!record || record.removed) return false;
      const hadFocus = record.element.contains(document.activeElement);
      record.removed = true; stopTimer(record);
      observer.unobserve(record.element); scheduleAttention();
      record.cleanup?.(); record.element.remove(); records.delete(record); record.scope.records.delete(record.id);
      for (const list of [visible, queue]) { const index = list.indexOf(record); if (index >= 0) list.splice(index, 1); }
      if (restoreFocus && hadFocus && record.returnFocus?.isConnected) record.returnFocus.focus({ preventScroll: true });
      drain(); return true;
    }
    function validate(options, kind) {
      if (typeof options.id !== 'string' || !options.id.trim()) throw new TypeError('Feedback requires a stable string id');
      if (typeof options.text !== 'string' || !options.text.trim()) throw new TypeError('Feedback requires plain message text');
      if (options.title !== undefined && typeof options.title !== 'string') throw new TypeError('Feedback title must be plain text');
      const tone = options.tone || (kind === 'temporary' ? 'success' : 'info');
      if (!tones.has(tone)) throw new TypeError('Unknown feedback tone');
      if (kind === 'temporary' && tone !== 'success') throw new TypeError('Errors and warnings must be persistent');
      if (kind === 'temporary' && options.blocking) throw new TypeError('Required outcomes must be persistent');
      if (options.blocking && options.dismissible) throw new TypeError('An active blocker cannot be dismissed');
      if (options.action) {
        if (typeof options.action.label !== 'string' || !options.action.label.trim()
            || (typeof options.action.onClick !== 'function' && !options.action.href)) throw new TypeError('Feedback action needs a label and callback or URL');
        if (options.action.href && options.action.onClick) throw new TypeError('Choose a link or callback action');
        if (options.action.errorText !== undefined && typeof options.action.errorText !== 'string') throw new TypeError('Action error must be plain text');
        if (options.action.href) {
          if (typeof options.action.href !== 'string') throw new TypeError('Feedback URL must be a string');
          const url = new URL(options.action.href, location.href);
          if (!['http:', 'https:'].includes(url.protocol)) throw new TypeError('Feedback links require HTTP(S) URLs');
        }
      }
      return { ...options, tone };
    }
    function render(record) {
      const revision = record.revision = (record.revision || 0)+1;
      const options = record.options, element = record.element;
      element.dataset.tone = options.tone;
      record.symbol.replaceChildren(icon(options.tone));
      record.title.textContent = options.title || ''; record.title.hidden = !options.title;
      record.text.textContent = options.text;
      const action = options.action;
      const oldFocused = record.actions.contains(document.activeElement);
      record.actions.replaceChildren();
      if (action) {
        const control = node(action.href ? 'a' : 'button', 'ab-feedback__action', action.label);
        if (action.href) control.href = action.href;
        else {
          control.type = 'button';
          control.disabled = !!record.actionBusy;
          control.addEventListener('click', async () => {
            if (record.actionBusy || !record.scope.active || record.removed) return;
            record.actionBusy = true; control.disabled = true;
            try { await action.onClick({ scope: record.scope.api }); }
            catch (error) {
              if (record.scope.active && !record.removed && record.revision === revision) {
                remove(record);
                record.scope.api.show({ id: `${record.id}:action-error`, target: options.actionErrorTarget || options.target || host,
                  text: action.errorText || 'Could not complete this action. Try again.', tone: 'error' });
              }
            } finally {
              record.actionBusy = false; control.disabled = false;
              const current = record.actions.querySelector('button');
              if (current) current.disabled = false;
            }
          });
        }
        record.actions.append(control);
        if (oldFocused) control.focus({ preventScroll: true });
      }
      record.actions.hidden = !action;
      const dismissible = record.kind === 'temporary' || !!options.dismissible;
      record.close.hidden = !dismissible;
    }
    function show(scope, supplied, kind) {
      if (!scope.active || destroyed) return null;
      const options = validate(supplied, kind);
      if (kind !== 'temporary') targetElement(options.target);
      let record = scope.records.get(options.id);
      if (record && record.kind !== kind) remove(record);
      record = scope.records.get(options.id);
      if (record) {
        stopTimer(record); record.options = options; record.remaining = 4000;
        if (kind !== 'temporary') targetElement(options.target).append(record.element);
        render(record); scheduleAttention(); if (record.visible) speak(record); resumeTimer(record); return record.element;
      }
      const element = node('div', `ab-feedback${kind === 'temporary' ? ' ab-feedback--temporary' : ''}`);
      element.id = uid(); element.dataset.messageId = options.id;
      const symbol = node('span'), content = node('div', 'ab-feedback__content');
      const title = node('strong', 'ab-feedback__title'), text = node('p', 'ab-feedback__text'), actions = node('div', 'ab-feedback__actions');
      const close = node('button', 'ab-feedback__close'); close.type = 'button'; close.setAttribute('aria-label', kind === 'temporary' ? 'Close notification' : 'Close message'); close.append(icon('neutral', true));
      content.append(title, text, actions); element.append(symbol, content, close);
      record = { scope, id: options.id, kind, options, element, symbol, title, text, actions, close, remaining: 4000,
        timer: null, started: 0, hovered: false, visible: kind !== 'temporary', removed: false, returnFocus: document.activeElement };
      close.addEventListener('click', () => remove(record, true));
      element.addEventListener('pointerenter', () => { record.hovered = true; stopTimer(record); });
      element.addEventListener('pointerleave', () => { record.hovered = false; resumeTimer(record); });
      element.addEventListener('focusin', () => stopTimer(record));
      element.addEventListener('focusout', () => queueMicrotask(() => resumeTimer(record)));
      scope.records.set(record.id, record); records.add(record); render(record);
      if (kind === 'temporary') {
        if (visible.length < 2) { queue.push(record); drain(); }
        else { if (queue.length >= 3) remove(queue[0]); queue.push(record); }
      } else { targetElement(options.target).append(element); observer.observe(element); scheduleAttention(); speak(record); }
      return element;
    }
    function field(scope, options) {
      if (!scope.active || destroyed) return null;
      validate({ ...options, tone: 'error' }, 'field');
      const input = targetElement(options.input);
      if (!input.matches('input, textarea, select')) throw new TypeError('Field errors require a form control');
      if (options.target) targetElement(options.target);
      if (options.validate !== undefined && typeof options.validate !== 'function') throw new TypeError('Field validator must be a function');
      const existing = scope.records.get(options.id);
      if (existing?.kind === 'field' && existing.input === input) {
        existing.options = { ...options, tone: 'error' }; existing.text.textContent = options.text; scheduleAttention(); speak(existing); return existing.element;
      }
      if (existing) remove(existing);
      if (inputs.has(input)) remove(inputs.get(input));
      const element = node('p', 'ab-field-error'); element.id = uid(); element.dataset.messageId = options.id;
      const text = node('span', '', options.text); element.append(icon('error'), text);
      const invalid = input.getAttribute('aria-invalid');
      const described = new Set((input.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean));
      described.add(element.id); input.setAttribute('aria-describedby', [...described].join(' ')); input.setAttribute('aria-invalid', 'true');
      const record = { scope, id: options.id, kind: 'field', options: { ...options, tone: 'error' }, element, text, input, removed: false, timer: null };
      const revalidate = () => { if (record.options.validate?.(input) === true) remove(record); };
      input.addEventListener('input', revalidate); input.addEventListener('change', revalidate);
      record.cleanup = () => {
        input.removeEventListener('input', revalidate); input.removeEventListener('change', revalidate); inputs.delete(input);
        const remaining = (input.getAttribute('aria-describedby') || '').split(/\s+/).filter(value => value && value !== element.id);
        if (remaining.length) input.setAttribute('aria-describedby', remaining.join(' ')); else input.removeAttribute('aria-describedby');
        if (invalid === null) input.removeAttribute('aria-invalid'); else input.setAttribute('aria-invalid', invalid);
      };
      scope.records.set(record.id, record); records.add(record); inputs.set(input, record);
      if (options.target) targetElement(options.target).append(element); else input.after(element);
      observer.observe(element); scheduleAttention(); speak(record); return element;
    }
    function confirmation(scope, options) {
      if (!scope.active || destroyed) return null;
      if (['id', 'title', 'text', 'confirmLabel'].some(key => typeof options[key] !== 'string' || !options[key].trim())) throw new TypeError('Confirmation requires id, title, text and action label');
      if (options.onConfirm !== undefined && typeof options.onConfirm !== 'function') throw new TypeError('Confirmation callback must be a function');
      scope.dialogs.get(options.id)?.destroy();
      const element = node('dialog', 'ab-feedback-dialog'); element.id = options.dialogId || uid();
      if (typeof element.showModal !== 'function') throw new Error('This browser does not support confirmation dialogs');
      const title = node('h2', 'ab-feedback-dialog__title', options.title); title.id = uid();
      const body = node('p', 'ab-feedback-dialog__body', options.text); body.id = uid();
      element.setAttribute('aria-labelledby', title.id); element.setAttribute('aria-describedby', body.id);
      const content = node('div', 'ab-feedback-dialog__content');
      if (options.content) content.append(targetElement(options.content));
      const errors = node('div', 'ab-feedback-dialog__errors'), actions = node('div', 'ab-feedback-dialog__actions');
      const cancel = node('button', 'ab-feedback__action', options.cancelLabel || 'Cancel'); cancel.type = 'button';
      const confirm = node('button', `ab-feedback__action ${options.destructive ? 'ab-feedback__action--destructive' : 'ab-feedback__action--primary'}`, options.confirmLabel); confirm.type = 'button';
      if (options.cancelButtonId) cancel.id = options.cancelButtonId;
      if (options.confirmButtonId) confirm.id = options.confirmButtonId;
      if (options.errorId) errors.id = options.errorId;
      actions.append(cancel, confirm); element.append(title, body, content, errors, actions);
      document.body.append(element);
      const live = liveRegion(element), abort = new AbortController(), returnFocus = document.activeElement;
      let busy = false, finished = false, confirmBlocked = false, resolve, lastError = '';
      const result = new Promise(done => { resolve = done; });
      const finish = (accepted, force = false) => {
        if (finished || (busy && !force)) return;
        finished = true; abort.abort(); live.destroy(); scope.dialogs.delete(options.id);
        element.close(); element.remove(); scheduleAttention(); resolve(accepted);
        if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
      };
      const setError = text => {
        if (finished || !scope.active) return;
        if (typeof text !== 'string') throw new TypeError('Dialog error must be plain text');
        const changed = text !== lastError; lastError = text;
        errors.replaceChildren();
        if (text) {
          const error = node('div', 'ab-feedback'); error.dataset.tone = 'error'; error.append(icon('error'), node('p', 'ab-feedback__text', text)); errors.append(error);
          error.scrollIntoView({ block: 'nearest', behavior: 'instant' });
          if (changed) live.say(text, true, () => !finished && scope.active && lastError === text);
        }
      };
      const controller = { element, content, result, setError, setConfirmDisabled(value) { confirmBlocked = Boolean(value); confirm.disabled = busy || confirmBlocked; }, destroy: () => finish(false, true) };
      scope.dialogs.set(options.id, controller);
      cancel.addEventListener('click', () => finish(false));
      element.addEventListener('cancel', event => { event.preventDefault(); finish(false); });
      element.addEventListener('close', () => { if (!finished) finish(false, true); });
      confirm.addEventListener('click', async () => {
        if (busy || finished || confirmBlocked || !scope.active) return;
        busy = true; cancel.disabled = confirm.disabled = true; element.setAttribute('aria-busy', 'true');
        confirm.textContent = options.busyLabel || 'Saving…'; setError('');
        try {
          const accepted = await options.onConfirm?.({ dialog: controller, scope: scope.api, signal: abort.signal });
          if (!finished && scope.active && accepted !== false) { busy = false; finish(true); }
        } catch (error) {
          if (!finished && scope.active) {
            let text;
            try { text = typeof options.errorMessage === 'function' ? options.errorMessage(error) : options.errorMessage; } catch { /* Fall back to safe copy. */ }
            setError(typeof text === 'string' && text.trim() ? text : 'Could not complete this action. Try again.');
          }
        } finally {
          if (!finished) { busy = false; cancel.disabled = false; confirm.disabled = confirmBlocked; confirm.textContent = options.confirmLabel; element.removeAttribute('aria-busy'); }
        }
      });
      element.showModal(); scheduleAttention();
      (options.initialFocus && element.contains(options.initialFocus) ? options.initialFocus : cancel).focus({ preventScroll: true });
      return controller;
    }
    const onVisibility = () => { for (const record of visible) { if (document.hidden) stopTimer(record); else resumeTimer(record); } };
    document.addEventListener('visibilitychange', onVisibility);
    const manager = {
      scope(name) {
        if (destroyed) throw new Error('Feedback manager was destroyed');
        if (typeof name !== 'string' || !name.trim()) throw new TypeError('A scope needs a stable name');
        scopes.get(name)?.api.dispose();
        const scope = { name, active: true, records: new Map(), dialogs: new Map() };
        const api = {
          notify: options => show(scope, options, 'temporary'),
          show: options => show(scope, options, 'persistent'),
          fieldError: options => field(scope, options),
          confirm: options => confirmation(scope, options),
          update(id, patch) {
            if (!scope.active) return null;
            const record = scope.records.get(id); if (!record) return null;
            const options = { ...record.options, ...patch, id };
            return record.kind === 'field' ? field(scope, options) : show(scope, options, record.kind);
          },
          clear: id => remove(scope.records.get(id)),
          clearAll() { for (const record of [...scope.records.values()]) remove(record); },
          dispose() {
            if (!scope.active) return;
            scope.active = false;
            for (const dialog of [...scope.dialogs.values()]) dialog.destroy();
            api.clearAll(); if (scopes.get(name) === scope) scopes.delete(name);
          },
          get active() { return scope.active; },
        };
        scope.api = api; scopes.set(name, scope); return api;
      },
      destroy() {
        if (destroyed) return;
        destroyed = true; for (const scope of [...scopes.values()]) scope.api.dispose();
        document.removeEventListener('visibilitychange', onVisibility); announcer.destroy(); observer.disconnect(); modalObserver.disconnect(); cancelAnimationFrame(frame);
        document.removeEventListener('scroll', scheduleAttention, true); window.removeEventListener('resize', scheduleAttention); dock.remove();
      },
    };
    return manager;
  }
  window.AssessBridgeFeedback = Object.freeze({ create });
})();
