(() => {
  const path = location.pathname === '/dashboard.html' ? '/' : location.pathname;
  const inhousePage = new URLSearchParams(location.search).get('page') || 'overview';
  const activeKey = path.startsWith('/inhouse/') ? `/inhouse/?page=${inhousePage}` : path;

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

  const isActive = href => activeKey === href;
  const linksHtml = links => links.map(([href, label]) =>
    `<a href="${href}"${isActive(href) ? ' class="active" aria-current="page"' : ''}>${label}</a>`
  ).join('');
  const isInhouse = path.startsWith('/inhouse/');
  const allRegionsOpen = !isInhouse;
  const inhouseOpen = isInhouse;
  const rainfallOpen = rainfallLinks.some(([href]) => isActive(href));
  const temperatureOpen = temperatureLinks.some(([href]) => isActive(href));

  const sidebar = document.createElement('aside');
  sidebar.className = 'site-sidebar';
  sidebar.setAttribute('aria-label', 'Weather dashboard navigation');
  sidebar.innerHTML = `
    <a class="sidebar-brand" href="/"><small>Operations intelligence</small><strong>Plantation Weather</strong></a>
    <section class="sidebar-section">
      <button class="sidebar-toggle sidebar-section-toggle" type="button" aria-expanded="${inhouseOpen}" aria-controls="inhouseMenu">
        <span>Inhouse Plantation</span><i aria-hidden="true"></i>
      </button>
      <nav id="inhouseMenu" class="sidebar-links sidebar-section-links"${inhouseOpen ? '' : ' hidden'}>${linksHtml(inhouseLinks)}</nav>
    </section>
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
    </section>
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

  document.body.prepend(sidebar);
  document.body.classList.add('with-sidebar');
})();
