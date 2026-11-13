let submitDialog = null, leaveDialog = null;
let attemptState = null;
let dirtyAnswer = false;
let saveTimer = null;
let saveOperation = null;
let countdownTimer = null;
let heartbeatTimer = null;
let deadlineTime = 0;
let serverAnchor = 0;
let clientAnchor = 0;
let timeoutCheckPending = false;
let nextTimeoutCheck = 0;
let finalUploadRequest = 0;

async function examRequest(path, method = "GET", body = null, signal = undefined) {
  const sebHeaders = { ...await window.assessbridgeSeb.headers(), "X-Exam-Client": window.examClientId };
  const timeout = new AbortController();
  const timeoutId = setTimeout(() => timeout.abort(), 12000);
  let response;
  try {
  response = await fetch(path, {
    method, credentials: "same-origin", signal: signal || timeout.signal,
    headers: body === null ? sebHeaders : { "Content-Type": "application/json", ...sebHeaders },
    body: body === null ? undefined : JSON.stringify(body),
  });
  } catch { window.examRecovery?.lost(); throw new Error("Could not reach AssessBridge. Reconnect and try again."); }
  finally { clearTimeout(timeoutId); }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status >= 500) window.examRecovery?.lost();
    const error = new Error(candidateFeedback.serverError(response, payload, "Could not connect to the exam. Reconnect and try again."));
    error.status = response.status;
    throw error;
  }
  return payload;
}

function setExamError(message) {
  candidateFeedback.show("exam-error", message);
}

function setSaveStatus(text, state) {
  candidateFeedback.status("save-status", text, state);
}

function stopCountdown() {
  if (countdownTimer !== null) clearInterval(countdownTimer);
  countdownTimer = null;
  if (heartbeatTimer !== null) clearInterval(heartbeatTimer);
  heartbeatTimer = null;
  byId("resume-timer").hidden = true;
}

async function sendHeartbeat() { await window.examRecovery.heartbeat(); }

function formatRemaining(milliseconds) {
  const seconds = Math.max(0, Math.ceil(milliseconds / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

function startCountdown(state) {
  stopCountdown();
  if (state.state !== "active") return;
  deadlineTime = Date.parse(state.deadline_at);
  serverAnchor = Date.parse(state.server_now);
  clientAnchor = performance.now();
  byId("resume-timer").hidden = false;
  const tick = () => {
    if (!attemptState || attemptState.state !== "active") return;
    window.examRecovery.tick();
    const remaining = Math.max(0, deadlineTime - (serverAnchor + performance.now() - clientAnchor));
    const formatted = formatRemaining(remaining);
    byId("exam-timer").textContent = formatted;
    byId("exam-timer").classList.toggle("is-low", remaining <= 5 * 60 * 1000);
    byId("resume-timer").textContent = `Exam in progress · ${formatted} remaining. The timer continues during this device check.`;
    if (remaining === 0 && !timeoutCheckPending && Date.now() >= nextTimeoutCheck) {
      nextTimeoutCheck = Date.now() + 3000;
      timeoutCheckPending = true;
      refreshAttempt().catch(() => {}).finally(() => { timeoutCheckPending = false; });
    }
  };
  tick();
  countdownTimer = setInterval(tick, 500);
  sendHeartbeat();
  heartbeatTimer = setInterval(sendHeartbeat, 10000);
}

function showDisqualified(state) { showSubmitted(state); }

function showSubmitted(state) {
  if (attemptState?.state === state.state && !byId("submitted-panel").hidden) return;
  attemptState = state;
  dirtyAnswer = false;
  if (saveTimer !== null) clearTimeout(saveTimer);
  saveTimer = null;
  stopCountdown();
  candidateFeedback.clearExamWarnings();
  submitDialog?.destroy(); submitDialog = null;
  leaveDialog?.destroy(); leaveDialog = null;
  candidateFeedback.show("recording-final-status", "Saving the final recording...", "loading");
  const interrupted = state.state === "interrupted", disqualified = state.state === "disqualified";
  byId('submitted-panel').dataset.outcome = state.state;
  byId('submitted-panel').querySelector('.accepted-mark').textContent = interrupted || disqualified ? '!' : '\u2713';
  byId("finished-eyebrow").textContent = interrupted ? "EXAM INTERRUPTED" : disqualified ? "EXAM DISQUALIFIED" : "EXAM SUBMITTED";
  byId("submitted-title").textContent = `${state.title} ${interrupted ? "interrupted" : disqualified ? "disqualified" : "submitted"}`;
  byId("submitted-message").textContent = interrupted ? "The recovery limit was exceeded. Your saved work has been kept for review. This attempt cannot be reopened." : disqualified ? "You exited before submitting. This attempt is disqualified." : state.submission_reason === "timeout"
    ? "Time is up. Answers received before the deadline were submitted. Unsynchronized changes were not included."
    : "Your exam was submitted successfully.";
  byId("submitted-progress").textContent = `${state.answered_count} of ${state.question_count} questions answered.`;
  byId("exit-exam").hidden = true;
  showPanel("submitted-panel");
  finishSubmittedUpload(state);
}

function finishSubmittedUpload(state) {
  const request = ++finalUploadRequest;
  const current = () => request === finalUploadRequest && attemptState === state;
  byId('retry-final-upload').hidden = true;
  byId('exit-exam').hidden = true;
  candidateFeedback.show('recording-completeness', '');
  window.assessbridgeRecording.finish(state)
    .then(async (result) => {
      if (!current()) return;
      await examRequest('/api/candidate/exam/exit-ready');
      if (!current()) return;
      candidateFeedback.show('recording-final-status', result.upload_complete ? 'Available recording uploaded.' : 'Recording upload window ended.', result.upload_complete ? 'success' : 'warning');
      candidateFeedback.show('recording-completeness', result.status === 'complete' ? 'Session recording complete.' : 'Session recording incomplete. The available footage has been kept.', result.status === 'complete' ? 'success' : 'warning');
      byId('recording-upload-progress').value = result.upload_complete ? 100 : byId('recording-upload-progress').value;
      byId('recording-upload-progress').hidden = !result.upload_complete;
      byId('recording-upload-detail').textContent = result.upload_complete ? 'Available recording uploaded. You can exit the exam.' : 'Upload time expired. Missing footage will be shown in the report.';
      byId('exit-exam').hidden = false;
    })
    .catch(() => {
      if (!current()) return;
      candidateFeedback.show("recording-final-status", "Upload confirmation failed. Reconnect and retry.");
      byId('retry-final-upload').hidden = false;
    })
    .finally(() => { if (current()) stopMedia(); });
}

async function refreshAttempt() {
  const updated = await examRequest("/api/candidate/exam/session");
  if (updated.state === "disqualified") {
    showDisqualified(updated);
  } else if (["submitted", "interrupted"].includes(updated.state)) {
    showSubmitted(updated);
  } else if (updated.state === "active") {
    attemptState = updated;
    deadlineTime = Date.parse(updated.deadline_at);
    serverAnchor = Date.parse(updated.server_now);
    clientAnchor = performance.now();
  }
  return updated;
}

async function bootstrapAttempt() {
  const state = await window.examRecovery.prepare();
  attemptState = state;
  await window.assessbridgeRecording.recoverPending();
  byId("exit-before-start").hidden = state.state !== "not_started";
  byId("report-interruption-devices").hidden = state.state !== "active";
  if (["submitted", "interrupted"].includes(state.state)) {
    showSubmitted(state);
    return true;
  }
  if (["disqualified", "interrupted"].includes(state.state)) {
    showDisqualified(state);
    return true;
  }
  startCountdown(state);
  return false;
}

function showInstructions(state) {
  attemptState = state;
  byId("exit-before-start").hidden = false;
  byId("report-interruption-devices").hidden = true;
  byId("instructions-title").textContent = state.title;
  byId("instruction-count").textContent = String(state.question_count);
  byId("instruction-duration").textContent = `${state.duration_minutes} minutes`;
  byId("instructions-description").textContent = state.description;
  byId("instructions-description").hidden = !state.description;
  byId("instruction-rules-text").textContent = state.rules_text;
  byId("instruction-rules").hidden = !state.rules_text;
  showPanel("instructions-panel");
}

async function openAfterDevices() {
  let state = await examRequest("/api/candidate/exam/session");
  if (state.state === "active" && state.resume_required) {
    byId("recovery-preview").srcObject = window.assessbridgeMedia.stream();
    showPanel("recovery-panel");
    return;
  }
  attemptState = state;
  if (state.state === "disqualified") showDisqualified(state);
  else if (["submitted", "interrupted"].includes(state.state)) showSubmitted(state);
  else if (state.state === "active") {
    let recordingError = "";
    if (!window.assessbridgeRecording.healthy()) {
      try {
        if (window.assessbridgeRecording.running()) {
          await window.assessbridgeRecording.retry(window.assessbridgeMedia.stream());
        } else {
          window.assessbridgeRecording.begin(window.assessbridgeMedia.stream());
          await window.assessbridgeRecording.activate();
        }
      } catch (error) {
        await window.assessbridgeRecording.cancel();
        recordingError = error.message;
      }
    }
    if (!recordingError) {
      state = await examRequest("/api/candidate/exam/recovery/resume", "POST");
      state = await window.examRecovery.restoreDraft(state);
    }
    showExam(state);
    if (recordingError) setExamError(recordingError + " Retry recording before continuing.");
  }
  else showInstructions(state);
}

function currentAnswer() {
  if (attemptState.question.type === "written") return byId("written-answer").value || null;
  const selected = document.querySelector('input[name="mcq-answer"]:checked');
  return selected ? Number(selected.value) : null;
}

function scheduleSave(delay = 500) {
  dirtyAnswer = true;
  window.examRecovery.saveDraft(attemptState.current_index, currentAnswer());
  setSaveStatus("Saving…", "saving");
  if (saveTimer !== null) clearTimeout(saveTimer);
  saveTimer = setTimeout(() => { saveTimer = null; ensureSaved().catch(() => {}); }, delay);
}

async function ensureSaved() {
  if (saveTimer !== null) clearTimeout(saveTimer);
  saveTimer = null;
  while (attemptState?.state === "active") {
    if (saveOperation) {
      await saveOperation;
      continue;
    }
    if (!dirtyAnswer) return;
    await window.examRecovery.settled();
    if (!window.examRecovery.draftAvailable()) { setSaveStatus("Save failed. Device storage is unavailable.", "error"); return; }
    if (!window.examRecovery.online()) { setSaveStatus("Saved on device · Upload pending", "saving"); return; }
    const draft = await window.examStorage.get("draft");
    const questionIndex = attemptState.current_index;
    const answer = currentAnswer();
    dirtyAnswer = false;
    const operation = (async () => {
      try {
        const saved = await examRequest("/api/candidate/exam/answer", "POST", { question_index: questionIndex, answer });
        attemptState.answered_count = saved.answered_count;
        await window.examRecovery.clearDraft(draft?.version);
        if (!dirtyAnswer) setSaveStatus("Saved", "saved");
      } catch (error) {
        dirtyAnswer = true;
        setSaveStatus(window.examRecovery.draftAvailable() ? "Saved on device. Server save pending." : "Save failed. Device storage is unavailable.", window.examRecovery.draftAvailable() ? "saving" : "error");
        if (error.status === 409) await refreshAttempt().catch(() => {});
        throw error;
      } finally {
        saveOperation = null;
      }
    })();
    saveOperation = operation;
    await operation;
  }
}

function renderQuestion(state) {
  byId("exam-progress").textContent = `Question ${state.current_index + 1} of ${state.question_count}`;
  byId("question-prompt").textContent = state.question.prompt;
  const area = byId("answer-area");
  area.replaceChildren();
  if (state.question.type === "written") {
    const label = document.createElement("label");
    label.setAttribute("for", "written-answer");
    label.textContent = "Your answer";
    const input = document.createElement("textarea");
    input.id = "written-answer";
    input.maxLength = 5000;
    input.rows = 7;
    input.placeholder = "Type your answer here";
    input.value = typeof state.answer === "string" ? state.answer : "";
    input.addEventListener("input", () => scheduleSave());
    area.append(label, input);
  } else {
    const group = document.createElement("fieldset");
    group.className = "answer-options";
    const legend = document.createElement("legend");
    legend.textContent = "Choose one answer";
    group.append(legend);
    state.question.options.forEach((option, index) => {
      if (!option) return;
      const label = document.createElement("label");
      label.className = "answer-option";
      const input = document.createElement("input");
      input.type = "radio";
      input.name = "mcq-answer";
      input.value = String(index);
      input.checked = state.answer === index;
      input.addEventListener("change", () => scheduleSave(0));
      const text = document.createElement("span");
      text.textContent = option;
      label.append(input, text);
      group.append(label);
    });
    area.append(group);
  }
  byId("previous-question").disabled = state.current_index === 0;
  byId("next-question").textContent = state.current_index === state.question_count - 1 ? "Submit exam" : "Next →";
  byId("next-question").disabled = false;
  setExamError("");
  setSaveStatus("Saved", "saved");
}

function showExam(state) {
  byId('connection-status').hidden = false;
  if (["disqualified", "interrupted"].includes(state.state)) {
    showDisqualified(state);
    return;
  }
  if (["submitted", "interrupted"].includes(state.state)) {
    showSubmitted(state);
    return;
  }
  attemptState = state;
  byId("exit-before-start").hidden = true;
  byId("report-interruption-devices").hidden = false;
  dirtyAnswer = false;
  renderQuestion(state);
  startCountdown(state);
  showPanel("exam-panel");
  deviceStatusChanged();
}

function deviceStatusChanged() {
  const devicesReady = mediaReady();
  const recordingReady = window.assessbridgeRecording.healthy();
  const canAnswer = devicesReady && recordingReady && !window.examRecovery.blocked() && !attemptState?.resume_required;
  const ready = canAnswer && window.examRecovery.online() && !attemptState?.recovery_started_at;
  document.querySelectorAll("#answer-area input, #answer-area textarea").forEach(input => { input.disabled = !canAnswer; });
  byId("exam-device-warning").hidden = ready;
  const warning = !devicesReady
    ? "Camera or microphone disconnected. Reconnect to continue."
    : !recordingReady ? "Recording is unavailable. Retry recording to continue."
    : window.examRecovery.blocked() ? "Answering is paused. Check the recovery status above."
    : attemptState?.resume_required ? "Complete recovery checks to continue."
    : "Reconnecting. You can edit this answer; navigation resumes when connected.";
  candidateFeedback.show("exam-monitoring-warning", ready ? "" : warning, "warning");
  byId("reconnect-devices").hidden = devicesReady;
  byId("retry-recording").hidden = !devicesReady || recordingReady;

  if (attemptState?.state === "active") {
    byId("previous-question").disabled = !ready || attemptState.current_index === 0;
    byId("next-question").disabled = !ready;
  }
}

async function moveQuestion(direction) {
  if (attemptState?.state !== "active") return;
  if (!mediaReady() || !window.assessbridgeRecording.healthy() || !window.examRecovery.online() || window.examRecovery.blocked()) { deviceStatusChanged(); return; }
  const previous = byId("previous-question");
  const next = byId("next-question");
  previous.disabled = true;
  next.disabled = true;
  setExamError("");
  try {
    await ensureSaved();
    if (attemptState?.state !== "active") return;
    if (!window.examRecovery.online() || window.examRecovery.blocked()
        || attemptState.resume_required || attemptState.recovery_started_at) { deviceStatusChanged(); return; }
    const state = await examRequest("/api/candidate/exam/navigate", "POST", { direction });
    showExam(state);
  } catch (error) {
    if (error.status === 409) await refreshAttempt().catch(() => {});
    if (attemptState?.state === "active") setExamError(error.message);
  } finally {
    if (attemptState?.state === "active" && !byId("exam-panel").hidden) {
      deviceStatusChanged();
    }
  }
}

byId("start-exam").addEventListener("click", async () => {
  if (!mediaReady()) {
    showPanel("device-panel");
    setDeviceError("Reconnect both devices before starting your exam.");
    return;
  }
  const button = byId("start-exam");
  button.disabled = true;
  button.textContent = "Starting exam…";
  candidateFeedback.show("start-error", "");
  try {
    await window.examRecovery.readiness();
    window.assessbridgeRecording.begin(window.assessbridgeMedia.stream());
    const state = await examRequest("/api/candidate/exam/start", "POST");
    let recordingError = "";
    if (state.state === "active") {
      try { await window.assessbridgeRecording.activate(); }
      catch (error) { await window.assessbridgeRecording.cancel(); recordingError = error.message; }
    } else await window.assessbridgeRecording.cancel();
    showExam(state);
    if (recordingError) setExamError(recordingError + " Retry recording before continuing.");
  } catch (error) {
    await window.assessbridgeRecording.cancel();
    candidateFeedback.show("start-error", error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Start exam →";
  }
});

byId("previous-question").addEventListener("click", () => moveQuestion("previous"));
byId("next-question").addEventListener("click", async () => {
  if (!mediaReady() || !window.assessbridgeRecording.healthy() || !window.examRecovery.online() || window.examRecovery.blocked()) { deviceStatusChanged(); return; }
  if (attemptState?.current_index !== attemptState?.question_count - 1) {
    await moveQuestion("next");
    return;
  }
  try {
    await ensureSaved();
    if (attemptState?.state !== "active") return;
    if (submitDialog) return;
    const dialog = candidateFeedback.confirm({ id: 'submit-exam', dialogId: 'submit-dialog', confirmButtonId: 'confirm-submit', cancelButtonId: 'cancel-submit',
      title: 'Submit your exam?', text: `You answered ${attemptState.answered_count} of ${attemptState.question_count} questions. You cannot change answers after submitting.`,
      confirmLabel: 'Submit exam', cancelLabel: 'Keep working', busyLabel: 'Submitting...',
      onConfirm: async () => {
        try {
          const state = await examRequest('/api/candidate/exam/submit', 'POST');
          showSubmitted(state); return true;
        } catch (error) {
          await refreshAttempt().catch(() => {});
          if (attemptState?.state !== 'active') return true;
          dialog.setError(error.message); return false;
        }
      },
    });
    submitDialog = dialog;
    dialog.result.then(() => { if (submitDialog === dialog) submitDialog = null; });
  } catch (error) {
    if (attemptState?.state === "active") setExamError(error.message);
  }
});
byId("reconnect-devices").addEventListener("click", async () => {
  const button = byId("reconnect-devices");
  button.disabled = true;
  try {
    await window.examRecovery.settled();
    if (attemptState?.state !== "active") return;
    showPanel("device-panel");
    setDeviceError("Reconnect both devices to return to the exam. Your timer keeps running.");
    checkAvailability();
  } catch (error) {
    if (attemptState?.state === "active") setExamError(error.message);
  } finally {
    button.disabled = false;
  }
});
byId("retry-recording").addEventListener("click", async () => {
  const button = byId("retry-recording");
  button.disabled = true;
  try {
    await window.assessbridgeRecording.retry(window.assessbridgeMedia.stream());
    await sendHeartbeat();
    setExamError("");
  } catch (error) {
    setExamError(error.message);
  } finally {
    button.disabled = false;
    deviceStatusChanged();
  }
});
async function leaveExam() {
  if (leaveDialog || attemptState?.state !== 'active') return;
  const content = document.createElement('div');
  const dialog = candidateFeedback.confirm({ id: 'leave-exam', dialogId: 'leave-exam-dialog', title: 'Leave this exam?',
    text: 'Leaving before submission disqualifies your attempt. You cannot resume it.',
    confirmLabel: 'Leave exam', cancelLabel: 'Keep working', busyLabel: 'Leaving...', destructive: true, content,
    onConfirm: async () => {
      const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 3000);
      try {
        await examRequest('/api/candidate/exam/leave', 'POST', null, controller.signal);
        await Promise.race([window.assessbridgeRecording.finish().catch(() => {}), new Promise(resolve => setTimeout(resolve, 1500))]);
        window.location.assign('/seb/exit'); return true;
      } catch {
        dialog.setError('Could not confirm the exit. Retry, or use Exit SEB to leave now.');
        if (!content.querySelector('a')) { const exit = document.createElement('a'); exit.href = '/seb/exit'; exit.className = 'ab-feedback__action'; exit.textContent = 'Exit SEB'; content.append(exit); }
        return false;
      } finally { clearTimeout(timeout); }
    },
  });
  leaveDialog = dialog;
  dialog.result.then(() => { if (leaveDialog === dialog) leaveDialog = null; });
}
byId("report-interruption").addEventListener("click", leaveExam);
byId("report-interruption-devices").addEventListener("click", leaveExam);

byId("verify-recovery").addEventListener("click", async () => {
  const button = byId("verify-recovery"); button.disabled = true;
  candidateFeedback.show("recovery-error", "");
  try {
    await window.examRecovery.liveCheck();
    await window.examRecovery.readiness();
    window.assessbridgeRecording.begin(window.assessbridgeMedia.stream());
    await window.assessbridgeRecording.activate();
    let state = await examRequest("/api/candidate/exam/recovery/resume", "POST");
    state = await window.examRecovery.restoreDraft(state);
    showExam(state);
  } catch (error) { candidateFeedback.show("recovery-error", error.message); }
  finally { button.disabled = false; }
});
byId("leave-recovery").addEventListener("click", leaveExam);
byId('retry-final-upload').addEventListener('click', () => finishSubmittedUpload(attemptState));
byId('exit-exam').addEventListener('click', async () => {
  const button = byId('exit-exam'); button.disabled = true;
  try {
    const result = await examRequest('/api/candidate/exam/exit-ready');
    window.location.assign(result.exit_url);
  } catch (error) {
    candidateFeedback.show('recording-final-status', 'Reconnect to confirm the upload before exiting.');
  } finally { button.disabled = false; }
});
window.examFlow = { bootstrap: bootstrapAttempt, openAfterDevices, stopCountdown, deviceStatusChanged };
