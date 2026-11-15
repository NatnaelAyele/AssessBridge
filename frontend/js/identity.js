let capturedSelfie = null;
let captureInProgress = false;
let identityCheckInProgress = false;
let demoAuthenticated = false;
function configureIdentity() {
  byId('demo-authentication').dataset.state = demoAuthenticated ? 'complete' : 'current';
  byId('authenticate-demo').hidden = demoAuthenticated;
  byId('capture-selfie').disabled = !demoAuthenticated;
  byId('identity-auth-status').textContent = demoAuthenticated ? 'Authenticated' : 'Required';
  byId('identity-photo-step').dataset.state = demoAuthenticated ? 'current' : 'locked';
  byId('identity-photo-locked').hidden = demoAuthenticated;
  byId('identity-photo-content').hidden = !demoAuthenticated;
  byId('identity-photo-actions').hidden = !demoAuthenticated;
  byId('identity-photo-status').textContent = demoAuthenticated ? (capturedSelfie ? 'Photo captured' : 'Ready') : 'Not started';
  byId('identity-instructions').textContent = demoAuthenticated
    ? 'Authentication complete. Take your live session photo to continue.'
    : 'Authenticate with eSignet, then take a live photo.';
}
function resetIdentityCapture() {
  capturedSelfie = null;
  byId('identity-selfie-preview').removeAttribute('src');
  byId('identity-selfie-preview').hidden = true;
  byId('identity-camera-preview').hidden = false;
  byId('identity-preview-label').textContent = 'Live camera';
  byId('identity-photo-status').textContent = demoAuthenticated ? 'Ready' : 'Not started';
  byId('identity-photo-hint').textContent = 'Take a photo to continue.';
  byId('capture-selfie').textContent = 'Take photo';
  byId('verify-identity').disabled = true;
}
function identityMessage(text) { candidateFeedback.show('identity-error', text || ''); }
async function prepareIdentity() {
  const session = await examRequest('/api/candidate/exam/session');
  if (session.state !== 'not_started') return true;
  const response = await roomRequest('/api/candidate/identity');
  const state = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(candidateFeedback.serverError(response, state, 'Could not check identity authentication. Try again.'));
  if (state.status === 'passed') return true;
  demoAuthenticated = !!state.demo?.authenticated;
  configureIdentity();
  byId('demo-authentication-state').textContent = demoAuthenticated
    ? `Test identity: ${state.demo.name}` : 'Sign in with the test identity named in your invitation.';
  byId('identity-assessment').textContent = state.assessment_title || '';
  byId('identity-assessment').hidden = !state.assessment_title;
  candidateFeedback.show('demo-authentication-error', state.demo?.message || '');
  identityMessage('');
  showPanel('identity-panel');
  byId('identity-camera-preview').play().catch(() => {});
  return false;
}
window.assessbridgeIdentity = { prepare: prepareIdentity, reset: resetIdentityCapture, configure: configureIdentity };
byId('authenticate-demo').addEventListener('click', async () => {
  if (identityCheckInProgress || captureInProgress) return;
  const button = byId('authenticate-demo');
  identityCheckInProgress = true; button.disabled = true; button.textContent = 'Opening eSignet...';
  candidateFeedback.show('demo-authentication-error', '');
  try {
    const response = await roomRequest('/api/candidate/identity/esignet/start', {method:'POST'});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail=typeof data.detail==='string' ? data.detail : '';
      const message=response.status===503 ? 'Identity authentication is unavailable. Please try again shortly.'
        : response.status===403 && detail==='The exam access window is closed' ? 'The exam window is closed. Check the schedule in your invitation.'
        : response.status===403 && detail==='Agreement is required before identity authentication' ? 'Accept the exam consent before authenticating.'
        : response.status===409 && detail==='The exam has already started' ? 'Your attempt has already started. Return to your exam.'
        : candidateFeedback.serverError(response,data,'Could not start authentication. Try again.');
      throw new Error(message);
    }
    window.location.assign(data.authorization_url);
  } catch (error) {
    candidateFeedback.show('demo-authentication-error',error.message);
    identityCheckInProgress = false; button.disabled = false; button.textContent = 'Continue to eSignet';
  }
});
byId('capture-selfie').addEventListener('click', async () => {
  if (captureInProgress || identityCheckInProgress || !demoAuthenticated) return;
  const button = byId('capture-selfie');
  captureInProgress = true; button.disabled = true; resetIdentityCapture(); identityMessage('');
  try {
    if (!mediaReady()) throw new Error('Restore camera and microphone access before capturing your photo.');
    const video = byId('identity-camera-preview');
    if (!video.videoWidth || !video.videoHeight) throw new Error('Wait for the camera image, then try again.');
    const canvas = document.createElement('canvas');
    const scale = Math.min(1,2400/Math.max(video.videoWidth,video.videoHeight));
    canvas.width = Math.round(video.videoWidth*scale);canvas.height = Math.round(video.videoHeight*scale);
    canvas.getContext('2d').drawImage(video,0,0,canvas.width,canvas.height);
    const image = canvas.toDataURL('image/jpeg',0.94);
    capturedSelfie = image.split(',')[1];
    byId('identity-selfie-preview').src = image;byId('identity-selfie-preview').hidden = false;
    video.hidden = true;
    byId('identity-preview-label').textContent = 'Captured photo';
    byId('identity-photo-status').textContent = 'Photo captured';
    byId('identity-photo-hint').textContent = 'Check your photo before continuing.';
    button.textContent = 'Retake photo';
    byId('verify-identity').disabled = false;
  } catch (error) { identityMessage(error.message); }
  finally { captureInProgress = false; button.disabled = !demoAuthenticated; }
});
byId('verify-identity').addEventListener('click', async () => {
  if (identityCheckInProgress || captureInProgress || !capturedSelfie || !demoAuthenticated) return;
  const button = byId('verify-identity');
  identityCheckInProgress = true; button.disabled = true;button.textContent = 'Saving photo...';
  byId('capture-selfie').disabled = true;identityMessage('');
  try {
    const response = await roomRequest('/api/candidate/identity/esignet/complete', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({selfie:capturedSelfie})});
    const result = await response.json().catch(() => ({}));
    if (response.status===403 && typeof result.detail==='string' && /authenticate.*(identity|again)/i.test(result.detail)) {
      demoAuthenticated=false;resetIdentityCapture();configureIdentity();
      byId('demo-authentication-state').textContent='Sign in with the test identity named in your invitation.';
      candidateFeedback.show('demo-authentication-error','Your authentication session expired. Start again.');
      return;
    }
    if (!response.ok) throw new Error(typeof result.detail === 'string' && [403,422].includes(response.status)
      ? result.detail : candidateFeedback.serverError(response,result,'Could not save your session photo. Try again.'));
    if (result.status !== 'passed') throw new Error('Identity authentication is incomplete. Try again.');
    resetIdentityCapture();
    await window.examFlow.openAfterDevices();
  } catch (error) { identityMessage(error.message); }
  finally { identityCheckInProgress = false;byId('capture-selfie').disabled = !demoAuthenticated;button.textContent = 'Save photo & continue';button.disabled = !capturedSelfie; }
});
