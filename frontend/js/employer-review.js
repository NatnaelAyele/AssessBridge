/* Candidate session review: vanilla JavaScript, server-authorized decisions. */
window.employerReview = (() => {
  const el = (id) => document.getElementById(id);
  const titles = { keep_score: "Keep score", change_score: "Change score", disqualify: "Disqualify",
    interrupted: "Interrupted", submitted: "Submitted", disqualified: "Disqualified", in_progress: "In progress", not_started: "Not started",
    reviewed: "Reviewed", awaiting_review: "Awaiting review", not_ready: "Not ready",
    eligible: "Eligible", supports_violation: "Supports a rule violation", dismissed: "Dismissed", inconclusive: "Inconclusive" };
  const eventTitles = { identity_mismatch: "Possible identity mismatch", face_absent: "Face absent",
    face_unusable: "Face could not be checked", additional_person: "Additional person detected",
    mobile_phone: "Phone visible", book: "Book visible", tablet: "Tablet visible", calculator: "Calculator visible",
    head_turned: "Sustained head turn", head_down: "Downward head orientation",
    repeated_look_away: "Repeated head-orientation changes", speech: "Speech activity", repeated_speech: "Repeated speech activity",
    camera_obscured: "Camera image obscured", suspected_frozen_video: "Suspected frozen video",
    framing_changed: "Framing changed", audio_clipping: "Audio clipping", digital_audio_silence: "Digital audio silence",
    identity_reference_unavailable: "Identity reference unavailable", video_analysis_unavailable: "Video analysis unavailable",
    audio_analysis_unavailable: "Audio analysis unavailable", video_coverage_gap: "Video coverage gap", audio_coverage_gap: "Audio coverage gap" };
  let assessments = null, initPromise = null, cards = [], cardsError = false, assessmentId = null;
  let report = null, monitoring = null, recording = null;
  let fullParts = [], fullIndex = 0, mainControls = null, inlinePlayback = null;
  let openedInvitationId = null;
  let sessionReadOnly = false;
  let loading = 0, cardLoading = 0, pendingDecision = null, busy = false, dirty = false, stale = false;
  const findingDrafts = new Map();
  const connectivityDrafts = new Map();
  const messageTimers = new Map();
  let currentTab = "overview";

  function node(tag, cls, text) {
    const element = document.createElement(tag);
    if (cls) element.className = cls;
    if (text !== undefined) element.textContent = text;
    return element;
  }
  function message(id, text = "", error = false, dismissAfter = 0) {
    const target = el(id);
    if (messageTimers.has(id)) { clearTimeout(messageTimers.get(id)); messageTimers.delete(id); }
    target.textContent = text; target.hidden = !text;
    target.dataset.tone = error ? "error" : "success";
    if (text && !error && dismissAfter > 0) {
      const timer = window.setTimeout(() => { target.textContent = ""; target.hidden = true; messageTimers.delete(id); }, dismissAfter);
      messageTimers.set(id, timer);
    }
  }
  function date(value) {
    return value ? new Date(value).toLocaleString("en-GB", { timeZone: "Africa/Nairobi", dateStyle: "medium", timeStyle: "short" }) + " EAT" : "—";
  }
  function time(ms) {
    const seconds = Math.max(0, Math.round(ms / 1000));
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  }
  function score(value, possible) {
    return value === null || value === undefined ? "Grading incomplete" : `${value} / ${possible} (${Math.round(100 * value / possible)}%)`;
  }
  function badge(value, bad = false) { return node("span", `review-badge${bad ? " is-warning" : ["eligible", "reviewed", "keep_score", "change_score"].includes(value) ? " is-success" : ""}`, titles[value] || value); }
  function facts(target, entries) {
    target.replaceChildren();
    for (const [label, value] of entries) {
      const box = node("div", "review-fact"); box.append(node("span", "", label), node("strong", "", value)); target.append(box);
    }
  }
  async function api(url, options = {}) {
    const response = await fetch(url, { credentials: "same-origin", ...options });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = typeof data.detail === "string" ? data.detail : Array.isArray(data.detail) ? data.detail.map((item) => item.msg).join("; ") : "Request failed. Please try again.";
      const error = new Error(detail); error.status = response.status; throw error;
    }
    return data;
  }
  function base() { return `/api/employer/assessments/${assessmentId}/invitations/${report.invitation_id}`; }
  function pauseMedia() { el("review-video").pause(); inlinePlayback?.video.pause(); }
  function closeInline() {
    if (!inlinePlayback) return;
    clearInterval(inlinePlayback.timer);
    inlinePlayback.video.pause(); inlinePlayback.video.removeAttribute("src"); inlinePlayback.video.load();
    inlinePlayback.wrap.remove(); inlinePlayback = null;
    document.querySelectorAll(".review-event.is-selected").forEach((card) => card.classList.remove("is-selected"));
  }
  function clearMedia() {
    closeInline(); el("review-video").onloadedmetadata = null;
    pauseMedia(); el("review-video").removeAttribute("src"); el("review-video").load();
    delete el("review-video").dataset.part;
    el("review-recording-wrap").hidden = true; el("review-recording-controls").replaceChildren();
    el("review-recording-toggle").textContent = "Open recording"; fullParts = []; fullIndex = 0; mainControls = null;
    el("review-reference-wrap").hidden = true; el("review-reference-photo").removeAttribute("src");
    el("review-reference-photo").hidden = true; el("review-reference-button").textContent = "Open reference photo";
  }
  function setSessionMode(readOnly) {
    sessionReadOnly = readOnly;
    const locked = readOnly || !!report?.published;
    el("review-decision-panel").hidden = locked;
    el("review-session").querySelector(".review-layout").classList.toggle("is-read-only", locked);
    el("review-back").textContent = readOnly ? "Back to results" : "Back to candidates";
  }
  function closeReadOnlyReport() {
    if (!sessionReadOnly) return;
    ++loading; clearMedia(); report = null; dirty = false; findingDrafts.clear(); connectivityDrafts.clear();
    el("review-session").hidden = true;
    el("review-session-home").after(el("review-session"));
    el("results-case-host").hidden = true; el("results-list").hidden = false;
    setSessionMode(false);
  }
  function allowDiscard() { return (!dirty && !findingDrafts.size && !connectivityDrafts.size) || window.confirm("Reloading or leaving will discard unsaved grades and reasons. Continue?"); }

  function examWindow(item) {
    const now = Date.now();
    return now < Date.parse(item.starts_at) ? "Scheduled" : now < Date.parse(item.ends_at) ? "Open" : "Closed";
  }
  function highlight(target, value, search) {
    const source = String(value || ""), term = search.toLocaleLowerCase();
    if (!term) { target.textContent = source; return; }
    const lower = source.toLocaleLowerCase();
    let position = 0;
    while (position < source.length) {
      const index = lower.indexOf(term, position);
      if (index < 0) { target.append(document.createTextNode(source.slice(position))); break; }
      target.append(document.createTextNode(source.slice(position, index)));
      target.append(node("mark", "", source.slice(index, index + search.length)));
      position = index + search.length;
    }
  }
  function examFilters() {
    const range = {};
    for (const field of ["candidates", "questions"]) {
      for (const bound of ["min", "max"]) {
        const input = el(`review-exam-${field}-${bound}`);
        if (input.value && (!input.checkValidity() || !/^\d+$/.test(input.value))) throw Error("Enter whole numbers from 0 to 200 in the count filters.");
        range[`${field}_${bound}`] = input.value === "" ? null : Number(input.value);
      }
      if (range[`${field}_min`] !== null && range[`${field}_max`] !== null && range[`${field}_min`] > range[`${field}_max`]) {
        throw Error("A minimum count cannot be greater than its maximum.");
      }
    }
    return range;
  }
  function renderExamList() {
    const list = el("review-exam-list");
    let range;
    try { range = examFilters(); message("review-exam-error"); }
    catch (error) { message("review-exam-error", error.message, true); return; }
    const search = el("review-exam-search").value.trim(), lower = search.toLocaleLowerCase();
    const status = el("review-exam-status").value;
    const matching = (assessments || []).filter((item) => {
      const invited = Number(item.sent_count), questions = Number(item.question_count);
      return `${item.title} ${item.internal_reference || ""}`.toLocaleLowerCase().includes(lower)
        && (status === "all" || examWindow(item).toLocaleLowerCase() === status)
        && (range.candidates_min === null || invited >= range.candidates_min)
        && (range.candidates_max === null || invited <= range.candidates_max)
        && (range.questions_min === null || questions >= range.questions_min)
        && (range.questions_max === null || questions <= range.questions_max);
    });
    list.replaceChildren();
    el("review-exam-total").textContent = `${matching.length} ${matching.length === 1 ? "assessment" : "assessments"}`;
    for (const item of matching) {
      const button = node("button", "review-exam-item"); button.type = "button";
      const name = node("strong"), code = node("span", "review-exam-code"), state = node("span", "review-exam-window", examWindow(item));
      const invited = node("span", "review-exam-invited", `${item.sent_count} ${item.sent_count === 1 ? "candidate invited" : "candidates invited"}`);
      highlight(name, item.title, search); highlight(code, item.internal_reference || "Not set", search);
      state.dataset.state = state.textContent.toLocaleLowerCase();
      button.append(name, code, state, invited);
      button.addEventListener("click", () => openExam(item));
      list.append(button);
    }
    if (!matching.length) list.append(node("p", "review-empty", assessments?.length ? "No assessments match these filters." : "No assessments have been created yet."));
  }
  function renderQueueExamHeader() {
    const item = (assessments || []).find((assessment) => assessment.id === assessmentId);
    el("review-queue-title").textContent = item?.title || "Assessment";
    el("review-queue-code").textContent = `Exam code: ${item?.internal_reference || "Not set"}`;
  }
  async function openExam(item) {
    if (busy || !allowDiscard()) return;
    ++loading; clearMedia(); report = null; dirty = false; findingDrafts.clear(); connectivityDrafts.clear();
    assessmentId = item.id;
    el("results-assessment").value = String(assessmentId);
    renderQueueExamHeader();
    el("review-search").value = ""; el("review-filter").value = "all"; el("review-attempt-filter").value = "all";
    el("review-exams").hidden = true; el("review-session").hidden = true; el("review-queue").hidden = false;
    el("review-queue").scrollIntoView({ behavior: "instant", block: "start" });
    await loadCards();
  }
  async function initialize(force = false) {
    if (initPromise) return initPromise;
    if (assessments && !force) return;
    initPromise = (async () => {
      const all = [];
      for (let page = 1; ; page += 1) {
        const result = await api(`/api/employer/assessments?page=${page}`); all.push(...result.items);
        if (all.length >= result.total || !result.items.length) break;
      }
      assessments = all;
      renderExamList();
      const select = el("results-assessment"); select.replaceChildren(new Option("Choose assessment", ""));
      for (const item of all) select.add(new Option(item.internal_reference ? `${item.title} · ${item.internal_reference}` : item.title, item.id));
      if (all.length) {
        if (!all.some((item) => item.id === assessmentId)) assessmentId = all[0].id;
        select.value = String(assessmentId);
        renderQueueExamHeader();
        await loadCards();
      } else { assessmentId = null; cards = []; cardsError = false; renderCards(); renderResults(); }
    })();
    try { await initPromise; } finally { initPromise = null; }
  }
  async function activate(tab = "review") {
    if (sessionReadOnly) closeReadOnlyReport();
    if (tab !== "review") pauseMedia();
    try { await initialize(true); }
    catch (error) { message("review-exam-error", error.message, true); message("review-queue-message", error.message, true); message("results-message", error.message, true); }
  }
  async function loadCards() {
    if (!assessmentId) { cards = []; cardsError = false; renderCards(); renderResults(); return; }
    const serial = ++cardLoading;
    message("review-queue-message", "Loading candidates…");
    message("results-message");
    el("results-count").textContent = "Loading...";
    el("review-results-table").replaceChildren();
    try {
      const result = await api(`/api/employer/assessments/${assessmentId}/reviews`);
      if (serial !== cardLoading) return;
      cards = result.candidates; cardsError = false; renderCards(); renderResults(); message("review-queue-message"); message("results-message");
    } catch (error) {
      if (serial !== cardLoading) return;
      cards = []; cardsError = true; renderCards(); renderResults();
      message("review-queue-message", error.message, true); message("results-message", error.message, true);
    }
  }
  function renderCards() {
    const query = el("review-search").value.trim().toLocaleLowerCase();
    const filter = el("review-filter").value, state = el("review-attempt-filter").value;
    const filtered = cards.filter((card) => `${card.name} ${card.email}`.toLocaleLowerCase().includes(query)
      && (state === "all" || card.attempt_state === state)
      && (filter === "all" || card.review_state === filter || (filter === "events" && card.event_count > 0) || (filter === "coverage" && card.coverage_attention)));
    const list = el("review-candidate-cards"); list.replaceChildren();
    el("review-card-count").textContent = `${filtered.length} of ${cards.length} candidates`;
    for (const card of filtered) {
      const article = node("article", "card review-candidate-card");
      const heading = node("div", "review-card-heading"); heading.append(node("h3", "", card.name), badge(card.attempt_state, card.attempt_state === "disqualified"));
      article.append(heading, node("p", "review-muted", card.email));
      const badges = node("div", "review-badges"); badges.append(badge(card.review_state));
      if (card.decision) badges.append(badge(card.decision.action, card.outcome === "disqualified"));
      article.append(badges, node("p", "review-card-score", card.has_session ? score(card.score, card.possible) : "No attempt yet"));
      article.append(node("p", "review-muted", `${card.event_count} observations${card.coverage_attention ? " · Monitoring needs attention" : ""}`));
      const button = node("button", "secondary-button", card.has_session ? "Open session" : "Not started");
      button.type = "button"; button.disabled = !card.has_session; button.addEventListener("click", () => openSession(card.invitation_id));
      article.append(button); list.append(article);
    }
    if (!filtered.length) list.append(node("p", "review-empty", "No candidates match these filters."));
  }
  function renderResults() {
    const table = el("review-results-table"); table.replaceChildren();
    el("results-count").textContent = `${cards.length} ${cards.length === 1 ? "candidate" : "candidates"}`;
    for (const card of cards) {
      const row = node("tr");
      const candidate = node("td", "results-candidate"); candidate.dataset.label = "Candidate";
      const person = node("div", "results-person"); person.append(node("strong", "", card.name), node("span", "review-muted", card.email)); candidate.append(person);
      const attempt = node("td", "", titles[card.attempt_state] || "Not started"); attempt.dataset.label = "Exam status";
      const completed = card.attempt_state === "submitted";
      const automatic = node("td", "", completed && card.objective_possible > 0 ? `${card.objective_earned} / ${card.objective_possible}` : "—"); automatic.dataset.label = "MCQ score";
      const final = node("td", "results-final"); final.dataset.label = "Final result";
      const finalValue = node("div", "results-value");
      if (card.outcome === "disqualified") {
        finalValue.append(node("strong", "results-disqualified", "Disqualified"));
        finalValue.append(node("span", "review-muted", card.attempt_state === "disqualified" ? "Exam ended early" : "Reviewer decision"));
      } else if (card.decision) {
        finalValue.append(node("strong", "", score(card.score, card.possible)));
        finalValue.append(node("span", "review-muted", card.decision.action === "change_score" ? "Score changed" : "Score kept"));
      } else {
        finalValue.append(node("span", "review-muted", card.attempt_state === "submitted" ? "Awaiting review" : "—"));
      }
      final.append(finalValue);
      const action = node("td", "results-action"); action.dataset.label = "Case report";
      if (card.has_session) {
        const button = node("button", "secondary-button", "View case"); button.type = "button";
        button.addEventListener("click", () => openSession(card.invitation_id, false, true));
        action.append(button);
      } else action.textContent = "—";
      row.append(candidate, attempt, automatic, final, action); table.append(row);
    }
    if (!cards.length) { const row = node("tr"); const cell = node("td", "table-empty", cardsError ? "Results unavailable." : assessmentId ? "No candidates invited." : assessments?.length ? "Choose an exam." : "No exams yet."); cell.colSpan = 5; row.append(cell); table.append(row); }
  }
  async function monitoringReport(url) {
    const result = await api(url); const events = [...result.events]; let after = result.next_after_id;
    while (result.events.length && events.length % 500 === 0 && after) {
      const nextUrl = new URL(url, location.origin); nextUrl.searchParams.set("after_id", after);
      const next = await api(nextUrl.pathname + nextUrl.search);
      if (!next.events.length) break;
      events.push(...next.events); after = next.next_after_id;
      if (next.events.length < 500) break;
    }
    return { ...result, events };
  }
  async function openSession(invitationId, reloading = false, readOnly = sessionReadOnly) {
    if (busy || (!reloading && !allowDiscard())) return;
    const serial = ++loading;
    openedInvitationId = invitationId;
    clearMedia(); report = null; stale = false; dirty = false; findingDrafts.clear(); connectivityDrafts.clear(); pendingDecision = null;
    setSessionMode(readOnly);
    if (readOnly) {
      el("results-list").hidden = true; el("results-case-host").hidden = false;
      el("results-case-host").append(el("review-session"));
    } else {
      el("review-session-home").after(el("review-session"));
    }
    el("review-exams").hidden = true; el("review-queue").hidden = true; el("review-session").hidden = false;
    el("review-session").scrollIntoView({ behavior: "instant", block: "start" });
    el("review-session").querySelector(".review-layout").hidden = true;
    message("review-session-message", "Loading session report…");
    const url = `/api/employer/assessments/${assessmentId}/invitations/${invitationId}`;
    try {
      const [detail, mon, media] = await Promise.all([
        api(`${url}/review`), monitoringReport(`${url}/monitoring?limit=500`),
        api(`${url}/recording`).catch((error) => {
          if (error.status === 404) return { parts: [], status: "unavailable" };
          throw error;
        }),
      ]);
      if (serial !== loading) return;
      report = detail; monitoring = mon; recording = media;
      renderSession(); el("review-session").querySelector(".review-layout").hidden = false;
      message("review-session-message");
      selectTab(reloading ? currentTab : "overview");
    } catch (error) { if (serial === loading) message("review-session-message", error.message, true); }
  }
  function selectTab(tab) {
    currentTab = tab;
    for (const button of document.querySelectorAll("[data-review-tab]")) {
      const selected = button.dataset.reviewTab === tab;
      button.setAttribute("aria-selected", String(selected)); button.tabIndex = selected ? 0 : -1;
    }
    for (const pane of document.querySelectorAll("[data-review-pane]")) pane.hidden = pane.dataset.reviewPane !== tab;
    if (tab !== "integrity") pauseMedia();
  }
  function renderSession() {
    setSessionMode(sessionReadOnly);
    el("review-publication-lock").hidden = !report.published;
    el("review-assessment-title").textContent = report.title;
    el("review-candidate-name").textContent = report.name; el("review-candidate-email").textContent = report.email;
    window.renderConnectivitySummary(el("review-connectivity"), report, () => selectTab("connectivity"));
    el("review-session-badges").replaceChildren(badge(report.attempt_state, report.system_disqualification_locked), badge(report.decision ? "reviewed" : "awaiting_review"), badge(report.outcome, report.outcome === "disqualified"));
    const scoreValue = report.decision?.score ?? report.scores.calculated_score;
    const endMethod = report.system_disqualification_locked
      ? report.disqualification_reason === "connection_lost" ? "Connection lost" : "Left the exam"
      : report.attempt_state === "interrupted" ? "Recovery limit reached"
      : report.submission_reason === "timeout" ? "Time expired"
      : report.submission_reason === "manual" ? "Submitted by candidate" : "In progress";
    updateTimeUsed();
    facts(el("review-overview-facts"), [["Scheduled window", `${date(report.starts_at)} – ${date(report.ends_at)}`],
      ["Started", date(report.started_at)], ["Ended", date(report.ended_at)],
      ["How exam ended", endMethod],
      ["Automatic multiple choice grade", report.scores.objective_possible ? `${report.scores.objective_earned} / ${report.scores.objective_possible}` : "—"],
      ["Final total grade", report.outcome === "disqualified" ? "Disqualified" : report.decision ? score(scoreValue, report.scores.possible) : report.attempt_state !== "in_progress" ? "Awaiting review" : "In progress"],
      ["Identity check", report.identity?.status === "passed" ? "Passed" : report.identity?.status === "failed" ? "Failed" : "Not checked"]]);
    renderCoverage(); renderHistory(); renderQuestions(); renderEvents(); renderFindingHistory(); renderRecording(); renderConnectivity();
    el("review-policy-lock").hidden = !report.system_disqualification_locked;
    el("review-decision-state").textContent = report.decision ? `${titles[report.decision.action]} · ${date(report.decision.created_at)}` : "No disposition recorded.";
    el("review-reviewer").textContent = el("account-name").textContent;
    el("review-action").value = ""; el("review-score").value = ""; el("review-score").max = report.scores.possible;
    el("review-reason").value = "";
    for (const option of el("review-action").options) option.disabled = report.system_disqualification_locked && ["keep_score", "change_score"].includes(option.value);
    actionChanged();
  }
  function updateTimeUsed() {
    if (!report?.started_at) return;
    const allowed = report.duration_minutes * 60000;
    const end = report.ended_at ? Date.parse(report.ended_at) : Date.now();
    const used = Math.max(0, Math.min(allowed, end - Date.parse(report.started_at)));
    const percent = allowed ? Math.round(100 * used / allowed) : 0;
    el("review-time-used").textContent = `${time(used)} of ${time(allowed)}`;
    el("review-time-progress").style.width = `${percent}%`;
    el("review-time-progress").parentElement.setAttribute("aria-valuenow", String(percent));
  }
  function renderCoverage() {
    const expected = Math.max(0, Number(monitoring.expected_duration_ms || 0));
    const video = (monitoring.coverage || []).find((item) => item.stream === "video");
    const audio = (monitoring.coverage || []).find((item) => item.stream === "audio");
    const checked = (value) => `${time(Math.min(expected, Math.max(0, Number(value || 0))))} of ${time(expected)}`;
    facts(el("review-coverage"), [["Video checked", checked(video?.analysed_ms)],
      ["Audio checked", checked(audio?.analysed_ms)],
      ["Identity compared", checked(video?.identity_checked_ms)],
      ["Observations", String(monitoring.events.length)]]);
    const status = monitoring.status === "analysed" ? "Finished" : monitoring.status === "processing" ? monitoring.worker_online === false ? "Delayed" : "In progress" : monitoring.status === "not_monitored" ? "Not available" : "Incomplete";
    el("review-processing").textContent = `Analysis status: ${status}`;
  }
  function renderHistory() {
    const target = el("review-decision-history"); target.replaceChildren();
    for (const [index, item] of report.history.entries()) {
      const article = node("article", "review-history-item");
      const heading = item.action === "disqualify" ? "Disqualified" : item.action === "change_score" ? "Changed score" : "Kept score";
      const title = node("strong", "", heading);
      if (index === 0) title.append(node("span", "review-latest", "Latest"));
      article.append(title);
      if (item.action !== "disqualify") article.append(node("p", "review-history-score", `Final total grade: ${score(item.score, report.scores.possible)}`));
      article.append(node("p", "review-muted", `Reviewed by ${item.reviewer} · ${date(item.created_at)}`),
        node("span", "review-history-reason-label", "Reason"), node("p", "review-preserve", item.reason));
      target.append(article);
    }
    if (!report.history.length) target.append(node("p", "review-muted", "No review decision has been recorded."));
  }
  function renderQuestions() {
    const written = report.scores.questions.filter((question) => question.type === "written");
    const graded = written.filter((question) => question.grading === "manual");
    const earned = Math.round(graded.reduce((total, question) => total + Number(question.earned || 0), 0) * 100) / 100;
    const writtenGrade = !written.length ? "—" : report.scores.written_pending && !graded.length ? "Awaiting grading"
      : `${earned} / ${written.length}${report.scores.written_pending ? " (partial)" : ""}`;
    const finalGrade = report.outcome === "disqualified" ? "Disqualified" : report.decision
      ? score(report.decision.score, report.scores.possible) : report.attempt_state !== "in_progress" ? "Awaiting review" : "In progress";
    facts(el("review-score-summary"), [["Automatic multiple choice grade", report.scores.objective_possible ? `${report.scores.objective_earned} / ${report.scores.objective_possible}` : "—"],
      ["Manual written question grade", writtenGrade],
      ["Final total grade", finalGrade],
      ["Written responses awaiting grading", String(report.scores.written_pending)]]);
    const list = el("review-question-list"); list.replaceChildren();
    for (const question of report.scores.questions) {
      const item = node("article", "card section-card review-question");
      item.append(node("h3", "", `${question.index + 1}. ${question.prompt}`));
      if (question.type === "multiple_choice") {
        const options = node("ol", "review-options"); options.type = "A";
        question.options.forEach((option, index) => {
          if (!option) return;
          const entry = node("li", "", option);
          if (index === question.answer) entry.append(node("span", "review-option-label", "Candidate answer"));
          if (index === question.correct_index) entry.append(node("span", "review-correct-label", "Correct answer"));
          options.append(entry);
        });
        item.append(options, node("p", "", question.answer === null ? "Unanswered · 0 / 1" : `Automatically marked: ${question.earned} / 1`));
      } else {
        item.append(node("strong", "", "Candidate answer"), node("p", "review-answer review-preserve", question.answer || "Unanswered"),
          node("strong", "", "Marking reference"), node("p", "review-preserve", question.key || "No reference supplied"));
        if (question.grading !== "unanswered") {
          if (sessionReadOnly || report.published) {
            item.append(node("strong", "", "Awarded marks"),
              node("p", "", question.earned === null ? "Awaiting grading" : `${question.earned} / 1`));
            if (question.grade_reason) item.append(node("strong", "", "Grading explanation"), node("p", "review-preserve", question.grade_reason));
            list.append(item); continue;
          }
          const gradeLabel = node("label", "", "Awarded marks (0–1)"); const input = node("input");
          input.type = "number"; input.min = "0"; input.max = "1"; input.step = "0.01"; input.dataset.gradeIndex = question.index;
          input.value = question.earned === null ? "" : question.earned; input.disabled = !report.can_decide || report.system_disqualification_locked;
          const reasonLabel = node("label", "", "Grading explanation"); const reason = node("textarea");
          reason.dataset.gradeReason = question.index; reason.maxLength = 2000; reason.rows = 2; reason.value = question.grade_reason || ""; reason.disabled = input.disabled;
          input.addEventListener("input", () => { dirty = true; draftScore(); }); reason.addEventListener("input", () => { dirty = true; });
          gradeLabel.append(input); reasonLabel.append(reason); item.append(gradeLabel, reasonLabel);
        } else item.append(node("p", "", "Unanswered · 0 / 1"));
      }
      list.append(item);
    }
  }
  function currentFinding(key) { return report.findings.find((item) => item.event_key === key); }
  function findingSummary(finding, observationTitle = "", showMeta = true) {
    const block = node("div", "review-finding-summary");
    if (observationTitle) block.append(node("h4", "review-finding-observation", observationTitle));
    const head = node("div", "review-finding-head");
    head.append(node("span", "review-finding-label", "Review finding"),
      node("strong", `review-finding-status review-finding-status-${finding.assessment}`, titles[finding.assessment] || finding.assessment));
    const reason = node("div", "review-finding-reason");
    reason.append(node("span", "review-finding-label", "Reviewer's reason"), node("p", "review-preserve", finding.reason));
    const meta = node("div", "review-finding-meta");
    meta.append(node("span", "", `Reviewed by ${finding.reviewer}`), node("span", "", `Saved ${date(finding.created_at)}`));
    block.append(head, reason);
    if (showMeta) block.append(meta);
    return block;
  }
  function eventOffset(event) {
    if (!event.part_id) return event.start_ms;
    const part = (monitoring.parts || []).find((item) => item.id === event.part_id);
    return event.start_ms + (part ? new Date(part.started_at).getTime() - new Date(report.started_at).getTime() : 0);
  }
  function renderEvents() {
    closeInline();
    const list = el("review-event-list"); list.replaceChildren();
    const filter = el("review-event-filter").value;
    const events = monitoring.events.filter((event) => filter === "all" || (filter === "unassessed" && !currentFinding(event.event_key)) || event.category === filter).sort((a, b) => eventOffset(a) - eventOffset(b));
    for (const event of events) {
      const article = node("article", "card section-card review-event"); article.id = `review-event-${event.id}`;
      const eventHead = node("div", "review-event-top");
      eventHead.append(node("h3", "", eventTitles[event.event_type] || event.event_type.replaceAll("_", " ")),
        node("span", "review-event-time", `${time(eventOffset(event))}–${time(eventOffset(event) + event.end_ms - event.start_ms)}${event.closed ? "" : " · Ongoing"}`));
      article.append(eventHead);
      article.append(node("p", "review-model-score", event.confidence === null || event.confidence === undefined
        ? "Model score unavailable" : `Model score (detection strength): ${Number(event.confidence).toFixed(2)} / 1`));
      if (event.evidence && recording.parts.some((part) => part.id === event.part_id && part.total_bytes > 0)) {
        const play = node("button", "secondary-button review-play-observation", "Play observation"); play.type = "button";
        play.addEventListener("click", () => playEvent(event, article)); article.append(play);
      } else article.append(node("p", "review-muted", "Video unavailable."));
      const latest = currentFinding(event.event_key);
      if (latest) article.append(findingSummary(latest, "", false));
      if (sessionReadOnly || report.published) { list.append(article); continue; }
      const form = node("form", "review-finding-form"); const choiceLabel = node("label", "", "Finding"); const choice = node("select");
      choice.add(new Option("Choose a finding", ""));
      for (const value of ["supports_violation", "dismissed", "inconclusive"]) choice.add(new Option(titles[value], value));
      choice.required = true; choiceLabel.append(choice);
      const reasonLabel = node("label", "", "Reason for finding"); const reason = node("textarea"); reason.rows = 2; reason.required = true; reason.minLength = 5; reason.maxLength = 5000; reasonLabel.append(reason);
      const draft = findingDrafts.get(event.event_key);
      if (draft) { choice.value = draft.assessment; reason.value = draft.reason; }
      const remember = () => findingDrafts.set(event.event_key, { assessment: choice.value, reason: reason.value });
      choice.addEventListener("change", remember); reason.addEventListener("input", remember);
      const save = node("button", "secondary-button", "Save finding"); save.type = "submit"; save.disabled = busy || stale;
      const feedback = node("p", "feedback-message"); feedback.hidden = true; feedback.setAttribute("role", "status");
      form.append(node("h4", "", latest ? "Add another finding" : "Record a finding"), choiceLabel, reasonLabel, save, feedback);
      form.addEventListener("submit", async (submit) => {
        submit.preventDefault(); if (busy || stale || report?.published || !form.reportValidity()) return;
        busy = true; save.disabled = true; updateDecisionDisabled();
        const serial = loading;
        try {
          const saved = await api(`${base()}/review/events/${event.event_key}/findings`, { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ expected_revision: report.revision, request_key: crypto.randomUUID(), assessment: choice.value, reason: reason.value.trim() }) });
          const fresh = await api(`${base()}/review`);
          if (serial !== loading) return;
          if (fresh.revision !== saved.revision) { stale = true; throw new Error("The review changed again. Reload the report before continuing."); }
          findingDrafts.delete(event.event_key);
          report.revision = fresh.revision; report.findings = fresh.findings;
          renderEvents(); renderFindingHistory(); message("review-session-message", "Finding saved.", false, 4000);
        } catch (error) {
          if (error.status === 409) stale = true;
          feedback.textContent = error.message; feedback.dataset.tone = "error"; feedback.hidden = false;
        } finally {
          busy = false; save.disabled = stale; updateDecisionDisabled();
          for (const button of document.querySelectorAll(".review-finding-form button[type=submit]")) button.disabled = stale;
        }
      });
      article.append(form); list.append(article);
    }
    if (!events.length) list.append(node("p", "review-empty", "No observations found."));
  }
  function renderFindingHistory() {
    const list = el("review-finding-history"); list.replaceChildren();
    for (const finding of report.findings) {
      const item = node("article", "review-finding-history-item");
      item.append(findingSummary(finding, eventTitles[finding.event_snapshot.event_type] || finding.event_snapshot.event_type.replaceAll("_", " ")));
      list.append(item);
    }
    if (!report.findings.length) list.append(node("p", "review-muted", "No observation findings recorded."));
  }
  function renderConnectivity() {
    window.renderConnectivityCase(el("review-connectivity-case"), report, {
      readOnly: sessionReadOnly || report.published,
      recordingAvailable: recording.parts.some(part => part.total_bytes > 0),
      onRecording: incident => {
        selectTab("integrity"); if (el("review-recording-wrap").hidden) openFullRecording();
        const when = Date.parse(incident.started_at);
        let index = fullParts.findIndex(part => Date.parse(part.started_at.endsWith("Z") ? part.started_at : `${part.started_at}Z`) >= when);
        if (index < 0) index = fullParts.length - 1;
        else if (index > 0) index -= 1;
        if (index >= 0) loadFullPart(index, 0, false);
        el("review-recording-wrap").scrollIntoView({ behavior: "instant", block: "center" });
      },
      draft: id => connectivityDrafts.get(id),
      onDraft: (id, value) => connectivityDrafts.set(id, value),
      onSave: async (incident, finding, reason) => {
        if (busy || stale) throw new Error("Reload the report before saving.");
        busy = true;
        try {
          const saved = await api(`${base()}/review/interruptions/${incident.id}/findings`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ expected_revision: report.revision, request_key: crypto.randomUUID(), finding, reason }),
          });
          const fresh = await api(`${base()}/review`);
          if (fresh.revision !== saved.revision) { stale = true; throw new Error("The review changed. Reload the report."); }
          report = fresh; connectivityDrafts.delete(incident.id);
          renderConnectivity(); updateDecisionDisabled();
          message("review-session-message", "Finding saved.", false, 4000);
        } catch (error) { if (error.status === 409) stale = true; throw error; }
        finally { busy = false; updateDecisionDisabled(); }
      },
    });
  }
  function renderRecording() {
    fullParts = recording.parts.filter((part) => part.total_bytes > 0).map((part) => ({
      ...part, duration: estimatedPartDuration(part),
    }));
    el("review-recording-toggle").hidden = !fullParts.length;
    const status = !fullParts.length ? "Recording unavailable." : recording.status === "complete" ? "" : "Recording incomplete.";
    el("review-recording-message").textContent = status;
    el("review-recording-message").hidden = !status;
    el("review-reference-button").hidden = !report.reference_available;
    el("review-reference-button").disabled = false;
  }
  function estimatedPartDuration(part) {
    const utc = (value) => value ? Date.parse(/[Z+-]\d\d:?\d\d$/.test(value) || value.endsWith("Z") ? value : `${value}Z`) : NaN;
    const elapsed = (utc(part.finalized_at) - utc(part.started_at)) / 1000;
    return Number.isFinite(elapsed) && elapsed > 0 ? elapsed : 1;
  }
  function makeControls(host, video, duration, position, seek, options = {}) {
    const panel = node("div", `review-player-controls${options.integrated ? " review-player-integrated" : ""}`);
    const track = node("input", "review-video-range"); track.type = "range"; track.min = "0"; track.step = "0.1";
    track.setAttribute("aria-label", options.label || "Recording position");
    const row = node("div", "review-player-buttons");
    const play = node("button", "secondary-button", "Play"); play.type = "button";
    const back = node("button", "secondary-button", "−10s"); back.type = "button";
    const forward = node("button", "secondary-button", "+10s"); forward.type = "button";
    const clock = node("span", "review-player-time");
    const mute = node("button", "secondary-button", "Mute"); mute.type = "button";
    const volume = node("input", "review-player-volume"); volume.type = "range"; volume.min = "0"; volume.max = "1"; volume.step = "0.05"; volume.value = "1";
    volume.setAttribute("aria-label", "Volume");
    const speed = options.integrated ? node("button", "review-player-speed", "1x") : null;
    const fullscreen = options.integrated ? node("button", "review-player-fullscreen", "Fullscreen") : null;
    if (speed) { speed.type = "button"; speed.setAttribute("aria-label", "Playback speed: 1x"); }
    if (fullscreen) { fullscreen.type = "button"; fullscreen.setAttribute("aria-label", "Toggle fullscreen"); }
    function update() {
      const length = Math.max(.1, Number(duration()) || .1), current = Math.min(length, Math.max(0, Number(position()) || 0));
      track.max = String(length); track.value = String(current);
      track.style.setProperty("--play-position", `${Math.min(100, current / length * 100)}%`);
      clock.textContent = `${time(current * 1000)} / ${time(length * 1000)}`;
      play.textContent = video.paused ? "Play" : "Pause"; mute.textContent = video.muted ? "Unmute" : "Mute";
      if (speed) { speed.textContent = `${Number(video.playbackRate.toFixed(2))}x`; speed.setAttribute("aria-label", `Playback speed: ${speed.textContent}`); }
      if (fullscreen) {
        fullscreen.textContent = "⛶";
        fullscreen.setAttribute("aria-label", document.fullscreenElement === options.container ? "Exit fullscreen" : "Enter fullscreen");
      }
      const clip = options.highlight?.();
      track.style.setProperty("--clip-start", `${clip ? Math.min(100, clip[0] / length * 100) : 0}%`);
      track.style.setProperty("--clip-end", `${clip ? Math.min(100, clip[1] / length * 100) : 0}%`);
    }
    play.addEventListener("click", () => {
      if (video.paused) { options.beforePlay?.(); video.play().catch(() => {}); } else video.pause();
    });
    video.addEventListener("click", () => play.click());
    back.addEventListener("click", () => seek(Math.max(0, position() - 10)));
    forward.addEventListener("click", () => seek(Math.min(duration(), position() + 10)));
    track.addEventListener("input", () => { seek(Number(track.value)); update(); });
    mute.addEventListener("click", () => { video.muted = !video.muted; update(); });
    volume.addEventListener("input", () => { video.volume = Number(volume.value); if (video.volume > 0) video.muted = false; update(); });
    speed?.addEventListener("click", () => { video.playbackRate = video.playbackRate >= 2 ? .75 : Math.round((video.playbackRate + .25) * 100) / 100; update(); });
    fullscreen?.addEventListener("click", () => {
      if (document.fullscreenElement === options.container) document.exitFullscreen();
      else options.container.requestFullscreen?.();
    });
    if (fullscreen) document.addEventListener("fullscreenchange", update);
    for (const event of ["timeupdate", "play", "pause", "loadedmetadata", "volumechange", "seeking", "ended"]) video.addEventListener(event, update);
    if (options.integrated) {
      row.append(play, mute, volume, clock, back, forward, speed, fullscreen);
    } else row.append(play, back, forward, clock, mute, volume);
    panel.append(track, row); host.append(panel); update();
    return { update, row };
  }
  function keyboardControls(container, video, duration, position, seek, beforePlay) {
    container.addEventListener("keydown", (event) => {
      if (event.altKey || event.ctrlKey || event.metaKey || event.target.matches("input, textarea, select, button")) return;
      let handled = true;
      switch (event.key.toLowerCase()) {
        case " ": case "k":
          if (video.paused) { beforePlay?.(); video.play().catch(() => {}); } else video.pause();
          break;
        case "arrowleft": seek(position() - 5); break;
        case "arrowright": seek(position() + 5); break;
        case "j": seek(position() - 10); break;
        case "l": seek(position() + 10); break;
        case "m": video.muted = !video.muted; break;
        case "arrowup": video.volume = Math.min(1, Math.round((video.volume + .1) * 100) / 100); video.muted = false; break;
        case "arrowdown": video.volume = Math.max(0, Math.round((video.volume - .1) * 100) / 100); break;
        case "home": seek(0); break;
        case "end": seek(duration()); break;
        case "f":
          if (document.fullscreenElement === container) document.exitFullscreen(); else container.requestFullscreen?.();
          break;
        default: handled = false;
      }
      if (handled) event.preventDefault();
    });
    video.tabIndex = 0;
    video.addEventListener("pointerdown", () => container.focus());
  }
  function fullDuration() { return fullParts.reduce((total, part) => total + part.duration, 0); }
  function fullPosition() { return fullParts.slice(0, fullIndex).reduce((total, part) => total + part.duration, 0) + (el("review-video").currentTime || 0); }
  function loadFullPart(index, seconds = 0, autoplay = false) {
    const part = fullParts[index]; if (!part) return;
    const video = el("review-video"); fullIndex = index;
    const ready = () => {
      if (video.dataset.part !== String(part.id)) return;
      if (Number.isFinite(video.duration) && video.duration > 0) part.duration = video.duration;
      video.currentTime = Math.min(Math.max(0, seconds), Math.max(0, part.duration - .05));
      mainControls?.update(); if (autoplay) { inlinePlayback?.video.pause(); video.play().catch(() => {}); }
    };
    if (video.dataset.part !== String(part.id)) {
      video.pause(); video.dataset.part = String(part.id);
      video.onloadedmetadata = ready; video.src = `${base()}/recording/parts/${part.id}`; video.load();
    } else if (video.readyState >= 1) ready(); else video.onloadedmetadata = ready;
  }
  function seekFull(seconds) {
    if (!fullParts.length) return;
    const target = Math.min(Math.max(0, seconds), Math.max(0, fullDuration() - .05));
    let offset = 0;
    for (let index = 0; index < fullParts.length; index += 1) {
      const end = offset + fullParts[index].duration;
      if (target < end || index === fullParts.length - 1) {
        loadFullPart(index, target - offset, !el("review-video").paused);
        return;
      }
      offset = end;
    }
  }
  function openFullRecording() {
    if (!fullParts.length) return;
    el("review-recording-wrap").hidden = false;
    el("review-recording-toggle").textContent = "Close recording";
    if (!mainControls) mainControls = makeControls(el("review-recording-controls"), el("review-video"), fullDuration, fullPosition, seekFull, {
      label: "Full recording position", beforePlay: () => inlinePlayback?.video.pause(), integrated: true, container: el("review-recording-wrap"),
    });
    if (!el("review-video").hasAttribute("src")) loadFullPart(0);
  }
  function closeFullRecording() {
    el("review-video").pause(); el("review-recording-wrap").hidden = true;
    el("review-recording-toggle").textContent = "Open recording";
  }
  function playEvent(event, article) {
    closeInline(); el("review-video").pause();
    article.classList.add("is-selected");
    const wrap = node("div", "review-inline-player");
    const head = node("div", "review-inline-head");
    head.append(node("strong", "", "Observation video"));
    const close = node("button", "secondary-button", "Close video"); close.type = "button"; close.addEventListener("click", closeInline); head.append(close);
    const video = node("video"); video.playsInline = true; video.preload = "metadata"; wrap.tabIndex = 0; wrap.setAttribute("aria-label", "Observation video player");
    const controlsHost = node("div"); wrap.append(head, video, controlsHost);
    article.querySelector(".review-play-observation").insertAdjacentElement("afterend", wrap);
    const parts = fullParts.map((part) => ({ ...part }));
    const eventIndex = parts.findIndex((part) => part.id === event.part_id);
    const state = { video, wrap, event, parts, index: eventIndex, loop: true, timer: null, controls: null, loopButton: null }; inlinePlayback = state;
    const duration = () => parts.reduce((total, part) => total + part.duration, 0);
    const prefix = (index) => parts.slice(0, index).reduce((total, part) => total + part.duration, 0);
    const position = () => prefix(state.index) + (video.currentTime || 0);
    const bounds = () => {
      const offset = prefix(eventIndex), partLength = parts[eventIndex].duration;
      const start = offset + Math.min(event.start_ms / 1000, Math.max(0, partLength - .2));
      return [start, offset + Math.min(partLength, Math.max(event.start_ms / 1000 + .1, event.end_ms / 1000))];
    };
    const loadPart = (index, seconds, autoplay) => {
      const part = parts[index]; if (!part || inlinePlayback !== state) return;
      state.index = index;
      const ready = () => {
        if (inlinePlayback !== state || video.dataset.part !== String(part.id)) return;
        if (Number.isFinite(video.duration) && video.duration > 0) part.duration = video.duration;
        video.currentTime = Math.min(Math.max(0, seconds), Math.max(0, part.duration - .05));
        state.controls?.update(); if (autoplay) { el("review-video").pause(); video.play().catch(() => {}); }
      };
      if (video.dataset.part !== String(part.id)) {
        video.pause(); video.dataset.part = String(part.id); video.onloadedmetadata = ready;
        video.src = `${base()}/recording/parts/${part.id}`; video.load();
      } else if (video.readyState >= 1) ready(); else video.onloadedmetadata = ready;
    };
    const seek = (seconds) => {
      const target = Math.min(Math.max(0, seconds), Math.max(0, duration() - .05));
      const [start, end] = bounds(); state.loop = target >= start && target < end;
      const autoplay = !video.paused;
      let offset = 0;
      for (let index = 0; index < parts.length; index += 1) {
        const next = offset + parts[index].duration;
        if (target < next || index === parts.length - 1) { loadPart(index, target - offset, autoplay); break; }
        offset = next;
      }
      updateLoop();
    };
    const updateLoop = () => { state.loopButton.textContent = state.loop ? "Loop on" : "Loop off"; state.loopButton.setAttribute("aria-pressed", String(state.loop)); };
    state.controls = makeControls(controlsHost, video, duration, position, seek, {
      label: "Observation video position", highlight: bounds, beforePlay: () => el("review-video").pause(),
    });
    keyboardControls(wrap, video, duration, position, seek, () => el("review-video").pause());
    state.loopButton = node("button", "secondary-button", "Loop on"); state.loopButton.type = "button"; state.loopButton.setAttribute("aria-pressed", "true");
    state.loopButton.addEventListener("click", () => {
      state.loop = !state.loop;
      if (state.loop) seek(bounds()[0]);
      updateLoop(); state.controls.update();
    }); state.controls.row.append(state.loopButton);
    const enforceLoop = () => {
      if (inlinePlayback !== state || !state.loop || video.paused || video.seeking) return;
      const [start, end] = bounds();
      if (position() >= end - .05) loadPart(eventIndex, event.start_ms / 1000, true);
    };
    state.timer = window.setInterval(enforceLoop, 80);
    video.addEventListener("timeupdate", enforceLoop);
    video.addEventListener("ended", () => {
      if (inlinePlayback !== state) return;
      if (state.loop) loadPart(eventIndex, event.start_ms / 1000, true);
      else if (state.index < parts.length - 1) loadPart(state.index + 1, 0, true);
    });
    const status = node("p", "review-muted"); wrap.append(status);
    video.addEventListener("error", () => { status.textContent = "Video unavailable."; });
    loadPart(eventIndex, event.start_ms / 1000, true);
  }
  function actionChanged() {
    const change = el("review-action").value === "change_score";
    el("review-score-label").hidden = !change; el("review-score").disabled = !change;
    el("review-score").required = change;
    updateDecisionDisabled(); draftScore();
  }
  function updateDecisionDisabled() {
    el("review-submit").disabled = !report?.can_decide || busy || stale;
    el("review-action").disabled = !report?.can_decide || busy || stale;
    el("review-reason").disabled = !report?.can_decide || busy || stale;
  }
  function collectGrades(requireComplete) {
    const result = [];
    for (const input of document.querySelectorAll("[data-grade-index]")) {
      const reason = document.querySelector(`[data-grade-reason="${input.dataset.gradeIndex}"]`);
      if (input.value === "" || reason.value.trim().length < 5 || !input.checkValidity()) {
        if (requireComplete) { selectTab("results"); input.focus(); throw new Error("Grade each answered written question from 0 to 1 and provide a grading explanation of at least 5 characters."); }
        continue;
      }
      result.push({ question_index: Number(input.dataset.gradeIndex), marks: Number(input.value), reason: reason.value.trim() });
    }
    return result;
  }
  function draftScore() {
    if (!report) return;
    let total = report.scores.objective_earned, incomplete = false;
    for (const input of document.querySelectorAll("[data-grade-index]")) {
      if (input.value === "" || !input.checkValidity()) incomplete = true; else total += Number(input.value);
    }
    el("review-draft-score").textContent = !report.can_decide ? (report.published ? "Results published" : report.decision_blocked_reason || "Exam still in progress") : `Answer grade total: ${incomplete ? "Incomplete" : score(Math.round(total * 100) / 100, report.scores.possible)}`;
  }
  el("review-decision-form").addEventListener("submit", (event) => {
    event.preventDefault(); if (busy || stale || !report?.can_decide) return;
    try {
      const action = el("review-action").value;
      const payload = { expected_revision: report.revision, request_key: crypto.randomUUID(), action,
        reason: el("review-reason").value.trim(), grades: collectGrades(action !== "disqualify"),
        event_keys: [...new Set(report.findings.map((finding) => finding.event_key))].filter((key) => monitoring.events.some((item) => item.event_key === key)) };
      if (action === "change_score") payload.score = Number(el("review-score").value);
      pendingDecision = payload;
      el("review-confirm-dialog").dataset.disposition = action;
      const summary = el("review-confirm-summary"); summary.replaceChildren();
      summary.append(node("p", "", `${report.name} · ${report.title}`), node("strong", "", titles[action]));
      summary.append(node("p", "", `Current score: ${score(report.decision?.score ?? report.scores.calculated_score, report.scores.possible)}`));
      if (action === "change_score") summary.append(node("p", "", `New final score: ${score(payload.score, report.scores.possible)}`));
      if (action === "keep_score") {
        let total = report.scores.objective_earned;
        const previous = report.decision?.grades || {};
        const unchanged = payload.grades.every((grade) => previous[String(grade.question_index)]?.marks === grade.marks);
        for (const grade of payload.grades) total += grade.marks;
        const resulting = report.decision?.score !== null && report.decision?.score !== undefined && unchanged ? report.decision.score : Math.round(total*100)/100;
        summary.append(node("p", "", `Resulting score: ${score(resulting, report.scores.possible)}`));
      }
      if (action === "disqualify") summary.append(node("p", "review-alert", "The candidate's result will be disqualified. Earned marks and earlier decisions remain in the history."));
      summary.append(node("p", "review-preserve", payload.reason), node("p", "", `Reviewer: ${el("account-name").textContent}`));
      const unassessed = monitoring.events.filter((item) => !currentFinding(item.event_key)).length;
      if (findingDrafts.size) summary.append(node("p", "review-alert", `${findingDrafts.size} observation findings are still drafts. This decision will not save those findings.`));
      if (connectivityDrafts.size) summary.append(node("p", "review-alert", `${connectivityDrafts.size} interruption findings are still drafts. Save them before confirming the decision.`));
      if (unassessed || monitoring.status === "processing") summary.append(node("p", "review-alert", `${unassessed} observations have no finding. ${monitoring.status === "processing" ? "Monitoring is still processing; more evidence may arrive." : ""}`));
      message("review-confirm-error"); el("review-confirm-save").disabled = false; el("review-confirm-cancel").disabled = false;
      el("review-confirm-dialog").showModal();
    } catch (error) { message("review-session-message", error.message, true); }
  });
  el("review-confirm-save").addEventListener("click", async () => {
    if (busy || !pendingDecision || stale) return;
    busy = true; el("review-confirm-save").disabled = true; el("review-confirm-cancel").disabled = true; updateDecisionDisabled();
    try {
      await api(`${base()}/review/decisions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(pendingDecision) });
      const invitation = report.invitation_id;
      const unsavedFindings = new Map(findingDrafts);
      dirty = false; pendingDecision = null; el("review-confirm-dialog").close(); busy = false;
      await openSession(invitation, true);
      for (const [key, draft] of unsavedFindings) if (monitoring.events.some((event) => event.event_key === key)) findingDrafts.set(key, draft);
      if (report && unsavedFindings.size) renderEvents();
      await loadCards(); message("review-session-message", "Review decision saved.", false, 4000);
    } catch (error) {
      if (error.status === 409) stale = true;
      message("review-confirm-error", error.message, true);
    } finally {
      busy = false; el("review-confirm-save").disabled = stale; el("review-confirm-cancel").disabled = false; updateDecisionDisabled();
    }
  });
  el("review-confirm-cancel").addEventListener("click", () => el("review-confirm-dialog").close());
  el("review-confirm-dialog").addEventListener("cancel", (event) => { if (busy) event.preventDefault(); });
  el("review-action").addEventListener("change", () => { dirty = true; actionChanged(); });
  el("review-reason").addEventListener("input", () => { dirty = true; });
  el("review-score").addEventListener("input", () => { dirty = true; });
  for (const id of ["review-search", "review-filter", "review-attempt-filter"]) el(id).addEventListener(id === "review-search" ? "input" : "change", renderCards);
  for (const id of ["review-exam-search", "review-exam-candidates-min", "review-exam-candidates-max", "review-exam-questions-min", "review-exam-questions-max"]) el(id).addEventListener("input", renderExamList);
  el("review-exam-status").addEventListener("change", renderExamList);
  el("review-exam-clear").addEventListener("click", () => {
    for (const id of ["review-exam-search", "review-exam-candidates-min", "review-exam-candidates-max", "review-exam-questions-min", "review-exam-questions-max"]) el(id).value = "";
    el("review-exam-status").value = "all"; renderExamList();
  });
  el("review-back-exams").addEventListener("click", () => {
    if (busy) return;
    el("review-queue").hidden = true; el("review-exams").hidden = false; renderExamList();
    el("review-exams").scrollIntoView({ behavior: "instant", block: "start" });
  });
  el("results-assessment").addEventListener("change", async () => {
    if (busy || !allowDiscard()) { el("results-assessment").value = assessmentId || ""; return; }
    ++loading; clearMedia(); report = null; dirty = false; findingDrafts.clear(); connectivityDrafts.clear(); assessmentId = el("results-assessment").value ? Number(el("results-assessment").value) : null;
    renderQueueExamHeader();
    el("review-exams").hidden = false; el("review-queue").hidden = true; el("review-session").hidden = true; await loadCards();
  });
  for (const id of ["review-refresh-cards", "results-refresh"]) el(id).addEventListener("click", loadCards);
  el("review-reload").addEventListener("click", () => { if (openedInvitationId && !busy && allowDiscard()) openSession(openedInvitationId, true); });
  el("review-back").addEventListener("click", () => {
    if (busy || !allowDiscard()) return;
    if (sessionReadOnly) {
      closeReadOnlyReport();
      el("results-list").scrollIntoView({ behavior: "instant", block: "start" });
      loadCards();
      return;
    }
    ++loading; clearMedia(); report = null; dirty = false; findingDrafts.clear(); connectivityDrafts.clear(); el("review-exams").hidden = true; el("review-queue").hidden = false; el("review-session").hidden = true;
    el("review-queue").scrollIntoView({ behavior: "instant", block: "start" }); loadCards();
  });
  for (const button of document.querySelectorAll("[data-review-tab]")) {
    button.addEventListener("click", () => selectTab(button.dataset.reviewTab));
    button.addEventListener("keydown", (event) => {
    const names = ["overview", "results", "integrity", "connectivity"], index = names.indexOf(button.dataset.reviewTab);
      const next = event.key === "ArrowRight" ? (index+1)%names.length : event.key === "ArrowLeft" ? (index+names.length-1)%names.length : event.key === "Home" ? 0 : event.key === "End" ? names.length-1 : null;
      if (next !== null) { event.preventDefault(); selectTab(names[next]); el(`review-${names[next]}-tab`).focus(); }
    });
  }
  el("review-event-filter").addEventListener("change", () => { if (report) renderEvents(); });
  el("review-recording-toggle").addEventListener("click", () => {
    if (el("review-recording-wrap").hidden) openFullRecording(); else closeFullRecording();
  });
  keyboardControls(el("review-recording-wrap"), el("review-video"), fullDuration, fullPosition, seekFull, () => inlinePlayback?.video.pause());
  el("review-reference-button").addEventListener("click", () => {
    if (!report?.reference_available) return;
    if (!el("review-reference-wrap").hidden) {
      el("review-reference-wrap").hidden = true; el("review-reference-photo").hidden = true;
      el("review-reference-photo").removeAttribute("src"); el("review-reference-button").textContent = "Open reference photo";
      return;
    }
    el("review-reference-wrap").hidden = false;
    el("review-reference-photo").src = `${base()}/review/reference`; el("review-reference-photo").hidden = false;
    el("review-reference-button").textContent = "Close reference photo";
  });
  el("review-reference-photo").addEventListener("error", () => { el("review-reference-wrap").hidden = true; el("review-reference-photo").hidden = true; el("review-reference-button").textContent = "Open reference photo"; message("review-session-message", "Reference photo unavailable.", true); });
  el("review-video").addEventListener("ended", () => {
    if (fullIndex < fullParts.length - 1 && !el("review-recording-wrap").hidden) loadFullPart(fullIndex + 1, 0, true);
  });
  el("review-video").addEventListener("error", () => { if (el("review-video").hasAttribute("src")) message("review-session-message", "This recording could not be played. It may be incomplete, expired, or no longer available.", true); });
  window.setInterval(() => { if (!el("review-exams").hidden) renderExamList(); else if (!el("review-session").hidden) updateTimeUsed(); }, 30000);
  window.addEventListener("beforeunload", (event) => { if (dirty || findingDrafts.size || connectivityDrafts.size) { event.preventDefault(); event.returnValue = ""; } });
  if (["review", "results"].includes(location.hash.slice(1))) activate();
  return { activate, pauseMedia };
})();
