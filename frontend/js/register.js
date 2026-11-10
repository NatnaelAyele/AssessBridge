const field = (id) => document.getElementById(id);
const invitedEmail = new URLSearchParams(location.search).get("email");
if (invitedEmail) {
  field("email").value = invitedEmail;
  field("resend-email").value = invitedEmail;
}

function status(id, text, success = false) {
  const target = field(id);
  target.textContent = text;
  target.hidden = false;
  target.classList.toggle("success", success);
}

field("show-resend").addEventListener("click", () => {
  const panel = field("resend-panel");
  panel.hidden = !panel.hidden;
  field("show-resend").setAttribute("aria-expanded", String(!panel.hidden));
  if (!panel.hidden) {
    field("resend-email").value ||= field("email").value.trim();
    field("resend-email").focus();
  }
});

field("register-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  field("register-message").hidden = true;
  const email = field("email").value.trim();
  const password = field("password").value;
  if (!email || !password) return status("register-message", "Enter your email and password.");
  if (!field("email").checkValidity()) return status("register-message", "Enter a valid email address.");
  if (password !== field("confirm-password").value) return status("register-message", "Passwords do not match.");
  const button = event.target.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    const response = await fetch("/api/auth/register-candidate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!response.ok) {
      const result = await response.json().catch(() => ({}));
      return status("register-message", response.status === 409
        ? "This email already has an account. Sign in, or request a new verification link below."
        : response.status === 422 ? "Use a valid email and a password of 12 to 72 UTF-8 bytes."
          : result.detail || "Registration is unavailable. Try again.");
    }
    field("register-form").hidden = true;
    field("register-title").textContent = "One more step";
    field("registered-email").textContent = email.toLowerCase();
    field("registered-panel").hidden = false;
    field("resend-email").value = email;
    field("password").value = "";
    field("confirm-password").value = "";
  } catch {
    status("register-message", "Could not reach AssessBridge. Try again.");
  } finally {
    button.disabled = false;
  }
});

field("resend-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  field("resend-message").hidden = true;
  const email = field("resend-email").value.trim();
  const password = field("resend-password").value;
  if (!email || !password) return status("resend-message", "Enter your email and password.");
  const button = event.target.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    const response = await fetch("/api/auth/resend-verification", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: email, password }),
    });
    const result = await response.json().catch(() => ({}));
    status("resend-message", response.ok ? "A new verification link was sent. Check your email."
      : result.detail || "Could not send a new link.", response.ok);
    if (response.ok) field("resend-password").value = "";
  } catch {
    status("resend-message", "Could not reach AssessBridge. Try again.");
  } finally {
    button.disabled = false;
  }
});
