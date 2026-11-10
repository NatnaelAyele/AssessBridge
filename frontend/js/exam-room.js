const byId = (id) => document.getElementById(id);
const panels = ["loading-panel", "consent-panel", "device-panel", "identity-panel", "recovery-panel", "instructions-panel", "exam-panel", "submitted-panel", "disqualified-panel", "room-error"];
let mediaStream = null;
let audioContext = null;
let meterFrame = null;
let pageClosing = false;
let availabilityRequest = 0;
window.assessbridgeMedia = { stream: () => mediaStream };

async function roomRequest(path, options = {}) {
  const headers = { ...(options.headers || {}), ...await window.assessbridgeSeb.headers() };
  return fetch(path, { credentials: "same-origin", ...options, headers });
}

function showPanel(id) {
  for (const panel of panels) byId(panel).hidden = panel !== id;
}

function setDeviceStatus(kind, text, state) {
  byId(`${kind}-status`).textContent = text;
  byId(`${kind}-indicator`).dataset.state = state;
}

function setDeviceError(message) {
  byId("device-error").textContent = message;
  byId("device-error").hidden = !message;
}

function mediaReady() {
  if (!mediaStream) return false;
  return [mediaStream.getVideoTracks()[0], mediaStream.getAudioTracks()[0]]
    .every((track) => track && track.readyState === "live" && !track.muted);
}

function stopMedia() {
  window.assessbridgeRecording?.markInterrupted?.();
  window.assessbridgeIdentity?.reset?.();
  if (meterFrame !== null) cancelAnimationFrame(meterFrame);
  meterFrame = null;
  if (mediaStream) mediaStream.getTracks().forEach((track) => track.stop());
  mediaStream = null;
  if (audioContext) audioContext.close().catch(() => {});
  audioContext = null;
  byId("camera-preview").srcObject = null;
  byId("exam-preview").srcObject = null;
  byId("identity-camera-preview").srcObject = null;
  byId("mic-check").hidden = true;
  byId("mic-level").style.width = "0%";
  byId("preview-placeholder").hidden = false;
  byId("continue-devices").disabled = true;
}

function showError(message) {
  stopMedia();
  byId("room-error-message").textContent = message;
  showPanel("room-error");
}

function syncMediaState() {
  const camera = mediaStream?.getVideoTracks()[0];
  const microphone = mediaStream?.getAudioTracks()[0];
  const connected = (track) => track && track.readyState === "live" && !track.muted;
  setDeviceStatus("camera", connected(camera) ? "Connected" : "Not connected", connected(camera) ? "ready" : "error");
  setDeviceStatus("microphone", connected(microphone) ? "Connected" : "Not connected", connected(microphone) ? "ready" : "error");
  byId("continue-devices").disabled = !mediaReady();
  if (!mediaReady()) window.assessbridgeRecording?.markInterrupted?.();
  if (!byId("exam-panel").hidden) window.examFlow.deviceStatusChanged();
  if (!mediaReady() && (!byId("instructions-panel").hidden || !byId("identity-panel").hidden)) {
    if (!byId("identity-panel").hidden) window.assessbridgeIdentity?.reset?.();
    showPanel("device-panel");
    setDeviceError("A device stopped working. Restore access and try the check again.");
  }
}

async function checkAvailability() {
  const request = ++availabilityRequest;
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    setDeviceStatus("camera", "Unavailable in this browser or connection", "error");
    setDeviceStatus("microphone", "Unavailable in this browser or connection", "error");
    byId("grant-devices").disabled = true;
    setDeviceError("Camera and microphone access needs a supported browser on HTTPS or local 127.0.0.1.");
    return;
  }
  if (mediaStream) return;
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    if (mediaStream || request !== availabilityRequest) return;
    const cameraFound = devices.some((device) => device.kind === "videoinput");
    const microphoneFound = devices.some((device) => device.kind === "audioinput");
    setDeviceStatus("camera", cameraFound ? "Detected; permission needed" : "Cannot confirm before permission", "pending");
    setDeviceStatus("microphone", microphoneFound ? "Detected; permission needed" : "Cannot confirm before permission", "pending");
  } catch {
    if (request !== availabilityRequest) return;
    setDeviceStatus("camera", "Permission needed to check", "pending");
    setDeviceStatus("microphone", "Permission needed to check", "pending");
  }
}

function beginMicrophoneMeter(stream) {
  const AudioContextType = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextType) return;
  try {
    audioContext = new AudioContextType();
    const source = audioContext.createMediaStreamSource(stream);
    const analyser = audioContext.createAnalyser();
    analyser.fftSize = 1024;
    source.connect(analyser);
    audioContext.resume().catch(() => {});
    const samples = new Uint8Array(analyser.fftSize);
    byId("mic-check").hidden = false;
    const draw = () => {
      if (!mediaStream || !audioContext) return;
      analyser.getByteTimeDomainData(samples);
      let sum = 0;
      for (const sample of samples) sum += ((sample - 128) / 128) ** 2;
      byId("mic-level").style.width = `${Math.min(100, Math.round(Math.sqrt(sum / samples.length) * 500))}%`;
      meterFrame = requestAnimationFrame(draw);
    };
    draw();
  } catch {
    if (audioContext) audioContext.close().catch(() => {});
    audioContext = null;
  }
}

function deviceFailureMessage(error) {
  if (error.name === "NotAllowedError" || error.name === "SecurityError") return "Camera or microphone access was blocked. Allow both in your browser and system settings, then try again.";
  if (error.name === "NotFoundError") return "A camera or microphone was not found. Connect both devices, then try again.";
  if (error.name === "NotReadableError" || error.name === "AbortError") return "A device could not start. Close other apps using it, reconnect it if needed, then try again.";
  if (error.name === "OverconstrainedError") return "The selected device cannot meet the requested settings. Try another camera or microphone.";
  return "Could not access both devices. Check browser permissions and try again.";
}

byId("grant-devices").addEventListener("click", async () => {
  availabilityRequest += 1;
  const button = byId("grant-devices");
  button.disabled = true;
  button.textContent = "Waiting for browser permission…";
  setDeviceError("");
  stopMedia();
  setDeviceStatus("camera", "Waiting for permission", "pending");
  setDeviceStatus("microphone", "Waiting for permission", "pending");
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 1920 }, height: { ideal: 1080 } }, audio: true,
    });
    if (pageClosing) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    mediaStream = stream;
    for (const track of stream.getTracks()) {
      track.addEventListener("ended", syncMediaState);
      track.addEventListener("mute", syncMediaState);
      track.addEventListener("unmute", syncMediaState);
    }
    byId("camera-preview").srcObject = stream;
    byId("exam-preview").srcObject = stream;
    byId("identity-camera-preview").srcObject = stream;
    byId("preview-placeholder").hidden = true;
    beginMicrophoneMeter(stream);
    syncMediaState();
    if (!mediaReady()) setDeviceError("Both devices must be connected before you can continue.");
  } catch (error) {
    setDeviceStatus("camera", "Not connected", "error");
    setDeviceStatus("microphone", "Not connected", "error");
    setDeviceError(deviceFailureMessage(error));
    byId("continue-devices").disabled = true;
  } finally {
    button.disabled = false;
    button.textContent = mediaReady() ? "Check devices again" : "Grant camera and microphone access";
  }
});

byId("continue-devices").addEventListener("click", async () => {
  if (!mediaReady()) {
    syncMediaState();
    return;
  }
  const button = byId("continue-devices");
  button.disabled = true;
  setDeviceError("");
  try {
    const response = await roomRequest("/api/candidate/exam-room");
    if (!response.ok) throw new Error("Your exam access has ended. Return to the candidate workspace and enter your exam credentials again.");
    const exam = await response.json();
    if (!exam.consented) throw new Error("Agreement is required before continuing.");
    if (!mediaReady()) throw new Error("A device stopped working. Check both devices again.");
    await window.examRecovery.readiness();
    if (await window.assessbridgeIdentity.prepare()) await window.examFlow.openAfterDevices();
  } catch (error) {
    setDeviceError(error.message);
  } finally {
    button.disabled = !mediaReady();
  }
});

byId("agree").addEventListener("change", () => {
  byId("continue-button").disabled = !byId("agree").checked;
  byId("consent-error").hidden = true;
});

byId("consent-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!byId("agree").checked) return;
  const button = byId("continue-button");
  button.disabled = true;
  button.textContent = "Saving agreement…";
  try {
    const response = await roomRequest("/api/candidate/exam-room/consent", {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agreed: true }),
    });
    if (!response.ok) {
      if (response.status === 403 || response.status === 401) throw new Error("Your exam access has ended. Return to the candidate workspace and enter your exam credentials again.");
      throw new Error("Could not save your agreement. Please try again.");
    }
    const submitted = await window.examFlow.bootstrap();
    if (!submitted) {
      showPanel("device-panel");
      checkAvailability();
    }
  } catch (error) {
    byId("consent-error").textContent = error.message;
    byId("consent-error").hidden = false;
  } finally {
    button.disabled = !byId("agree").checked;
    button.innerHTML = 'Agree and continue <span aria-hidden="true">→</span>';
  }
});

roomRequest("/api/candidate/exam-room")
  .then(async (response) => {
    if (!response.ok) {
      const detail = (await response.json().catch(() => ({}))).detail;
      throw new Error(typeof detail === "string" ? detail : "Could not load your assessment. Please try again.");
    }
    return response.json();
  })
  .then(async (exam) => {
    byId("exam-title").textContent = exam.title;
    window.assessbridgeIdentity.configure(exam.identity_policy);
    if (!exam.consented) {
      showPanel("consent-panel");
      return;
    }
    const submitted = await window.examFlow.bootstrap();
    if (!submitted) {
      showPanel("device-panel");
      checkAvailability();
    }
  })
  .catch((error) => showError(error.message));

navigator.mediaDevices?.addEventListener?.("devicechange", () => {
  if (!mediaStream && !byId("device-panel").hidden) checkAvailability();
  else if (mediaStream) syncMediaState();
});
window.addEventListener("pagehide", () => { pageClosing = true; stopMedia(); });
window.addEventListener("pageshow", (event) => { if (event.persisted) window.location.reload(); });
