(() => {
  const el = id => document.getElementById(id);
  const node = (tag, cls = "", text) => { const e = document.createElement(tag); e.className = cls; if (text !== undefined) e.textContent = text; return e; };
  const button = (text, action, cls = "secondary-button") => { const b = node("button", cls, text); b.type = "button"; b.addEventListener("click", action); return b; };
  const date = value => value ? new Date(value).toLocaleString("en-GB", { timeZone: "Africa/Nairobi", dateStyle: "medium", timeStyle: "short" }) + " EAT" : "Not recorded";
  const time = ms => { const seconds = Math.max(0, Math.round(Number(ms || 0) / 1000)); return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`; };
  const pct = value => value === null || value === undefined ? "Not recorded" : `${Math.round(Number(value) * 100)}%`;
  const score = (value, possible) => value === null || value === undefined ? "Awaiting grading" : `${value} / ${possible}${possible ? ` (${Math.round(value / possible * 100)}%)` : ""}`;
  const actions = { keep_score: "Score kept", change_score: "Score changed", disqualify: "Disqualified" };
  const states = { interrupted: "Interrupted", submitted: "Submitted", disqualified: "Disqualified", in_progress: "In progress", not_started: "Not started" };
  const findings = { supports_violation: "Supports a rule violation", dismissed: "Dismissed", inconclusive: "Inconclusive" };
  const flags = { identity_mismatch: "Possible identity mismatch", face_absent: "Face absent", face_unusable: "Face could not be checked", additional_person: "Additional person detected", mobile_phone: "Phone visible", book: "Book visible", tablet: "Tablet visible", calculator: "Calculator visible", head_turned: "Sustained head turn", head_down: "Downward head orientation", repeated_look_away: "Repeated head-orientation changes", speech: "Speech activity", repeated_speech: "Repeated speech activity", camera_obscured: "Camera image obscured", suspected_frozen_video: "Suspected frozen video", framing_changed: "Framing changed", audio_clipping: "Audio clipping", digital_audio_silence: "Digital audio silence", identity_reference_unavailable: "Identity reference unavailable", video_analysis_unavailable: "Video analysis unavailable", audio_analysis_unavailable: "Audio analysis unavailable", video_coverage_gap: "Video coverage gap", audio_coverage_gap: "Audio coverage gap" };
  let page = 1, exams = [], selectedExam = null, cards = [], selectedInvitation = null, data = null;
  let fullPlayer = null, evidencePlayer = null, examSerial = 0, candidateSerial = 0, caseSerial = 0, saving = false;
  let publication = null, publicationDraft = null, publishing = false;
  let historyPage = 1, historySerial = 0;
  let historyDetailSerial = 0, historyReportId = null, historySearchTimer = null;
  const base = () => `/api/auditor/assessments/${selectedExam.id}/candidates/${selectedInvitation}`;
  const feedback = AssessBridgeFeedback.create({ notificationHost: '#auditor-notifications' });
  const pageFeedback = feedback.scope('auditor-page');
  let caseFeedback = feedback.scope('auditor-case');
  let candidateFeedback = feedback.scope('auditor-candidates');
  let publicationFeedback = feedback.scope('auditor-publication');
  let publicationStale = false, publicationDialog = null;
  let examFeedback = feedback.scope('auditor-exams'), historyFeedback = feedback.scope('auditor-history'), historyDetailFeedback = feedback.scope('auditor-history-detail');
  const publicationForm = el("publication-form");
  function notice(scope, target, text, tone = 'error', action) {
    if (!text) return scope.clear(target);
    return scope.show({ id: target, target: el(target), text, tone, action });
  }
  function message(text = '', error = true) { return notice(pageFeedback, 'page-message', text, error ? 'error' : 'neutral'); }
  function refreshCase() { if (!saving && selectedInvitation) return openCase(selectedInvitation, true); }
  async function api(url, options = {}) {
    let response;
    try { response = await fetch(url, { credentials: 'same-origin', ...options }); }
    catch { throw Error('Could not reach AssessBridge. Try again.'); }
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = typeof result.detail === 'string' ? result.detail : '';
      const safe = {
        'Closed assessment not found': 'This closed assessment is unavailable.',
        'Candidate session not found': 'This candidate session is unavailable.',
        'Published results not found': 'These published results are unavailable.',
        'The report changed while loading. Reload the report.': 'The report changed. Reload it to continue.',
        'The report changed. Reload it before marking it audited.': 'The report changed. Reload it before marking it as audited.',
        'A completed exam and reviewer decision are required before auditing.': 'A completed attempt and reviewer decision are required before auditing.',
        'Wait for monitoring to finish before marking the report audited.': 'Wait for monitoring to finish before marking this report as audited.',
        'Results are published. Audits cannot be changed.': 'Results are published. Audits cannot be changed.',
        'The assessment or audits changed. Refresh the candidate list before publishing.': 'The assessment or audits changed. Refresh the candidate list before publishing.',
        'Every candidate must be audited before publication.': 'Audit every candidate before publishing results.',
        'Choose a valid From and To date range.': 'Choose a valid From and To date range.',
        'Choose dates within the supported date range.': 'Choose dates within the supported date range.',
      };
      const text = safe[detail] || (response.status === 401 ? 'Your session ended. Sign in again.' : response.status === 403 ? 'Your account cannot perform this action.'
        : response.status === 409 ? 'This report or its audit status changed. Refresh before continuing.'
          : response.status === 422 ? 'Check the entered information and try again.'
            : options.method === 'POST' ? 'Could not confirm the save. Try again.' : 'Could not load this information. Try again.');
      throw Object.assign(Error(text), { status: response.status });
    }
    return result;
  }
  function facts(target, values) {
    target.replaceChildren();
    for (const [label, value] of values) { const item = node("div", "review-fact"); item.append(node("span", "", label), node("strong", "", value)); target.append(item); }
  }
  function highlight(target, value, term) {
    const text = String(value || ""), search = term.toLowerCase();
    if (!search) { target.textContent = text; return; }
    let pos = 0, index;
    while ((index = text.toLowerCase().indexOf(search, pos)) >= 0) { target.append(document.createTextNode(text.slice(pos, index)), node("mark", "", text.slice(index, index + search.length))); pos = index + search.length; }
    target.append(document.createTextNode(text.slice(pos)));
  }
  function auditStatus(audit) { return audit.status === "audited" ? "Audited" : audit.status === "changed" ? "Needs audit · Report updated" : audit.status === "not_ready" ? "No attempt" : "Needs audit"; }
  function pill(text, good = false, bad = false) { return node("span", `audit-status-pill${good ? " is-audited" : ""}${bad ? " is-error" : ""}`, text); }
  function pausePlayers() { fullPlayer?.pause(); evidencePlayer?.pause(); }
  function clearPlayers() { fullPlayer?.destroy(); evidencePlayer?.destroy(); fullPlayer = evidencePlayer = null; el("full-player-host").hidden = true; el("recording-toggle").textContent = "Open recording"; }
  function view(name) {
    for (const item of ["exam", "candidate", "case"]) el(`${item}-view`).hidden = item !== name;
    if (name !== 'case') caseFeedback.dispose();
    message(); el(`${name}-view`).scrollIntoView({ block: "start", behavior: "instant" });
  }
  function emptyRows(target, text, columns) { const row = node("tr"), td = node("td", "table-empty", text); td.colSpan = columns; row.append(td); target.replaceChildren(row); }
  async function loadPublicationHistory() {
    const serial = ++historySerial;
    const scope = historyFeedback = feedback.scope('auditor-history');
    emptyRows(el("publication-history-rows"), "Loading publications…", 6);
    el("history-previous").disabled = true; el("history-next").disabled = true;
    message();
    try {
      const term = el("history-search").value.trim(), period = el("history-period").value;
      const params = new URLSearchParams({ page: historyPage, search: term, period });
      if (period === "custom") {
        const from = el("history-from").value, to = el("history-to").value;
        if (!from || !to || from > to) throw Error("Choose a valid From and To date range.");
        params.set("date_from", from); params.set("date_to", to);
      }
      const result = await api(`/api/auditor/publications?${params}`);
      if (serial !== historySerial || !scope.active) return;
      el("publication-history-rows").replaceChildren();
      for (const item of result.items) {
        const row = node("tr"), exam = node("td"), title = node("strong"), code = node("td");
        highlight(title, item.title, term); exam.append(title); highlight(code, item.internal_reference || "Not set", term);
        row.append(exam, code, node("td", "", date(item.published_at)), node("td", "", item.publisher_username), node("td", "", String(item.candidate_count)));
        const action = node("td"); action.append(button("View results", () => openPublishedResults(item.id)));
        row.append(action);
        ["Exam", "Exam code", "Published on", "Published by", "Candidates", "Results"].forEach((label,index) => row.children[index].dataset.label = label);
        el("publication-history-rows").append(row);
      }
      if (!result.items.length) emptyRows(el("publication-history-rows"), term || period !== "all" ? "No published exams match these filters." : "No results have been published yet.", 6);
      el("history-count").textContent = `${result.total} published ${result.total === 1 ? "exam" : "exams"} · Page ${historyPage}`;
      el("history-previous").disabled = historyPage <= 1; el("history-next").disabled = historyPage * result.page_size >= result.total;
    } catch (error) {
      if (serial === historySerial && scope.active) { el("publication-history-rows").replaceChildren(); el("history-count").textContent = ""; notice(scope, "history-message", error.message, "error", { label: "Retry", onClick: loadPublicationHistory }); }
    }
  }
  async function openPublishedResults(id) {
    const serial = ++historyDetailSerial;
    const scope = historyDetailFeedback = feedback.scope('auditor-history-detail');
    pausePlayers(); message();
    el("history-list-view").hidden = true; el("history-detail-view").hidden = false;
    el("history-detail-content").hidden = true; el("history-detail-title").textContent = "Loading published results…"; el("history-detail-code").textContent = "";
    el("history-detail-view").scrollIntoView({ block: "start", behavior: "instant" });
    try {
      const result = await api(`/api/auditor/publications/${id}`); if (serial !== historyDetailSerial || !scope.active) return;
      const p = result.publication;
      el("history-detail-title").textContent = p.title; el("history-detail-code").textContent = `Exam code: ${p.internal_reference || "Not set"}`;
      facts(el("history-detail-facts"), [["Published by", p.publisher_username], ["Published on", date(p.published_at)], ["Candidates", String(p.candidate_count)]]);
      el("history-detail-remark").textContent = p.remark; el("history-result-rows").replaceChildren();
      for (const candidate of result.candidates) {
        const r = candidate.result, review = r.review, written = r.written_score, mcq = r.multiple_choice_score;
        const row = node("tr"), who = node("td"), person = node("div"); person.append(node("strong", "", candidate.name), node("span", "review-muted", candidate.email)); who.append(person);
        const final = review.outcome === "disqualified" ? "Disqualified" : score(review.score,review.possible);
        row.append(who, node("td", "", review.outcome === "disqualified" ? "Disqualified" : "Eligible"),
          node("td", "", mcq ? score(mcq.earned,mcq.possible) : "No MCQs"),
          node("td", "", written ? `${score(written.earned,written.possible)}${written.pending ? " · Incomplete" : ""}` : "No written questions"), node("td", "", final));
        const action = node("td"); action.append(button("View report", () => {
          selectMainTab("auditing", false); historyReportId = id;
          selectedExam = { id:p.assessment_id, title:p.title, internal_reference:p.internal_reference };
          openCase(candidate.invitation_id);
        })); row.append(action);
        ["Candidate", "Review outcome", "Original MCQ score", "Written score", "Final score", "Report"].forEach((label,index) => row.children[index].dataset.label = label);
        el("history-result-rows").append(row);
      }
      if (!result.candidates.length) emptyRows(el("history-result-rows"), "No published candidate results.", 6);
      el("history-detail-content").hidden = false;
    } catch (error) { if (serial === historyDetailSerial && scope.active) { el("history-detail-title").textContent = "Published results unavailable"; notice(scope, "history-detail-message", error.message, "error", { label: "Retry", onClick: () => openPublishedResults(id) }); } }
  }
  async function loadExams() {
    const serial = ++examSerial;
    const scope = examFeedback = feedback.scope('auditor-exams');
    const params = new URLSearchParams({ page, search: el("exam-search").value.trim(), audit: el("exam-audit").value });
    emptyRows(el("exam-rows"), "Loading assessments…", 6); message();
    try {
      const result = await api(`/api/auditor/assessments?${params}`); if (serial !== examSerial || !scope.active) return;
      exams = result.items; el("exam-rows").replaceChildren();
      for (const exam of exams) {
        const row = node("tr"), name = node("td"), link = button("", () => openExam(exam), "audit-exam-link");
        highlight(link, exam.title, el("exam-search").value.trim()); name.append(link);
        const code = node("td"); highlight(code, exam.internal_reference || "Not set", el("exam-search").value.trim());
        const status = node("td", "", date(exam.ends_at));
        row.append(name, code, status, node("td", "", String(exam.candidate_count)), node("td", "", `${exam.reviewed_count} / ${exam.candidate_count}`), node("td", "", `${exam.audited_count} / ${exam.candidate_count}`));
        row.addEventListener("click", e => { if (!e.target.closest("button")) openExam(exam); }); el("exam-rows").append(row);
      }
      if (!exams.length) emptyRows(el("exam-rows"), "No assessments match these filters.", 6);
      el("exam-count").textContent = `${result.total} assessments · Page ${page}`;
      el("exam-previous").disabled = page <= 1; el("exam-next").disabled = page * result.page_size >= result.total;
    } catch (error) { if (serial === examSerial && scope.active) { el("exam-rows").replaceChildren(); notice(scope, "exam-message", error.message, "error", { label: "Retry", onClick: loadExams }); } }
  }
  async function openExam(exam, fromHistory = false) {
    historyReportId = null;
    ++caseSerial; clearPlayers(); selectedExam = exam; selectedInvitation = null; data = null; cards = [];
    el("back-exams").textContent = fromHistory ? "Back to publication history" : "Back to assessments";
    el("back-exams").dataset.history = String(fromHistory);
    el("candidate-exam-title").textContent = exam.title; el("candidate-exam-code").textContent = `Exam code: ${exam.internal_reference || "Not set"}`;
    el("candidate-search").value = ""; el("candidate-audit").value = "all";
    view("candidate"); await loadCandidates();
  }
  async function loadCandidates() {
    if (publishing || publicationDialog?.element.open) return;
    const serial = ++candidateSerial, id = selectedExam.id;
    const scope = candidateFeedback = feedback.scope('auditor-candidates');
    publication = null; el("publication-panel").hidden = true;
    emptyRows(el("candidate-rows"), "Loading candidates…", 5); message();
    try { const result = await api(`/api/auditor/assessments/${id}/candidates`); if (serial !== candidateSerial || id !== selectedExam?.id || !scope.active) return; cards = result.candidates; publication = result.publication; renderCandidates(); renderPublication(); }
    catch (error) { if (serial === candidateSerial && id === selectedExam?.id && scope.active) { el("candidate-rows").replaceChildren(); notice(scope, "candidate-message", error.message, "error", { label: "Retry", onClick: loadCandidates }); } }
  }
  function renderPublication() {
    if (!publication) return;
    const published = publication.status === "published", latest = publication.latest;
    el("publication-panel").hidden = false; el("publication-panel").classList.toggle("is-published", published);
    el("publication-status").textContent = published ? "Published" : "Not published";
    el("publication-status").className = published ? "audit-done" : "audit-pending";
    el("publication-count").textContent = `${publication.audited_count} of ${publication.candidate_count} candidates audited`;
    notice(candidateFeedback, 'publication-blocker', published ? '' : publication.blocked_reason || '', 'warning');
    el("publish-open").hidden = published; el("publish-open").disabled = !publication.can_publish || publishing;
    el("publish-open").textContent = "Approve and publish results";
    el("publication-record").hidden = !latest;
    el("publication-by").textContent = latest ? `Published by ${latest.publisher_username} · ${date(latest.published_at)}` : "";
    el("publication-remark").textContent = latest?.remark || "";
  }
  function renderCandidates() {
    const query = el("candidate-search").value.toLowerCase().trim(), filter = el("candidate-audit").value;
    const matching = cards.filter(c => `${c.name} ${c.email}`.toLowerCase().includes(query) && (filter === "all" || (filter === "audited") === (c.audit.status === "audited")));
    el("candidate-rows").replaceChildren();
    for (const card of matching) {
      const row = node("tr"), who = node("td"); who.append(node("strong", "", card.name), node("span", "review-muted", card.email));
      const result = card.outcome === "disqualified" ? "Disqualified" : card.decision ? score(card.decision.score, card.possible) : "Awaiting review";
      const status = node("td"); status.append(pill(auditStatus(card.audit), card.audit.status === "audited"));
      const action = node("td"), open = button(card.audit.status === "audited" ? "View report" : "Audit report", () => openCase(card.invitation_id)); open.disabled = !card.has_session; action.append(open);
      row.append(who, node("td", "", states[card.attempt_state]), node("td", "", result), status, action); el("candidate-rows").append(row);
    }
    if (!matching.length) emptyRows(el("candidate-rows"), "No candidates match these filters.", 5);
  }
  async function openCase(invitationId, reload = false) {
    const scope = caseFeedback = feedback.scope('auditor-case');
    const serial = ++caseSerial; selectedInvitation = invitationId; clearPlayers(); data = null;
    view("case"); el("audit-action-bar").hidden = true;
    el("back-candidates").textContent = historyReportId ? "Back to published results" : "Back to candidates";
    el("case-name").textContent = "Loading report…"; el("case-exam-title").textContent = selectedExam.title; el("case-email").textContent = "";
    document.querySelector(".audit-tabs").hidden = true;
    for (const pane of document.querySelectorAll("[data-case-pane]")) pane.hidden = true;
    try {
      const result = await api(`${base()}/report`); if (serial !== caseSerial || !scope.active) return; data = result;
      renderCase(); document.querySelector(".audit-tabs").hidden = false; el("audit-action-bar").hidden = false;
      selectSubtab("case", reload ? document.querySelector("[data-case-tab][aria-selected=true]")?.dataset.caseTab || "results" : "results");
      selectSubtab("integrity", "recordings");
    } catch (error) { if (serial === caseSerial && scope.active) { el("case-name").textContent = "Report unavailable"; notice(scope, "case-message", error.message, "error", { label: "Reload report", onClick: refreshCase }); } }
  }
  function renderCase() {
    const r = data.review, s = r.scores;
    window.renderConnectivityCase(el("audit-connectivity-case"), r, { readOnly: true, feedback: caseFeedback });
    el("case-exam-title").textContent = r.title; el("case-name").textContent = r.name; el("case-email").textContent = r.email;
    const written = s.questions.filter(q => q.type === "written"), earned = Math.round(written.reduce((n, q) => n + Number(q.earned || 0), 0) * 100) / 100;
    const final = r.outcome === "disqualified" ? "Disqualified" : r.decision ? score(r.decision.score, s.possible) : "Awaiting reviewer decision";
    facts(el("audit-grade-facts"), [["Automatic MCQ score", s.objective_possible ? score(s.objective_earned, s.objective_possible) : "No multiple choice questions"], ["Manual written question grade", !written.length ? "No written questions" : `${earned} / ${written.length}${s.written_pending ? " · Incomplete" : ""}`], ["Total grade", score(s.calculated_score, s.possible)], ["Final recorded result", final], ["Written responses awaiting grading", String(s.written_pending)]]);
    const systemReason = r.system_disqualification_locked ? r.disqualification_reason === "connection_lost" ? "Disqualified after the exam connection was lost." : "Disqualified after leaving the exam before submission." : "";
    el("audit-result-reason").textContent = [systemReason, r.decision?.reason].filter(Boolean).join("\n\n") || "No final result has been recorded.";
    renderAnswers(); renderReviewer(); renderCoverage(); renderIdentity(); renderFlags(); renderAudit();
    const available = data.recording.parts.some(p => p.total_bytes > 0);
    el("recording-toggle").hidden = !available;
    notice(caseFeedback, 'recording-state', !available ? 'Recording unavailable.' : data.recording.status === 'complete' ? '' : 'Recording incomplete.', 'warning');
  }
  function renderAnswers() {
    el("audit-answers").replaceChildren();
    for (const q of data.review.scores.questions) {
      const item = node("article", "audit-answer"); item.append(node("h4", "", `${q.index + 1}. ${q.prompt}`));
      if (q.type === "multiple_choice") {
        const list = node("ol", "review-options"); list.type = "A";
        q.options.forEach((text, index) => { if (!text) return; const li = node("li", "", text); if (index === q.answer) li.append(node("span", "review-option-label", "Candidate answer")); if (index === q.correct_index) li.append(node("span", "review-correct-label", "Correct answer")); list.append(li); }); item.append(list);
        if (q.answer === null) item.append(node("p", "review-muted", "Unanswered"));
      } else {
        item.append(node("small", "", "Candidate answer"), node("p", "audit-preserve", q.answer || "Unanswered"), node("small", "", "Marking reference"), node("p", "audit-preserve", q.key || "Not supplied"));
        if (q.grade_reason) item.append(node("small", "", "Grading explanation"), node("p", "audit-preserve", q.grade_reason));
      }
      item.append(node("small", "", "Awarded marks"), node("p", "", q.earned === null ? "Awaiting grading" : `${q.earned} / 1`)); el("audit-answers").append(item);
    }
  }
  function renderReviewer() {
    const d = data.review.decision, reviewer = data.reviewer;
    facts(el("reviewer-facts"), d ? [["Reviewer username", d.reviewer], ["User ID", String(d.reviewer_id)], ["Email", reviewer?.email || "Not registered"], ["Role", "Employer Company Personnel"], ["Review submitted", date(d.created_at)], ["Recorded decision", actions[d.action]]] : [["Review status", "No review submitted"]]);
    el("reviewer-history").replaceChildren();
    for (const entry of data.review.history) {
      const article = node("article", "audit-history-entry"); article.append(node("strong", "", actions[entry.action]), node("p", "review-muted", `Reviewed by ${entry.reviewer} · ${date(entry.created_at)}`));
      if (entry.action !== "disqualify") article.append(node("p", "", `Recorded result: ${score(entry.score, data.review.scores.possible)}`));
      article.append(node("span", "detail-field-label", "Recorded reason"), node("p", "audit-preserve", entry.reason)); el("reviewer-history").append(article);
    }
    if (!data.review.history.length) el("reviewer-history").append(node("p", "review-muted", "No review submitted."));
  }
  function renderCoverage() {
    const mon = data.monitoring, expected = Number(mon.expected_duration_ms || 0), coverage = mon.coverage || [];
    const video = coverage.find(c => c.stream === "video"), audio = coverage.find(c => c.stream === "audio");
    const value = n => `${time(Math.min(expected, Number(n || 0)))} of ${time(expected)}`;
    facts(el("audit-coverage"), [["Video checked", value(video?.analysed_ms)], ["Audio checked", value(audio?.analysed_ms)], ["Identity compared", value(video?.identity_checked_ms)], ["Flags and events", String(mon.events.length)]]);
    el("audit-processing").textContent = `Analysis status: ${mon.status === "analysed" ? "Finished" : mon.status === "processing" ? "In progress" : mon.status === "not_monitored" ? "Not available" : "Incomplete"}`;
  }
  function renderIdentity() {
    const id = data.identity;
    facts(el("identity-facts"), [["Identity check", id?.status === "passed" ? "Passed" : id?.status === "failed" ? "Failed" : "Not checked"], ["Face similarity score", pct(id?.face_similarity)], ["Name match score", pct(id?.name_similarity)]]);
    el("identity-images").hidden = true; el("identity-images").replaceChildren(); el("identity-toggle").textContent = "Open verification images";
    el("identity-toggle").hidden = !id?.card_available && !id?.selfie_available && !data.review.reference_available;
    const missing = !id?.card_available && !id?.selfie_available && !data.review.reference_available
      ? "Verification images unavailable or expired." : !id?.card_available ? "ID card image unavailable or expired." : !id?.selfie_available && !data.review.reference_available ? "Reference photo unavailable or expired." : "";
    let status = el("identity-image-status");
    if (!status) { status = node("div", "editor-feedback-host"); status.id = "identity-image-status"; el("identity-facts").after(status); }
    notice(caseFeedback, 'identity-image-status', missing, 'warning');
  }
  function eventOffset(event) { const part = (data.monitoring.parts || []).find(p => p.id === event.part_id); return event.start_ms + (part ? Date.parse(part.started_at) - Date.parse(data.review.started_at) : 0); }
  function renderFinding(finding) {
    const block = node("div", "audit-finding"); block.append(node("h4", "", "Recorded finding"), pill(findings[finding.assessment] || finding.assessment, finding.assessment === "dismissed", finding.assessment === "supports_violation"), node("span", "detail-field-label", "Reason for the finding"), node("p", "audit-preserve", finding.reason), node("p", "review-muted", `Recorded by ${finding.reviewer} · ${date(finding.created_at)}`)); return block;
  }
  function renderFlags() {
    for (const item of el("audit-flags").querySelectorAll("[data-message-id]")) caseFeedback.clear(item.dataset.messageId);
    el("audit-flags").replaceChildren();
    for (const event of [...data.monitoring.events].sort((a,b) => eventOffset(a) - eventOffset(b))) {
      const article = node("article", "card section-card review-event"), head = node("div", "review-event-top");
      head.append(node("h3", "", flags[event.event_type] || event.event_type.replaceAll("_", " ")), node("span", "review-event-time", `${time(eventOffset(event))}–${time(eventOffset(event) + event.end_ms - event.start_ms)}`)); article.append(head, node("p", "review-model-score", `Detection strength: ${pct(event.confidence)}`));
      const recorded = data.review.findings.filter(f => f.event_key === event.event_key);
      if (recorded.length) article.append(renderFinding(recorded[0]));
      else { const block = node("div", "audit-finding"); block.append(node("h4", "", "Recorded finding"), node("p", "", "No finding recorded.")); article.append(block); }
      if (recorded.length > 1) { const details = node("details"); details.append(node("summary", "", "Previous findings")); recorded.slice(1).forEach(f => details.append(renderFinding(f))); article.append(details); }
      if (event.evidence && data.recording.parts.some(p => p.id === event.part_id && p.total_bytes > 0)) {
        const play = button("Play evidence", () => {
          evidencePlayer?.destroy(); fullPlayer?.pause();
          const host = node("div"); play.after(host);
          evidencePlayer = new AuditPlayer(host, data.recording.parts, base(), { feedback: caseFeedback, event, beforePlay: () => fullPlayer?.pause(), onClose: () => { host.remove(); evidencePlayer = null; } });
        }, "secondary-button audit-play-evidence"); article.append(play);
      } else { const warning = node("div", "editor-feedback-host"); article.append(warning); caseFeedback.show({ id: `missing-evidence-${event.event_key}`, target: warning, text: "Evidence recording unavailable.", tone: "warning" }); }
      el("audit-flags").append(article);
    }
    if (!data.monitoring.events.length) el("audit-flags").append(node("p", "review-empty", "No flags or events recorded."));
  }
  function renderAudit() {
    const a = data.audit, entry = a.current || a.history[0];
    el("audit-status").textContent = auditStatus(a); el("audit-status").className = a.current ? "audit-done" : "audit-pending";
    el("audit-attribution").textContent = [entry ? `${a.current ? "Audited" : "Previously audited"} by ${entry.auditor_username} · ${date(entry.created_at)}` : ""].filter(Boolean).join(" · ");
    notice(caseFeedback, 'audit-blocker', !a.current ? a.blocked_reason || '' : '', 'warning');
    el("audit-action-bar").classList.toggle("is-audited", !!a.current);
    el("mark-audited").hidden = !!a.current;
    el("mark-audited").textContent = saving ? "Saving…" : "Mark as audited"; el("mark-audited").disabled = saving || !!a.current || !a.can_mark;
  }
  function selectSubtab(group, name) {
    for (const b of document.querySelectorAll(`[data-${group}-tab]`)) { const active = b.getAttribute(`data-${group}-tab`) === name; b.setAttribute("aria-selected", String(active)); b.tabIndex = active ? 0 : -1; }
    for (const pane of document.querySelectorAll(`[data-${group}-pane]`)) pane.hidden = pane.getAttribute(`data-${group}-pane`) !== name;
    pausePlayers();
  }
  for (const group of ["case", "integrity"]) {
    const buttons = [...document.querySelectorAll(`[data-${group}-tab]`)];
    buttons.forEach((b,index) => {
      b.addEventListener("click", () => selectSubtab(group, b.getAttribute(`data-${group}-tab`)));
      b.addEventListener("keydown", e => { const next = e.key === "ArrowRight" ? (index+1)%buttons.length : e.key === "ArrowLeft" ? (index+buttons.length-1)%buttons.length : e.key === "Home" ? 0 : e.key === "End" ? buttons.length-1 : null; if (next !== null) { e.preventDefault(); buttons[next].click(); buttons[next].focus(); } });
    });
  }
  function selectMainTab(name, loadHistory = true) {
    if (saving || publishing) return;
    if (name !== 'auditing') {
      ++examSerial; ++candidateSerial; ++caseSerial;
      examFeedback.dispose(); candidateFeedback.dispose(); caseFeedback.dispose(); clearPlayers();
      data = null; selectedInvitation = null; view('exam');
    }
    if (name !== 'publications') {
      ++historySerial; ++historyDetailSerial; clearTimeout(historySearchTimer);
      historyFeedback.dispose(); historyDetailFeedback.dispose();
    }
    pausePlayers(); message();
    for (const item of document.querySelectorAll("[data-main-tab]")) { const active = item.dataset.mainTab === name; item.classList.toggle("is-active", active); if (active) item.setAttribute("aria-current", "page"); else item.removeAttribute("aria-current"); }
    for (const pane of document.querySelectorAll("[data-main-pane]")) pane.hidden = pane.dataset.mainPane !== name;
    el("page-title").textContent = name === "auditing" ? "Assessment auditing" : name === "overview" ? "Overview" : "Publication history";
    if (name === "auditing" && el("exam-view").hidden === false) loadExams();
    if (name === "publications" && loadHistory) { ++historyDetailSerial; historyDetailFeedback.dispose(); el("history-detail-view").hidden = true; el("history-list-view").hidden = false; historyPage = 1; loadPublicationHistory(); }
  }
  for (const b of document.querySelectorAll("[data-main-tab]")) b.addEventListener("click", () => selectMainTab(b.dataset.mainTab));
  el("history-back").addEventListener("click", () => { ++historyDetailSerial; historyDetailFeedback.dispose(); el("history-detail-view").hidden = true; el("history-list-view").hidden = false; message(); el("history-list-view").scrollIntoView({block:"start",behavior:"instant"}); });
  function applyHistoryFilters() { clearTimeout(historySearchTimer); historyPage = 1; loadPublicationHistory(); }
  el("history-filters").addEventListener("submit", e => { e.preventDefault(); applyHistoryFilters(); });
  el("history-search").addEventListener("input", () => { clearTimeout(historySearchTimer); historySearchTimer = setTimeout(applyHistoryFilters, 250); });
  el("history-period").addEventListener("change", () => {
    const custom = el("history-period").value === "custom";
    for (const field of ["from", "to"]) { el(`history-${field}-label`).hidden = !custom; el(`history-${field}`).required = custom; }
    applyHistoryFilters();
  });
  for (const field of ["from", "to"]) el(`history-${field}`).addEventListener("change", applyHistoryFilters);
  el("history-clear").addEventListener("click", () => { el("history-filters").reset(); for (const field of ["from", "to"]) { el(`history-${field}-label`).hidden = true; el(`history-${field}`).required = false; } applyHistoryFilters(); });
  el("history-refresh").addEventListener("click", loadPublicationHistory);
  el("history-previous").addEventListener("click", () => { historyPage--; loadPublicationHistory(); });
  el("history-next").addEventListener("click", () => { historyPage++; loadPublicationHistory(); });
  el("exam-filters").addEventListener("submit", e => { e.preventDefault(); page = 1; loadExams(); });
  el("exam-audit").addEventListener("change", () => { page = 1; loadExams(); });
  el("exam-clear").addEventListener("click", () => { el("exam-filters").reset(); page = 1; loadExams(); });
  el("exam-refresh").addEventListener("click", loadExams); el("exam-previous").addEventListener("click", () => { page--; loadExams(); }); el("exam-next").addEventListener("click", () => { page++; loadExams(); });
  el("candidate-refresh").addEventListener("click", loadCandidates); el("candidate-search").addEventListener("input", renderCandidates); el("candidate-audit").addEventListener("change", renderCandidates);
  el("back-exams").addEventListener("click", () => {
    ++candidateSerial; ++caseSerial; clearPlayers();
    if (el("back-exams").dataset.history === "true") document.querySelector('[data-main-tab="publications"]').click();
    else { view("exam"); loadExams(); }
  });
  el("back-candidates").addEventListener("click", () => {
    ++caseSerial; clearPlayers();
    if (historyReportId) { const id = historyReportId; historyReportId = null; selectMainTab("publications",false); openPublishedResults(id); }
    else { view("candidate"); loadCandidates(); }
  });
  el("case-reload").addEventListener("click", () => { if (!saving) openCase(selectedInvitation, true); });
  el("recording-toggle").addEventListener("click", () => {
    if (!data) return;
    if (!el("full-player-host").hidden) { fullPlayer?.pause(); el("full-player-host").hidden = true; el("recording-toggle").textContent = "Open recording"; return; }
    if (!fullPlayer) fullPlayer = new AuditPlayer(el("full-player-host"), data.recording.parts, base(), { feedback: caseFeedback, beforePlay: () => evidencePlayer?.pause() });
    el("full-player-host").hidden = false; el("recording-toggle").textContent = "Close recording";
  });
  el("identity-toggle").addEventListener("click", () => {
    const host = el("identity-images"), scope = caseFeedback;
    for (const kind of ["card", "selfie"]) scope.clear(`image-${kind}`);
    if (!host.hidden) { host.hidden = true; host.replaceChildren(); el("identity-toggle").textContent = "Open verification images"; return; }
    const identity = data.identity;
    for (const [kind, label, available] of [["card", "ID card", identity?.card_available], ["selfie", "Verified reference photo", identity?.selfie_available || data.review.reference_available]]) {
      const figure = node("figure"); figure.append(node("figcaption", "", label));
      const imageError = () => { if (scope.active && figure.isConnected) scope.show({ id: `image-${kind}`, target: figure, tone: 'error', text: 'Image unavailable or expired. Close the images and open them again.' }); };
      host.append(figure);
      if (available) { const image = node("img"); image.alt = label; image.src = `${base()}/identity/${kind}`; image.addEventListener("error", () => { image.remove(); imageError(); }); figure.prepend(image); }
      else imageError();
    }
    host.hidden = false; el("identity-toggle").textContent = "Close verification images";
  });
  el('mark-audited').addEventListener('click', async () => {
    if (!data || saving || !data.audit.can_mark || data.audit.current) return;
    const scope = caseFeedback, serial = caseSerial, url = base();
    saving = true; renderAudit(); notice(scope, 'audit-save-message', '');
    try {
      const result = await api(`${url}/audit`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ expected_version: data.audit.version }) });
      if (serial !== caseSerial || !scope.active) return;
      data.audit = { ...data.audit, ...result.audit };
      scope.notify({ id: 'audit-saved', text: 'Candidate marked as audited.' });
    } catch (error) {
      if (serial !== caseSerial || !scope.active) return;
      if (error.status === 409) { data.audit.can_mark = false; data.audit.blocked_reason = null; }
      notice(scope, 'audit-save-message', error.message, 'error', error.status === 409 ? { label: 'Reload report', onClick: refreshCase } : undefined);
    } finally { saving = false; if (serial === caseSerial && data) renderAudit(); }
  });
  el("sign-out").addEventListener("click", async () => {
    el("sign-out").disabled = true;
    try { await api("/api/auth/logout", { method: "POST" }); location.assign("/"); }
    catch (error) { el("sign-out").disabled = false; message(error.message); }
  });
  el('publish-open').addEventListener('click', () => {
    if (!publication?.can_publish || publishing || publicationDialog) return;
    const previous = publicationDraft;
    publicationDraft = { assessmentId: selectedExam.id, expected_version: publication.version };
    publicationFeedback = feedback.scope('auditor-publication'); publicationStale = false;
    if (!previous || previous.assessmentId !== selectedExam.id) publicationForm.reset();
    const scope = publicationFeedback, draft = publicationDraft, outcomeScope = candidateFeedback;
    const dialog = scope.confirm({ id: 'publish-results', dialogId: 'publication-dialog', confirmButtonId: 'publish-confirm',
      cancelButtonId: 'publish-cancel', errorId: 'publication-error', title: 'Approve and publish results?',
      text: `${selectedExam.title}: publish results for all ${publication.candidate_count} candidates. Published results are final.`,
      content: publicationForm, confirmLabel: 'Confirm and publish', busyLabel: 'Publishing...',
      onConfirm: async () => {
        if (publicationStale || publishing) return false;
        const input = el('publication-note');
        const valid = input => input.value.trim().length > 0 && input.value.length <= 5000;
        if (!valid(input)) { scope.fieldError({ id: 'publication-remark', input, text: 'Enter a publication remark.', validate: valid }); input.focus(); return false; }
        scope.clear('publication-remark'); publishing = true;
        try {
          const result = await api(`/api/auditor/assessments/${draft.assessmentId}/publish`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ expected_version: draft.expected_version, confirmed: true, remark: input.value.trim() }) });
          if (!scope.active) return false;
          if (!result.publication || result.publication.status !== 'published') throw Error('Could not confirm publication. Try again.');
          publicationDraft = null;
          if (selectedExam?.id === draft.assessmentId) publication = result.publication;
          return true;
        } catch (error) {
          publicationStale = error.status === 409;
          dialog.setError(error.message + (publicationStale ? ' Cancel and refresh the candidates.' : ''));
          dialog.setConfirmDisabled(publicationStale); return false;
        } finally { publishing = false; }
      },
    });
    publicationDialog = dialog;
    dialog.result.then(accepted => {
      el('publication-form-home').append(publicationForm);
      if (publicationDialog === dialog) publicationDialog = null;
      scope.clear('publication-remark');
      if (selectedExam?.id !== draft.assessmentId) return;
      if (accepted) {
        renderPublication();
        if (outcomeScope.active) outcomeScope.notify({ id: 'results-published', text: 'Results published.' });
      } else if (publicationStale && scope.active) {
        notice(candidateFeedback, 'candidate-message', 'The assessment or audits changed. Refresh the candidates before publishing.', 'error', { label: 'Refresh candidates', onClick: loadCandidates });
        if (publication) publication.can_publish = false;
        renderPublication();
      }
    });
  });
  publicationForm.addEventListener('submit', event => { event.preventDefault(); el('publish-confirm')?.click(); });
  (async () => {
    try { const user = await api("/api/auth/me"); if (user.role !== "auditor") return location.replace("/"); el("account-name").textContent = user.username; await loadExams(); }
    catch (error) { message(error.message); }
  })();
})();
