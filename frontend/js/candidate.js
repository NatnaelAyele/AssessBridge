const byId = (id) => document.getElementById(id);

let dashboardLoad = 0;
function dashboardMessage(text = '', action) {
  candidateFeedback.show('dashboard-message', text, 'error', { action: action || (text ? { label: 'Retry', onClick: loadDashboard } : undefined) });
}

function selectTab(tab) {
  ++dashboardLoad; dashboardMessage();
  const history = tab === "history";
  byId("enter-panel").hidden = history;
  byId("history-panel").hidden = !history;
  for (const [id, selected] of [["enter-tab", !history], ["history-tab", history]]) {
    byId(id).classList.toggle("is-active", selected);
    byId(id).setAttribute("aria-selected", String(selected));
  }
}

function formatDate(iso) {
  return iso ? new Date(iso).toLocaleString("en-GB", {
    timeZone: "Africa/Nairobi", dateStyle: "medium", timeStyle: "short",
  }) + " EAT" : "—";
}

function node(tag, className, text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
}

const labels = {
  upcoming: "Upcoming", ready: "Ready to enter", in_progress: "In progress",
  interrupted: "Interrupted", submitted: "Submitted", disqualified: "Disqualified", missed: "Window closed",
};

function facts(items) {
  const list = node("dl", "assessment-facts");
  for (const [label, value] of items) {
    const entry = node("div", "assessment-fact");
    entry.append(node("dt", "", label), node("dd", "", value));
    list.append(entry);
  }
  return list;
}

function renderAssessment(assessment) {
  const card = node("article", "assessment-card");
  const heading = node("div", "assessment-heading");
  const title = node("h3", "", assessment.title);
  const status = node("span", `status status-${assessment.status}`, labels[assessment.status] || assessment.status);
  heading.append(title, status);
  card.append(heading);
  if (assessment.description) card.append(node("p", "assessment-description", assessment.description));
  card.append(facts([
    ["Window", `${formatDate(assessment.starts_at)} – ${formatDate(assessment.ends_at)}`],
    ["Duration", `${assessment.duration_minutes} minutes`],
    ["Questions", String(assessment.question_count)],
    ["Invitation email", assessment.email],
  ]));
  if (assessment.status === "in_progress") {
    card.append(node("p", "assessment-note", `Question ${assessment.current_question} of ${assessment.question_count} reached. Your timer continues while you are away.`));
  }
  if (!["missed", "disqualified", "interrupted"].includes(assessment.status)) {
    const actions = node("div", "assessment-actions");
    const launch = node("a", "primary-button", "Open in SEB");
    launch.href = assessment.seb_launch_url;
    const download = node("a", "secondary-button", "Download configuration");
    download.href = assessment.seb_download_url;
    actions.append(launch, download);
    card.append(actions);
  }
  return card;
}

function renderHistory(assessment) {
  const card = node("article", "assessment-card");
  const heading = node("div", "assessment-heading");
  heading.append(node("h3", "", assessment.title), node("span", `status status-${assessment.status}`, labels[assessment.status]));
  card.append(heading);
  if (assessment.description) card.append(node("p", "assessment-description", assessment.description));
  if (!assessment.publication) {
    card.append(node("p", "assessment-note", "Results not published."), facts([
      ["Submitted time", assessment.submitted_at ? formatDate(assessment.submitted_at) : "Not submitted"],
    ]));
    return card;
  }
  const mcq = assessment.multiple_choice_score, written = assessment.written_score, review = assessment.review;
  const mark = (earned, possible) => `${earned} / ${possible}${possible ? ` (${Math.round(100 * earned / possible)}%)` : ""}`;
  card.append(facts([
    ["Review outcome", review.outcome === "disqualified" ? "Disqualified" : "Eligible"],
    ["Original multiple choice score", mcq ? mark(mcq.earned, mcq.possible) : "No multiple choice questions"],
    ["Written question score", written ? `${mark(written.earned, written.possible)}${written.pending ? " · Incomplete" : ""}` : "No written questions"],
    ["Final score", review.outcome === "disqualified" ? "Disqualified" : review.score === null ? "Not graded" : mark(review.score, review.possible)],
    ["Reviewed time", formatDate(review.reviewed_at)],
    ["Submitted time", assessment.submitted_at ? formatDate(assessment.submitted_at) : "Not submitted"],
  ]));
  return card;
}

function renderList(id, items, renderer, emptyText) {
  const target = byId(id);
  target.replaceChildren();
  if (!items.length) {
    target.append(node("p", "empty-state", emptyText));
    return;
  }
  target.append(...items.map(renderer));
}

async function loadDashboard() {
  const serial = ++dashboardLoad;
  let response;
  try {
    response = await fetch('/api/candidate/assessments', { credentials: 'same-origin' });
    if (!response.ok) throw new Error('Could not load your assessments. Try again.');
    const data = await response.json();
    if (serial !== dashboardLoad) return;
    dashboardMessage();
    const assessments = data.assessments || [];
    renderList('active-list', assessments.filter(item => !['submitted', 'disqualified', 'interrupted'].includes(item.status)), renderAssessment,
      'No invitations are assigned to your registered email yet.');
    renderList('history-list', assessments.filter(item => ['submitted', 'disqualified', 'interrupted'].includes(item.status)), renderHistory,
      'No completed exams yet.');
  } catch {
    if (serial !== dashboardLoad) return;
    const expired = response?.status === 401, denied = response?.status === 403;
    dashboardMessage(expired ? 'Your session ended. Sign in again.' : denied ? 'Your account cannot access these assessments.' : 'Could not load your assessments. Try again.',
      expired || denied ? { label: 'Sign in', href: '/' } : undefined);
  }
}

byId("enter-tab").addEventListener("click", () => selectTab("enter"));
byId("history-tab").addEventListener("click", async () => {
  selectTab("history");
  await loadDashboard();
});
byId("sign-out").addEventListener("click", async () => {
  const button = byId("sign-out");
  button.disabled = true;
  try {
    const response = await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
    if (!response.ok) throw new Error();
    location.assign("/");
  } catch {
    button.disabled = false;
    candidateFeedback.show("dashboard-message", "Could not sign out. Try again.");
  }
});

(async () => {
  try {
    const response = await fetch("/api/auth/me", { credentials: "same-origin" });
    if (!response.ok) return location.replace("/");
    const user = await response.json();
    if (user.role !== "candidate") return location.replace("/");
    byId("account-name").textContent = user.username;
    await loadDashboard();
  } catch (error) {
    dashboardMessage("Could not load your assessments. Try again.");
    byId("active-list").replaceChildren();
  }
})();
