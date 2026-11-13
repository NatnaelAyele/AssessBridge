const examRecording = (() => {
  let recorder = null, stream = null, part = null, writing = Promise.resolve();
  let active = false, failed = false, pumping = null, finalizing = null, timer = null;
  let captureStopped = false, bitrate = 1200000, desiredBitrate = 1200000, adapting = false;
  let uploadProgress = null;
  function finalProgress(message, tone = 'saving') {
    if (!uploadProgress) return;
    const bar = document.getElementById('recording-upload-progress');
    const detail = document.getElementById('recording-upload-detail');
    bar.hidden = false;
    if (uploadProgress.ready) {
      const total = Math.max(uploadProgress.total, uploadProgress.ack);
      bar.value = total ? Math.min(99, Math.floor(100*uploadProgress.ack/total)) : 99;
      const mb = bytes => (bytes/1048576).toFixed(2);
      detail.textContent = `Final upload: ${mb(uploadProgress.ack)} / ${mb(total)} MB. Keep SEB open.`;
    } else { bar.removeAttribute('value'); detail.textContent = 'Preparing the final recording. Keep SEB open.'; }
    if (message) {
      candidateFeedback.show('recording-final-status', message, tone === 'error' ? 'error' : 'loading', { announce: false });
    }
  }
  const store = () => window.examStorage;
  const partName = id => `part:${String(id).padStart(12, "0")}`;
  const chunkName = (id, seq) => `chunk:${String(id).padStart(12, "0")}:${String(seq).padStart(8, "0")}`;
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  function status(label, tone = "saved") {
    if (typeof attemptState !== "undefined" && ["submitted", "interrupted", "disqualified"].includes(attemptState?.state)) return;
    candidateFeedback.status("recording-status", label, tone);
    window.examFlow?.deviceStatusChanged?.();
  }
  async function request(path, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch(path, { credentials: "same-origin", ...options, signal: controller.signal,
        headers: { ...options.headers, ...await window.assessbridgeSeb.headers(), "X-Exam-Client": window.examClientId } });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) { const error = new Error(candidateFeedback.serverError(response, body, "Recording upload failed. Reconnect and retry.")); error.status = response.status; throw error; }
      return body;
    } catch (error) {
      if (!error.status || error.status >= 500) window.examRecovery?.lost();
      throw error;
    } finally { clearTimeout(timeout); }
  }
  function supportedType() {
    if (!window.MediaRecorder) throw new Error("This SEB browser cannot record video and audio.");
    const type = ["video/webm;codecs=vp8,opus", "video/webm", "video/mp4"].find(t => MediaRecorder.isTypeSupported(t));
    if (!type) throw new Error("This browser has no supported recording format.");
    return type;
  }
  function begin(value) {
    if (!value || value.getVideoTracks().length !== 1 || value.getAudioTracks().length !== 1) throw new Error("Connect the camera and microphone first.");
    if (recorder?.state === "recording") throw new Error("Recording is already running.");
    supportedType(); stream = value; failed = false; finalizing = null; captureStopped = false;
  }
  async function recoverPending() {
    for (const name of await store().names("part:")) {
      const old = await store().get(name);
      if (old && !old.stopped && old.id !== part?.id) {
        old.stopped = true; old.interrupted = true;
        await store().put(name, old);
      }
    }
    startPump();
  }
  async function activate() {
    const type = supportedType();
    const opened = await request("/api/candidate/exam/recording/parts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mime_type: type }) });
    part = { id: opened.part_id, next: 0, mime: type, stopped: false, interrupted: false };
    await store().put(partName(part.id), part);
    const capturePart = part;
    bitrate = desiredBitrate;
    recorder = new MediaRecorder(stream, { mimeType: type, videoBitsPerSecond: bitrate, audioBitsPerSecond: 64000 });
    recorder.addEventListener("dataavailable", event => {
      if (!event.data?.size) return;
      const sequence = capturePart.next++;
      writing = writing.then(async () => {
        if (failed) throw new Error("Recording is paused. Restore storage and retry.");
        if (await store().bytesUsed() + event.data.size > store().MAX_BYTES) throw new Error("Local recording storage is full. Restore your connection.");
        await store().put(chunkName(capturePart.id, sequence), event.data, true);
        await store().put(partName(capturePart.id), capturePart);
        startPump();
      }).catch(error => {
        failed = true; capturePart.interrupted = true;
        status("Recording could not be saved on this device. Free storage and retry.", "error");
        if (recorder?.state === "recording") recorder.stop();
      });
    });
    recorder.addEventListener("error", () => { failed = true; status("Recording interrupted", "error"); });
    recorder.addEventListener("stop", () => {
      capturePart.stopped = true;
      capturePart.interrupted ||= failed;
      writing = writing.then(() => store().put(partName(capturePart.id), capturePart)).catch(() => { failed = true; });
      if (!captureStopped && !adapting) { failed = true; status("Recording stopped", "error"); }
    });
    recorder.start(5000); active = true; status("Recording"); startPump();
  }
  async function pump() {
    await writing;
    for (const name of await store().names("part:")) {
      let saved = await store().get(name);
      if (!saved) continue;
      const chunks = await store().names(`chunk:${String(saved.id).padStart(12, "0")}:`);
      if (chunks.length >= 8) desiredBitrate = 800000;
      for (const name of chunks) {
        const blob = await store().get(name);
        if (!blob) continue;
        const sequence = Number(name.split(":")[2]);
        await request(`/api/candidate/exam/recording/parts/${saved.id}/chunks/${sequence}`, { method: "POST", headers: { "Content-Type": saved.mime }, body: blob });
        await store().remove(name);
        if (uploadProgress) { uploadProgress.ack += blob.size; finalProgress('Uploading recording…'); }
      }
      await writing;
      saved = await store().get(name);
      if (saved?.stopped && !(await store().names(`chunk:${String(saved.id).padStart(12, "0")}:`)).length) {
        await request(`/api/candidate/exam/recording/parts/${saved.id}/finish`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ final_sequence: saved.next - 1, interrupted: saved.interrupted }) });
        await store().remove(name);
      }
    }
    if (active && !failed) status("Recording");
  }
  function startPump() {
    if (pumping) return pumping;
    clearTimeout(timer);
    pumping = pump().catch(() => {
      if (active && !failed) status("Recording saved on device · Upload pending", "saving");
      finalProgress('Upload paused. Reconnecting…', 'error');
    })
      .finally(() => { pumping = null; if (active || finalizing) timer = setTimeout(startPump, 2000); });
    return pumping;
  }
  async function stopRecorder() {
    captureStopped = true;
    if (recorder && recorder.state !== "inactive") await new Promise(resolve => { recorder.addEventListener("stop", resolve, { once: true }); recorder.stop(); });
    await writing;
  }
  async function finish(state = {}) {
    if (finalizing) return finalizing;
    active = false;
    uploadProgress = { total: 0, ack: 0, ready: false };
    finalProgress('Preparing recording upload…');
    finalizing = (async () => {
      await stopRecorder();
      // Take a stable snapshot after the final recorder event has been saved.
      if (pumping) await pumping;
      clearTimeout(timer);
      for (const name of await store().names('chunk:')) {
        const blob = await store().get(name);
        uploadProgress.total += blob?.size || 0;
      }
      uploadProgress.total += uploadProgress.ack;
      uploadProgress.ready = true;
      finalProgress('Uploading recording…');
      await recoverPending();
      const deadline = Date.parse(state.upload_deadline_at || "") || Date.now() + 15 * 60000;
      while (Date.now() < deadline) {
        await startPump();
        const pending = await store().names("part:");
        if (!pending.length) finalProgress('Confirming recording upload…');
        if (!pending.length) {
          try {
            const result = await request("/api/candidate/exam/recording/finish", { method: "POST" });
            if (result.status === 'recording') throw new Error('Recording upload is still pending');
            await store().clear(); return { ...result, upload_complete: true };
          } catch (error) {
            if (error.status === 409) {
              const saved = await request("/api/candidate/exam/recording");
              if (saved.status !== "recording") { await store().clear(); return { ...saved, upload_complete: true }; }
            }
            finalProgress('Upload paused. Reconnecting…', 'error');
          }
        }
        await delay(2000);
      }
      // Release only after the server confirms collection ended. Keep local
      // evidence if confirmation is unavailable, so Retry can finish safely.
      const exit = await request('/api/candidate/exam/exit-ready');
      await store().clear();
      return { status: exit.recording_status, upload_complete: false };
    })().finally(() => { clearTimeout(timer); timer = null; finalizing = null; });
    return finalizing;
  }
  async function cancel() { active = false; await stopRecorder(); clearTimeout(timer); recorder = null; part = null; }
  async function retry(value) { await cancel(); begin(value); await recoverPending(); await activate(); }
  async function adapt() {
    if (!active || failed || adapting || bitrate === desiredBitrate || finalizing) return;
    adapting = true;
    try { await stopRecorder(); captureStopped = false; await activate(); }
    catch { failed = true; status("Recording needs to restart", "error"); }
    finally { adapting = false; }
  }
  return { begin, activate, cancel, finish, retry, recoverPending, adapt, pause: stopRecorder,
    markInterrupted: () => { failed = true; if (part) part.interrupted = true; status("Recording interrupted", "error"); },
    healthy: () => Boolean(active && recorder?.state === "recording" && !failed),
    running: () => Boolean(active) };
})();
window.assessbridgeRecording = examRecording;
