const field = id => document.getElementById(id);
const feedback = AssessBridgeAccountFeedback;
const registration = feedback.manager.scope('registration');
let resend = feedback.manager.scope('resend');
const invitedEmail = new URLSearchParams(location.search).get('email');
if (invitedEmail) field('email').value = field('resend-email').value = invitedEmail;
const passwordValid = input => Array.from(input.value).length >= 12 && new TextEncoder().encode(input.value).length <= 72;
const confirmationValid = input => Boolean(input.value) && input.value === field('password').value;
field('password').addEventListener('input', () => {
  if (confirmationValid(field('confirm-password'))) registration.clear('field-confirm-password');
});
field('show-resend').addEventListener('click', () => {
  const panel = field('resend-panel');
  panel.hidden = !panel.hidden;
  field('show-resend').setAttribute('aria-expanded', String(!panel.hidden));
  if (panel.hidden) resend.dispose();
  else {
    resend = feedback.manager.scope('resend');
    field('resend-email').value ||= field('email').value.trim();
    field('resend-email').focus();
  }
});
field('register-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.target.querySelector('[type="submit"]');
  if (button.disabled) return;
  registration.clear('status');
  if (!feedback.validate(registration, [
    [field('email'), feedback.email, 'Enter a valid email address.'],
    [field('password'), passwordValid, 'Use at least 12 characters, up to 72 bytes. Accented letters and symbols may use more than one byte.'],
    [field('confirm-password'), confirmationValid, 'Enter the same password in both fields.'],
  ])) return;
  const email = field('email').value.trim();
  button.disabled = true;
  try {
    const response = await fetch('/api/auth/register-candidate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password: field('password').value }),
    });
    if (!response.ok) {
      feedback.notice(registration, '#register-message', response.status === 409
        ? 'This email already has an account. Sign in or request a new verification link.'
        : response.status === 422 ? 'Check your email and password requirements, then try again.'
          : 'Could not complete registration. Try again. If you received a verification email, open its link first.', 'error',
      response.status === 409 ? { label: 'Sign in', href: '/' } : undefined);
      return;
    }
    registration.clearAll();
    field('register-form').hidden = true;
    field('register-title').textContent = 'Check your email';
    field('registered-panel').hidden = false;
    feedback.notice(registration, '#registered-panel', `Open the verification link sent to ${email.toLowerCase()}, then sign in.`, 'success',
      { label: 'Sign in after verification', href: '/' });
    field('resend-email').value = email;
    field('password').value = field('confirm-password').value = '';
  } catch {
    feedback.notice(registration, '#register-message', 'Could not confirm registration. Check your email for a verification link before trying again.');
  } finally { button.disabled = false; }
});
field('resend-form').addEventListener('submit', async event => {
  event.preventDefault();
  const task = resend;
  const button = event.target.querySelector('[type="submit"]');
  if (button.disabled || !task.active) return;
  task.clear('status');
  if (!feedback.validate(task, [
    [field('resend-email'), feedback.email, 'Enter a valid email address.'],
    [field('resend-password'), feedback.present, 'Enter your password.'],
  ])) return;
  button.disabled = true;
  try {
    const response = await fetch('/api/auth/resend-verification', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: field('resend-email').value.trim(), password: field('resend-password').value }),
    });
    if (!task.active) return;
    feedback.notice(task, '#resend-message', response.ok ? 'Verification email sent. Open the link in your inbox.'
      : response.status === 401 ? 'Email or password is incorrect, or this account is already verified.'
        : response.status === 429 ? 'Wait one minute before requesting another link.'
          : 'Could not send a verification email. Try again.', response.ok ? 'success' : 'error');
    if (response.ok) field('resend-password').value = '';
  } catch {
    feedback.notice(task, '#resend-message', 'Could not reach AssessBridge. Try again.');
  } finally { button.disabled = false; }
});
