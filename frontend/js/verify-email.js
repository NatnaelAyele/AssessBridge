const feedback = AssessBridgeAccountFeedback;
const verification = feedback.manager.scope('verification');
const token = new URLSearchParams(location.hash.slice(1)).get('token');
history.replaceState(null, '', location.pathname);
const show = (text, tone, action) => feedback.notice(verification, '#verification-message', text, tone, action);
const anotherLink = { label: 'Request a new verification link', href: '/register' };
async function verify() {
  show('Checking verification link...', 'loading');
  try {
    const response = await fetch('/api/auth/verify-email', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token }),
    });
    if (response.ok) show('Your email is verified. You can sign in.', 'success', { label: 'Sign in', href: '/' });
    else if ([400, 422].includes(response.status)) show('This verification link is invalid or expired.', 'error', anotherLink);
    else show('Could not verify your email. Try again.', 'error', { label: 'Try again', onClick: verify });
  } catch {
    show('Could not reach AssessBridge. Try again.', 'error', { label: 'Try again', onClick: verify });
  }
}
if (!token) show('This verification link is incomplete.', 'error', anotherLink);
else verify();
