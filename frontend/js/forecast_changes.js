let DATA;
const $ = selector => document.querySelector(selector);
const fmt = value => value == null ? '—' : Number(value).toLocaleString(undefined, {maximumFractionDigits: 1});
const escapeHtml = value => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const tip = document.createElement('div');
tip.className = 'revision-tooltip';
tip.setAttribute('role', 'status');
document.body.appendChild(tip);

function showTip(event, html) {
  tip.innerHTML = html;
  tip.classList.add('show');
  tip.style.left = Math.min(innerWidth - tip.offsetWidth - 10, event.clientX + 14) + 'px';
  tip.style.top = Math.max(10, Math.min(innerHeight - tip.offsetHeight - 10, event.clientY - tip.offsetHeight - 14)) + 'px';
}
const hideTip = () => tip.classList.remove('show');

function selectedRegion() {
  return DATA.regions.find(region => region.key === $('#regionSelect').value) || DATA.regions[0];
}

function changeText(region) {
  if (region.delta == null) return '—';
  return `${region.delta > 0 ? '+' : ''}${fmt(region.delta)} mm`;
}

function renderTable() {
  const selected = $('#regionSelect').value;
  let html = '';
  for (const country of ['Indonesia', 'Malaysia', 'Thailand']) {
    html += `<tr class="country-row"><th colspan="5">${country}</th></tr>`;
    for (const region of DATA.regions.filter(row => row.country === country)) {
      const tone = region.delta > 0 ? 'wetter' : region.delta < 0 ? 'drier' : '';
      const overall = region.key.startsWith('Overall ') ? 'overall-row' : '';
      const current = region.key === selected ? 'selected-row' : '';
      html += `<tr class="${overall} ${current}"><td><button class="region-button" data-key="${escapeHtml(region.key)}">${escapeHtml(region.region)}</button></td>` +
        `<td>${region.previousTotal == null ? '—' : fmt(region.previousTotal) + ' mm'}</td>` +
        `<td>${region.latestTotal == null ? '—' : fmt(region.latestTotal) + ' mm'}</td><td class="${tone}">${changeText(region)}</td>` +
        `<td class="${tone}">${region.percent == null ? '—' : (region.percent > 0 ? '+' : '') + fmt(region.percent) + '%'}</td></tr>`;
    }
  }
  $('#revisionTable').innerHTML = html;
}

function renderRegionalChart() {
  const el = $('#changeChart');
  if (!DATA.previousRun) {
    el.textContent = 'Regional changes will appear after the next daily forecast update.';
    return;
  }
  const rows = DATA.regions;
  const maxAbs = Math.max(1, ...rows.map(row => Math.abs(row.delta || 0)));
  const W = 820, H = Math.max(470, 82 + rows.length * 30), zero = 500, scale = 260 / maxAbs;
  const chartBottom = H - 38;
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Change in regional forecast rainfall in millimeters">`;
  svg += `<line x1="${zero}" y1="27" x2="${zero}" y2="${chartBottom}" stroke="#a9bbbd" stroke-width="1.4"/>`;
  svg += `<text x="${zero}" y="17" text-anchor="middle" class="axis">0 mm</text>`;
  rows.forEach((row, index) => {
    const y = 34 + index * 30;
    const width = Math.abs(row.delta || 0) * scale;
    const x = row.delta < 0 ? zero - width : zero;
    const fill = row.delta < 0 ? '#cc6670' : '#14a99d';
    const selected = row.key === $('#regionSelect').value;
    svg += `<g data-index="${index}"><text x="8" y="${y + 13}" fill="${selected ? '#087d74' : '#38545d'}" font-size="12" font-weight="${selected ? '700' : '400'}">${escapeHtml(row.region)}</text>`;
    svg += `<rect x="${x}" y="${y}" width="${Math.max(width, 1)}" height="17" rx="3" fill="${fill}"/>`;
    svg += `<text x="${Math.max(220, Math.min(776, row.delta < 0 ? x - 7 : x + width + 7))}" y="${y + 13}" text-anchor="${row.delta < 0 ? 'end' : 'start'}" fill="${fill}" font-size="11" font-weight="700">${row.delta > 0 ? '+' : ''}${fmt(row.delta)}</text>`;
    svg += `<rect x="0" y="${y - 4}" width="${W}" height="26" fill="transparent"/></g>`;
  });
  svg += `<text x="220" y="${H - 14}" class="axis">Drier than previous run</text><text x="${zero + 18}" y="${H - 14}" class="axis">Wetter than previous run</text></svg>`;
  el.innerHTML = svg;
  el.onpointermove = event => {
    const group = event.target.closest('[data-index]');
    if (!group) { hideTip(); return; }
    const row = rows[Number(group.dataset.index)];
    showTip(event, `<strong>${escapeHtml(row.region)}</strong><span>Previous: ${fmt(row.previousTotal)} mm</span><span>Latest: ${fmt(row.latestTotal)} mm</span><span>Change: ${changeText(row)}</span>`);
  };
  el.onpointerleave = hideTip;
  el.onclick = event => {
    const group = event.target.closest('[data-index]');
    if (group) { $('#regionSelect').value = rows[Number(group.dataset.index)].key; $('#regionSelect').dispatchEvent(new Event('change', {bubbles: true})); }
  };
}

function renderDailyChart(region) {
  const el = $('#dailyChart');
  const rows = region.daily;
  if (!rows.length) { el.textContent = 'No matching forecast dates are available for this region.'; return; }
  const W = 820, H = 250, left = 49, right = 18, top = 14, bottom = 36;
  const max = Math.max(5, ...rows.flatMap(row => [row.latest, row.previous].filter(value => value != null))) * 1.12;
  const x = index => left + index / Math.max(1, rows.length - 1) * (W - left - right);
  const y = value => H - bottom - value / max * (H - top - bottom);
  const path = field => rows.filter(row => row[field] != null).map((row, index) => `${index ? 'L' : 'M'}${x(rows.indexOf(row)).toFixed(1)} ${y(row[field]).toFixed(1)}`).join(' ');
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Previous and latest daily rainfall forecasts for ${escapeHtml(region.region)}">`;
  for (let i = 0; i <= 3; i++) {
    const yy = top + i / 3 * (H - top - bottom);
    svg += `<line x1="${left}" y1="${yy}" x2="${W - right}" y2="${yy}" class="gridline"/><text x="2" y="${yy + 4}" class="axis">${Math.round(max * (1 - i / 3))}</text>`;
  }
  if (DATA.previousRun) svg += `<path d="${path('previous')}" fill="none" stroke="#d79530" stroke-width="2" stroke-dasharray="6 4"/>`;
  svg += `<path d="${path('latest')}" fill="none" stroke="#14a99d" stroke-width="2.8"/>`;
  rows.forEach((row, index) => {
    const xx = x(index);
    if (row.previous != null) svg += `<circle cx="${xx}" cy="${y(row.previous)}" r="3" fill="#fff" stroke="#d79530" stroke-width="1.5"/>`;
    svg += `<circle cx="${xx}" cy="${y(row.latest)}" r="3.5" fill="#14a99d" stroke="#fff" stroke-width="1"/>`;
    svg += `<text x="${xx}" y="${H - 13}" text-anchor="middle" class="axis">${row.date.slice(5)}</text>`;
    svg += `<rect data-index="${index}" x="${xx - (W - left - right) / Math.max(1, rows.length - 1) / 2}" y="${top}" width="${(W - left - right) / Math.max(1, rows.length - 1)}" height="${H - top - bottom}" fill="transparent"/>`;
  });
  svg += '</svg>';
  el.innerHTML = svg;
  el.onpointermove = event => {
    const target = event.target.closest('[data-index]');
    if (!target) { hideTip(); return; }
    const row = rows[Number(target.dataset.index)];
    showTip(event, `<strong>${row.date}</strong><span>Latest: ${fmt(row.latest)} mm</span><span>Previous: ${row.previous == null ? '—' : fmt(row.previous) + ' mm'}</span><span>Change: ${row.delta == null ? '—' : (row.delta > 0 ? '+' : '') + fmt(row.delta) + ' mm'}</span>`);
  };
  el.onpointerleave = hideTip;
}

function renderSelected() {
  const region = selectedRegion();
  if (!region) return;
  $('#previousTotal').textContent = region.previousTotal == null ? '—' : `${fmt(region.previousTotal)} mm`;
  $('#latestTotal').textContent = region.latestTotal == null ? '—' : `${fmt(region.latestTotal)} mm`;
  $('#deltaTotal').textContent = changeText(region);
  $('#deltaTotal').className = region.delta > 0 ? 'wetter' : region.delta < 0 ? 'drier' : '';
  $('#deltaPercent').textContent = region.percent == null ? 'Percentage change unavailable' : `${region.percent > 0 ? '+' : ''}${fmt(region.percent)}% vs previous run`;
  $('#windowNote').textContent = `${region.daysCompared} ${DATA.previousRun ? 'matching' : 'available'} forecast dates`;
  $('#dailyTitle').textContent = `${region.region}: daily rainfall forecasts (mm/day)`;
  renderTable();
  renderRegionalChart();
  renderDailyChart(region);
}

async function init() {
  try {
    const response = await fetch('/api/forecast-changes');
    DATA = await response.json();
    if (!response.ok) throw new Error(DATA.error || `HTTP ${response.status}`);
    if (!DATA.latestRun) {
      $('#coverage').textContent = 'No saved forecast run';
      $('#availability').hidden = false;
      $('#availability').textContent = 'Run the rainfall forecast update to begin recording forecast revisions.';
      return;
    }
    $('#coverage').textContent = `Latest forecast run: ${DATA.latestRun}`;
    $('#runDates').textContent = DATA.previousRun ? `${DATA.previousRun} → ${DATA.latestRun}` : `First saved run: ${DATA.latestRun}`;
    if (!DATA.previousRun) {
      $('#availability').hidden = false;
      $('#availability').textContent = 'Only one forecast run is available. Day-on-day changes will appear after the next daily weather update.';
    }
    const select = $('#regionSelect');
    DATA.regions.forEach(region => {
      const option = document.createElement('option');
      option.value = region.key;
      option.textContent = region.key.startsWith('Overall ') ? region.region : `${region.region}, ${region.country}`;
      select.appendChild(option);
    });
    select.onchange = renderSelected;
    renderSelected();
  } catch (error) {
    $('#coverage').textContent = 'Data unavailable';
    $('#error').hidden = false;
    $('#error').textContent = `Forecast changes could not be loaded: ${error.message}`;
  }
}
init();
