// This instance is exclusively a fictional identity provider. Its server has
// no CAPTCHA validator; the upstream interceptor still requires a nonempty
// marker. Avoid third-party CAPTCHA/network dependencies in the local demo.
(() => {
  const owns = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
  document.addEventListener('DOMContentLoaded', () => {
    const notice = document.createElement('aside');
    notice.setAttribute('aria-label', 'Demo authentication');
    notice.textContent = 'Demo identity verification. Use OTP 111111. No SMS or email is sent.';
    notice.style.cssText = 'padding:12px 20px;background:#eef5ff;color:#123d78;text-align:center;font:500 14px/1.5 system-ui;border-bottom:1px solid #cbdcf5';
    document.body.prepend(notice);
  });
  function bodyForDemo(url, body) {
    if (new URL(url, location.href).pathname.replace('/v1/esignet', '') !== '/flow/execute' || typeof body !== 'string') return body;
    try {
      const data = JSON.parse(body);
      if (data.inputs && Object.keys(data.inputs).length) {
        // The bundled UI retains earlier form values. Re-sending username at
        // the OTP step overwrites the provider's normalized ID in its context.
        // Send only the fields accepted by the selected action.
        const accepted = owns(data.inputs, 'consent_decisions') ? ['consent_decisions']
          : owns(data.inputs, 'otp') && String(data.inputs.otp).length ? ['otp']
          : owns(data.inputs, 'username') ? ['username'] : null;
        if (accepted) data.inputs = Object.fromEntries(accepted.filter(key => owns(data.inputs, key)).map(key => [key, data.inputs[key]]));
        if (['RESEND_OTP', 'BACK_BUTTON'].includes(data.action)) data.inputs = {};
        data.inputs.captcha_token = 'assessbridge-fictional-identity-demo';
        return JSON.stringify(data);
      }
    } catch { /* Leave non-JSON requests unchanged. */ }
    return body;
  }
  const fetchOriginal = window.fetch;
  window.fetch = (input, options) => fetchOriginal(input, options?.body ? {
    ...options, body: bodyForDemo(typeof input === 'string' ? input : input.url, options.body),
  } : options);
  const openOriginal = XMLHttpRequest.prototype.open;
  const sendOriginal = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url, ...args) {
    this.assessbridgeDemoUrl = url;
    return openOriginal.call(this, method, url, ...args);
  };
  XMLHttpRequest.prototype.send = function (body) {
    return sendOriginal.call(this, bodyForDemo(this.assessbridgeDemoUrl || '', body));
  };
})();
