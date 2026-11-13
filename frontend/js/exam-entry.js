const element = (id) => document.getElementById(id);

const feedback = AssessBridgeAccountFeedback;
let task = feedback.manager.scope('exam-entry');
let interacted = false;
for (const form of document.querySelectorAll('form')) form.addEventListener('input', () => { interacted = true; });
function message(value, scope = task, tone = 'error', action) {
  if (!value) scope.clear('status');
  else feedback.notice(scope, '#entry-message', value, tone, action);
}
function changeStep() {
  task.dispose();
  task = feedback.manager.scope('exam-entry');
}

function showAccountStep() {
  changeStep();
  element("account-form").hidden = false;
  element("exam-step").hidden = true;
  element("exam-result").hidden = true;
  element("entry-step").textContent = "Step 1 of 2";
  element("entry-step-title").textContent = "Candidate account";
}

function showExamStep(user) {
  changeStep();
  element("account-form").hidden = true;
  element("exam-step").hidden = false;
  element("exam-result").hidden = true;
  element("entry-step").textContent = "Step 2 of 2";
  element("entry-step-title").textContent = "Invitation credentials";
  element("signed-in-as").textContent = `Signed in as ${user.username}`;
  element("config-hint").hidden = !new URLSearchParams(location.search).has("exam");
}

element("account-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const requestTask = task;
  const button = event.target.querySelector('[type="submit"]');
  if (button.disabled) return;
  interacted = true;
  message("");
  if (!feedback.validate(requestTask, [
    [element("account-username"), feedback.email, "Enter a valid candidate email address."],
    [element("account-password"), feedback.present, "Enter your password."],
  ])) return;
  const username = element("account-username").value.trim();
  const password = element("account-password").value;

  button.disabled = true;
  try {
    const response = await fetch("/api/auth/login", {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!requestTask.active) return;
    if (!response.ok) return message(response.status === 401 ? "Candidate email or password is incorrect. If you just registered, verify your email first." : "Sign-in is unavailable. Try again.", requestTask);
    const user = await response.json();
    if (!requestTask.active) return;
    if (user.role !== "candidate") {
      await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
      return message("Sign in with a candidate account.", requestTask);
    }
    element("account-password").value = "";
    showExamStep(user);
  } catch {
    message("Could not reach AssessBridge. Try again.", requestTask);
  } finally {
    button.disabled = false;
  }
});

element("exam-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const requestTask = task;
  const button = event.target.querySelector('[type="submit"]');
  if (button.disabled) return;
  message("");
  if (!feedback.validate(requestTask, [
    [element("exam-username"), feedback.present, "Enter the exam username from your invitation."],
    [element("exam-password"), feedback.present, "Enter the exam password from your invitation."],
  ])) return;
  element("exam-result").hidden = true;
  const username = element("exam-username").value.trim();
  const password = element("exam-password").value;

  button.disabled = true;
  try {
    const response = await fetch("/api/candidate/exam-entry", {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", ...await window.assessbridgeSeb.headers() },
      body: JSON.stringify({ username, password }),
    });
    if (!requestTask.active) return;
    if (!response.ok) {
      const detail = await feedback.detail(response);
      const accessErrors = {
        'This invitation belongs to another candidate account': 'This invitation is linked to another candidate account. Use the account that received it.',
        'This invitation is addressed to a different candidate email': 'This invitation was sent to a different email. Sign in with that email.',
        'Your candidate account needs a registered email address': 'Your account needs a registered email. Contact the exam organizer.',
        'Open this assessment with the approved Safe Exam Browser configuration': 'Open the SEB link from the invitation for this exam.',
        'SEB configuration is not installed on this server': 'The SEB setup for this exam is unavailable. Contact the exam organizer.',
      };
      const signInAgain = response.status === 403 && !accessErrors[detail];
      return message(accessErrors[detail] || (response.status === 401 ? 'Exam username or password is incorrect.'
        : signInAgain ? 'Your account session ended. Sign in again.' : 'Could not check exam access. Try again.'), requestTask, 'error',
        signInAgain ? { label: 'Sign in again', onClick: () => { showAccountStep(); element('account-username').focus(); } } : undefined);
    }
    const exam = await response.json();
    if (!requestTask.active) return;
    requestTask.clearAll();
    element("exam-password").value = "";
    element("exam-title").textContent = exam.title;
    const format = (value) => new Date(value).toLocaleString("en-GB", { timeZone: "Africa/Nairobi", dateStyle: "medium", timeStyle: "short" });
    element("exam-window").textContent = `${format(exam.starts_at)} – ${format(exam.ends_at)} EAT · ${exam.duration_minutes} minutes`;
    const status = exam.window === "open" ? "Your exam room is ready."
      : exam.window === "submitted" ? "Your exam has been submitted. Open the room to see the confirmation and exit."
      : exam.window === "interrupted" ? "Your attempt was interrupted. Open the room to finish pending uploads and exit."
      : exam.window === "disqualified" ? "This attempt was disqualified after the exam was left. It cannot be reopened or submitted."
      : exam.window === "upcoming" ? "The exam room opens at the scheduled time. Return then and enter these credentials again."
        : "The scheduled access window has ended.";
    requestTask.show({ id: 'exam-status', target: '#exam-status', text: status, tone:
      ['disqualified', 'interrupted', 'closed'].includes(exam.window) ? 'warning'
        : ['open', 'submitted'].includes(exam.window) ? 'success' : 'info' });
    element("open-exam-room").hidden = !["open", "submitted", "interrupted"].includes(exam.window);
    element("open-exam-room").textContent = exam.window === "submitted" ? "View submission" : exam.window === "interrupted" ? "View attempt" : "Open assessment";
    element("leave-seb").hidden = ["open", "submitted", "interrupted"].includes(exam.window);
    element("exam-step").hidden = true;
    element("entry-step-title").textContent = exam.title;
    element("exam-result").hidden = false;
  } catch {
    message("Could not reach AssessBridge. Try again.", requestTask);
  } finally {
    button.disabled = false;
  }
});

element("try-another-exam").addEventListener("click", async () => {
  const requestTask = task;
  const button = element('try-another-exam');
  if (button.disabled) return;
  button.disabled = true;
  try {
    const response = await fetch('/api/auth/me', { credentials: 'same-origin' });
    if (!requestTask.active) return;
    if (response.status === 401 || response.status === 403) { showAccountStep(); return; }
    if (!response.ok) return message('Could not check your account. Try again.', requestTask);
    const user = await response.json();
    if (!requestTask.active) return;
    element('exam-form').reset();
    if (user.role !== 'candidate') return showAccountStep();
    showExamStep(user);
    element('exam-username').focus();
  } catch { message('Could not reach AssessBridge. Try again.', requestTask); }
  finally { button.disabled = false; }
});

element("switch-account").addEventListener("click", async () => {
  const requestTask = task;
  const button = element('switch-account');
  if (button.disabled) return;
  button.disabled = true;
  try {
    const response = await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' });
    if (!requestTask.active) return;
    if (!response.ok) return message('Could not sign out. Try again.', requestTask);
    element('exam-form').reset();
    showAccountStep();
    element('account-username').focus();
  } catch { message('Could not sign out. Try again.', requestTask); }
  finally { button.disabled = false; }
});

fetch('/api/auth/me', { credentials: 'same-origin' })
  .then(response => response.ok ? response.json() : null)
  .then(user => {
    if (interacted) return;
    if (user?.role === 'candidate') showExamStep(user); else showAccountStep();
  })
  .catch(() => { if (!interacted) showAccountStep(); });
