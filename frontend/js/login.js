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

function setFieldError(input, show) {
  input.setAttribute("aria-invalid", String(show));
  document.querySelector(`#${input.id}-error`).hidden = !show;
}

function showMessage(message, isError = true) {
  formMessage.textContent = message;
  formMessage.classList.toggle("error", isError);
  formMessage.hidden = false;
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
  signOutMessage.hidden = true;
}

for (const input of [username, password]) {
  input.addEventListener("input", () => {
    if (input.value.trim()) setFieldError(input, false);
    formMessage.hidden = true;
  });
}

togglePassword.addEventListener("click", () => {
  const isVisible = password.type === "password";
  password.type = isVisible ? "text" : "password";
  togglePassword.setAttribute("aria-label", isVisible ? "Hide password" : "Show password");
  togglePassword.setAttribute("aria-pressed", String(isVisible));
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const missingUsername = !username.value.trim();
  const missingPassword = !password.value.trim();
  setFieldError(username, missingUsername);
  setFieldError(password, missingPassword);
  formMessage.hidden = true;

  if (missingUsername || missingPassword) {
    (missingUsername ? username : password).focus();
    return;
  }

  signInButton.disabled = true;
  try {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ username: username.value.trim(), password: password.value }),
    });
    if (!response.ok) {
      showMessage(response.status === 401 ? "Invalid email, username, or password." : "Sign-in is unavailable. Please try again.");
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
  signOutButton.disabled = true;
  try {
    const response = await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
    if (!response.ok) throw new Error("Sign-out failed");
    signedInPanel.hidden = true;
    form.hidden = false;
    form.reset();
    username.focus();
  } catch {
    signOutMessage.textContent = "Could not sign out. Please try again.";
    signOutMessage.hidden = false;
  } finally {
    signOutButton.disabled = false;
  }
});

fetch("/api/auth/me", { credentials: "same-origin" })
  .then((response) => response.ok ? response.json() : null)
  .then((user) => { if (user) showAuthenticated(user); })
  .catch(() => {});
