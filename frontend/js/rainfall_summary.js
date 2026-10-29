let DATA;
const $ = selector => document.querySelector(selector);
const fmt = value => value == null ? '—' : Number(value).toLocaleString(undefined, {maximumFractionDigits: 1});
const escapeHtml = value => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));

function percent(current, comparison) {
  return comparison > 0 ? (current / comparison - 1) * 100 : null;
}

function pctText(value) {
  return value == null ? '—' : `${value > 0 ? '+' : ''}${fmt(value)}%`;
}

function currentTotal(region, year) {
  const start = `${year}-07-01`;
  const end = `${year}-09-30`;
  const rows = DATA.rainDaily.filter(row => row.region === region && row.date >= start && row.date <= end);
  return rows.length ? rows.reduce((sum, row) => sum + Number(row.rain), 0) : null;
}

function historicalTotal(region) {
  const values = [7, 8, 9].map(month => {
    const key = `2015-${String(month).padStart(2, '0')}`;
    const row = DATA.monthly.find(item => item.region === region && item.month === key);
    return row ? Number(row.rain) : null;
  });
  return values.every(value => value != null) ? values.reduce((sum, value) => sum + value, 0) : null;
}

function regionRows(currentYear) {
  let html = '';
  for (const country of ['Indonesia', 'Malaysia', 'Thailand']) {
    html += `<tr class="country-row"><th colspan="4">${country}</th></tr>`;
    const regions = DATA.regions.filter(region => region.country === country)
      .sort((a, b) => Number(b.key.startsWith('Overall ')) - Number(a.key.startsWith('Overall ')) || a.region.localeCompare(b.region));
    for (const region of regions) {
      const current = currentTotal(region.key, currentYear);
      const history = historicalTotal(region.key);
      const change = current == null || history == null ? null : percent(current, history);
      const overall = region.key.startsWith('Overall ') ? 'overall-row' : '';
      const tone = change > 0 ? 'wetter' : change < 0 ? 'drier' : '';
      html += `<tr class="${overall}"><td>${escapeHtml(region.region)}</td><td>${fmt(current)}</td><td>${fmt(history)}</td><td class="${tone}">${pctText(change)}</td></tr>`;
    }
  }
  return html;
}

async function init() {
  try {
    const response = await fetch('/api/data');
    DATA = await response.json();
    if (!response.ok) throw new Error(DATA.error || `HTTP ${response.status}`);
    const currentYear = Number(DATA.lastActual.slice(0, 4));
    const periodEnd = `${currentYear}-09-30`;
    const availableEnd = DATA.forecastEnd < periodEnd ? DATA.forecastEnd : periodEnd;
    $('#coverage').textContent = `Actuals through ${DATA.rainLastActual} • Forecast to ${DATA.forecastEnd}`;
    $('#currentHeading').textContent = `${currentYear} Jul–Sep (mm)`;
    $('#periodNote').textContent = `${currentYear} combines historical actuals through ${DATA.rainLastActual} with available forecast through ${availableEnd}. The comparison uses complete July–September 2015 actuals.`;
    $('#summaryTable').innerHTML = regionRows(currentYear);
  } catch (error) {
    $('#coverage').textContent = 'Data unavailable';
    $('#error').hidden = false;
    $('#error').textContent = `Rainfall summary could not be loaded: ${error.message}`;
  }
}

init();
