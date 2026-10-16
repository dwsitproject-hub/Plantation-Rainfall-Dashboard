let DATA;
const $=selector=>document.querySelector(selector);
const fmt=value=>value==null?'—':Number(value).toLocaleString(undefined,{maximumFractionDigits:1});
const escapeHtml=value=>String(value).replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const dateLabel=value=>new Date(value+'T00:00:00').toLocaleDateString(undefined,{day:'numeric',month:'short',year:'numeric'});
const tip=document.createElement('div');tip.className='accuracy-tooltip';tip.setAttribute('role','status');document.body.appendChild(tip);

function rowsHtml(regions,selected){
  let html='';
  for(const country of ['Indonesia','Malaysia','Thailand']){
    html+=`<tr class="country-row"><th colspan="4">${country}</th></tr>`;
    for(const row of regions.filter(item=>item.country===country)){
      const overall=row.key.startsWith('Overall ')?'overall-row':'',chosen=row.key===selected?'selected-row':'',tone=row.errorPercent==null?'unavailable':row.errorPercent>0?'error-high':row.errorPercent<0?'error-low':'';
      const reason=row.excludedRegions?.length?` title="Both totals use ${row.includedRegions.length}/${row.totalRegionCount} regions; excluded ${escapeHtml(row.excludedRegions.join(', '))}"`:'';
      html+=`<tr class="${overall} ${chosen}"><td><button class="region-button" data-key="${escapeHtml(row.key)}">${escapeHtml(row.region)}</button></td><td>${fmt(row.actual)}</td><td${reason}>${fmt(row.forecast)}</td><td class="${tone}">${row.errorPercent==null?'—':`${row.errorPercent>0?'+':''}${fmt(row.errorPercent)}%`}</td></tr>`;
    }
  }
  return html;
}

function seriesPath(rows,field,x,y){let connected=false;return rows.map((row,index)=>{if(row[field]==null){connected=false;return''}const step=`${connected?'L':'M'}${x(index)} ${y(row[field])}`;connected=true;return step}).join(' ')}

function showTip(event,row){tip.innerHTML=`<strong>${dateLabel(row.date)}</strong><span>Actual: ${row.actual==null?'—':fmt(row.actual)+' mm'}</span><span>Forecast: ${row.forecast==null?'—':fmt(row.forecast)+' mm'}</span>`;tip.classList.add('show');tip.style.left=Math.max(10,Math.min(innerWidth-tip.offsetWidth-10,event.clientX+14))+'px';tip.style.top=Math.max(10,Math.min(innerHeight-tip.offsetHeight-10,event.clientY-tip.offsetHeight-12))+'px'}

function renderChart(region){
  const chart=$('#dailyChart'),rows=region.daily,W=700,H=340,p={l:48,r:15,t:18,b:43},iw=W-p.l-p.r,ih=H-p.t-p.b;
  const values=rows.flatMap(row=>[row.actual,row.forecast]).filter(value=>value!=null),ceiling=Math.max(5,...values)*1.15;
  const x=index=>p.l+index/6*iw,y=value=>p.t+ih-value/ceiling*ih;
  let svg=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">`;
  for(let tick=0;tick<=4;tick++){
    const yy=p.t+tick/4*ih;
    svg+=`<line class="gridline" x1="${p.l}" y1="${yy}" x2="${W-p.r}" y2="${yy}"/><text class="axis" x="2" y="${yy+4}">${fmt(ceiling*(1-tick/4))}</text>`;
  }
  svg+=`<path d="${seriesPath(rows,'actual',x,y)}" fill="none" stroke="#14a99d" stroke-width="3"/><path d="${seriesPath(rows,'forecast',x,y)}" fill="none" stroke="#d79530" stroke-width="3" stroke-dasharray="7 5"/>`;
  rows.forEach((row,index)=>{
    const xx=x(index),short=new Date(row.date+'T00:00:00').toLocaleDateString(undefined,{weekday:'short',day:'numeric'});
    if(row.actual!=null)svg+=`<circle cx="${xx}" cy="${y(row.actual)}" r="4" fill="#14a99d" stroke="#fff" stroke-width="1"/>`;
    if(row.forecast!=null)svg+=`<circle cx="${xx}" cy="${y(row.forecast)}" r="4" fill="#fff" stroke="#d79530" stroke-width="2"/>`;
    svg+=`<text class="axis" text-anchor="middle" x="${xx}" y="${H-14}">${short}</text>`;
    svg+=`<rect data-index="${index}" x="${Math.max(p.l,index?xx-iw/12:p.l)}" y="${p.t}" width="${index===0||index===6?iw/12:iw/6}" height="${ih}" fill="transparent"/>`;
  });
  chart.innerHTML=svg;
  chart.onpointermove=event=>{const target=event.target.closest('[data-index]');if(target)showTip(event,rows[Number(target.dataset.index)]);else tip.classList.remove('show')};
  chart.onpointerleave=()=>tip.classList.remove('show');
  $('#chartTitle').textContent=`${region.region}: actual vs forecast (mm/day)`;
  if(region.excludedRegions?.length){
    $('#chartNote').textContent=`Both lines use the same ${region.includedRegions.length} of ${region.totalRegionCount} regions with complete actual and archived forecast data. Excluded: ${region.excludedRegions.join(', ')}.`;
  }else if(region.forecast==null){
    $('#chartNote').textContent='The archived run has fewer than seven forecast days for this region. The weekly forecast total and error are unavailable.';
  }else{
    $('#chartNote').textContent=`Seven daily values from the ${dateLabel(DATA.forecastRun)} forecast run; weekly totals appear in the table.`;
  }
}

function renderSelected(){const selected=$('#regionSelect').value,region=DATA.regions.find(item=>item.key===selected);if(!region)return;$('#accuracyTable').innerHTML=rowsHtml(DATA.regions,selected);renderChart(region)}

async function init(){
  try{
    const response=await fetch('/api/last-week-forecast-accuracy');DATA=await response.json();
    if(!response.ok)throw new Error(DATA.error||`HTTP ${response.status}`);
    $('#coverage').textContent=`Completed week: ${dateLabel(DATA.weekStart)} – ${dateLabel(DATA.weekEnd)}`;
    $('#periodNote').textContent=DATA.forecastRun?`Archived forecast from ${dateLabel(DATA.forecastRun)} compared with final actual rainfall for each day and for the seven-day total.`:'No archived forecast run covers the last completed week.';
    const partial=DATA.regions.filter(row=>row.key.startsWith('Overall ')&&row.excludedRegions?.length);
    if(partial.length){$('#overallNote').hidden=false;$('#overallNote').textContent=partial.map(row=>`${row.region}: actual and forecast both average ${row.includedRegions.length}/${row.totalRegionCount} regions. Excluded: ${row.excludedRegions.join(', ')}${row.missingForecastRegions?.length?' (no complete archived forecast)':''}.`).join(' ')}
    const select=$('#regionSelect');
    select.innerHTML=DATA.regions.map(row=>`<option value="${escapeHtml(row.key)}">${escapeHtml(row.key.startsWith('Overall ')?row.region:`${row.region}, ${row.country}`)}</option>`).join('');
    select.value=DATA.regions.find(row=>row.key==='Overall Indonesia'&&row.forecast!=null)?.key||DATA.regions.find(row=>row.forecast!=null)?.key||DATA.regions[0]?.key;
    select.onchange=renderSelected;
    $('#accuracyTable').onclick=event=>{const button=event.target.closest('[data-key]');if(button){select.value=button.dataset.key;select.dispatchEvent(new Event('change',{bubbles:true}))}};
    renderSelected();
  }catch(error){$('#coverage').textContent='Data unavailable';$('#error').hidden=false;$('#error').textContent=`Last-week comparison could not be loaded: ${error.message}`}
}
init();
