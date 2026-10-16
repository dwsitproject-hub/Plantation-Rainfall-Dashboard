/* Country-grouped seasonality pages. Chart calculations are shared with the main dashboard. */
let DATA;
const $ = selector => document.querySelector(selector);
const fmt = value => Number(value).toLocaleString(undefined, {maximumFractionDigits: 1});
const period = document.body.dataset.period;
const hoverTip = document.createElement('div');
hoverTip.className = 'chart-tooltip';
hoverTip.setAttribute('role', 'status');
document.body.appendChild(hoverTip);

function attachHover(element, rows, render) {
  element.onpointermove = event => {
    if (!rows.length) return;
    const box = element.getBoundingClientRect();
    const viewWidth = Number(element.dataset.hoverViewWidth) || 100;
    const left = Number(element.dataset.hoverLeft) || 5;
    const right = Number(element.dataset.hoverRight) || 5;
    const pointerX = (event.clientX - box.left) / box.width * viewWidth;
    const ratio = Math.max(0, Math.min(1, (pointerX - left) / (viewWidth - left - right)));
    hoverTip.innerHTML = render(rows[Math.round(ratio * (rows.length - 1))]);
    hoverTip.classList.add('show');
    hoverTip.style.left = Math.min(innerWidth - hoverTip.offsetWidth - 10, event.clientX + 14) + 'px';
    hoverTip.style.top = Math.max(10, Math.min(innerHeight - hoverTip.offsetHeight - 10, event.clientY - hoverTip.offsetHeight - 14)) + 'px';
  };
  element.onpointerleave = () => hoverTip.classList.remove('show');
}

const chartTargets = [];

function addRegionCard(container, region, index) {
  const card = document.createElement('article');
  card.className = 'region-card panel';
  const heading = document.createElement('div');
  heading.className = 'region-card-head';
  const title = document.createElement('h3');
  title.textContent = region.region;
  heading.appendChild(title);
  const subtitle = document.createElement('span');
  subtitle.textContent = region.key.startsWith('Overall ') ? 'Major plantation-area average' : 'Region';
  heading.appendChild(subtitle);
  card.appendChild(heading);

  const grid = document.createElement('div');
  grid.className = 'season-grid';
  for (const [metric, label, unit] of [['rain', 'Rainfall', 'mm'], ['soil', 'Soil moisture · 28–100 cm', 'm³/m³']]) {
    const block = document.createElement('div');
    const metricTitle = document.createElement('h4');
    metricTitle.textContent = `${label} (${unit})`;
    block.appendChild(metricTitle);
    const chart = document.createElement('div');
    chart.className = 'season-chart';
    chart.id = `region-${index}-${metric}`;
    chart.setAttribute('role', 'img');
    chart.setAttribute('aria-label', `${period === 'week' ? 'Weekly' : 'Monthly'} ${label.toLowerCase()} seasonality for ${region.region}`);
    block.appendChild(chart);
    grid.appendChild(block);
    chartTargets.push({region: region.key, metric, unit, target: `#${chart.id}`});
  }
  card.appendChild(grid);
  container.appendChild(card);
}

function buildCountries() {
  const root = $('#regionSections');
  root.replaceChildren();
  chartTargets.length = 0;
  let index = 0;
  const selected = $('#overviewRegion').value;
  for (const country of ['Indonesia', 'Malaysia', 'Thailand']) {
    const section = document.createElement('section');
    section.className = 'country-section';
    section.id = country.toLowerCase();
    const heading = document.createElement('div');
    heading.className = 'country-heading';
    const title = document.createElement('h2');
    title.textContent = country;
    heading.appendChild(title);
    const detail = document.createElement('span');
    detail.textContent = 'Overall → regions';
    heading.appendChild(detail);
    section.appendChild(heading);
    const regions = DATA.regions.filter(region => region.country === country && (selected === 'ALL' || region.key === selected))
      .sort((a, b) => Number(b.key.startsWith('Overall ')) - Number(a.key.startsWith('Overall ')) || a.region.localeCompare(b.region));
    if (!regions.length) continue;
    for (const region of regions) addRegionCard(section, region, index++);
    root.appendChild(section);
  }
}

function renderAll() {
  const start = Number($('#overviewStart').value);
  const end = Number($('#overviewEnd').value);
  for (const chart of chartTargets) {
    renderSeasonalChart(period, chart.metric, chart.target, chart.unit, {region: chart.region, start, end});
  }
}

function setupYears() {
  const current = Number(DATA.lastActual.slice(0, 4));
  const years = [...new Set(DATA.weekly.filter(row => row.type === 'historical_actual' && row.year >= 2000 && row.year < current).map(row => row.year))].sort((a, b) => a - b);
  const options = years.map(year => `<option value="${year}">${year}</option>`).join('');
  const start = $('#overviewStart');
  const end = $('#overviewEnd');
  start.innerHTML = end.innerHTML = options;
  start.value = Math.max(years[0], years.at(-1) - 9, Number(DATA.soilHistoryStart.slice(0, 4)));
  end.value = years.at(-1);
  start.onchange = () => { if (+start.value > +end.value) end.value = start.value; renderAll(); };
  end.onchange = () => { if (+end.value < +start.value) start.value = end.value; renderAll(); };
}

function setupRegions() {
  const select = $('#overviewRegion');
  const regions = [...DATA.regions].sort((a, b) => a.country.localeCompare(b.country) || Number(b.key.startsWith('Overall ')) - Number(a.key.startsWith('Overall ')) || a.region.localeCompare(b.region));
  select.innerHTML = '<option value="ALL">All regions</option>' + regions.map(region => `<option value="${region.key}">${region.country} · ${region.region}</option>`).join('');
  select.onchange = () => { buildCountries(); renderAll(); };
}

async function init() {
  try {
    const response = await fetch('/api/data');
    DATA = await response.json();
    if (!response.ok) throw new Error(DATA.error || `HTTP ${response.status}`);
    $('#coverage').textContent = `Actuals through ${DATA.lastActual} • Forecast to ${DATA.forecastEnd}`;
    setupYears();
    setupRegions();
    buildCountries();
    renderAll();
  } catch (error) {
    $('#coverage').textContent = 'Data unavailable';
    $('#error').hidden = false;
    $('#error').textContent = `Seasonality data could not be loaded: ${error.message}`;
  }
}
init();
