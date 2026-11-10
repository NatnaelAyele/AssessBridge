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
let clearEditorOnDialogClose = false;

function byId(id) { return document.getElementById(id); }
function value(id) { return byId(id).value.trim(); }
function label(text, fallback = "Not entered") { return text || fallback; }
function selectedChoices(selector) { return [...document.querySelectorAll(`${selector}:checked`)].map((input) => input.value); }
function setFeedback(element, message, tone) {
  element.textContent = message;
  element.hidden = !message;
  if (tone) element.dataset.tone = tone;
  else delete element.dataset.tone;
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
    return name && email && emailInput.checkValidity() ? { name, email } : null;
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

  let incomplete = 0;
  for (const row of rosterRows()) {
    const nameInput = row.querySelector(".roster-name");
    const emailInput = row.querySelector(".roster-email");
    const name = nameInput.value.trim();
    const email = emailInput.value.trim();
    const hasContent = Boolean(name || email);
    if (hasContent && (!name || !email || !emailInput.checkValidity())) incomplete += 1;
    nameInput.setAttribute("aria-invalid", String(Boolean(email && !name)));
    emailInput.setAttribute("aria-invalid", String(Boolean(name && (!email || !emailInput.checkValidity()))));
    row.querySelector(".roster-remove").hidden = !hasContent;
  }
  setFeedback(byId("roster-status"), incomplete ? `${incomplete} ${incomplete === 1 ? "row needs" : "rows need"} a name and valid email.` : "", "error");
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
    if (required) message = "Enter both access window dates in East Africa Time.";
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
  for (const id of ["schedule-start", "schedule-end"]) {
    byId(id).setAttribute("aria-invalid", String(id === invalidField));
  }
  setFeedback(byId("schedule-status"), message, tone);
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
  const message = byId("assessment-list-message");
  let filters;
  try {
    filters = assessmentFilters();
    setFeedback(byId("assessment-filter-error"), "", "");
  } catch (error) {
    setFeedback(byId("assessment-filter-error"), error.message, "error");
    return;
  }
  filters.set("page", String(page));
  message.dataset.tone = "neutral";
  message.textContent = "Loading saved assessments…";
  try {
    const response = await fetch(`/api/employer/assessments?${filters}`, { credentials: "same-origin" });
    if (!response.ok) throw new Error("Could not load saved assessments.");
    const data = await response.json();
    if (request !== assessmentListRequest) return;
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
    message.textContent = data.total ? "" : filters.size > 1 ? "No assessments match these filters." : "No saved assessments yet. Create one in the editor and send invitations to save it.";
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
    if (request !== assessmentListRequest) return;
    message.textContent = error.message || "Could not load saved assessments.";
    message.dataset.tone = "error";
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
  const message = byId("assessment-list-message");
  message.dataset.tone = "neutral";
  message.textContent = "Loading assessment details…";
  try {
    const response = await fetch(`/api/employer/assessments/${id}`, { credentials: "same-origin" });
    if (!response.ok) throw new Error("Could not load this assessment.");
    const assessment = await response.json();
    if (request !== detailRequest) return;
    selectedAssessmentId = id;
    document.querySelectorAll(".assessment-list-item").forEach((button) => {
      button.classList.toggle("is-selected", Number(button.dataset.assessmentId) === id);
    });
    renderDetail(assessment);
    message.textContent = "";
    if (scrollToDetail) byId("selected-assessment").scrollIntoView({ behavior: "instant", block: "start" });
  } catch (error) {
    if (request === detailRequest) {
      message.textContent = error.message || "Could not load this assessment.";
      message.dataset.tone = "error";
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
    return (name || emailInput.value.trim()) && (!name || !emailInput.value.trim() || !emailInput.checkValidity());
  });
  if (!title) throw new Error("Enter an assessment title in Basics & duration.");
  if (/[\r\n]/.test(title)) throw new Error("The assessment title must be one line.");
  if (!Number.isInteger(duration) || duration < 1 || duration > 480) throw new Error("Enter a duration from 1 to 480 minutes.");
  if (!questions.length) throw new Error("Add at least one question and answer key.");
  if (incomplete) throw new Error("Complete or remove every candidate roster row.");
  if (!candidates.length) throw new Error("Add at least one candidate with a valid email.");
  if (new Set(candidates.map((candidate) => candidate.email.toLowerCase())).size !== candidates.length) throw new Error("Each candidate email must be unique.");
  const start = value("schedule-start");
  const end = value("schedule-end");
  if (!start || !end) throw new Error("Enter both access window dates in East Africa Time.");
  const startDate = new Date(`${start}+03:00`);
  const endDate = new Date(`${end}+03:00`);
  if (Number.isNaN(startDate.getTime()) || startDate <= new Date()) throw new Error("Choose a future access window start.");
  if (Number.isNaN(endDate.getTime()) || endDate <= startDate) throw new Error("The access window must close after it opens.");
  if (endDate - startDate < duration * 60 * 1000) throw new Error("The access window must allow the full assessment duration.");
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

byId("send-invitations").addEventListener("click", async () => {
  const button = byId("send-invitations");
  const status = byId("invite-status");
  if (button.disabled) return;
  let payload;
  try {
    payload = invitationPayload();
    if (submittedPayload && JSON.stringify(payload) !== submittedPayload) {
      throw new Error("This invitation request was already submitted. Restore its details before retrying.");
    }
  } catch (error) {
    setFeedback(status, error.message, "error");
    setFeedback(byId("review-readiness"), "", "");
    updateScheduleFeedback();
    return;
  }
  button.disabled = true;
  button.textContent = "Sending…";
  button.dataset.state = "sending";
  button.setAttribute("aria-busy", "true");
  setFeedback(status, "", "");
  try {
    const response = await fetch("/api/employer/invitations", {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) {
      if (response.status === 503 && result.detail === "Email delivery is not configured") {
        throw new Error("Email delivery is not configured yet. No invitations were sent.");
      }
      throw new Error(typeof result.detail === "string" ? result.detail : "Could not send invitations. Check the assessment details and try again.");
    }
    submittedPayload = JSON.stringify(payload);
    invitationResult = result;
    setEditorLocked(true);
    const badge = byId("editor-state");
    badge.textContent = "Saved assessment";
    badge.dataset.tone = "success";
    selectedAssessmentId = result.assessment_id;
    loadAssessments(1, result.assessment_id);
    if (result.sent === payload.candidates.length && !result.failed && !result.pending) {
      button.textContent = "Sent";
      button.dataset.state = "sent";
      setFeedback(status, "", "");
      setFeedback(byId("review-readiness"), "", "");
      byId("send-confirmation-message").textContent = `“${payload.title}” is saved. Calendar invitations were sent to all ${result.sent} candidates.`;
      clearEditorOnDialogClose = true;
      byId("send-confirmation").showModal();
    } else {
      button.textContent = result.failed ? "Retry failed invitations" : "Retry invitations";
      button.dataset.state = "error";
      setFeedback(status, `Assessment saved, but only ${result.sent} of ${payload.candidates.length} invitations were sent. ${result.failed} failed and ${result.pending} remain pending. Retry to send the remaining invitations.`, "error");
      setFeedback(byId("review-readiness"), "", "");
    }
  } catch (error) {
    button.textContent = invitationResult?.failed || invitationResult?.pending ? "Retry failed invitations" : "Confirm and send invitations";
    button.dataset.state = "error";
    setFeedback(status, error.message || "Could not send invitations. Please try again.", "error");
  } finally {
    button.removeAttribute("aria-busy");
    if (!invitationResult || invitationResult.failed || invitationResult.pending) button.disabled = false;
  }
});

byId("close-send-confirmation").addEventListener("click", () => byId("send-confirmation").close());
byId("send-confirmation").addEventListener("close", () => {
  if (!clearEditorOnDialogClose) return;
  clearEditorOnDialogClose = false;
  resetEditor();
});
byId("view-sent-assessment").addEventListener("click", () => {
  byId("send-confirmation").close();
  showTab("detail");
});

function showTab(tab) {
  if (!tabs.includes(tab)) return;
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
    return (name || email) && (!name || !email || !emailInput.checkValidity());
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
  if (!questions.length) questionList.append(document.createElement("li")).textContent = "No questions added.";
  if (!candidates.length) candidateList.append(document.createElement("li")).textContent = "No candidates added.";
  byId("summary-description").textContent = `Description: ${label(value("assessment-description"), "None")}`;
  byId("summary-rules-text").textContent = `Prohibited items: ${prohibitedItems.length ? prohibitedItems.join(", ") : "None selected"}`;
  byId("summary-identity-text").textContent = `Identity requirement: ${identityLabel}`;
  const readiness = byId("review-readiness");
  if (invitationResult) {
    setFeedback(readiness, "", "");
  } else {
    try {
      invitationPayload();
      setFeedback(readiness, "", "");
    } catch (error) {
      setFeedback(readiness, `Before sending: ${error.message}`, "error");
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
byId("new-assessment").addEventListener("click", () => {
  const draftHasContent = questions.length || rosterRows().some((row) => row.querySelector(".roster-name").value.trim() || row.querySelector(".roster-email").value.trim()) ||
    selectedChoices(".prohibited-item").length ||
    [...document.querySelectorAll(".mcq-option")].some((input) => input.value.trim()) || [
    "assessment-title", "assessment-duration", "assessment-description", "assessment-reference",
    "schedule-start", "schedule-end", "question-prompt", "question-key",
  ].some((id) => value(id));
  if (draftHasContent && !window.confirm("Clear the current editor and start a new assessment?")) return;
  resetEditor();
  showTab("editor");
});

function resetEditor() {
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
  byId("question-message").hidden = true;
}
byId("question-type").addEventListener("change", syncQuestionType);
byId("add-question").addEventListener("click", () => {
  const prompt = value("question-prompt");
  const type = value("question-type");
  const message = byId("question-message");
  if (!prompt) {
    message.textContent = "Enter a question prompt first.";
    message.hidden = false;
    byId("question-prompt").focus();
    return;
  }

  let question;
  if (type === "multiple_choice") {
    const options = [...document.querySelectorAll(".mcq-option")].map((input) => input.value.trim());
    const chosen = document.querySelector('input[name="correct-option"]:checked');
    const filled = options.filter(Boolean);
    if (!options[0] || !options[1]) {
      message.textContent = "Enter at least options A and B.";
      message.hidden = false;
      return;
    }
    if (new Set(filled.map((option) => option.toLocaleLowerCase())).size !== filled.length) {
      message.textContent = "Each answer option must be different.";
      message.hidden = false;
      return;
    }
    if (!chosen || !options[Number(chosen.value)]) {
      message.textContent = "Mark a filled option as the correct answer.";
      message.hidden = false;
      return;
    }
    question = { type, prompt, options, correctIndex: Number(chosen.value) };
    document.querySelectorAll(".mcq-option").forEach((input) => { input.value = ""; });
    chosen.checked = false;
  } else {
    const key = value("question-key");
    if (!key) {
      message.textContent = "Enter an answer key or expected answer.";
      message.hidden = false;
      byId("question-key").focus();
      return;
    }
    question = { type: "written", prompt, key };
    byId("question-key").value = "";
  }

  questions.push(question);
  byId("question-prompt").value = "";
  message.hidden = true;
  renderQuestions();
  byId("question-prompt").focus();
});

byId("logout-button").addEventListener("click", async () => {
  const button = byId("logout-button");
  button.disabled = true;
  try {
    const response = await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
    if (!response.ok) throw new Error("Sign-out failed");
    window.location.assign("/");
  } catch {
    byId("session-message").textContent = "Could not sign out. Please try again.";
    byId("session-message").hidden = false;
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
    byId("session-message").textContent = "Could not confirm your session. Please refresh the page.";
    byId("session-message").hidden = false;
  });

addRosterRow();
updateRoster();
showStep("basics");
syncQuestionType();
showTab(tabs.includes(location.hash.slice(1)) ? location.hash.slice(1) : "overview");
