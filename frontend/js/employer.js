const tabs = ["overview", "editor", "detail", "review", "results"];
const steps = ["basics", "questions", "rules", "roster", "schedule", "final"];
const headings = {
  overview: ["Overview", "Your starting point for planning and managing assessments."],
  editor: ["Assessment editor", "Move through the setup steps to prepare an assessment."],
  detail: ["Assessment detail", "Choose a saved assessment to see its schedule and invitations."],
  review: ["Review cases", "Review candidate results and evidence, then record your decision."],
  results: ["Results & reports", "Candidate results by exam."],
};
const questions = [];
let activeStep = 0;
let invitationRequestKey = crypto.randomUUID();
let submittedPayload = null;
let invitationResult = null;
let selectedAssessmentId = null;
let assessmentPage = 1;
let assessmentTotal = 0;
let assessmentListRequest = 0;
let detailRequest = 0;
const employerFeedback = AssessBridgeFeedback.create({ notificationHost: '#employer-notifications' });
const employerNotices = employerFeedback.scope('employer');
let editorFeedback = employerFeedback.scope('editor');
let listFeedback = employerFeedback.scope('assessment-list');
let detailFeedback = employerFeedback.scope('assessment-detail');
let rosterFieldSequence = 0;
function editorError(text, step, input) { return Object.assign(new Error(text), { step, input }); }
function fieldError(input, text, validate) {
  const id = `field-${input.id || (input.dataset.feedbackId ||= ++rosterFieldSequence)}`;
  if (!text) { editorFeedback.clear(id); return; }
  editorFeedback.fieldError({ id, input, target: input.closest('.form-field, td') || input.parentElement, text, validate });
}
function showEditorError(error) {
  setFeedback(byId('review-readiness'), '', '');
  setFeedback(byId('invite-status'), '', '');
  if (error.step) showStep(error.step);
  if (error.input) {
    const input = typeof error.input === 'string' ? byId(error.input) : error.input;
    fieldError(input, error.message, () => { try { invitationPayload(); return true; } catch (next) { return next.input !== error.input; } });
    input.focus();
  } else setFeedback(byId(error.step === 'questions' ? 'question-message' : error.step === 'roster' ? 'roster-status' : 'invite-status'), error.message, 'error');
}

function byId(id) { return document.getElementById(id); }
function value(id) { return byId(id).value.trim(); }
function label(text, fallback = "Not entered") { return text || fallback; }
function selectedChoices(selector) { return [...document.querySelectorAll(`${selector}:checked`)].map((input) => input.value); }
function setFeedback(element, message, tone, action) {
  const scope = element.id === 'assessment-filter-error' ? listFeedback : editorFeedback;
  if (!message) return scope.clear(element.id);
  return scope.show({ id: element.id, target: element, text: message, tone: tone || 'info', action });
}

function setEditorLocked(locked) {
  document.querySelectorAll(".editor-main input, .editor-main textarea, .editor-main select, #add-question, .question-list button, .roster-remove").forEach((control) => {
    control.disabled = locked;
  });
}

function rosterRows() {
  return [...byId("roster-rows").querySelectorAll("tr")];
}

function candidateRoster() {
  return rosterRows().map((row) => {
    const name = row.querySelector(".roster-name").value.trim();
    const emailInput = row.querySelector(".roster-email");
    const email = emailInput.value.trim();
    return name && email && emailInput.checkValidity() && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) ? { name, email } : null;
  }).filter(Boolean);
}

function updateRoster() {
  const rows = rosterRows();
  const last = rows.at(-1);
  if (last) {
    const name = last.querySelector(".roster-name").value.trim();
    const emailInput = last.querySelector(".roster-email");
    if (name && emailInput.value.trim() && emailInput.checkValidity()) addRosterRow();
  }

  const seenEmails = new Set();
  for (const row of rosterRows()) {
    const nameInput = row.querySelector('.roster-name');
    const emailInput = row.querySelector('.roster-email');
    const hasContent = Boolean(nameInput.value.trim() || emailInput.value.trim());
    const emailValid = () => Boolean(emailInput.value.trim()) && emailInput.checkValidity()
      && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(emailInput.value.trim());
    fieldError(nameInput, hasContent && !nameInput.value.trim() ? 'Enter the candidate name.' : '', input => Boolean(input.value.trim()));
    const email = emailInput.value.trim().toLowerCase();
    const duplicate = emailValid() && seenEmails.has(email);
    fieldError(emailInput, hasContent && !emailValid() ? 'Enter a valid invitation email.' : duplicate ? 'Use a different email for each candidate.' : '',
      () => emailValid() && rosterRows().filter(current => current.querySelector('.roster-email').value.trim().toLowerCase() === emailInput.value.trim().toLowerCase()).length === 1);
    if (emailValid()) seenEmails.add(email);
    row.querySelector('.roster-remove').hidden = !hasContent;
  }
  if (candidateRoster().length) setFeedback(byId('roster-status'), '', '');
}

function addRosterRow() {
  const row = document.createElement("tr");
  const nameCell = document.createElement("td");
  const emailCell = document.createElement("td");
  const nameWrap = document.createElement("div");
  const nameInput = document.createElement("input");
  const emailInput = document.createElement("input");
  const remove = document.createElement("button");
  const rowNumber = rosterRows().length + 1;

  nameWrap.className = "roster-cell";
  nameInput.className = "roster-name";
  nameInput.type = "text";
  nameInput.maxLength = 120;
  nameInput.placeholder = "Candidate name";
  nameInput.setAttribute("aria-label", `Candidate ${rowNumber} name`);
  emailInput.className = "roster-email";
  emailInput.type = "email";
  emailInput.maxLength = 254;
  emailInput.placeholder = "name@example.com";
  emailInput.setAttribute("aria-label", `Candidate ${rowNumber} email`);
  remove.className = "roster-remove";
  remove.type = "button";
  remove.textContent = "Remove";
  remove.setAttribute("aria-label", `Remove candidate ${rowNumber}`);
  remove.hidden = true;

  for (const input of [nameInput, emailInput]) input.addEventListener("input", updateRoster);
  remove.addEventListener("click", () => {
    for (const input of row.querySelectorAll("input")) fieldError(input, "");
    row.remove();
    if (!rosterRows().length) addRosterRow();
    rosterRows().forEach((current, index) => {
      current.querySelector(".roster-name").setAttribute("aria-label", `Candidate ${index + 1} name`);
      current.querySelector(".roster-email").setAttribute("aria-label", `Candidate ${index + 1} email`);
      current.querySelector(".roster-remove").setAttribute("aria-label", `Remove candidate ${index + 1}`);
    });
    updateRoster();
  });
  nameWrap.append(nameInput, remove);
  nameCell.append(nameWrap);
  emailCell.append(emailInput);
  row.append(nameCell, emailCell);
  byId("roster-rows").append(row);
}

function formatDate(raw) {
  if (!raw) return "Not set";
  const date = new Date(`${raw}+03:00`);
  return Number.isNaN(date.getTime()) ? "Not set" : `${date.toLocaleString("en-GB", { timeZone: "Africa/Nairobi", dateStyle: "medium", timeStyle: "short" })} EAT`;
}

function updateScheduleFeedback(required = false) {
  const start = value("schedule-start");
  const end = value("schedule-end");
  const duration = Number(value("assessment-duration"));
  const startDate = start ? new Date(`${start}+03:00`) : null;
  const endDate = end ? new Date(`${end}+03:00`) : null;
  let message = "";
  let tone = "";
  let invalidField = "";
  if (!start && !end) {
    if (required) { message = "Enter when the access window opens."; invalidField = "schedule-start"; }
  } else if (!start) {
    if (required) { message = "Enter when the access window opens."; invalidField = "schedule-start"; }
  } else if (Number.isNaN(startDate.getTime()) || startDate <= new Date()) {
    message = "Choose a future access window start.";
    invalidField = "schedule-start";
  } else if (!end) {
    if (required) { message = "Enter when the access window closes."; invalidField = "schedule-end"; }
  } else if (Number.isNaN(endDate.getTime()) || endDate <= startDate) {
    message = "The access window must close after it opens.";
    invalidField = "schedule-end";
  } else if (Number.isInteger(duration) && duration > 0 && endDate - startDate < duration * 60 * 1000) {
    message = "The access window must allow the full assessment duration.";
    invalidField = "schedule-end";
  } else if (required && (!Number.isInteger(duration) || duration < 1 || duration > 480)) {
    message = "Set a duration from 1 to 480 minutes in Basics & duration.";
  }
  if (message && !tone) tone = "error";
  for (const id of ['schedule-start', 'schedule-end']) {
    fieldError(byId(id), id === invalidField ? message : '');
  }
  setFeedback(byId('schedule-status'), message && !invalidField ? message : '', tone);
  if (required && invalidField) byId(invalidField).focus();
  return tone !== "error" && (start && end ? true : !required);
}

for (const id of ["schedule-start", "schedule-end"]) {
  byId(id).addEventListener("input", () => updateScheduleFeedback());
  byId(id).addEventListener("change", () => updateScheduleFeedback());
}
byId("assessment-duration").addEventListener("input", () => updateScheduleFeedback());

function formatSavedDate(raw) {
  if (!raw) return "Not set";
  const date = new Date(raw);
  return Number.isNaN(date.getTime()) ? "Not set" : `${date.toLocaleString("en-GB", { timeZone: "Africa/Nairobi", dateStyle: "medium", timeStyle: "short" })} EAT`;
}

function scheduleState(startsAt, endsAt) {
  const now = Date.now();
  const start = Date.parse(startsAt);
  const end = Date.parse(endsAt);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return "Scheduled";
  return now < start ? "Scheduled" : now < end ? "Open" : "Closed";
}

function refreshScheduleStates() {
  document.querySelectorAll(".assessment-list-item").forEach((button) => {
    const state = scheduleState(button.dataset.startsAt, button.dataset.endsAt);
    const status = button.querySelector(".assessment-list-status");
    if (status) {
      status.textContent = state;
      status.dataset.state = state.toLowerCase();
    }
  });
  const detail = byId("selected-assessment");
  if (!detail.hidden) {
    const state = scheduleState(detail.dataset.startsAt, detail.dataset.endsAt);
    byId("detail-schedule-state").textContent = state;
    byId("detail-schedule-state").dataset.state = state.toLowerCase();
  }
}

window.setInterval(() => {
  refreshScheduleStates();
  if (!document.querySelector('[data-panel="detail"]').hidden && value("assessment-status-filter") !== "all") {
    loadAssessments(assessmentPage, selectedAssessmentId);
  }
}, 30000);

function assessmentFilters() {
  const filters = new URLSearchParams();
  const search = value("assessment-search");
  if (search) filters.set("search", search);
  const status = value("assessment-status-filter");
  if (status !== "all") filters.set("status", status);
  for (const id of ["assessment-candidates-min", "assessment-candidates-max", "assessment-questions-min", "assessment-questions-max"]) {
    const input = byId(id);
    if (!input.value) continue;
    if (!input.checkValidity() || !/^\d+$/.test(input.value)) throw new Error("Enter whole numbers from 0 to 200 in the count filters.");
    filters.set(id.replace("assessment-", "").replaceAll("-", "_"), input.value);
  }
  for (const field of ["candidates", "questions"]) {
    const min = byId(`assessment-${field}-min`).value;
    const max = byId(`assessment-${field}-max`).value;
    if (min !== "" && max !== "" && Number(min) > Number(max)) throw new Error("A minimum count cannot be greater than its maximum.");
  }
  return filters;
}

function appendHighlighted(parent, source, search) {
  const text = String(source || "");
  if (!search) { parent.textContent = text; return; }
  const lowerText = text.toLocaleLowerCase();
  const lowerSearch = search.toLocaleLowerCase();
  let index = 0;
  while (index < text.length) {
    const match = lowerText.indexOf(lowerSearch, index);
    if (match < 0) { parent.append(document.createTextNode(text.slice(index))); break; }
    parent.append(document.createTextNode(text.slice(index, match)));
    const mark = document.createElement("mark");
    mark.textContent = text.slice(match, match + search.length);
    parent.append(mark);
    index = match + search.length;
  }
}

async function loadAssessments(page = assessmentPage, selectId = selectedAssessmentId) {
  const request = ++assessmentListRequest;
  ++detailRequest; detailFeedback.dispose();
  byId("selected-assessment").hidden = true;
  listFeedback.dispose();
  const scope = listFeedback = employerFeedback.scope("assessment-list");
  let filters;
  try {
    filters = assessmentFilters();
    setFeedback(byId("assessment-filter-error"), "", "");
  } catch (error) {
    setFeedback(byId("assessment-filter-error"), error.message, "error");
    return;
  }
  filters.set("page", String(page));
  scope.show({ id: "list", target: "#assessment-list-message", text: "Loading assessments...", tone: "loading" });
  try {
    const response = await fetch(`/api/employer/assessments?${filters}`, { credentials: "same-origin" });
    if (!response.ok) throw new Error("Could not load saved assessments.");
    const data = await response.json();
    if (request !== assessmentListRequest || !scope.active) return;
    const lastPage = Math.max(1, Math.ceil(data.total / data.page_size));
    if (page > lastPage) return loadAssessments(lastPage, selectId);
    assessmentPage = data.page;
    assessmentTotal = data.total;
    const list = byId("assessment-list");
    list.replaceChildren();
    byId("assessment-total").textContent = `${data.total} ${filters.size > 1 ? "matching " : ""}${data.total === 1 ? "assessment" : "assessments"}`;
    byId("assessment-page").textContent = `Page ${page} of ${lastPage}`;
    byId("assessments-previous").disabled = page <= 1;
    byId("assessments-next").disabled = page * data.page_size >= data.total;
    scope.clear("list");
    if (!data.total) scope.show({ id: "list", target: "#assessment-list-message", tone: "neutral", text: filters.size > 1 ? "No assessments match these filters." : "No saved assessments." });
    const search = value("assessment-search");
    for (const item of data.items) {
      const button = document.createElement("button");
      const heading = document.createElement("strong");
      const code = document.createElement("span");
      const invited = document.createElement("span");
      const status = document.createElement("span");
      button.type = "button";
      button.className = "assessment-list-item";
      button.dataset.assessmentId = String(item.id);
      button.dataset.startsAt = item.starts_at;
      button.dataset.endsAt = item.ends_at;
      button.classList.toggle("is-selected", item.id === selectId);
      appendHighlighted(heading, item.title, search);
      code.className = "assessment-list-code";
      appendHighlighted(code, item.internal_reference || "Not set", search);
      invited.textContent = `${item.sent_count} ${item.sent_count === 1 ? "candidate invited" : "candidates invited"}`;
      status.className = "assessment-list-status";
      button.append(heading, code, status, invited);
      button.addEventListener("click", () => selectAssessment(item.id, true));
      list.append(button);
    }
    refreshScheduleStates();
    const target = data.items.some((item) => item.id === selectId) ? selectId : data.items[0]?.id;
    if (target) await selectAssessment(target);
    else {
      selectedAssessmentId = null;
      byId("selected-assessment").hidden = true;
    }
  } catch (error) {
    if (request !== assessmentListRequest || !scope.active) return;
    scope.show({ id: "list", target: "#assessment-list-message", text: "Could not load assessments. Try again.", tone: "error", action: { label: "Retry", onClick: () => loadAssessments(page, selectId) } });
  }
}

let assessmentFilterTimer;
for (const id of ["assessment-search", "assessment-candidates-min", "assessment-candidates-max", "assessment-questions-min", "assessment-questions-max"]) {
  byId(id).addEventListener("input", () => {
    clearTimeout(assessmentFilterTimer);
    assessmentFilterTimer = setTimeout(() => loadAssessments(1, selectedAssessmentId), 200);
  });
}
byId("assessment-status-filter").addEventListener("change", () => loadAssessments(1, selectedAssessmentId));
byId("assessment-clear-filters").addEventListener("click", () => {
  clearTimeout(assessmentFilterTimer);
  for (const id of ["assessment-search", "assessment-candidates-min", "assessment-candidates-max", "assessment-questions-min", "assessment-questions-max"]) byId(id).value = "";
  byId("assessment-status-filter").value = "all";
  loadAssessments(1, selectedAssessmentId);
});

async function selectAssessment(id, scrollToDetail = false) {
  const request = ++detailRequest;
  detailFeedback.dispose();
  const scope = detailFeedback = employerFeedback.scope("assessment-detail");
  scope.show({ id: "detail", target: "#assessment-detail-message", text: "Loading assessment details...", tone: "loading" });
  try {
    const response = await fetch(`/api/employer/assessments/${id}`, { credentials: "same-origin" });
    if (!response.ok) throw new Error("Could not load this assessment.");
    const assessment = await response.json();
    if (request !== detailRequest || !scope.active) return;
    selectedAssessmentId = id;
    document.querySelectorAll(".assessment-list-item").forEach((button) => {
      button.classList.toggle("is-selected", Number(button.dataset.assessmentId) === id);
    });
    renderDetail(assessment);
    scope.clear("detail");
    if (scrollToDetail) byId("selected-assessment").scrollIntoView({ behavior: "instant", block: "start" });
  } catch (error) {
    if (request === detailRequest && scope.active) {
      scope.show({ id: "detail", target: "#assessment-detail-message", text: "Could not load this assessment. Try again.", tone: "error", action: { label: "Retry", onClick: () => selectAssessment(id, scrollToDetail) } });
    }
  }
}

function invitationPayload() {
  const title = value("assessment-title");
  const duration = Number(value("assessment-duration"));
  const candidates = candidateRoster();
  const incomplete = rosterRows().some((row) => {
    const name = row.querySelector(".roster-name").value.trim();
    const emailInput = row.querySelector(".roster-email");
    return (name || emailInput.value.trim()) && (!name || !emailInput.value.trim() || (!emailInput.checkValidity() || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(emailInput.value.trim())));
  });
  if (!title) throw editorError('Enter an assessment title.', 'basics', 'assessment-title');
  if (/[\r\n]/.test(title)) throw editorError('Use one line for the assessment title.', 'basics', 'assessment-title');
  if (!Number.isInteger(duration) || duration < 1 || duration > 480) throw editorError('Enter a duration from 1 to 480 minutes.', 'basics', 'assessment-duration');
  if (!questions.length) throw editorError('Add at least one question and answer key.', 'questions', null);
  if (incomplete) {
    const row = rosterRows().find(row => { const n = row.querySelector('.roster-name'); const e = row.querySelector('.roster-email'); return (n.value.trim() || e.value.trim()) && (!n.value.trim() || !e.value.trim() || !e.checkValidity() || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(e.value.trim())); });
    const name = row.querySelector('.roster-name');
    throw editorError(name.value.trim() ? 'Enter a valid invitation email.' : 'Enter the candidate name.', 'roster', name.value.trim() ? row.querySelector('.roster-email') : name);
  }
  if (!candidates.length) throw editorError('Add a candidate name and invitation email.', 'roster', null);
  if (candidates.length > 200) throw editorError('An assessment can invite up to 200 candidates.', 'roster', null);
  if (new Set(candidates.map(candidate => candidate.email.toLowerCase())).size !== candidates.length) {
    const seen = new Set();
    const duplicate = rosterRows().map(row => row.querySelector('.roster-email')).find(input => { const email = input.value.trim().toLowerCase(); if (!email) return false; if (seen.has(email)) return true; seen.add(email); return false; });
    throw editorError('Use a different email for each candidate.', 'roster', duplicate);
  }
  const start = value("schedule-start");
  const end = value("schedule-end");
  if (!start || !end) throw editorError('Enter both access window dates.', 'schedule', !start ? 'schedule-start' : 'schedule-end');
  const startDate = new Date(`${start}+03:00`);
  const endDate = new Date(`${end}+03:00`);
  if (Number.isNaN(startDate.getTime()) || startDate <= new Date()) throw editorError('Choose a future access window start.', 'schedule', 'schedule-start');
  if (Number.isNaN(endDate.getTime()) || endDate <= startDate) throw editorError('The access window must close after it opens.', 'schedule', 'schedule-end');
  if (endDate - startDate < duration * 60 * 1000) throw editorError('The access window must allow the full assessment duration.', 'schedule', 'schedule-end');
  return {
    request_key: invitationRequestKey,
    title,
    description: value("assessment-description"),
    internal_reference: value("assessment-reference"),
    duration_minutes: duration,
    rules_text: selectedChoices(".prohibited-item").join(", "),
    identity_policy: document.querySelector('input[name="identity-policy"]:checked').value,
    questions,
    candidates,
    starts_at: `${start}:00+03:00`,
    ends_at: `${end}:00+03:00`,
  };
}

async function sendInvitations() {
  const button = byId('send-invitations');
  if (button.disabled) return;
  const scope = editorFeedback;
  const retrying = Boolean(submittedPayload);
  let payload;
  try { payload = submittedPayload ? JSON.parse(submittedPayload) : invitationPayload(); }
  catch (error) { showEditorError(error); return; }
  button.disabled = true;
  byId('new-assessment').disabled = true;
  button.textContent = 'Sending...';
  button.dataset.state = 'sending';
  button.setAttribute('aria-busy', 'true');
  scope.clear('review-readiness');
  // Retain the exact request even if acknowledgement is lost after a save.
  submittedPayload = JSON.stringify(payload);
  setEditorLocked(true);
  scope.show({ id: 'invite-status', target: '#invite-status', text: 'Sending invitations...', tone: 'loading' });
  let completed = false;
  let safeError = "Could not confirm invitation delivery. Retry to check and send the remaining invitations.";
  let retryAction = { label: 'Retry invitations', onClick: sendInvitations };
  try {
    const response = await fetch('/api/employer/invitations', {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: submittedPayload,
    });
    const result = await response.json().catch(() => ({}));
    if (!scope.active) return;
    if (!response.ok) {
      const safe = {
        'Email delivery is not configured': 'Email delivery is unavailable. Configure email delivery, then retry.',
        'SEB invitation setup is incomplete': 'SEB invitation setup is unavailable. Check the setup, then retry.',
        'Invitation request does not match the saved assessment': 'This request differs from the saved assessment. Open Assessment detail to check its invitations.',
        'Results are published. Invitations cannot be changed.': 'Results are published. Invitations cannot be changed.',
      };
      // Validation and setup checks occur before saving. A new draft can be corrected safely.
      if (!retrying && !invitationResult && (response.status === 422 || ['Email delivery is not configured', 'SEB invitation setup is incomplete'].includes(result.detail))) {
        submittedPayload = null;
        setEditorLocked(false);
      }
      safeError = safe[result.detail] || (response.status === 422
        ? 'Check the assessment details and schedule, then try again.'
        : response.status === 401 || response.status === 403 ? 'Your session cannot send invitations. Sign in again.'
          : 'Could not confirm invitation delivery. Retry to check and send the remaining invitations.');
      const scheduleStarted = response.status === 422 && Array.isArray(result.detail)
        && result.detail.some(item => typeof item.msg === 'string' && item.msg.includes('Assessment start must be in the future'));
      if (retrying && scheduleStarted) {
        safeError = 'The access window has started. Check invitation delivery in Assessment detail.';
        retryAction = { label: 'View assessment details', onClick: () => showTab('detail') };
      }
      throw new Error(safeError);
    }
    if (!Number.isInteger(result.sent) || !Number.isInteger(result.failed) || !Number.isInteger(result.pending)
        || result.sent + result.failed + result.pending !== payload.candidates.length || !result.assessment_id
        || [result.sent, result.failed, result.pending].some(count => count < 0)) {
      throw new Error('Could not confirm invitation delivery. Retry to check and send the remaining invitations.');
    }
    invitationResult = result;
    selectedAssessmentId = result.assessment_id;
    byId('editor-state').textContent = 'Saved assessment';
    byId('editor-state').dataset.tone = 'success';
    loadAssessments(1, result.assessment_id);
    completed = result.sent === payload.candidates.length && !result.failed && !result.pending;
    if (completed) {
      resetEditor();
      employerNotices.notify({ id: 'invitations-sent', text: 'Assessment saved. Invitations sent.' });
    } else {
      scope.show({ id: 'invite-status', target: '#invite-status', tone: 'error',
        text: `Assessment saved. ${result.sent} of ${payload.candidates.length} invitations sent.\n${result.failed} failed. ${result.pending} pending.`,
        action: { label: 'Retry failed invitations', onClick: sendInvitations } });
    }
  } catch (error) {
    if (scope.active) scope.show({ id: 'invite-status', target: '#invite-status', tone: 'error',
      text: safeError,
      action: retryAction });
  } finally {
    button.removeAttribute('aria-busy');
    byId('new-assessment').disabled = false;
    if (!completed && scope.active) {
      button.textContent = submittedPayload ? 'Retry failed invitations' : 'Confirm and send invitations';
      button.dataset.state = 'error';
      button.disabled = false;
    }
  }
}
byId('send-invitations').addEventListener('click', sendInvitations);

let currentEmployerTab = null;
function showTab(tab) {
  if (!tabs.includes(tab)) return;
  if (currentEmployerTab === 'detail' && tab !== 'detail') {
    ++assessmentListRequest; ++detailRequest; clearTimeout(assessmentFilterTimer);
    listFeedback.dispose(); detailFeedback.dispose();
  }
  if (currentEmployerTab === 'editor' && tab !== 'editor') editorFeedback.clearAll();
  if (!['review', 'results'].includes(tab)) window.employerReview?.deactivate();
  currentEmployerTab = tab;
  for (const button of document.querySelectorAll("[data-tab]")) {
    const selected = button.dataset.tab === tab;
    button.classList.toggle("is-active", selected);
    if (selected) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
  for (const panel of document.querySelectorAll("[data-panel]")) {
    panel.hidden = panel.dataset.panel !== tab;
  }
  byId("page-title").textContent = headings[tab][0];
  byId("page-subtitle").textContent = headings[tab][1];
  if (tab === "detail") loadAssessments();
  if (tab === "review" || tab === "results") window.employerReview?.activate(tab);
  else window.employerReview?.pauseMedia();
  if (tab === "editor" && activeStep === steps.length - 1) renderSummary();
  history.replaceState(null, "", `#${tab}`);
}

function showStep(step) {
  const index = steps.indexOf(step);
  if (index < 0) return;
  activeStep = index;
  for (const button of document.querySelectorAll("[data-step]")) {
    const selected = button.dataset.step === step;
    button.classList.toggle("is-active", selected);
    if (selected) button.setAttribute("aria-current", "step");
    else button.removeAttribute("aria-current");
  }
  for (const panel of document.querySelectorAll("[data-step-panel]")) {
    panel.hidden = panel.dataset.stepPanel !== step;
  }
  byId("step-count").textContent = `STEP ${index + 1} OF ${steps.length}`;
  byId("previous-step").disabled = index === 0;
  byId("next-step").hidden = index === steps.length - 1;
  if (step === "schedule") updateScheduleFeedback();
  if (step === "final") renderSummary();
}

function renderQuestions() {
  const list = byId("question-list");
  list.replaceChildren();
  questions.forEach((question, index) => {
    const item = document.createElement("li");
    const content = document.createElement("div");
    const typeLabel = document.createElement("span");
    const title = document.createElement("strong");
    const remove = document.createElement("button");
    typeLabel.className = "question-type-label";
    typeLabel.textContent = question.type === "multiple_choice" ? "MULTIPLE CHOICE" : "WRITTEN ANSWER";
    title.textContent = `${index + 1}. ${question.prompt}`;
    remove.type = "button";
    remove.textContent = "Remove";
    remove.setAttribute("aria-label", `Remove question ${index + 1}`);
    remove.addEventListener("click", () => {
      questions.splice(index, 1);
      renderQuestions();
    });
    content.append(typeLabel, title);
    if (question.type === "multiple_choice") {
      const options = document.createElement("ul");
      options.className = "question-options";
      question.options.forEach((option, optionIndex) => {
        if (!option) return;
        const entry = document.createElement("li");
        entry.textContent = `${String.fromCharCode(65 + optionIndex)}. ${option}${optionIndex === question.correctIndex ? " — Correct answer" : ""}`;
        entry.classList.toggle("is-correct", optionIndex === question.correctIndex);
        options.append(entry);
      });
      content.append(options);
    } else {
      const answer = document.createElement("small");
      answer.textContent = `Answer key: ${question.key}`;
      content.append(answer);
    }
    item.append(content, remove);
    list.append(item);
  });
  byId("question-count").textContent = `${questions.length} ${questions.length === 1 ? "question" : "questions"}`;
  byId("question-empty").hidden = questions.length > 0;
}

function renderSummary() {
  const candidates = candidateRoster();
  const incomplete = rosterRows().filter((row) => {
    const name = row.querySelector(".roster-name").value.trim();
    const emailInput = row.querySelector(".roster-email");
    const email = emailInput.value.trim();
    return (name || email) && (!name || !email || (!emailInput.checkValidity() || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(emailInput.value.trim())));
  }).length;
  byId("summary-title").textContent = label(value("assessment-title"));
  byId("summary-duration").textContent = value("assessment-duration") ? `${value("assessment-duration")} minutes` : "Not entered";
  byId("summary-questions").textContent = `${questions.length} added`;
  byId("summary-candidates").textContent = `${candidates.length} ready${incomplete ? `, ${incomplete} incomplete` : ""}`;
  const start = value("schedule-start");
  const end = value("schedule-end");
  byId("summary-schedule").textContent = start || end ? `${formatDate(start)} – ${formatDate(end)}` : "Not set";
  const prohibitedItems = selectedChoices(".prohibited-item");
  const identityPolicy = document.querySelector('input[name="identity-policy"]:checked').value;
  const identityLabel = identityPolicy === "Other ID cards" ? "Other ID cards" : "Fayda ID card";
  byId("summary-rules").textContent = `${prohibitedItems.length} prohibited · ${identityLabel}`;
  const questionList = byId("summary-question-list");
  const candidateList = byId("summary-candidate-list");
  questionList.replaceChildren();
  candidateList.replaceChildren();
  for (const question of questions) {
    const item = document.createElement("li");
    const answer = question.type === "multiple_choice" ? question.options[question.correctIndex] : question.key;
    item.textContent = `${question.prompt} (${question.type === "multiple_choice" ? "multiple choice" : "written answer"}; key: ${answer})`;
    questionList.append(item);
  }
  for (const candidate of candidates) {
    const item = document.createElement("li");
    item.textContent = `${candidate.name} · ${candidate.email}`;
    candidateList.append(item);
  }
  if (!questions.length) questionList.appendChild(document.createElement("li")).textContent = "No questions added.";
  if (!candidates.length) candidateList.appendChild(document.createElement("li")).textContent = "No candidates added.";
  byId("summary-description").textContent = `Description: ${label(value("assessment-description"), "None")}`;
  byId("summary-rules-text").textContent = `Prohibited items: ${prohibitedItems.length ? prohibitedItems.join(", ") : "None selected"}`;
  byId("summary-identity-text").textContent = `Identity requirement: ${identityLabel}`;
  const readiness = byId("review-readiness");
  if (submittedPayload || invitationResult) {
    setFeedback(readiness, "", "");
  } else {
    try {
      invitationPayload();
      setFeedback(readiness, "", "");
    } catch (error) {
      setFeedback(readiness, error.message, "error", { label: "Fix setup", onClick: () => showEditorError(error) });
    }
  }
}

function renderDetail(assessment) {
  const detail = byId("selected-assessment");
  detail.hidden = false;
  detail.dataset.startsAt = assessment.starts_at;
  detail.dataset.endsAt = assessment.ends_at;
  byId("detail-title").textContent = assessment.title;
  byId("detail-description").textContent = assessment.description || "No instructions provided.";
  byId("detail-identity").textContent = assessment.identity_policy === "Other ID cards" ? "Other ID cards" : "Fayda ID card";
  byId("detail-code").textContent = assessment.internal_reference || "Not set";
  byId("detail-duration").textContent = `${assessment.duration_minutes} min`;
  byId("detail-questions").textContent = String(assessment.question_count);
  byId("detail-candidates").textContent = String(assessment.candidates.length);
  byId("detail-start").textContent = formatSavedDate(assessment.starts_at);
  byId("detail-end").textContent = formatSavedDate(assessment.ends_at);
  refreshScheduleStates();

  const questions = Array.isArray(assessment.questions) ? assessment.questions : [];
  byId("detail-question-count").textContent = `${questions.length} ${questions.length === 1 ? "question" : "questions"}`;
  const questionList = byId("detail-question-list");
  questionList.replaceChildren();
  questions.forEach((question, index) => {
    const item = document.createElement("li");
    item.className = "detail-question";
    const head = document.createElement("div");
    head.className = "detail-question-head";
    const number = document.createElement("strong");
    number.textContent = `Question ${index + 1}`;
    const type = document.createElement("span");
    type.textContent = question.type === "multiple_choice" ? "Multiple choice" : "Written answer";
    head.append(number, type);
    const prompt = document.createElement("p");
    prompt.className = "detail-question-prompt";
    prompt.textContent = question.prompt || "";
    item.append(head, prompt);
    if (question.type === "multiple_choice") {
      const options = document.createElement("ol");
      options.className = "detail-question-options";
      (question.options || []).forEach((option, optionIndex) => {
        if (!option) return;
        const choice = document.createElement("li");
        choice.textContent = `${String.fromCharCode(65 + optionIndex)}. ${option}`;
        if (optionIndex === question.correctIndex) {
          choice.className = "is-correct";
          const answerLabel = document.createElement("span");
          answerLabel.textContent = "Correct answer";
          choice.append(answerLabel);
        }
        options.append(choice);
      });
      item.append(options);
    } else {
      const answer = document.createElement("div");
      answer.className = "detail-written-key";
      const label = document.createElement("span");
      label.textContent = "Answer key / expected answer";
      const text = document.createElement("p");
      text.textContent = question.key || "Not provided";
      answer.append(label, text);
      item.append(answer);
    }
    questionList.append(item);
  });

  const table = byId("candidate-table");
  table.replaceChildren();
  if (!assessment.candidates.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 4;
    cell.className = "table-empty";
    cell.textContent = "No candidates assigned.";
    row.append(cell);
    table.append(row);
    return;
  }
  for (const candidate of assessment.candidates) {
    const row = document.createElement("tr");
    const progress = candidate.progress === "disqualified"
      ? `Disqualified (${candidate.disqualification_reason === "connection_lost" ? "connection lost" : "left exam"}) · ${candidate.answered_count}/${assessment.question_count} answered`
      : candidate.progress === "interrupted"
      ? `Interrupted · ${candidate.answered_count}/${assessment.question_count} answered`
      : candidate.progress === "submitted"
      ? `Submitted${candidate.submission_reason === "timeout" ? " (time expired)" : ""} · ${candidate.answered_count}/${assessment.question_count} answered`
      : candidate.progress === "in_progress"
        ? `In progress · question ${candidate.current_question} · ${candidate.answered_count}/${assessment.question_count} answered`
        : "Not started";
    for (const text of [candidate.name, candidate.email, candidate.invitation, progress]) {
      const cell = document.createElement("td");
      cell.textContent = text;
      row.append(cell);
    }
    table.append(row);
  }
}

document.querySelectorAll("[data-tab]").forEach((button) => {
  button.addEventListener("click", () => showTab(button.dataset.tab));
});
document.querySelectorAll("[data-open-tab]").forEach((button) => {
  button.addEventListener("click", () => showTab(button.dataset.openTab));
});
document.querySelectorAll("[data-open-editor]").forEach((button) => {
  button.addEventListener("click", () => showTab("editor"));
});
let discardEditorDialog = null;
byId("new-assessment").addEventListener("click", async () => {
  if (byId("send-invitations").disabled) return;
  const draftHasContent = questions.length || rosterRows().some((row) => row.querySelector(".roster-name").value.trim() || row.querySelector(".roster-email").value.trim()) ||
    selectedChoices(".prohibited-item").length ||
    [...document.querySelectorAll(".mcq-option")].some((input) => input.value.trim()) || [
    "assessment-title", "assessment-duration", "assessment-description", "assessment-reference",
    "schedule-start", "schedule-end", "question-prompt", "question-key",
  ].some((id) => value(id));
  if (discardEditorDialog) return;
  if (draftHasContent) {
    discardEditorDialog = employerNotices.confirm({ id: 'discard-editor', title: 'Start a new assessment?',
      text: 'The current editor will be cleared. Saved assessments and sent invitations are kept.',
      confirmLabel: 'Clear editor', cancelLabel: 'Keep editing', destructive: true });
    const accepted = await discardEditorDialog.result; discardEditorDialog = null;
    if (!accepted || byId('send-invitations').disabled) return;
  }
  resetEditor();
  showTab("editor");
});

function resetEditor() {
  editorFeedback.dispose();
  editorFeedback = employerFeedback.scope("editor");
  for (const id of [
    "assessment-title", "assessment-duration", "assessment-description", "assessment-reference",
    "schedule-start", "schedule-end",
    "question-prompt", "question-key",
  ]) byId(id).value = "";
  document.querySelectorAll(".prohibited-item").forEach((input) => { input.checked = false; });
  document.querySelector('input[name="identity-policy"][value="Fayda ID"]').checked = true;
  document.querySelectorAll(".mcq-option").forEach((input) => { input.value = ""; });
  document.querySelectorAll('input[name="correct-option"]').forEach((input) => { input.checked = false; });
  byId("question-type").value = "written";
  questions.length = 0;
  renderQuestions();
  byId("roster-rows").replaceChildren();
  addRosterRow();
  updateRoster();
  invitationRequestKey = crypto.randomUUID();
  submittedPayload = null;
  invitationResult = null;
  const badge = byId("editor-state");
  badge.textContent = "Unsaved draft";
  delete badge.dataset.tone;
  setFeedback(byId("invite-status"), "", "");
  setFeedback(byId("review-readiness"), "", "");
  setFeedback(byId("schedule-status"), "", "");
  const sendButton = byId("send-invitations");
  sendButton.disabled = false;
  sendButton.textContent = "Confirm and send invitations";
  delete sendButton.dataset.state;
  setEditorLocked(false);
  syncQuestionType();
  showStep("basics");
}
byId("assessments-previous").addEventListener("click", () => { if (assessmentPage > 1) loadAssessments(assessmentPage - 1, null); });
byId("assessments-next").addEventListener("click", () => { if (assessmentPage * 20 < assessmentTotal) loadAssessments(assessmentPage + 1, null); });
document.querySelectorAll("[data-open-editor-step]").forEach((button) => {
  button.addEventListener("click", () => {
    showTab("editor");
    showStep(button.dataset.openEditorStep);
  });
});
document.querySelectorAll("[data-step]").forEach((button) => {
  button.addEventListener("click", () => showStep(button.dataset.step));
});
byId("previous-step").addEventListener("click", () => showStep(steps[activeStep - 1]));
byId("next-step").addEventListener("click", () => {
  if (steps[activeStep] === "schedule" && !updateScheduleFeedback(true)) return;
  showStep(steps[activeStep + 1]);
});
function syncQuestionType() {
  const isMultipleChoice = value("question-type") === "multiple_choice";
  byId("written-answer-fields").hidden = isMultipleChoice;
  byId("multiple-choice-fields").hidden = !isMultipleChoice;
  setFeedback(byId("question-message"), "", "");
  for (const id of ["question-prompt", "question-key", "mcq-option-a", "mcq-option-b", "mcq-option-c", "mcq-option-d"]) fieldError(byId(id), "");
}
byId("question-type").addEventListener("change", syncQuestionType);
byId('add-question').addEventListener('click', () => {
  editorFeedback.clear('question-message');
  const prompt = value('question-prompt');
  const type = value('question-type');
  const rules = [[byId('question-prompt'), input => Boolean(input.value.trim()) && input.value.length <= 5000, 'Enter a question, up to 5,000 characters.']];
  let options, chosen;
  if (type === 'multiple_choice') {
    options = [...document.querySelectorAll('.mcq-option')].map(input => input.value.trim());
    chosen = document.querySelector('input[name="correct-option"]:checked');
    for (const id of ['mcq-option-a', 'mcq-option-b']) rules.push([byId(id), input => Boolean(input.value.trim()), 'Enter an answer option.']);
  } else rules.push([byId('question-key'), input => Boolean(input.value.trim()) && input.value.length <= 5000, 'Enter an answer key, up to 5,000 characters.']);
  let first;
  for (const [input, valid, text] of rules) {
    fieldError(input, valid(input) ? '' : text, valid);
    if (!valid(input)) first ||= input;
  }
  if (first) { first.focus(); return; }
  if (questions.length >= 200) return setFeedback(byId('question-message'), 'An assessment can have up to 200 questions.', 'error');
  let question;
  if (type === 'multiple_choice') {
    const filled = options.filter(Boolean);
    if (new Set(filled.map(option => option.toLocaleLowerCase())).size !== filled.length) {
      return setFeedback(byId('question-message'), 'Each answer option must be different.', 'error');
    }
    if (!chosen || !options[Number(chosen.value)]) {
      setFeedback(byId('question-message'), 'Select a filled option as the correct answer.', 'error');
      document.querySelector('input[name="correct-option"]').focus();
      return;
    }
    question = { type, prompt, options, correctIndex: Number(chosen.value) };
    document.querySelectorAll('.mcq-option').forEach(input => { input.value = ''; });
    chosen.checked = false;
  } else {
    question = { type: 'written', prompt, key: value('question-key') };
    byId('question-key').value = '';
  }
  questions.push(question);
  byId('question-prompt').value = '';
  editorFeedback.clear('question-message');
  renderQuestions();
  byId('question-prompt').focus();
});

byId("logout-button").addEventListener("click", async () => {
  const button = byId("logout-button");
  button.disabled = true;
  try {
    const response = await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
    if (!response.ok) throw new Error("Sign-out failed");
    window.location.assign("/");
  } catch {
    employerNotices.show({ id: "session", target: "#session-message", text: "Could not sign out. Try again.", tone: "error" });
    button.disabled = false;
  }
});

fetch("/api/auth/me", { credentials: "same-origin" })
  .then((response) => response.ok ? response.json() : null)
  .then((user) => {
    if (!user || user.role !== "employer_personnel") {
      window.location.replace("/");
      return;
    }
    byId("account-name").textContent = user.username;
  })
  .catch(() => {
    employerNotices.show({ id: "session", target: "#session-message", text: "Could not confirm your session. Refresh the page.", tone: "error", action: { label: "Refresh", onClick: () => location.reload() } });
  });

addRosterRow();
updateRoster();
showStep("basics");
syncQuestionType();
showTab(tabs.includes(location.hash.slice(1)) ? location.hash.slice(1) : "overview");
