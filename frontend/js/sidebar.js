(() => {
  // Any same-origin API call that finds the session expired sends the user back to sign in.
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const response = await nativeFetch(...args);
    if (response.status === 401 && !location.pathname.startsWith('/login')) {
      location.href = `/login.html?next=${encodeURIComponent(location.pathname + location.search)}`;
    }
    return response;
  };

  const path = location.pathname === '/dashboard.html' ? '/' : location.pathname;
  const inhousePage = new URLSearchParams(location.search).get('page') || 'overview';
  const activeKey = path.startsWith('/inhouse/') ? `/inhouse/?page=${inhousePage}` : path;
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const rainfallLinks = [
    ['/', 'Weather overview'],
    ['/weekly-seasonality.html', 'Weekly seasonality'],
    ['/cumulative-rainfall.html', 'Cumulative rainfall'],
    ['/two-week-comparison.html', 'Historical comparison'],
    ['/last-week-accuracy.html', 'Last week accuracy'],
    ['/forecast-changes.html', 'Forecast changes'],
  ];
  const temperatureLinks = [
    ['/temperature.html', 'Temperature overview'],
    ['/temperature-weekly-seasonality.html', 'Weekly seasonality'],
    ['/temperature-historical-comparison.html', 'Historical comparison'],
  ];
  const inhouseLinks = [
    ['/inhouse/?page=seasonality', 'Plantation Rainfall Seasonality'],
    ['/inhouse/?page=overview', 'Weather overview'],
    ['/inhouse/?page=cumulative', 'Cumulative rainfall'],
  ];
  const adminLinks = [['/admin/users.html', 'User management']];

  const isActive = href => activeKey === href;
  const linksHtml = links => links.map(([href, label]) =>
    `<a href="${href}"${isActive(href) ? ' class="active" aria-current="page"' : ''}>${label}</a>`
  ).join('');

  const sidebar = document.createElement('aside');
  sidebar.className = 'site-sidebar';
  sidebar.setAttribute('aria-label', 'Weather dashboard navigation');
  sidebar.innerHTML = '<a class="sidebar-brand" href="/"><small>Operations intelligence</small><strong>Plantation Weather</strong></a>';
  document.body.prepend(sidebar);
  document.body.classList.add('with-sidebar');

  function render(me) {
    const can = section => me.sections.includes(section);
    const isInhouse = path.startsWith('/inhouse/');
    const isAdmin = path.startsWith('/admin/');
    const inhouseOpen = isInhouse;
    const allRegionsOpen = !isInhouse && !isAdmin && path !== '/account/password.html';
    const rainfallOpen = rainfallLinks.some(([href]) => isActive(href));
    const temperatureOpen = temperatureLinks.some(([href]) => isActive(href));
    const sections = [];
    if (can('inhouse')) sections.push(`
      <section class="sidebar-section">
        <button class="sidebar-toggle sidebar-section-toggle" type="button" aria-expanded="${inhouseOpen}" aria-controls="inhouseMenu">
          <span>Inhouse Plantation</span><i aria-hidden="true"></i>
        </button>
        <nav id="inhouseMenu" class="sidebar-links sidebar-section-links"${inhouseOpen ? '' : ' hidden'}>${linksHtml(inhouseLinks)}</nav>
      </section>`);
    if (can('all_regions')) sections.push(`
      <section class="sidebar-section">
        <button class="sidebar-toggle sidebar-section-toggle" type="button" aria-expanded="${allRegionsOpen}" aria-controls="allRegionsMenu">
          <span>All Regions</span><i aria-hidden="true"></i>
        </button>
        <div id="allRegionsMenu" class="sidebar-section-body"${allRegionsOpen ? '' : ' hidden'}>
          <div class="sidebar-category">
            <button class="sidebar-toggle sidebar-category-toggle" type="button" aria-expanded="${rainfallOpen}" aria-controls="rainfallMenu">
              <span>Rainfall</span><i aria-hidden="true"></i>
            </button>
            <nav id="rainfallMenu" class="sidebar-links"${rainfallOpen ? '' : ' hidden'}>${linksHtml(rainfallLinks)}</nav>
          </div>
          <div class="sidebar-category">
            <button class="sidebar-toggle sidebar-category-toggle" type="button" aria-expanded="${temperatureOpen}" aria-controls="temperatureMenu">
              <span>Temperature</span><i aria-hidden="true"></i>
            </button>
            <nav id="temperatureMenu" class="sidebar-links"${temperatureOpen ? '' : ' hidden'}>${linksHtml(temperatureLinks)}</nav>
          </div>
        </div>
      </section>`);
    if (can('user_admin')) sections.push(`
      <section class="sidebar-section">
        <button class="sidebar-toggle sidebar-section-toggle" type="button" aria-expanded="${isAdmin}" aria-controls="adminMenu">
          <span>Administration</span><i aria-hidden="true"></i>
        </button>
        <nav id="adminMenu" class="sidebar-links sidebar-section-links"${isAdmin ? '' : ' hidden'}>${linksHtml(adminLinks)}</nav>
      </section>`);

    sidebar.innerHTML = `
      <a class="sidebar-brand" href="${me.home}"><small>Operations intelligence</small><strong>Plantation Weather</strong></a>
      ${sections.join('')}
      <div class="sidebar-user">
        <strong>${escape(me.fullName)}</strong>
        <small>${escape(me.email)} · ${escape(me.roleName)}</small>
        <div class="sidebar-user-actions">
          <a href="/account/password.html"${isActive('/account/password.html') ? ' aria-current="page"' : ''}>Change password</a>
          <button type="button" id="signOut">Sign out</button>
        </div>
      </div>
      <p class="sidebar-source">All Regions: Open-Meteo and ECMWF IFS<br>Inhouse: SAP and Open-Meteo</p>`;

    sidebar.querySelectorAll('.sidebar-toggle').forEach(button => {
      button.addEventListener('click', () => {
        const target = sidebar.querySelector(`#${button.getAttribute('aria-controls')}`);
        const expanded = button.getAttribute('aria-expanded') === 'true';
        if (!expanded && button.classList.contains('sidebar-section-toggle')) {
          sidebar.querySelectorAll('.sidebar-section-toggle').forEach(other => {
            if (other === button) return;
            other.setAttribute('aria-expanded', 'false');
            sidebar.querySelector(`#${other.getAttribute('aria-controls')}`).hidden = true;
          });
        }
        if (!expanded && button.classList.contains('sidebar-category-toggle')) {
          button.closest('.sidebar-section-body').querySelectorAll('.sidebar-category-toggle').forEach(other => {
            if (other === button) return;
            other.setAttribute('aria-expanded', 'false');
            sidebar.querySelector(`#${other.getAttribute('aria-controls')}`).hidden = true;
          });
        }
        button.setAttribute('aria-expanded', String(!expanded));
        target.hidden = expanded;
      });
    });

    sidebar.querySelector('#signOut').addEventListener('click', async () => {
      await nativeFetch('/api/logout', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' }, body: '{}' });
      location.href = '/login.html';
    });
  }

  fetch('/api/me').then(response => response.ok ? response.json() : null).then(me => { if (me) render(me); });
})();
