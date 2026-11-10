const message = document.getElementById("verification-message");
const token = new URLSearchParams(location.hash.slice(1)).get("token");
history.replaceState(null, "", location.pathname);

if (!token) {
  message.textContent = "This verification link is incomplete.";
  message.classList.add("verification-error");
  document.getElementById("retry-link").hidden = false;
} else {
  fetch("/api/auth/verify-email", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  }).then(async (response) => {
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || "Verification failed.");
    message.textContent = "Your email is verified. Sign in to your dashboard, then use the SEB exam link from your invitation.";
    message.classList.add("verification-success");
    document.getElementById("continue-link").hidden = false;
  }).catch((error) => {
    message.textContent = error.message;
    message.classList.add("verification-error");
    document.getElementById("retry-link").hidden = false;
  });
}
