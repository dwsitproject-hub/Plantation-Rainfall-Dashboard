(() => {
  const form = document.getElementById('loginForm');
  const error = document.getElementById('error');
  const submit = document.getElementById('submit');
  const password = document.getElementById('password');
  const toggle = document.getElementById('togglePassword');
  const params = new URLSearchParams(location.search);

  // Only return to a page on this site; never to another host.
  const next = params.get('next');
  const safeNext = next && /^\/(?![\/\\])/.test(next) && !next.startsWith('/login') ? next : null;

  // DWS Hub single sign-on: show the button when the backend has it enabled.
  fetch('/api/sso/config').then(response => response.ok ? response.json() : null).then(sso => {
    if (!sso || !sso.ssoEnabled) return;
    const button = document.getElementById('ssoButton');
    button.textContent = sso.ssoLabel;
    button.href = '/api/sso/login' + (safeNext ? `?next=${encodeURIComponent(safeNext)}` : '');
    document.getElementById('ssoBlock').hidden = false;
    if (!sso.localLoginEnabled) {
      form.hidden = true;
      document.getElementById('ssoDivider').hidden = true;
      document.querySelector('.login-hint').textContent = 'Use your DWS Hub account.';
    }
  }).catch(() => {});
  if (params.get('sso_error')) {
    const ssoError = document.getElementById('ssoError');
    ssoError.textContent = params.get('sso_error');
    ssoError.hidden = false;
    document.getElementById('ssoBlock').hidden = false;
  }

  if (params.has('expired')) {
    const notice = document.getElementById('notice');
    notice.textContent = 'Your session ended. Please sign in again.';
    notice.hidden = false;
  }

  toggle.addEventListener('click', () => {
    const show = password.type === 'password';
    password.type = show ? 'text' : 'password';
    toggle.textContent = show ? 'Hide' : 'Show';
    toggle.setAttribute('aria-pressed', String(show));
    toggle.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
  });

  form.addEventListener('submit', async event => {
    event.preventDefault();
    error.hidden = true;
    const email = form.email.value.trim();
    if (!email || !password.value) {
      error.textContent = 'Enter your email and password.';
      error.hidden = false;
      return;
    }
    submit.disabled = true;
    submit.textContent = 'Signing in…';
    try {
      const response = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
        body: JSON.stringify({ email, password: password.value }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || `Sign-in failed (HTTP ${response.status}).`);
      location.href = safeNext || result.home || '/';
    } catch (problem) {
      error.textContent = problem.message;
      error.hidden = false;
      password.value = '';
      password.focus();
      submit.disabled = false;
      submit.textContent = 'Sign in';
    }
  });
})();
