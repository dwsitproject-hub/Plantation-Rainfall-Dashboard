let DATA;
const $ = selector => document.querySelector(selector);
const fmt = value => Number(value).toLocaleString(undefined, {maximumFractionDigits: 1});
const period = document.body.dataset.period;
const hoverTip = document.createElement('div');
hoverTip.className = 'chart-tooltip'; hoverTip.setAttribute('role', 'status'); document.body.appendChild(hoverTip);

function attachHover(element, rows, render) {
  element.onpointermove = event => {
    if (!rows.length) return;
    const box=element.getBoundingClientRect(),viewWidth=Number(element.dataset.hoverViewWidth)||100,left=Number(element.dataset.hoverLeft)||5,right=Number(element.dataset.hoverRight)||5;
    const pointerX=(event.clientX-box.left)/box.width*viewWidth,ratio=Math.max(0,Math.min(1,(pointerX-left)/(viewWidth-left-right)));
    hoverTip.innerHTML=render(rows[Math.round(ratio*(rows.length-1))]);hoverTip.classList.add('show');
    hoverTip.style.left=Math.min(innerWidth-hoverTip.offsetWidth-10,event.clientX+14)+'px';hoverTip.style.top=Math.max(10,Math.min(innerHeight-hoverTip.offsetHeight-10,event.clientY-hoverTip.offsetHeight-14))+'px';
  };
  element.onpointerleave=()=>hoverTip.classList.remove('show');
}

const chartTargets=[];
function buildCountries(){const root=$('#regionSections');root.replaceChildren();chartTargets.length=0;let index=0;for(const country of ['Indonesia','Malaysia','Thailand']){const section=document.createElement('section');section.className='country-section';section.id=country.toLowerCase();section.innerHTML=`<div class="country-heading"><h2>${country}</h2><span>Overall → regions</span></div>`;const regions=DATA.regions.filter(region=>region.country===country).sort((a,b)=>Number(b.key.startsWith('Overall '))-Number(a.key.startsWith('Overall '))||a.region.localeCompare(b.region));for(const region of regions){const card=document.createElement('article');card.className='region-card panel';card.innerHTML=`<div class="region-card-head"><h3>${region.region}</h3><span>${region.key.startsWith('Overall ')?'Major plantation-area average':'Region'}</span></div><div><h4>Daily mean temperature (°C)</h4><div id="temperature-region-${index}" class="season-chart" role="img" aria-label="${period==='week'?'Weekly':'Monthly'} temperature seasonality for ${region.region}"></div></div>`;section.appendChild(card);chartTargets.push({region:region.key,target:`#temperature-region-${index++}`});}root.appendChild(section);}}
function renderAll(){const start=Number($('#overviewStart').value),end=Number($('#overviewEnd').value);for(const chart of chartTargets)renderSeasonalChart(period,'temperature',chart.target,'°C',{region:chart.region,start,end});}
function setupYears(){const current=Number(DATA.temperatureLastActual.slice(0,4)),years=[...new Set(DATA.weekly.filter(row=>row.type==='historical_actual'&&row.year>=2000&&row.year<current&&row.temperature!=null).map(row=>row.year))].sort((a,b)=>a-b),options=years.map(year=>`<option value="${year}">${year}</option>`).join(''),start=$('#overviewStart'),end=$('#overviewEnd');start.innerHTML=end.innerHTML=options;start.value=Math.max(years[0],years.at(-1)-9);end.value=years.at(-1);start.onchange=()=>{if(+start.value>+end.value)end.value=start.value;renderAll()};end.onchange=()=>{if(+end.value<+start.value)start.value=end.value;renderAll()};}
async function init(){try{const response=await fetch('/api/data');DATA=await response.json();if(!response.ok)throw new Error(DATA.error||`HTTP ${response.status}`);$('#coverage').textContent=`Actuals through ${DATA.temperatureLastActual} • Forecast to ${DATA.forecastEnd}`;setupYears();buildCountries();renderAll()}catch(error){$('#coverage').textContent='Data unavailable';$('#error').hidden=false;$('#error').textContent=`Temperature seasonality could not be loaded: ${error.message}`}}
init();
