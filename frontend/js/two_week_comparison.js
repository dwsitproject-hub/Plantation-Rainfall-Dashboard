let DATA;
const $ = selector => document.querySelector(selector);
const fmt = value => value == null ? '—' : Number(value).toLocaleString(undefined, {maximumFractionDigits: 1});
const escapeHtml = value => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));

function isoInfo(dateText) {
  const date = new Date(`${dateText}T00:00:00Z`);
  const day = date.getUTCDay() || 7;
  const monday = new Date(date);
  monday.setUTCDate(date.getUTCDate() - day + 1);
  const thursday = new Date(monday);
  thursday.setUTCDate(monday.getUTCDate() + 3);
  const year = thursday.getUTCFullYear();
  const firstThursday = new Date(Date.UTC(year, 0, 4));
  firstThursday.setUTCDate(firstThursday.getUTCDate() + 3 - (firstThursday.getUTCDay() || 7));
  const week = 1 + Math.round((thursday - firstThursday) / 604800000);
  return {year, week, monday};
}

const isoKey = info => `${info.year}-${String(info.week).padStart(2, '0')}`;
const dateText = date => date.toISOString().slice(0, 10);
const addDays = (date, days) => { const result = new Date(date); result.setUTCDate(result.getUTCDate() + days); return result; };
const percent = (current, comparison) => comparison ? (current / comparison - 1) * 100 : null;
const pctText = value => value == null ? '—' : `${value > 0 ? '+' : ''}${fmt(value)}%`;

function sumDaily(region, keys) {
  return DATA.daily.filter(row => row.region === region && keys.has(isoKey(isoInfo(row.date))))
    .reduce((sum, row) => sum + Number(row.rain), 0);
}

function historicalTotal(region, selectedYear, forwardInfos) {
  const baseYear = forwardInfos[0].year;
  return forwardInfos.reduce((total, info) => {
    const comparisonYear = selectedYear + (info.year - baseYear);
    const row = DATA.weekly.find(item => item.region === region && item.type === 'historical_actual' && item.year === comparisonYear && item.week === info.week);
    return total + (row ? Number(row.rain) : 0);
  }, 0);
}

function monthLabel(monthKey) {
  return new Date(`${monthKey}-01T00:00:00Z`).toLocaleDateString(undefined, {month: 'long', year: 'numeric', timeZone: 'UTC'});
}

function shiftMonth(monthKey, offset) {
  const [year, month] = monthKey.split('-').map(Number);
  const date = new Date(Date.UTC(year, month - 1 + offset, 1));
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}`;
}

function sumDailyMonth(region, monthKey) {
  return DATA.daily.filter(row => row.region === region && row.date.startsWith(monthKey))
    .reduce((sum, row) => sum + Number(row.rain), 0);
}

function monthlyActual(region, monthKey) {
  const row = DATA.monthly.find(item => item.region === region && item.month === monthKey);
  return row ? Number(row.rain) : null;
}

function regionRows(rowBuilder, headings) {
  let html = '';
  for (const country of ['Indonesia', 'Malaysia', 'Thailand']) {
    html += `<tr class="country-row"><th colspan="6">${country}</th></tr>`;
    html += `<tr class="repeated-header">${headings.map(heading => `<th>${escapeHtml(heading)}</th>`).join('')}</tr>`;
    const regions = DATA.regions.filter(region => region.country === country)
      .sort((a, b) => Number(b.key.startsWith('Overall ')) - Number(a.key.startsWith('Overall ')) || a.region.localeCompare(b.region));
    for (const region of regions) html += rowBuilder(region);
  }
  return html;
}

function renderMonthly(selectedYear) {
  const currentMonth = DATA.lastActual.slice(0, 7);
  const previousMonth = shiftMonth(currentMonth, -1);
  const monthNumber = currentMonth.slice(5, 7);
  const historicalMonth = `${selectedYear}-${monthNumber}`;
  $('#currentMonthPeriod').textContent = `Current: ${monthLabel(currentMonth)} actual + forecast`;
  $('#lastMonthPeriod').textContent = `Previous: ${monthLabel(previousMonth)} actual`;
  $('#historicalMonthPeriod').textContent = `Historical: ${monthLabel(historicalMonth)}`;
  $('#historicalMonthHeading').textContent = `${selectedYear} month`;
  $('#historicalMonthPctHeading').textContent = `Vs ${selectedYear}`;
  $('#monthlyComparisonTable').innerHTML = regionRows(region => {
    const current = sumDailyMonth(region.key, currentMonth);
    const previous = monthlyActual(region.key, previousMonth);
    const history = monthlyActual(region.key, historicalMonth);
    const mom = percent(current, previous);
    const vsHistory = percent(current, history);
    const overall = region.key.startsWith('Overall ') ? 'overall-row' : '';
    const momTone = mom > 0 ? 'wetter' : mom < 0 ? 'drier' : '';
    const historyTone = vsHistory > 0 ? 'wetter' : vsHistory < 0 ? 'drier' : '';
    return `<tr class="${overall}"><td>${escapeHtml(region.region)}</td><td>${fmt(current)}</td><td>${fmt(previous)}</td>` +
      `<td class="${momTone}">${pctText(mom)}</td><td>${fmt(history)}</td><td class="${historyTone}">${pctText(vsHistory)}</td></tr>`;
  }, ['Region', 'Current month', 'Last month', 'MoM', `${selectedYear} month`, `Vs ${selectedYear}`]);
}

function render() {
  const selectedYear = Number($('#historyYear').value);
  const firstForecast = DATA.daily.filter(row => row.type === 'forecast').map(row => row.date).sort()[0];
  const start = isoInfo(firstForecast);
  const second = isoInfo(dateText(addDays(start.monday, 7)));
  const priorFirst = isoInfo(dateText(addDays(start.monday, -14)));
  const priorSecond = isoInfo(dateText(addDays(start.monday, -7)));
  const forwardInfos = [start, second];
  const forwardKeys = new Set(forwardInfos.map(isoKey));
  const priorKeys = new Set([priorFirst, priorSecond].map(isoKey));
  const forwardEnd = addDays(second.monday, 6);
  const priorEnd = addDays(priorSecond.monday, 6);
  $('#forwardPeriod').textContent = `Forward: ${dateText(start.monday)} to ${dateText(forwardEnd)}`;
  $('#priorPeriod').textContent = `Last two weeks: ${dateText(priorFirst.monday)} to ${dateText(priorEnd)}`;
  $('#historicalPeriod').textContent = `Historical: ISO W${start.week}–W${second.week} in ${selectedYear}`;
  $('#historicalTotalHeading').textContent = `${selectedYear} 2W`;
  $('#historicalPctHeading').textContent = `Vs ${selectedYear}`;

  const html = regionRows(region => {
      const forward = sumDaily(region.key, forwardKeys);
      const prior = sumDaily(region.key, priorKeys);
      const history = historicalTotal(region.key, selectedYear, forwardInfos);
      const wow = percent(forward, prior);
      const vsHistory = percent(forward, history);
      const overall = region.key.startsWith('Overall ') ? 'overall-row' : '';
      const wowTone = wow > 0 ? 'wetter' : wow < 0 ? 'drier' : '';
      const historyTone = vsHistory > 0 ? 'wetter' : vsHistory < 0 ? 'drier' : '';
      return `<tr class="${overall}"><td>${escapeHtml(region.region)}</td><td>${fmt(forward)}</td><td>${fmt(prior)}</td>` +
        `<td class="${wowTone}">${pctText(wow)}</td><td>${history ? fmt(history) : '—'}</td><td class="${historyTone}">${history ? pctText(vsHistory) : '—'}</td></tr>`;
  }, ['Region', 'Forward 2W', 'Previous 2W', 'WoW', `${selectedYear} 2W`, `Vs ${selectedYear}`]);
  $('#comparisonTable').innerHTML = html;
  renderMonthly(selectedYear);
}

async function init() {
  try {
    const response = await fetch('/api/data');
    DATA = await response.json();
    if (!response.ok) throw new Error(DATA.error || `HTTP ${response.status}`);
    $('#coverage').textContent = `Actuals through ${DATA.lastActual} • Forecast to ${DATA.forecastEnd}`;
    const currentYear = Number(DATA.lastActual.slice(0, 4));
    const years = [...new Set(DATA.weekly.filter(row => row.type === 'historical_actual' && row.year < currentYear).map(row => row.year))].sort((a, b) => a - b);
    $('#historyYear').innerHTML = years.map(year => `<option value="${year}">${year}</option>`).join('');
    $('#historyYear').value = years.includes(2015) ? '2015' : String(years.at(-1));
    $('#historyYear').onchange = render;
    render();
  } catch (error) {
    $('#coverage').textContent = 'Data unavailable';
    $('#error').hidden = false;
    $('#error').textContent = `Two-week comparison could not be loaded: ${error.message}`;
  }
}
init();
