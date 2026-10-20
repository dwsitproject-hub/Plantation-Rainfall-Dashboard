(() => {
  const $ = selector => document.querySelector(selector);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const SECTION_LABELS = { all_regions: 'All Regions', inhouse: 'In-house Plantation', user_admin: 'User management' };
  const EVENT_LABELS = {
    login_succeeded: 'Signed in', login_failed: 'Failed sign-in', login_blocked: 'Sign-in blocked (locked)',
    logout: 'Signed out', user_created: 'User registered', user_updated: 'User updated',
    user_unlocked: 'Account unlocked', password_reset: 'Password reset by admin', password_changed: 'Password changed',
    sso_login_succeeded: 'Signed in with DWS Hub', sso_linked: 'Linked to DWS Hub account', sso_launch_restarted: 'DWS Hub launch (sign-in restarted)',
  };
  let state = { users: [], roles: [], me: null, minLength: 12, ssoEnabled: false };
  let editing = null;

  async function api(url, body) {
    const options = body === undefined ? {} : {
      method: 'POST', body: JSON.stringify(body),
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
    };
    const response = await fetch(url, options);
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || `Request failed (HTTP ${response.status}).`);
    return result;
  }

  const when = iso => iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : 'Never';

  function generatePassword() {
    const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789';
    const bytes = crypto.getRandomValues(new Uint32Array(16));
    let value = Array.from(bytes, n => alphabet[n % alphabet.length]).join('');
    return value.slice(0, 6) + '-' + value.slice(6, 11) + '-' + value.slice(11, 15) + (Number(bytes[0] % 8) + 2);
  }

  function showMessage(element, text, ok = false) {
    element.textContent = text;
    element.classList.toggle('ok', ok);
    element.hidden = !text;
  }

  function roleOptions(select, selected) {
    select.innerHTML = state.roles.map(role =>
      `<option value="${escape(role.role_code)}"${role.role_code === selected ? ' selected' : ''}>${escape(role.role_name)}</option>`).join('');
  }

  function renderRoles() {
    $('#roleRows').innerHTML = state.roles.map(role => `<tr>
      <td><span class="badge role">${escape(role.role_name)}</span></td><td>${escape(role.description)}</td>
      ${['all_regions', 'inhouse', 'user_admin'].map(section => role.sections.includes(section)
        ? '<td class="yes" aria-label="Yes">✓</td>' : '<td class="no" aria-label="No">—</td>').join('')}</tr>`).join('');
    roleOptions($('#createRole'), state.roles.find(role => role.role_code === 'regional') ? 'regional' : state.roles[0]?.role_code);
    updateRoleHelp();
  }

  function updateRoleHelp() {
    const role = state.roles.find(item => item.role_code === $('#createRole').value);
    $('#roleHelp').textContent = role ? `Can open: ${role.sections.map(s => SECTION_LABELS[s] || s).join(', ') || 'nothing'}.` : '';
  }

  function renderUsers() {
    const term = $('#search').value.trim().toLowerCase();
    const users = state.users.filter(user => !term ||
      [user.fullName, user.email].some(value => (value || '').toLowerCase().includes(term)));
    $('#userRows').innerHTML = users.length ? users.map(user => {
      const isMe = state.me && user.userId === state.me.userId;
      const status = !user.isActive ? '<span class="badge disabled">Disabled</span>'
        : user.isLocked ? '<span class="badge locked">Locked</span>' : '<span class="badge active">Active</span>';
      return `<tr>
        <td class="user-cell"><strong>${escape(user.fullName)}${isMe ? '<span class="badge you">You</span>' : ''}</strong>
          <small>${escape(user.email)}${user.ssoLinked ? ' · <span class="badge role">DWS Hub</span>' : ''}</small></td>
        <td><span class="badge role">${escape(user.roleName)}</span></td>
        <td>${status}</td>
        <td class="muted">${when(user.lastLoginAt)}</td>
        <td><div class="row-actions">
          <button type="button" data-action="edit" data-id="${user.userId}">Edit</button>
          <button type="button" data-action="reset" data-id="${user.userId}">Reset password</button>
          ${user.isLocked ? `<button type="button" data-action="unlock" data-id="${user.userId}">Unlock</button>` : ''}
          ${isMe ? '' : `<button type="button" data-action="toggle" data-id="${user.userId}" class="${user.isActive ? 'danger' : ''}">${user.isActive ? 'Disable' : 'Enable'}</button>`}
        </div></td></tr>`;
    }).join('') : `<tr><td colspan="5" class="muted">${term ? 'No users match your search.' : 'No users yet.'}</td></tr>`;

    const admins = state.users.filter(user => user.isActive && user.sections.includes('user_admin'));
    $('#kpiTotal').textContent = state.users.length;
    $('#kpiActive').textContent = state.users.filter(user => user.isActive).length;
    $('#kpiAdmins').textContent = admins.length;
    $('#kpiLocked').textContent = state.users.filter(user => user.isLocked).length;
  }

  async function renderAudit() {
    try {
      const { events } = await api('/api/admin/audit');
      $('#auditRows').innerHTML = events.length ? events.map(event => `<tr>
        <td class="muted">${when(event.occurred_at)}</td>
        <td>${escape(EVENT_LABELS[event.action] || event.action)}</td>
        <td>${escape(event.actor || '—')}</td>
        <td>${escape(event.target || event.email || '—')}</td>
        <td class="muted">${escape(event.ip_address || '—')}</td></tr>`).join('')
        : '<tr><td colspan="5" class="muted">No activity yet.</td></tr>';
    } catch (problem) {
      $('#auditRows').innerHTML = `<tr><td colspan="5" class="muted">${escape(problem.message)}</td></tr>`;
    }
  }

  async function load() {
    try {
      const [data, me] = await Promise.all([api('/api/admin/users'), api('/api/me')]);
      state = { users: data.users, roles: data.roles, me, minLength: data.passwordMinLength, ssoEnabled: data.ssoEnabled };
      $('#passwordRule').textContent = state.ssoEnabled
        ? `Leave empty for users who sign in with DWS Hub. Otherwise at least ${state.minLength} characters with letters and numbers.`
        : `At least ${state.minLength} characters with letters and numbers. Share it with the user securely; they can change it after signing in.`;
      $('#createPassword').required = !state.ssoEnabled;
      renderRoles();
      renderUsers();
      renderAudit();
    } catch (problem) {
      showMessage($('#error'), problem.message);
    }
  }

  // ---- register ---------------------------------------------------------
  $('#createRole').addEventListener('change', updateRoleHelp);
  $('#generate').addEventListener('click', () => { $('#createPassword').value = generatePassword(); });
  $('#createForm').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.target;
    const button = form.querySelector('button[type=submit]');
    const data = Object.fromEntries(new FormData(form));
    button.disabled = true;
    try {
      const user = await api('/api/admin/users', data);
      showMessage($('#createMessage'), `Registered ${user.fullName} (${user.email}) as ${user.roleName}. They sign in with this email.`, true);
      form.reset();
      roleOptions($('#createRole'), data.role);
      updateRoleHelp();
      await load();
    } catch (problem) {
      showMessage($('#createMessage'), problem.message);
    } finally {
      button.disabled = false;
    }
  });

  // ---- row actions ------------------------------------------------------
  $('#search').addEventListener('input', renderUsers);
  $('#userRows').addEventListener('click', async event => {
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    const user = state.users.find(item => item.userId === Number(button.dataset.id));
    if (!user) return;
    editing = user;
    if (button.dataset.action === 'edit') {
      const form = $('#editForm');
      $('#editSub').textContent = user.email;
      form.fullName.value = user.fullName;
      form.email.value = user.email;
      roleOptions($('#editRole'), user.role);
      form.isActive.checked = user.isActive;
      form.isActive.disabled = state.me && user.userId === state.me.userId;
      showMessage($('#editMessage'), '');
      $('#editDialog').showModal();
    } else if (button.dataset.action === 'reset') {
      $('#resetSub').textContent = `${user.fullName} (${user.email})`;
      $('#resetPassword').value = '';
      showMessage($('#resetMessage'), '');
      $('#resetDialog').showModal();
    } else {
      const body = button.dataset.action === 'unlock' ? { unlock: true } : { isActive: !user.isActive };
      if (button.dataset.action === 'toggle' && user.isActive &&
          !confirm(`Disable ${user.fullName}? They will be signed out and cannot sign in until enabled again.`)) return;
      try {
        await api(`/api/admin/users/${user.userId}`, body);
        await load();
      } catch (problem) {
        showMessage($('#error'), problem.message);
      }
    }
  });

  document.querySelectorAll('dialog [data-close]').forEach(button =>
    button.addEventListener('click', () => button.closest('dialog').close()));

  $('#editForm').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.target;
    try {
      await api(`/api/admin/users/${editing.userId}`, {
        fullName: form.fullName.value, email: form.email.value, role: form.role.value, isActive: form.isActive.checked,
      });
      $('#editDialog').close();
      await load();
    } catch (problem) {
      showMessage($('#editMessage'), problem.message);
    }
  });

  $('#resetGenerate').addEventListener('click', () => { $('#resetPassword').value = generatePassword(); });
  $('#resetForm').addEventListener('submit', async event => {
    event.preventDefault();
    try {
      await api(`/api/admin/users/${editing.userId}/password`, { password: $('#resetPassword').value });
      $('#resetDialog').close();
      showMessage($('#error'), '');
      await load();
    } catch (problem) {
      showMessage($('#resetMessage'), problem.message);
    }
  });

  load();
})();
