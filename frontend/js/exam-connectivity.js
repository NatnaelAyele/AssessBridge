window.examClientId = crypto.randomUUID();
window.examRecovery = (() => {
  let offlineAt = null, blockedAt = null, lastSuccess = performance.now(), busy = false;
  let localStopped = false, connected = true, storageReady = false, draftWrites = Promise.resolve();
  let storageBlocked = false, latestDraft = null;
  let lossRevision = 0;
  function label(text, error = false) {
    const target = document.getElementById("connection-status");
    target.textContent = text; target.dataset.state = error ? "error" : "saved";
  }
  async function prepare() {
    const state = await examRequest("/api/candidate/exam/prepare", "POST");
    await window.examStorage.init(state.storage_scope);
    storageReady = true;
    return state;
  }
  function lost() {
    lossRevision += 1;
    connected = false;
    if (offlineAt === null) offlineAt = performance.now();
    label("Connection lost · Reconnecting", true);
    window.examFlow?.deviceStatusChanged();
  }
  async function readiness() {
    const target = document.getElementById("network-check-status");
    target.textContent = "Checking connection and local storage…";
    try {
      await window.examStorage.check();
      // A timed round trip to our server and an acknowledged upload, not Wi-Fi state.
      await examRequest("/api/candidate/exam/network-check", "POST");
      const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 10000);
      try {
        const response = await fetch("/api/candidate/exam/network-check", { method: "PUT", credentials: "same-origin", signal: controller.signal,
          headers: { ...await window.assessbridgeSeb.headers(), "X-Exam-Client": window.examClientId, "Content-Type": "application/octet-stream" }, body: new Uint8Array(131072) });
        if (!response.ok) throw new Error((await response.json()).detail || "Connection check failed");
      } finally { clearTimeout(timeout); }
      target.textContent = "Connection and storage ready";
      return true;
    } catch (error) { target.textContent = "Connection or storage check failed"; throw error; }
  }
  function saveDraft(index, answer) {
    const draft = { question_index: index, answer, version: crypto.randomUUID() };
    latestDraft = draft;
    draftWrites = draftWrites.then(() => window.examStorage.put("draft", draft)).catch(() => {
      storageBlocked = true;
      label("Local storage is unavailable. Free space to continue.", true);
      window.examFlow?.deviceStatusChanged();
    });
    return draftWrites;
  }
  function clearDraft(version) {
    // Keep acknowledgement and new keystrokes in the same ordered queue.
    draftWrites = draftWrites.then(async () => {
      const current = await window.examStorage.get("draft");
      if (!version || current?.version === version) await window.examStorage.remove("draft");
    });
    return draftWrites;
  }
  async function restoreDraft(state) {
    const draft = await window.examStorage.get("draft");
    if (!draft || state.state !== "active") return state;
    if (draft.question_index !== state.current_index) {
      await clearDraft(); // Navigation only follows an acknowledged save.
      return state;
    }
    const saved = await examRequest("/api/candidate/exam/answer", "POST", draft);
    await clearDraft(draft.version);
    return saved;
  }
  async function heartbeat() {
    if (busy || attemptState?.state !== "active") return;
    busy = true;
    const revision = lossRevision;
    try {
      if (storageBlocked && !localStopped) {
        draftWrites = draftWrites.then(async () => {
          try {
            await window.examStorage.check();
            if (latestDraft) await window.examStorage.put("draft", latestDraft);
            storageBlocked = false;
          } catch { /* Keep answering paused until storage is writable again. */ }
        });
        await draftWrites;
      }
      const ready = mediaReady() && window.assessbridgeRecording.healthy() && !localStopped && !storageBlocked;
      const state = await examRequest("/api/candidate/exam/heartbeat", "POST", { ready,
        offline_seconds: offlineAt === null ? 0 : Math.max(1, Math.ceil((performance.now() - offlineAt) / 1000)) });
      // A response already in flight must not erase a newer offline event.
      const restored = revision === lossRevision;
      connected = restored;
      if (restored) offlineAt = null;
      lastSuccess = performance.now();
      if (state.state !== "active") { showSubmitted(state); return; }
      attemptState = { ...attemptState, ...state };
      deadlineTime = Date.parse(state.deadline_at); serverAnchor = Date.parse(state.server_now); clientAnchor = performance.now();
      if (state.resume_required) label("Complete recovery checks to continue", true);
      else if (state.recovery_started_at) label("Restore recording to continue", true);
      else if (restored) {
        label("Connected");
        if (dirtyAnswer && !byId("exam-panel").hidden) await ensureSaved();
        if (ready) await window.assessbridgeRecording.adapt();
      }
      window.examFlow?.deviceStatusChanged();
    } catch (error) {
      if (error.status === 428) { localStopped = true; label("This exam was reopened. Reload to complete recovery checks.", true); }
      else lost();
    } finally { busy = false; }
  }
  function tick() {
    if (attemptState?.state !== "active") return;
    const elapsed = performance.now();
    const now = serverAnchor + elapsed - clientAnchor;
    const media = mediaReady() && window.assessbridgeRecording.healthy() && !storageBlocked;
    if (!media && blockedAt === null) blockedAt = elapsed;
    if (media) blockedAt = null;
    const starts = [offlineAt, blockedAt].filter(x => x !== null);
    let used = starts.length ? (elapsed - Math.min(...starts)) / 1000 : 0;
    if (attemptState.recovery_started_at) used = Math.max(used, (now - Date.parse(attemptState.recovery_started_at)) / 1000);
    const remaining = Math.max(0, Math.min(180, 600 - (attemptState.recovery_seconds || 0)) - used);
    if (starts.length || attemptState.recovery_started_at) label(`${connected ? "Restore recording" : "Reconnecting"} · ${Math.ceil(remaining)}s recovery time remaining`, true);
    if (remaining <= 0 || now >= deadlineTime) {
      if (!localStopped) {
        localStopped = true;
        window.assessbridgeRecording.pause().catch(() => {});
      }
      label(now >= deadlineTime ? "Time is up · Reconnecting to confirm submission" : "Recovery time exceeded · Reconnecting to save the interruption", true);
    }
    if (elapsed - lastSuccess >= 10000) heartbeat();
    window.examFlow?.deviceStatusChanged();
  }
  async function liveCheck() {
    const video = byId("recovery-preview");
    if (!mediaReady() || !video.videoWidth) throw new Error("Connect your camera and wait for the preview.");
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth; canvas.height = video.videoHeight;
    canvas.getContext("2d").drawImage(video, 0, 0);
    const result = await examRequest("/api/candidate/exam/recovery/identity", "POST", { selfie: canvas.toDataURL("image/jpeg", .9).split(",")[1] });
    if (!result.passed) throw new Error(result.message);
  }
  window.addEventListener('offline', () => {
    // Browser network state is a hint. Only a failed server request starts
    // recovery; localhost can still be reachable without external internet.
    connected = false; lossRevision += 1;
    label('Checking connection', true);
    window.examFlow?.deviceStatusChanged();
    heartbeat();
  });
  window.addEventListener('online', () => { heartbeat(); });
  return { prepare, readiness, heartbeat, tick, lost, liveCheck, saveDraft, clearDraft, restoreDraft,
    settled: () => draftWrites, online: () => connected, blocked: () => localStopped || storageBlocked,
    storageReady: () => storageReady };
})();
