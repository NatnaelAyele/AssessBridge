const form = document.querySelector("#login-form");
const username = document.querySelector("#username");
const password = document.querySelector("#password");
const togglePassword = document.querySelector("#toggle-password");
const formMessage = document.querySelector("#form-message");
const signedInPanel = document.querySelector("#signed-in-panel");
const signOutButton = document.querySelector("#sign-out-button");
const signOutMessage = document.querySelector("#sign-out-message");
const signInButton = form.querySelector('button[type="submit"]');

const roleNames = {
  candidate: "Candidate",
  employer_personnel: "Employer Company Personnel",
  auditor: "Employer Company Representative Auditor",
};

document.querySelector("#year").textContent = new Date().getFullYear();

const feedback = AssessBridgeAccountFeedback;
const loginTask = feedback.manager.scope("sign-in");
const signOutTask = feedback.manager.scope("sign-out");
let interacted = false;
form.addEventListener("input", () => { interacted = true; });
function showMessage(text) {
  feedback.notice(loginTask, formMessage, text);
}

function showAuthenticated(user) {
  password.value = "";
  if (user.role === "employer_personnel") {
    window.location.assign("/employer");
    return;
  }
  if (user.role === "candidate") {
    window.location.assign("/candidate");
    return;
  }
  if (user.role === "auditor") {
    window.location.assign("/auditor");
    return;
  }
  document.querySelector("#signed-in-user").textContent = user.username;
  document.querySelector("#signed-in-role").textContent = roleNames[user.role] || user.role;
  form.hidden = true;
  signedInPanel.hidden = false;
  loginTask.clearAll();
  signOutTask.clearAll();
}

togglePassword.addEventListener("click", () => {
  const isVisible = password.type === "password";
  password.type = isVisible ? "text" : "password";
  togglePassword.setAttribute("aria-label", isVisible ? "Hide password" : "Show password");
  togglePassword.setAttribute("aria-pressed", String(isVisible));
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  if (signInButton.disabled) return;
  interacted = true;
  loginTask.clear('status');
  if (!feedback.validate(loginTask, [
    [username, feedback.present, "Enter your email or username."],
    [password, feedback.present, "Enter your password."],
  ])) return;

  signInButton.disabled = true;
  try {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ username: username.value.trim(), password: password.value }),
    });
    if (!response.ok) {
      showMessage(response.status === 401 ? "Email, username, or password is incorrect. If you just registered, verify your email first." : "Sign-in is unavailable. Please try again.");
      return;
    }
    showAuthenticated(await response.json());
  } catch {
    showMessage("Could not reach AssessBridge. Please try again.");
  } finally {
    signInButton.disabled = false;
  }
});

signOutButton.addEventListener("click", async () => {
  if (signOutButton.disabled) return;
  signOutButton.disabled = true;
  try {
    const response = await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
    if (!response.ok) throw new Error("Sign-out failed");
    signedInPanel.hidden = true;
    form.hidden = false;
    form.reset();
    signOutTask.clearAll();
    username.focus();
  } catch {
    feedback.notice(signOutTask, signOutMessage, "Could not sign out. Try again.");
  } finally {
    signOutButton.disabled = false;
  }
});

fetch("/api/auth/me", { credentials: "same-origin" })
  .then((response) => response.ok ? response.json() : null)
  .then((user) => { if (user && !interacted) showAuthenticated(user); })
  .catch(() => {});
