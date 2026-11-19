// This instance is exclusively a fictional identity provider. Its server has
// no CAPTCHA validator; the upstream interceptor still requires a nonempty
// marker. Avoid third-party CAPTCHA/network dependencies in the local demo.
(() => {
  const owns = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
  let feedback, feedbackHost, lastView, uncertain = false, pending = false, fatal = false;
  let currentError = null;
  let identityNumber = null, restoreIdentityNumber = false;
  const step = () => document.getElementById('username_input') ? 'id'
    : document.getElementById('action_submit_otp') ? 'otp' : 'other';
  const fields = () => step() === 'id' ? [document.getElementById('username_input')]
    : [...document.querySelectorAll('.thunderid-otp-field__input')];
  const value = () => fields().map(input => input.value).join('').trim();
  function placeFeedback() {
    if (!feedbackHost) return;
    const button = document.getElementById('submit_uin') || document.getElementById('action_submit_otp');
    const target = button?.parentElement || document.querySelector('.thunderid-signin') || document.getElementById('root') || document.body;
    if (feedbackHost.parentElement !== target) {
      if (button) button.before(feedbackHost); else target.append(feedbackHost);
    }
    for (const input of fields()) {
      if (currentError?.field === step()) {
        if (input.getAttribute('aria-invalid') !== 'true') input.setAttribute('aria-invalid', 'true');
        const described = new Set((input.getAttribute('aria-describedby') || '').split(' ').filter(Boolean));
        described.add(feedbackHost.id);input.setAttribute('aria-describedby', [...described].join(' '));
      }
    }
  }
  function clearFeedback() {
    currentError = null;
    feedback?.clear('authentication');
    if (feedbackHost) feedbackHost.hidden = true;
    for (const input of fields()) {
      input.setAttribute('aria-invalid','false');
      const ids=(input.getAttribute('aria-describedby')||'').split(' ').filter(id=>id && id!==feedbackHost?.id);
      if(ids.length)input.setAttribute('aria-describedby',ids.join(' '));else input.removeAttribute('aria-describedby');
    }
  }
  function showFeedback(text, options = {}) {
    currentError = options.tone === 'loading' ? null : {field:options.field, value:value()};
    if (!feedback) return;
    placeFeedback();feedbackHost.hidden = false;
    feedback.show({id:'authentication',target:feedbackHost,text,tone:options.tone || 'error',
      action:options.restart ? {label:'Start again',onClick:()=>location.assign(window.assessbridgeIdentityProvider.returnUrl)} : undefined});
  }
  function validate(event) {
    const button=event.type==='submit' ? event.submitter : event.target.closest?.('button');
    if (!button || !['submit_uin','action_submit_otp','RESEND_OTP','back-button'].includes(button.id)) return;
    if (pending || fatal) {
      event.preventDefault();event.stopImmediatePropagation();return;
    }
    if (!['submit_uin','action_submit_otp'].includes(button.id) || uncertain) return;
    const text=value();const field=step();
    const message = field==='id' ? (!text ? 'Enter your ID number.' : !/^\d{12}$/.test(text) ? 'Enter a 12-digit ID number.' : '')
      : !/^\d{6}$/.test(text) ? 'Enter the 6-digit code.' : '';
    if (message) {
      event.preventDefault();event.stopImmediatePropagation();
      showFeedback(message,{field});fields()[0]?.focus({preventScroll:true});
    }
  }
  document.addEventListener('DOMContentLoaded', () => {
    document.body.classList.add('ab-provider-managed');
    const notice = document.createElement('aside');
    notice.setAttribute('aria-label', 'Demo authentication');
    notice.className='ab-provider-heading';
    notice.textContent = 'eSignet authentication (Fayda national Id simulation)';
    const hint=document.createElement('small');hint.textContent='Test OTP: 111111';notice.append(hint);
    document.body.prepend(notice);
    const notifications=document.createElement('div');document.body.append(notifications);
    feedback=window.AssessBridgeFeedback.create({notificationHost:notifications}).scope('identity-provider');
    feedbackHost=document.createElement('div');feedbackHost.id='ab-provider-feedback';feedbackHost.className='ab-provider-feedback';feedbackHost.hidden=true;
    placeFeedback();new MutationObserver(placeFeedback).observe(document.body,{childList:true,subtree:true});
    const back=document.createElement('a');back.className='ab-provider-return';back.href=window.assessbridgeIdentityProvider.returnUrl;back.textContent='Return to identity check';document.body.append(back);
    document.addEventListener('click',validate,true);document.addEventListener('submit',validate,true);
    document.addEventListener('keydown',event=>{
      if(event.key!=='Enter' || !event.target.matches('input'))return;
      const button=document.getElementById(step()==='id' ? 'submit_uin' : 'action_submit_otp');
      validate({type:'submit',submitter:button,preventDefault:()=>event.preventDefault(),stopImmediatePropagation:()=>event.stopImmediatePropagation()});
    },true);
    const clearChangedField=()=>{
      if(currentError?.field===step() && value()!==currentError.value)clearFeedback();
    };
    document.addEventListener('input',clearChangedField);
    document.addEventListener('paste',()=>setTimeout(clearChangedField,0));
  });
  function bodyForDemo(url, body) {
    if (new URL(url, location.href).pathname.replace('/v1/esignet', '') !== '/flow/execute' || typeof body !== 'string') return body;
    try {
      const data = JSON.parse(body);
      if (data.inputs && (Object.keys(data.inputs).length || data.action)) {
        // The bundled UI retains earlier form values. Re-sending username at
        // the OTP step overwrites the provider's normalized ID in its context.
        // Send only the fields accepted by the selected action.
        const accepted = owns(data.inputs, 'consent_decisions') ? ['consent_decisions']
          : owns(data.inputs, 'otp') && String(data.inputs.otp).length ? ['otp']
          : owns(data.inputs, 'username') ? ['username'] : null;
        if (accepted) data.inputs = Object.fromEntries(accepted.filter(key => owns(data.inputs, key)).map(key => [key, data.inputs[key]]));
        if (data.action==='submit_uin' && typeof data.inputs.username==='string') {
          identityNumber=data.inputs.username;restoreIdentityNumber=false;
        }
        if (['RESEND_OTP', 'BACK_BUTTON'].includes(data.action)) data.inputs = {};
        // The provider clears both username and OTP after failed credentials.
        // Restore the ID from the accepted ID step only when retrying, rather
        // than re-sending the upstream form's stale username on every action.
        if (restoreIdentityNumber && identityNumber && ['action_submit_otp','RESEND_OTP'].includes(data.action))data.inputs.username=identityNumber;
        if (data.action==='BACK_BUTTON')restoreIdentityNumber=false;
        data.inputs.captcha_token = 'assessbridge-fictional-identity-demo';
        return JSON.stringify(data);
      }
    } catch { /* Leave non-JSON requests unchanged. */ }
    return body;
  }
  const fetchOriginal = window.fetch;
  const friendlyResponse = (data) => new Response(JSON.stringify(data),{status:200,headers:{'Content-Type':'application/json'}});
  function failedView(message, request, restart = false) {
    fatal=restart;
    showFeedback(message,{restart});
    // Preserve the prompt instead of letting the upstream UI discard the form.
    // Never invent a success result, assertion, or replacement challenge token.
    return friendlyResponse({...lastView,executionId:lastView?.executionId || request.executionId,
      flowStatus:lastView ? 'INCOMPLETE' : 'ERROR',
      error:{code:'assessbridge_feedback',message:{key:'assessbridge_feedback',defaultValue:message}}});
  }
  async function execute(input, options, request) {
    pending=true;
    showFeedback(uncertain ? 'Checking your connection...' : request.action==='submit_uin' ? 'Checking ID number...'
      : request.action==='action_submit_otp' ? 'Verifying code...' : 'Please wait...',{tone:'loading'});
    const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),15000);
    try {
      // A lost response does not tell us whether the action was processed.
      // Read the current prompt on retry; do not replay the previous action.
      const body=uncertain ? JSON.stringify({executionId:request.executionId,challengeToken:lastView?.challengeToken,verbose:true}) : options.body;
      const response=await fetchOriginal(input,{...options,body,signal:controller.signal});
      const data=await response.clone().json().catch(()=>null);
      if (!response.ok || !data) {
        if(response.status>=500 || !data){uncertain=!!lastView;return failedView('Could not connect. Check your connection and try again.',request,!lastView);}
        return failedView('Your authentication session expired. Start again.',request,true);
      }
      uncertain=false;
      if(data.flowStatus==='ERROR'){
        if(data.redirectUrl){
          const redirect=new URL(data.redirectUrl,location.href),app=new URL(window.assessbridgeIdentityProvider.returnUrl);
          if(redirect.origin===app.origin && redirect.pathname==='/identity/esignet/callback'){
            clearFeedback();return response;
          }
        }
        const reason=String(data.error?.code||'')+' '+String(data.error?.message?.key||'');
        return failedView(/attempt|limit/.test(reason) ? 'Too many attempts. Start again.'
          : /expir|challenge|session|execution/.test(reason) ? 'Your authentication session expired. Start again.'
          : 'Authentication could not continue. Start again.',request,true);
      }
      if(data.type==='VIEW' && data.data?.meta?.components)lastView=data;
      if(data.error){
        const code=String(data.error.code||'').toLowerCase();
        const key=String(data.error.message?.key||'').toLowerCase();
        const expired=/expir|invalid.*(execution|challenge|session)|attempt.*exceed|limit.*exceed/.test(code+' '+key);
        if(request.action==='action_submit_otp' && (code==='fet-1005' || /invalid_credentials|invalid_otp/.test(key+' '+code)))restoreIdentityNumber=true;
        const message=expired ? 'Your authentication session expired or its attempt limit was reached. Start again.'
          : code==='invalid_individual_id' ? 'This test identity was not found. Check the ID number.'
          : request.action==='action_submit_otp' && (code==='fet-1005' || /invalid_credentials|invalid_otp/.test(key+' '+code)) ? 'Incorrect code. Try again.'
          : request.action==='RESEND_OTP' ? 'Could not prepare another code. Wait a moment and try again.'
          : 'Authentication could not be completed. Try again.';
        fatal=expired;
        showFeedback(message,{field:expired ? undefined : request.action==='submit_uin' ? 'id' : request.action==='action_submit_otp' ? 'otp' : undefined,restart:expired});
        data.error={code:data.error.code,message:{key:'assessbridge_feedback',defaultValue:message}};
        return friendlyResponse(data);
      }
      clearFeedback();return response;
    } catch {
      uncertain=!!lastView;
      return failedView('Could not connect. Check your connection and try again.',request,!lastView);
    } finally {clearTimeout(timer);pending=false;}
  }
  window.fetch = (input, options) => {
    const url=typeof input==='string' ? input : input.url;
    const amended=options?.body ? {...options,body:bodyForDemo(url,options.body)} : options;
    if(new URL(url,location.href).pathname.replace('/v1/esignet','')==='/flow/execute' && typeof amended?.body==='string'){
      try{return execute(input,amended,JSON.parse(amended.body));}catch{/* Use the original request for non-JSON bodies. */}
    }
    if(new URL(url,location.href).pathname.replace('/v1/esignet','')==='/oauth2/auth/callback'){
      return (async()=>{
        const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),15000);
        try{
          const result=await fetchOriginal(input,{...amended,signal:controller.signal});
          if(!result.ok)throw new Error('Authentication return failed');
          return result;
        }catch{
          fatal=true;
          showFeedback('Could not finish authentication. Return to the identity check and start again.',{restart:true});
          return new Response(JSON.stringify({error:'authentication_failed'}),{status:503,headers:{'Content-Type':'application/json'}});
        }finally{clearTimeout(timer);}
      })();
    }
    return fetchOriginal(input,amended);
  };
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
