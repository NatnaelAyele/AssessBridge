/* Candidate interruption evidence shared by the reviewer and auditor views. */
(() => {
  const node = (tag, className = "", value = "") => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    element.textContent = value;
    return element;
  };
  const date = value => value ? new Date(value).toLocaleString("en-GB", {
    timeZone: "Africa/Nairobi", dateStyle: "medium", timeStyle: "medium",
  }) + " EAT" : "Time unavailable";
  const elapsed = value => {
    const seconds = Math.max(0, Math.round(value || 0));
    return seconds >= 60 ? `${Math.floor(seconds / 60)} min ${seconds % 60} sec` : `${seconds} sec`;
  };
  const titles = { connection_lost: "Exam contact lost", monitoring_unavailable: "Exam page reported unavailable devices or storage",
    session_restart: "Exam reopened", platform_unavailable: "Platform unavailable", unknown: "Interruption detected" };
  const outcomes = { resumed: "Resumed", limit_reached: "Recovery limit reached", time_expired: "Time expired",
    confirmed_exit: "Candidate selected Exit SEB", awaiting_recovery: "Awaiting recovery" };
  const findings = { explained: "Explained", unresolved: "Unresolved", needs_attention: "Needs attention" };
  const recordingStatus = { recording: "Upload pending", complete: "Upload complete", incomplete: "Recording incomplete",
    unavailable: "Recording unavailable", deleted: "Recording expired" };

  function renderFinding(item) {
    const block = node("div", "connectivity-finding");
    block.append(node("strong", "", findings[item.finding] || item.finding),
      node("p", "", item.reason), node("small", "", `Recorded by ${item.reviewer} · ${date(item.created_at)}`));
    return block;
  }
  window.renderConnectivitySummary = (host, report, onOpen) => {
    host.replaceChildren();
    const data = report.connectivity_report || { incidents: [], recovery_seconds: 0 };
    const count = data.incidents.length;
    if (!count && !(data.confirmed_exits || []).length) { host.hidden = true; return; }
    host.hidden = false;
    host.append(node("h3", "", "Connectivity & interruptions"),
      node("p", "", `${count} ${count === 1 ? "interruption" : "interruptions"} · ${elapsed(data.recovery_seconds)} recovery used`));
    const open = node("button", "secondary-button", "View interruptions");
    open.type = "button"; open.addEventListener("click", onOpen); host.append(open);
  };
  window.renderConnectivityCase = (host, report, options = {}) => {
    host.replaceChildren(); host.hidden = false;
    const data = report.connectivity_report || { incidents: [], confirmed_exits: [], recovery_seconds: 0,
      recording_status: "unavailable", coverage_ready: false };
    const summary = node("div", "card section-card connectivity-summary");
    summary.append(node("h3", "", "Session status"));
    const facts = node("div", "connectivity-summary-facts");
    for (const [label, value] of [["Interruptions", String(data.incidents.length)],
      ["Recovery used", `${elapsed(data.recovery_seconds)} of 10 min`],
      ["Recording", recordingStatus[data.recording_status] || "Status unavailable"],
      ["Attempt", ({ submitted: "Submitted", interrupted: "Interrupted", disqualified: "Disqualified",
        in_progress: "In progress" })[report.attempt_state] || "Not started"]]) {
      const cell = node("div", "connectivity-summary-fact"); cell.append(node("span", "", label), node("strong", "", value)); facts.append(cell);
    }
    summary.append(facts); host.append(summary);
    if (!data.incidents.length && !data.confirmed_exits?.length) {
      host.append(node("p", "card section-card", "No interruptions recorded.")); return;
    }
    for (const incident of data.incidents) {
      const card = node("article", "card section-card connectivity-incident");
      const head = node("div", "connectivity-incident-head");
      head.append(node("h3", "", titles[incident.trigger] || titles.unknown),
        node("span", "review-badge", outcomes[incident.outcome] || "Outcome unknown"));
      card.append(head);
      const cause = incident.trigger === "platform_unavailable" ? "Platform interruption recorded" : "Cause unknown";
      const times = incident.ended_at
        ? `${date(incident.started_at)} – ${date(incident.ended_at)} · ${elapsed(incident.duration_seconds)}`
        : `${date(incident.started_at)} · End time unavailable`;
      card.append(node("p", "connectivity-incident-time", times), node("p", "", cause));
      if (incident.face_checks.length) {
        for (const check of incident.face_checks) {
          const similarity = check.similarity !== null && check.similarity !== undefined && Number.isFinite(Number(check.similarity))
            ? ` · Similarity ${Number(check.similarity).toFixed(2)} / 1` : "";
          const result = check.status === "unusable" ? "Could not check" : check.passed ? "Passed" : "Not matched";
          card.append(node("p", "", `Recovery face check: ${result}${similarity} · ${date(check.at)}`));
        }
      } else if (incident.trigger === "session_restart") card.append(node("p", "", "Recovery face check: Not completed"));
      if (["unavailable", "deleted"].includes(data.recording_status)) card.append(node("p", "review-muted", "Recording unavailable."));
      else if (!data.coverage_ready) card.append(node("p", "review-muted", "Recording coverage pending."));
      else if (incident.gaps.length) {
        for (const gap of incident.gaps) card.append(node("p", "review-muted", `Estimated missing ${gap.stream}: ${elapsed(gap.estimated_seconds)}`));
      } else card.append(node("p", "review-muted", "No confirmed recording gap around this interruption."));
      if (incident.earlier_recording_available) card.append(node("p", "review-muted", "Earlier recording available."));
      if (incident.later_recording_part_available) card.append(node("p", "review-muted", "New recording part available."));
      if (incident.delayed_upload_received) card.append(node("p", "review-muted", "Earlier recording part uploaded after recovery."));
      if (options.onRecording) {
        const open = node("button", "secondary-button", "View nearby recording"); open.type = "button";
        open.disabled = !options.recordingAvailable; open.addEventListener("click", () => options.onRecording(incident));
        card.append(open);
      }
      const history = (report.connectivity_findings || []).filter(item => item.incident_id === incident.id);
      if (history.length) {
        card.append(renderFinding(history[0]));
        if (history.length > 1) {
          const older = node("details"), label = node("summary", "", "Previous findings"); older.append(label);
          history.slice(1).forEach(item => older.append(renderFinding(item))); card.append(older);
        }
      } else card.append(node("p", "review-muted", "No finding recorded."));
      if (options.onSave && !options.readOnly && !report.published && incident.outcome !== "awaiting_recovery") {
        if (!report.can_decide) card.append(node("p", "review-muted", report.decision_blocked_reason || "Review unavailable."));
        else {
          const form = node("form", "connectivity-finding-form");
          const choiceLabel = node("label", "", "Finding"), choice = node("select");
          choice.required = true; choice.add(new Option("Choose a finding", ""));
          for (const [value, label] of Object.entries(findings)) choice.add(new Option(label, value));
          choiceLabel.append(choice);
          const reasonLabel = node("label", "", "Reason"), reason = node("textarea");
          reason.required = true; reason.minLength = 5; reason.maxLength = 5000; reason.rows = 3;
          reasonLabel.append(reason);
          const draft = options.draft?.(incident.id);
          if (draft) { choice.value = draft.finding; reason.value = draft.reason; }
          const remember = () => options.onDraft?.(incident.id, { finding: choice.value, reason: reason.value });
          choice.addEventListener("change", remember); reason.addEventListener("input", remember);
          const save = node("button", "secondary-button", "Save finding"); save.type = "submit";
          const feedback = node("p", "feedback-message"); feedback.hidden = true; feedback.setAttribute("role", "status");
          form.append(choiceLabel, reasonLabel, save, feedback);
          form.addEventListener("submit", async event => {
            event.preventDefault(); if (!form.reportValidity()) return;
            save.disabled = true; feedback.hidden = true;
            try { await options.onSave(incident, choice.value, reason.value.trim()); }
            catch (error) { feedback.textContent = error.message; feedback.dataset.tone = "error"; feedback.hidden = false; save.disabled = false; }
          });
          card.append(form);
        }
      }
      host.append(card);
    }
    for (const item of data.confirmed_exits || []) {
      const card = node("article", "card section-card connectivity-incident");
      card.append(node("h3", "", "Candidate selected Exit SEB"), node("p", "", date(item.at)));
      host.append(card);
    }
  };
})();
