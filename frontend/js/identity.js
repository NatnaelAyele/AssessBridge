let capturedCard = null;
let capturedSelfie = null;
let captureInProgress = false;
let identityCheckInProgress = false;
let demoIdentity = false;
let demoAuthenticated = false;
function captureReady() { return !!capturedSelfie && (demoIdentity ? demoAuthenticated : !!capturedCard && !window.assessbridgeCardCrop.isOpen()); }
function configureIdentity(policy) {
  demoIdentity = policy === 'Fayda demo (eSignet)';
  byId('demo-authentication').hidden = !demoIdentity;
  byId('verify-identity').textContent = demoIdentity ? 'Save session photo' : 'Check identity';
  byId('identity-card-capture').hidden = demoIdentity;
  byId('identity-selfie-label').textContent = demoIdentity ? 'Session photo' : '2. Your face';
  byId('identity-method-note').textContent = demoIdentity
    ? 'Demo authentication does not verify a real Fayda identity. Your camera photo is used for session monitoring.'
    : "This compares the printed name and portrait with your invitation and camera image. It does not check the card against the issuer's records.";
  if (demoIdentity) {
    byId('consent-identity-description').innerHTML = 'Before the exam, you will authenticate a fictional test identity and capture a camera photo for session monitoring. No card or portrait comparison is performed in this demo mode.<span id="consent-id-type" hidden></span>';
    byId('identity-title').textContent = demoAuthenticated ? 'Capture your session photo' : 'Authenticate your test identity';
    byId('identity-instructions').textContent = demoAuthenticated ? 'Look at the camera, capture your face, then save the session photo.' : 'Authenticate the test identity named in your invitation. Then capture a clear photo of your face for this session.';
    byId('authenticate-demo').hidden = demoAuthenticated;
    byId('capture-selfie').disabled = !demoAuthenticated;
    return;
  }
  if (byId('consent-id-type').hidden) byId('consent-identity-description').innerHTML = 'Before the exam, we will capture your <span id="consent-id-type">ID card</span> and a camera image to compare the card name and portrait with your invitation and face.';
  const other = policy === "Other ID cards";
  const label = other ? "photo ID card" : "Fayda ID card";
  byId("consent-id-type").textContent = label;
  byId("identity-title").textContent = `Match your ${label} to your face`;
  byId("identity-card-label").textContent = other ? "1. Photo ID card" : "1. Fayda ID card";
  byId("identity-card-preview").alt = `Captured ${label}`;
  byId("identity-instructions").textContent = `Hold your ${label} close enough to fill most of the camera view, with every edge visible. Keep the printed name and portrait clear and avoid glare. The name must use Latin letters and match your invitation. Then remove the card and capture your face.`;
}

function showIdentityResult(result) {
  byId("identity-assessment").textContent = result.assessment_title ? `Assessment: ${result.assessment_title}` : "";
  // Failure guidance is shown once beside the capture controls.
  candidateFeedback.show('identity-check-result', result.status === 'passed' ? (demoIdentity ? 'Demo identity authenticated.' : 'Identity check passed.') : '', 'success');

}

function resetIdentityCapture() {
  capturedCard = null;
  capturedSelfie = null;
  window.assessbridgeCardCrop.reset();
  for (const kind of ["card", "selfie"]) {
    byId(`identity-${kind}-preview`).removeAttribute("src");
    byId(`identity-${kind}-preview`).hidden = true;
  }
  byId("verify-identity").disabled = true;
}

function identityMessage(message) {
  candidateFeedback.show("identity-error", message || "");
}

function cardSharpness(frame) {
  const canvas = document.createElement("canvas");
  // Keep text-sized edges. A 240px thumbnail mostly measures the portrait
  // and room edges, not whether the printed name is in focus.
  canvas.width = Math.min(720, frame.width);
  canvas.height = Math.round(canvas.width * frame.height / frame.width);
  const context = canvas.getContext("2d", { willReadFrequently: true });
  context.drawImage(frame, frame.width * 0.3, frame.height * 0.15,
    frame.width * 0.6, frame.height * 0.5, 0, 0, canvas.width, canvas.height);
  const { width, height } = canvas;
  const pixels = context.getImageData(0, 0, width, height).data;
  const gray = new Float32Array(width * height);
  for (let index = 0; index < gray.length; index += 1) {
    const offset = index * 4;
    gray[index] = pixels[offset] * 0.299 + pixels[offset + 1] * 0.587 + pixels[offset + 2] * 0.114;
  }
  let score = 0;
  for (let y = 1; y < height - 1; y += 2) {
    for (let x = 1; x < width - 1; x += 2) {
      const index = y * width + x;
      const edge = 4 * gray[index] - gray[index - 1] - gray[index + 1] - gray[index - width] - gray[index + width];
      score += edge * edge;
    }
  }
  return score;
}

async function identityCapture(kind) {
  if (captureInProgress || identityCheckInProgress) return;
  if (!mediaReady()) throw new Error("Camera access stopped. Return to the device check and reconnect it.");
  const video = byId("identity-camera-preview");
  if (!video.videoWidth || !video.videoHeight) throw new Error("Wait for the camera image, then try again.");
  captureInProgress = true;
  for (const buttonKind of ["card", "selfie"]) byId(`capture-${buttonKind}`).disabled = true;
  if (kind === "card") capturedCard = null;
  else capturedSelfie = null;
  byId(`identity-${kind}-preview`).hidden = true;
  byId("identity-check-result").hidden = true;
  byId("verify-identity").disabled = true;
  const button = byId(`capture-${kind}`);
  const previousLabel = button.textContent;
  button.textContent = "Capturing…";
  try {
    const stream = window.assessbridgeMedia.stream();
    const scale = Math.min(1, 2400 / Math.max(video.videoWidth, video.videoHeight));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(video.videoWidth * scale);
    canvas.height = Math.round(video.videoHeight * scale);
    const context = canvas.getContext("2d");
    let best = null;
    let bestScore = -1;
    const frames = kind === "card" ? 6 : 1;
    for (let index = 0; index < frames; index += 1) {
      if (!mediaReady() || window.assessbridgeMedia.stream() !== stream) {
        throw new Error("Camera access stopped. Return to the device check and reconnect it.");
      }
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      const score = kind === "card" ? cardSharpness(canvas) : 0;
      if (score > bestScore) {
        bestScore = score;
        best = canvas.toDataURL("image/jpeg", 0.94);
      }
      if (index < frames - 1) await new Promise((resolve) => setTimeout(resolve, 140));
    }
    const image = byId(`identity-${kind}-preview`);
    image.src = best;
    image.hidden = false;
    if (kind === "card") {
      capturedCard = best.split(",")[1];
      window.assessbridgeCardCrop.setSource(best);
    }
    else capturedSelfie = best.split(",")[1];
    byId("verify-identity").disabled = !captureReady();
    identityMessage("");
  } finally {
    button.textContent = previousLabel;
    for (const buttonKind of ["card", "selfie"]) byId(`capture-${buttonKind}`).disabled = false;
    byId("verify-identity").disabled = !captureReady();
    captureInProgress = false;
  }
}

for (const kind of ["card", "selfie"]) {
  byId(`capture-${kind}`).addEventListener("click", async () => {
    try { await identityCapture(kind); }
    catch (error) { identityMessage(error.message); }
  });
}

async function prepareIdentity() {
  const session = await examRequest("/api/candidate/exam/session");
  if (session.state !== "not_started") return true;
  const response = await roomRequest("/api/candidate/identity");
  const state = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(candidateFeedback.serverError(response, state, "Could not check your identity status. Try again."));
  demoAuthenticated = state.identity_policy === 'Fayda demo (eSignet)' && !!state.demo?.authenticated;
  configureIdentity(state.identity_policy);
  if (demoIdentity) {
    byId('demo-authentication-state').textContent = demoAuthenticated ? `Authenticated test identity: ${state.demo.name}` : 'Use the test identity named in your invitation.';
    byId('authenticate-demo').textContent = 'Authenticate test identity';
    byId('capture-selfie').disabled = !demoAuthenticated;
  }
  if (state.status === "passed") return true;
  showIdentityResult(state);
  candidateFeedback.show('demo-authentication-error', demoIdentity ? state.demo?.message || '' : '');
  identityMessage(state.message || '');
  showPanel("identity-panel");
  byId("identity-camera-preview").play().catch(() => {});
  return false;
}

window.assessbridgeIdentity = { prepare: prepareIdentity, reset: resetIdentityCapture, configure: configureIdentity };
window.addEventListener("identity-crop-change", () => {
  byId("verify-identity").disabled = identityCheckInProgress || captureInProgress || !captureReady();
});

byId('authenticate-demo').addEventListener('click', async () => {
  const button = byId('authenticate-demo');
  if (identityCheckInProgress || captureInProgress) return;
  identityCheckInProgress = true;
  const label = button.textContent;
  button.disabled = true;
  button.textContent = 'Opening authentication...';
  byId('verify-identity').disabled = true;
  candidateFeedback.show('demo-authentication-error', '');
  try {
    const response = await roomRequest('/api/candidate/identity/esignet/start', {method:'POST'});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(candidateFeedback.serverError(response,data,'Could not start demo authentication. Try again.'));
    window.location.assign(data.authorization_url);
  } catch (error) {
    candidateFeedback.show('demo-authentication-error', error.message);
    identityCheckInProgress = false;
    button.disabled = false;
    button.textContent = label;
    byId('verify-identity').disabled = !captureReady();
  }
});

byId("verify-identity").addEventListener("click", async () => {
  if (identityCheckInProgress || !captureReady()) return;
  identityCheckInProgress = true;
  window.assessbridgeCardCrop.setBusy(true);
  for (const kind of ["card", "selfie"]) byId(`capture-${kind}`).disabled = true;
  const button = byId("verify-identity");
  button.disabled = true;
  button.textContent = "Checking identity…";
  identityMessage("");
  try {
    const response = await roomRequest(demoIdentity ? '/api/candidate/identity/esignet/complete' : "/api/candidate/identity/verify", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(demoIdentity ? {selfie:capturedSelfie} : { card: capturedCard, selfie: capturedSelfie,
        ...(window.assessbridgeCardCrop.corners() ? { card_corners: window.assessbridgeCardCrop.corners() } : {}) }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      const photoGuidance = demoIdentity && typeof result.detail === 'string'
        && (response.status === 422 || (response.status === 403 && result.detail.startsWith('Authenticate')));
      throw new Error(photoGuidance ? result.detail : candidateFeedback.serverError(response, result, "Identity check could not run. Try again."));
    }
    showIdentityResult(result);
    if (result.status !== "passed") {
      identityMessage(result.message || "The images could not be matched. Retake both images and try again.");
      if (result.stage === "card_capture") window.assessbridgeCardCrop.open();
      return;
    }
    resetIdentityCapture();
    await window.examFlow.openAfterDevices();
  } catch (error) {
    identityMessage(error.message);
  } finally {
    identityCheckInProgress = false;
    window.assessbridgeCardCrop.setBusy(false);
    for (const kind of ["card", "selfie"]) byId(`capture-${kind}`).disabled = false;
    button.textContent = demoIdentity ? 'Save session photo' : 'Check identity';
    button.disabled = !captureReady();
  }
});
