/* Seasonal comparison charts shared by the weekly and monthly views. */
function isoWeek(dateText) {
  const d = new Date(`${dateText}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 4 - (d.getUTCDay() || 7));
  const year = d.getUTCFullYear();
  return {year, week: Math.ceil((((d - new Date(Date.UTC(year, 0, 1))) / 86400000) + 1) / 7)};
}

function currentBuckets(region, period, metric) {
  const year = Number(DATA.lastActual.slice(0, 4));
  const buckets = new Map();
  const source = metric === 'rain' ? (DATA.rainDaily || DATA.daily) :
    metric === 'soil' ? (DATA.soilDaily || DATA.daily) : (DATA.temperatureDaily || DATA.daily);
  for (const row of source) {
    if (row.region !== region) continue;
    const key = period === 'week' ? isoWeek(row.date) : {year: Number(row.date.slice(0, 4)), week: Number(row.date.slice(5, 7))};
    if (key.year !== year) continue;
    let bucket = buckets.get(key.week);
    if (!bucket) {
      bucket = {rain: 0, soilTotal: 0, temperatureTotal: 0, days: 0, actualDays: 0, forecastDays: 0, startDate: row.date, endDate: row.date};
      buckets.set(key.week, bucket);
    }
    if (metric === 'rain') bucket.rain += row.rain;
    else if (metric === 'soil') bucket.soilTotal += row.soil;
    else bucket.temperatureTotal += row.temperature;
    bucket.days++;
    if (row.date < bucket.startDate) bucket.startDate = row.date;
    if (row.date > bucket.endDate) bucket.endDate = row.date;
    if (row.type === 'forecast') bucket.forecastDays++;
    else bucket.actualDays++;
  }
  return buckets;
}

function baselineBuckets(region, metric, period, start, end) {
  const buckets = new Map();
  const source = period === 'week' ? DATA.weekly : DATA.monthly;
  for (const row of source) {
    if (row.region !== region || row.type === 'forecast' || row[metric] == null) continue;
    const year = period === 'week' ? row.year : Number(row.month.slice(0, 4));
    if (year < start || year > end) continue;
    const index = period === 'week' ? row.week : Number(row.month.slice(5, 7));
    if (!buckets.has(index)) buckets.set(index, []);
    buckets.get(index).push({year, value: row[metric]});
  }
  return buckets;
}

function seasonalRows(region, metric, period, start, end) {
  const current = currentBuckets(region, period, metric);
  const baseline = baselineBuckets(region, metric, period, start, end);
  const length = period === 'week' ? 53 : 12;
  return Array.from({length}, (_, i) => {
    const index = i + 1;
    const values = baseline.get(index) || [];
    const nums = values.map(v => v.value);
    const candidate = current.get(index);
    // Plot current-year points only when the complete calendar period is
    // covered by actual and/or forecast rows. This removes partial forecast
    // weeks and months at either data boundary.
    const requiredDays = period === 'week' ? 7 : new Date(Date.UTC(Number(DATA.lastActual.slice(0, 4)), index, 0)).getUTCDate();
    const currentBucket = candidate?.days === requiredDays ? candidate : null;
    const min = nums.length ? Math.min(...nums) : null;
    const max = nums.length ? Math.max(...nums) : null;
    return {
      index,
      current: currentBucket ? metric === 'rain' ? currentBucket.rain :
        metric === 'soil' ? currentBucket.soilTotal / currentBucket.days : currentBucket.temperatureTotal / currentBucket.days : null,
      actualDays: currentBucket?.actualDays || 0,
      forecastDays: currentBucket?.forecastDays || 0,
      startDate: currentBucket?.startDate || null,
      endDate: currentBucket?.endDate || null,
      average: nums.length ? nums.reduce((a, b) => a + b, 0) / nums.length : null,
      min, max,
      minYears: values.filter(v => v.value === min).map(v => v.year),
      maxYears: values.filter(v => v.value === max).map(v => v.year)
    };
  });
}

function seasonalCutoff(period) {
  const last = DATA.lastActual;
  if (period === 'week') {
    const info = isoWeek(last);
    const completedDays = ((new Date(`${last}T00:00:00Z`).getUTCDay() + 6) % 7) + 1;
    // Weekly points sit at the centre of each Monday–Sunday bucket. Place the
    // cutoff at the end of the latest actual day, between adjacent week points.
    return Math.min(53.5, info.week - 0.5 + completedDays / 7);
  }
  const month = Number(last.slice(5, 7));
  const day = Number(last.slice(8, 10));
  const days = new Date(Date.UTC(Number(last.slice(0, 4)), month, 0)).getUTCDate();
  return month + day / days;
}

function segmentedPath(rows, field, x, y) {
  let drawing = false;
  return rows.map(row => {
    if (row[field] == null) { drawing = false; return ''; }
    const part = `${drawing ? 'L' : 'M'}${x(row.index).toFixed(1)} ${y(row[field]).toFixed(1)}`;
    drawing = true;
    return part;
  }).join(' ');
}

function bandPaths(rows, x, y) {
  const groups = [];
  let group = [];
  for (const row of rows) {
    if (row.min == null || row.max == null) {
      if (group.length) groups.push(group);
      group = [];
    } else group.push(row);
  }
  if (group.length) groups.push(group);
  return groups.map(group => {
    const top = group.map(row => `${x(row.index).toFixed(1)},${y(row.max).toFixed(1)}`).join(' ');
    const bottom = [...group].reverse().map(row => `${x(row.index).toFixed(1)},${y(row.min).toFixed(1)}`).join(' ');
    return `<polygon class="range-band" points="${top} ${bottom}"/>`;
  }).join('');
}

function renderSeasonalChart(period, metric, target, unit, options = {}) {
  const region = options.region ?? selected();
  const prefix = period === 'week' ? 'baseline' : 'monthlyBaseline';
  const start = options.start ?? Number($(`#${prefix}Start`).value);
  const end = options.end ?? Number($(`#${prefix}End`).value);
  const currentYear = Number(DATA.lastActual.slice(0, 4));
  const rows = seasonalRows(region, metric, period, start, end);
  const values = rows.flatMap(r => [r.current, r.min, r.max]).filter(v => v != null);
  if (!values.length) { $(target).textContent = 'No data for this selection.'; return; }

  const W = 700, H = 230, left = 46, right = 16, top = 14, bottom = 31;
  const count = period === 'week' ? 53 : 12;
  const low = metric === 'soil' ? Math.floor(Math.min(...values) * 100) / 100 :
    metric === 'temperature' ? Math.floor(Math.min(...values) - 1) : 0;
  const high = metric === 'temperature' ? Math.ceil(Math.max(...values) + 1) : Math.max(...values) * 1.08;
  const span = Math.max(high - low, .001);
  const x = i => left + (i - 1) / (count - 1) * (W - left - right);
  const y = v => top + (H - top - bottom) * (1 - (v - low) / span);

  let svg = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">`;
  for (let i = 0; i <= 3; i++) {
    const yy = top + i * (H - top - bottom) / 3;
    const value = high - i * span / 3;
    svg += `<line class="gridline" x1="${left}" y1="${yy}" x2="${W - right}" y2="${yy}"/>`;
    svg += `<text class="axis" x="2" y="${yy + 4}">${metric === 'soil' ? value.toFixed(2) : metric === 'temperature' ? value.toFixed(1) : Math.round(value)}</text>`;
  }
  svg += bandPaths(rows, x, y);
  svg += `<path class="average-line" d="${segmentedPath(rows, 'average', x, y)}"/>`;
  svg += `<path class="current-line" d="${segmentedPath(rows, 'current', x, y)}"/>`;
  for (const row of rows) {
    if (row.average != null) svg += `<circle class="average-dot" cx="${x(row.index)}" cy="${y(row.average)}" r="${period === 'week' ? 2.3 : 3}"/>`;
    if (row.current != null) svg += `<circle class="current-dot" cx="${x(row.index)}" cy="${y(row.current)}" r="${period === 'week' ? 3.4 : 4.2}"/>`;
  }
  if (period === 'week') {
    const cutoffX = x(seasonalCutoff(period));
    svg += `<line class="cutoff-line" x1="${cutoffX}" y1="${top}" x2="${cutoffX}" y2="${H - bottom}"/>`;
    const labelX = Math.min(cutoffX + 5, W - 90);
    svg += `<text class="cutoff-label" x="${labelX}" y="${top + 11}">${DATA.lastActual}</text>`;
  }
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  if (period === 'week') {
    [1,5,9,13,17,21,25,29,33,37,41,45,49,53].forEach(i => {
      svg += `<text class="axis" text-anchor="middle" x="${x(i)}" y="${H - 7}">W${i}</text>`;
    });
  } else months.forEach((m, i) => {
    svg += `<text class="axis" text-anchor="middle" x="${x(i + 1)}" y="${H - 7}">${m}</text>`;
  });
  svg += '</svg>';
  const el = $(target);
  el.innerHTML = svg;
  // Use the SVG's exact plot bounds when converting the pointer position to
  // a week/month. Generic percentage margins select the neighbouring bucket.
  el.dataset.hoverViewWidth = W;
  el.dataset.hoverLeft = left;
  el.dataset.hoverRight = right;
  attachHover(el, rows, row => {
    const label = period === 'week' ? `Week ${row.index}` : `${months[row.index - 1]} ${currentYear}`;
    const sourceLabel = row.current == null ? `${currentYear} value` :
      row.actualDays && row.forecastDays ? `${currentYear} actual + forecast` :
      row.forecastDays ? `${currentYear} forecast` : `${currentYear} actual`;
    const composition = row.current == null ?
      (period === 'week' ? 'No complete seven-day current-year week' : 'No complete current-year calendar month') :
      `${row.startDate}–${row.endDate} • ${row.actualDays ? `${row.actualDays} actual day${row.actualDays === 1 ? '' : 's'}` : ''}` +
      `${row.actualDays && row.forecastDays ? ' + ' : ''}` +
      `${row.forecastDays ? `${row.forecastDays} forecast day${row.forecastDays === 1 ? '' : 's'}` : ''}`;
    const number = value => value == null ? 'No data' : `${metric === 'soil' ? value.toFixed(3) : fmt(value)} ${unit}`;
    return `<strong>${label}</strong><span>${sourceLabel} <b>${number(row.current)}</b></span><small>${composition}</small>` +
      `<span>Baseline average <b>${number(row.average)}</b></span>` +
      `<span>Minimum <b>${number(row.min)}</b></span><small>Year: ${row.minYears.join(', ') || 'No data'}</small>` +
      `<span>Maximum <b>${number(row.max)}</b></span><small>Year: ${row.maxYears.join(', ') || 'No data'}</small>` +
      `<small>Baseline ${start}–${end}</small>`;
  });
}

function drawSeasonality() {
  const key = selected();
  const region = DATA.regions.find(r => r.key === key).region;
  const year = DATA.lastActual.slice(0, 4);
  const weeklyStart = $('#baselineStart').value;
  const weeklyEnd = $('#baselineEnd').value;
  const monthlyStart = $('#monthlyBaselineStart').value;
  const monthlyEnd = $('#monthlyBaselineEnd').value;
  $('#seasonTitle').textContent = `${region}: weekly ${year} vs ${weeklyStart}–${weeklyEnd}`;
  $('#monthlySeasonTitle').textContent = `${region}: monthly ${year} vs ${monthlyStart}–${monthlyEnd}`;
  $('#baselineAvgLabel').textContent = `${weeklyStart}–${weeklyEnd} average`;
  $('#baselineRangeLabel').textContent = `${weeklyStart}–${weeklyEnd} min–max`;
  $('#monthlyBaselineAvgLabel').textContent = `${monthlyStart}–${monthlyEnd} average`;
  $('#monthlyBaselineRangeLabel').textContent = `${monthlyStart}–${monthlyEnd} min–max`;
  renderSeasonalChart('week', 'rain', '#seasonRain', 'mm');
  renderSeasonalChart('week', 'soil', '#seasonSoil', 'm³/m³');
  renderSeasonalChart('month', 'rain', '#monthRain', 'mm');
  renderSeasonalChart('month', 'soil', '#monthSoil', 'm³/m³');
}
