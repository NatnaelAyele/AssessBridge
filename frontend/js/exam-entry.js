const element = (id) => document.getElementById(id);

function message(value) {
  element("entry-message").textContent = value;
  element("entry-message").hidden = !value;
}

function showAccountStep() {
  element("account-form").hidden = false;
  element("exam-step").hidden = true;
  element("exam-result").hidden = true;
  element("entry-step").textContent = "Step 1 of 2";
  element("entry-step-title").textContent = "Candidate account";
}

function showExamStep(user) {
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
  message("");
  const username = element("account-username").value.trim();
  const password = element("account-password").value;
  if (!username || !password) return message("Enter your candidate email and password.");
  const button = event.target.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    const response = await fetch("/api/auth/login", {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!response.ok) return message(response.status === 401 ? "Invalid candidate email or password." : "Sign-in is unavailable. Try again.");
    const user = await response.json();
    if (user.role !== "candidate") {
      await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
      return message("Sign in with a candidate account.");
    }
    element("account-password").value = "";
    showExamStep(user);
  } catch {
    message("Could not reach AssessBridge. Try again.");
  } finally {
    button.disabled = false;
  }
});

element("exam-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  message("");
  element("exam-result").hidden = true;
  const username = element("exam-username").value.trim();
  const password = element("exam-password").value;
  if (!username || !password) return message("Enter both credentials from your invitation email.");
  const button = event.target.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    const response = await fetch("/api/candidate/exam-entry", {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", ...await window.assessbridgeSeb.headers() },
      body: JSON.stringify({ username, password }),
    });
    if (!response.ok) {
      const detail = (await response.json().catch(() => ({}))).detail;
      if (response.status === 403 && typeof detail === "string" && detail.includes("Safe Exam Browser")) return message(detail);
      return message(response.status === 403 && [
        "This invitation belongs to another candidate account",
        "This invitation is addressed to a different candidate email",
        "Your candidate account needs a registered email address",
      ].includes(detail)
        ? detail : response.status === 401 ? "Invalid exam username or password."
          : response.status === 403 ? "Your account session ended. Sign in again." : "Could not check exam access. Try again.");
    }
    const exam = await response.json();
    element("exam-password").value = "";
    element("exam-title").textContent = exam.title;
    const format = (value) => new Date(value).toLocaleString("en-GB", { timeZone: "Africa/Nairobi", dateStyle: "medium", timeStyle: "short" });
    element("exam-window").textContent = `${format(exam.starts_at)} – ${format(exam.ends_at)} EAT · ${exam.duration_minutes} minutes`;
    element("exam-status").textContent = exam.window === "open" ? "Your exam room is ready."
      : exam.window === "submitted" ? "Your exam has been submitted. Open the room to see the confirmation and exit."
      : exam.window === "interrupted" ? "Your attempt was interrupted. Open the room to finish pending uploads and exit."
      : exam.window === "disqualified" ? "This attempt was disqualified after the exam was left. It cannot be reopened or submitted."
      : exam.window === "upcoming" ? "The exam room opens at the scheduled time. Return then and enter these credentials again."
        : "The scheduled access window has ended.";
    element("open-exam-room").hidden = !["open", "submitted", "interrupted"].includes(exam.window);
    element("open-exam-room").textContent = exam.window === "submitted" ? "View submission" : exam.window === "interrupted" ? "View attempt" : "Open assessment";
    element("leave-seb").hidden = ["open", "submitted", "interrupted"].includes(exam.window);
    element("exam-step").hidden = true;
    element("entry-step-title").textContent = exam.title;
    element("exam-result").hidden = false;
  } catch {
    message("Could not reach AssessBridge. Try again.");
  } finally {
    button.disabled = false;
  }
});

element("try-another-exam").addEventListener("click", async () => {
  message("");
  element("exam-form").reset();
  const response = await fetch("/api/auth/me", { credentials: "same-origin" }).catch(() => null);
  if (!response?.ok) return showAccountStep();
  showExamStep(await response.json());
  element("exam-username").focus();
});

element("switch-account").addEventListener("click", async () => {
  message("");
  try {
    const response = await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
    if (!response.ok) throw new Error();
    element("exam-form").reset();
    showAccountStep();
    element("account-username").focus();
  } catch {
    message("Could not sign out. Try again.");
  }
});

fetch("/api/auth/me", { credentials: "same-origin" })
  .then((response) => response.ok ? response.json() : null)
  .then((user) => { if (user?.role === "candidate") showExamStep(user); else showAccountStep(); })
  .catch(() => showAccountStep());
