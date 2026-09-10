let map, markersLayer, facilityLayer, pathLayer, data = {events:[], facilities:[], paths:[]};

const center=[12.9716,77.5946];

function setStatus(text, ok=false){
  document.getElementById('statusText').textContent=text;
  document.getElementById('statusDot').style.background=ok?'#54d18a':'#ffc857';
}

function initMap(){
  map=L.map('map').setView(center,10);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'© OpenStreetMap contributors'}).addTo(map);
  markersLayer=L.layerGroup().addTo(map);
  facilityLayer=L.layerGroup().addTo(map);
  pathLayer=L.layerGroup().addTo(map);
}

function markerColor(e){
  return e.risk_score>=72 ? '#ff4f5e' : '#ff9d45';
}

function popup(e){
  return `<div style="min-width:190px"><b>${e.classification}</b><br>
  FRP: ${num(e.frp)}<br>Nearest: ${e.nearest_name||'—'}<br>
  Distance: ${num(e.distance_km,2)} km<br>Inside buffer: ${e.inside_industrial_buffer?'YES':'NO'}<br>
  Risk: ${num(e.risk_score,1)}/100</div>`;
}

function num(x,d=1){return x===null||x===undefined||Number.isNaN(Number(x))?'—':Number(x).toFixed(d)}

function render(){
  const only=document.getElementById('onlyIndustrial').checked;
  const minRisk=Number(document.getElementById('minRisk').value);
  const showMovement=document.getElementById('showMovement').checked;
  const filtered=data.events.filter(e=>(!only||e.inside_industrial_buffer)&&Number(e.risk_score||0)>=minRisk);

  markersLayer.clearLayers(); facilityLayer.clearLayers(); pathLayer.clearLayers();

  data.facilities.forEach(f=>{
    L.circleMarker([f.lat,f.lon],{radius:5,color:'#b887ff',fillColor:'#b887ff',fillOpacity:.9})
      .bindPopup(`<b>${f.name}</b><br>OSM type: ${f.kind||'industrial'}`).addTo(facilityLayer);
  });

  filtered.forEach(e=>{
    L.circleMarker([e.latitude,e.longitude],{radius:7,color:markerColor(e),fillColor:markerColor(e),fillOpacity:.9,weight:2})
      .bindPopup(popup(e))
      .on('click',()=>showDetail(e))
      .addTo(markersLayer);
  });

  if(showMovement){
    data.paths.forEach(p=>{
      if(!p.coordinates||p.coordinates.length<2) return;
      L.polyline(p.coordinates,{color:'#ff7a45',weight:4,opacity:.8})
       .bindTooltip(`${p.group}: ${num(p.distance_km,2)} km · ${num(p.speed_kmh,2)} km/h`)
       .addTo(pathLayer);
    });
  }

  document.getElementById('countDetections').textContent=filtered.length;
  document.getElementById('countOsm').textContent=data.facilities.length;
  document.getElementById('countBuffer').textContent=filtered.filter(x=>x.inside_industrial_buffer).length;
  document.getElementById('countHigh').textContent=filtered.filter(x=>x.risk_score>=72).length;
  document.getElementById('countPaths').textContent=showMovement?data.paths.length:0;

  const body=document.getElementById('eventsBody');
  body.innerHTML=filtered.map((e,i)=>{
    const cls=e.risk_score>=72?'high':e.risk_score>=45?'mid':'low';
    return `<tr onclick='showDetail(data.events[${data.events.indexOf(e)}])'>
      <td>${e.datetime_utc?new Date(e.datetime_utc).toLocaleString():'—'}</td>
      <td>${num(e.frp)}</td><td>${e.nearest_name||'—'}</td>
      <td>${num(e.distance_km,2)} km</td><td>${e.inside_industrial_buffer?'YES':'NO'}</td>
      <td>${num(e.risk_score,1)}</td><td><span class="badge ${cls}">${e.classification}</span></td>
    </tr>`;
  }).join('');
}

function showDetail(e){
  document.getElementById('detail').innerHTML=`<div class="detail-grid">
    <div class="detail-item"><small>Classification</small><b>${e.classification}</b></div>
    <div class="detail-item"><small>Risk score</small><b>${num(e.risk_score,1)}/100</b></div>
    <div class="detail-item"><small>FRP</small><b>${num(e.frp,1)}</b></div>
    <div class="detail-item"><small>Distance to nearest industrial feature</small><b>${num(e.distance_km,2)} km</b></div>
    <div class="detail-item"><small>Industrial buffer</small><b>${e.inside_industrial_buffer?'Inside':'Outside'}</b></div>
    <div class="detail-item"><small>Nearest feature</small><b>${e.nearest_name||'—'}</b></div>
    <div class="detail-item"><small>Latitude / Longitude</small><b>${num(e.latitude,5)}, ${num(e.longitude,5)}</b></div>
    <div class="detail-item"><small>Timestamp</small><b>${e.datetime_utc?new Date(e.datetime_utc).toLocaleString():'—'}</b></div>
  </div>`;
}

let firmsSessionKey = sessionStorage.getItem('firms_map_key') || '';
const keyInput = document.getElementById('firmsKey');
const saveKeyBtn = document.getElementById('saveKey');
const clearKeyBtn = document.getElementById('clearKey');

if(firmsSessionKey){
  keyInput.value=firmsSessionKey;
  document.getElementById('keyStatus').textContent='Session key set';
}

saveKeyBtn.addEventListener('click',()=>{
  const value=keyInput.value.trim();
  if(!value){alert('Paste your NASA FIRMS MAP_KEY first.');return;}
  firmsSessionKey=value;
  sessionStorage.setItem('firms_map_key',value);
  document.getElementById('keyStatus').textContent='Saved for this session';
});

clearKeyBtn.addEventListener('click',()=>{
  sessionStorage.removeItem('firms_map_key');
  firmsSessionKey='';
  keyInput.value='';
  document.getElementById('keyStatus').textContent='Not set';
});

async function loadData(mode){
  try{
    setStatus('Loading…');
    const source=document.getElementById('firmsSource').value;
    const days=Math.max(1,Math.min(30,parseInt(document.getElementById('days').value||'2',10)));
    const buffer=Math.max(1,Math.min(15,parseFloat(document.getElementById('bufferKm').value||'5')));
    document.getElementById('days').value=days;
    document.getElementById('bufferKm').value=buffer;

    let r;
    if(mode==='live'){
      firmsSessionKey = keyInput.value.trim() || sessionStorage.getItem('firms_map_key') || '';
      if(!firmsSessionKey){
        alert('Enter your NASA FIRMS MAP_KEY in the box on the left, then click Save key or Load live data.');
        setStatus('Demo mode');
        return;
      }
      sessionStorage.setItem('firms_map_key',firmsSessionKey);
      document.getElementById('keyStatus').textContent='Session key set';
      r=await fetch('/api/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({map_key:firmsSessionKey,source,days,buffer_km:buffer})});
    } else {
      r=await fetch(`/api/demo?buffer_km=${buffer}`);
    }

    const contentType=r.headers.get('content-type')||'';
    const text=await r.text();
    let j;
    try{j=JSON.parse(text);}catch{throw new Error(`Server returned ${r.status} without valid JSON.`);}
    if(!r.ok) throw new Error(j.detail||j.error||`Request failed (${r.status})`);
    data=j; render();
    setStatus(mode==='live'?'Live NASA FIRMS + OSM':'Demo mode',mode==='live');
    if(map && data.events && data.events.length) map.setView([data.events[0].latitude,data.events[0].longitude],10);
  }catch(err){
    console.error(err);
    alert(err instanceof Error ? err.message : String(err));
    setStatus('Error');
  }
}

document.getElementById('loadDemo').onclick=()=>loadData('demo');
document.getElementById('loadLive').onclick=()=>loadData('live');
document.getElementById('onlyIndustrial').onchange=render;
document.getElementById('showMovement').onchange=render;
document.getElementById('minRisk').oninput=e=>{document.getElementById('riskValue').textContent=e.target.value;render()};

document.getElementById('exportBtn').onclick=()=>{
  const only=document.getElementById('onlyIndustrial').checked;
  const minRisk=Number(document.getElementById('minRisk').value);
  const rows=data.events.filter(e=>(!only||e.inside_industrial_buffer)&&Number(e.risk_score||0)>=minRisk);
  const cols=['latitude','longitude','datetime_utc','frp','distance_km','inside_industrial_buffer','nearest_name','classification','risk_score'];
  const csv=[cols.join(','),...rows.map(r=>cols.map(c=>JSON.stringify(r[c]??'')).join(','))].join('\\n');
  const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));a.download='bengaluru_thermal_analysis.csv';a.click();
};

initMap(); loadData('auto');
if(firmsSessionKey){document.getElementById('keyStatus').textContent='Session key set';}
