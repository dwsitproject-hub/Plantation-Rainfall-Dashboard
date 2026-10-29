let DATA, range = 90;
const $ = selector => document.querySelector(selector);
const fmt = value => value == null ? '—' : Number(value).toLocaleString(undefined, {maximumFractionDigits: 1});
const escapeHtml = value => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const tooltip = document.createElement('div');
tooltip.className = 'chart-tooltip'; tooltip.setAttribute('role', 'status'); document.body.appendChild(tooltip);

function attachHover(element, rows, render) {
  element.onpointermove = event => {
    if (!rows.length) return;
    const box = element.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (event.clientX - box.left) / box.width));
    const row = rows[Math.round(ratio * (rows.length - 1))];
    tooltip.innerHTML = render(row); tooltip.classList.add('show');
    const width = tooltip.offsetWidth, height = tooltip.offsetHeight;
    tooltip.style.left = Math.min(innerWidth - width - 10, event.clientX + 14) + 'px';
    tooltip.style.top = Math.max(10, Math.min(innerHeight - height - 10, event.clientY - height - 14)) + 'px';
  };
  element.onpointerleave = () => tooltip.classList.remove('show');
}

function selected() { return $('#region').value; }

function visibleRows(key) {
  const rows = DATA.temperatureDaily.filter(row => row.region === key);
  return [...rows.filter(row => row.type === 'historical_actual').slice(-range), ...rows.filter(row => row.type === 'forecast')];
}

function drawSeries(key) {
  const rows = visibleRows(key), W = 1200, H = 350, p = {l:48,r:14,t:15,b:30};
  const iw = W-p.l-p.r, ih = H-p.t-p.b, values = rows.map(row => row.temperature);
  const min = Math.floor(Math.min(...values)-1), max = Math.ceil(Math.max(...values)+1);
  const x = index => p.l + index / Math.max(1, rows.length-1) * iw;
  const y = value => p.t + ih - (value-min)/(max-min)*ih;
  const actual = rows.map((row,index) => ({...row,index})).filter(row => row.type === 'historical_actual');
  const forecast = rows.map((row,index) => ({...row,index})).filter(row => row.type === 'forecast');
  const path = list => list.map((row,index) => `${index?'L':'M'}${x(row.index)} ${y(row.temperature)}`).join(' ');
  let svg = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">`;
  for (let i=0;i<=4;i++) { const yy=p.t+i*ih/4,val=max-(max-min)*i/4; svg += `<line class="gridline" x1="${p.l}" y1="${yy}" x2="${W-p.r}" y2="${yy}"/><text class="axis" x="3" y="${yy+4}">${val.toFixed(1)}°C</text>`; }
  if (actual.length) svg += `<path class="temperature-actual-line" d="${path(actual)}"/>`;
  if (forecast.length) { const bridge = actual.length ? [actual.at(-1), ...forecast] : forecast; svg += `<path class="temperature-forecast-line" d="${path(bridge)}"/>`; }
  rows.forEach((row,index) => svg += `<circle class="${row.type==='forecast'?'temperature-dot-forecast':'temperature-dot-actual'}" cx="${x(index)}" cy="${y(row.temperature)}" r="2.7"/>`);
  [0,Math.floor((rows.length-1)/2),rows.length-1].forEach(index => svg += `<text class="axis" text-anchor="${index===0?'start':index===rows.length-1?'end':'middle'}" x="${x(index)}" y="${H-7}">${rows[index].date.slice(5)}</text>`);
  svg += '</svg>';
  const element = $('#temperatureSeries'); element.innerHTML = svg;
  attachHover(element, rows, row => `<strong>${row.date}</strong><span>Daily mean <b>${fmt(row.temperature)} °C</b></span><small>${row.type==='forecast'?'Forecast':'Historical actual'}</small>`);
}

function forecastStats(key) {
  const values = DATA.temperatureDaily.filter(row => row.region === key && row.type === 'forecast').map(row => Number(row.temperature));
  return {days:values.length,average:values.length?values.reduce((a,b)=>a+b,0)/values.length:null,minimum:values.length?Math.min(...values):null,maximum:values.length?Math.max(...values):null};
}

function drawTable() {
  let html = '';
  for (const country of ['Indonesia','Malaysia','Thailand']) {
    html += `<tr class="country-row"><th colspan="4">${country}</th></tr>`;
    const regions = DATA.regions.filter(region => region.country === country).sort((a,b)=>Number(b.key.startsWith('Overall '))-Number(a.key.startsWith('Overall '))||a.region.localeCompare(b.region));
    for (const region of regions) { const stats=forecastStats(region.key),overall=region.key.startsWith('Overall ')?'overall-row':''; html += `<tr class="${overall}"><td>${escapeHtml(region.region)}</td><td>${fmt(stats.average)} °C</td><td>${fmt(stats.minimum)} °C</td><td>${fmt(stats.maximum)} °C</td></tr>`; }
  }
  $('#temperatureTable').innerHTML = html;
}

function drawMonthly(key) {
  const rows = DATA.monthly.filter(row => row.region === key && row.temperature != null), W=760,H=400,p={l:46,r:12,t:12,b:30};
  const iw=W-p.l-p.r,ih=H-p.t-p.b,values=rows.map(row=>row.temperature),min=Math.floor(Math.min(...values)-1),max=Math.ceil(Math.max(...values)+1);
  const x=index=>p.l+index/Math.max(1,rows.length-1)*iw,y=value=>p.t+ih-(value-min)/(max-min)*ih;
  let svg=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">`;
  for(let i=0;i<=4;i++){const yy=p.t+i*ih/4,val=max-(max-min)*i/4;svg+=`<line class="gridline" x1="${p.l}" y1="${yy}" x2="${W-p.r}" y2="${yy}"/><text class="axis" x="2" y="${yy+4}">${val.toFixed(1)}°</text>`}
  svg+=`<path class="temperature-actual-line" d="${rows.map((row,index)=>(index?'L':'M')+x(index)+' '+y(row.temperature)).join(' ')}"/>`;
  [0,Math.floor((rows.length-1)/2),rows.length-1].forEach(index=>svg+=`<text class="axis" text-anchor="${index===0?'start':index===rows.length-1?'end':'middle'}" x="${x(index)}" y="${H-7}">${rows[index].month}</text>`);svg+='</svg>';
  const element=$('#monthlyTemperature');element.innerHTML=svg;$('#monthlyLabel').textContent=`${rows.length} months`;
  attachHover(element,rows,row=>`<strong>${row.month}</strong><span>Monthly average <b>${fmt(row.temperature)} °C</b></span>`);
}

function update() {
  const key=selected(),stats=forecastStats(key);$('#forecastAverage').textContent=fmt(stats.average)+' °C';$('#forecastMinimum').textContent=fmt(stats.minimum)+' °C';$('#forecastMaximum').textContent=fmt(stats.maximum)+' °C';$('#forecastDays').textContent=stats.days+' days';drawSeries(key);drawMonthly(key);
}

async function init(){try{const response=await fetch('/api/data');DATA=await response.json();if(!response.ok)throw new Error(DATA.error||`HTTP ${response.status}`);$('#coverage').textContent=`Actuals through ${DATA.temperatureLastActual} • Forecast to ${DATA.forecastEnd}`;const ordered=[...DATA.regions].sort((a,b)=>(b.key.startsWith('Overall ')-a.key.startsWith('Overall '))||a.country.localeCompare(b.country)||a.region.localeCompare(b.region));$('#region').innerHTML=ordered.map(region=>`<option value="${region.key}">${region.key.startsWith('Overall ')?region.region:region.region+', '+region.country}</option>`).join('');$('#region').onchange=update;document.querySelectorAll('[data-range]').forEach(button=>button.onclick=()=>{range=Number(button.dataset.range);document.querySelectorAll('[data-range]').forEach(item=>item.classList.toggle('active',item===button));update()});drawTable();update()}catch(error){$('#coverage').textContent='Data unavailable';$('#error').hidden=false;$('#error').textContent=`Temperature dashboard could not be loaded: ${error.message}`}}
init();
