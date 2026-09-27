/* Kadal mobile client: no synthetic values and no offline route advice. */
const $ = id => document.getElementById(id);
const state = {ports: [], map: null, markers: [], layers: [], samples: [], pick: null, routeVersion: 0, conditionVersion: 0, routeExpiry: null, dataAvailable: false, boundary: null, directLine: null, watchId: null, boatMarker: null, lastFix: null, lastBorderState: null, trackingEpoch: 0, audio: null};
const number = (v, digits = 1) => Number.isFinite(v) ? v.toFixed(digits) : '—';
const clockTime = value => new Date(value).toLocaleTimeString('en-IN', {hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Kolkata'});
const stamp = value => new Date(value).toLocaleString('en-IN', {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Kolkata'}) + ' IST';
const escapeHTML = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const modelName = name => ({random_forest:'Random Forest',xgboost:'XGBoost',auto:'Auto'}[name] || name);
function badge(id,label) {const safe=['safe','moderate','danger'].includes(label)?label:'unknown';$(id).className='risk-badge '+safe;$(id).textContent=safe==='unknown'?'Unavailable':safe[0].toUpperCase()+safe.slice(1);}

async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 90000);
  try {
    const response = await fetch(path, {...options, signal: controller.signal, cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Check your coordinates and boat limits.');
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('The request timed out. Please retry.');
    throw error;
  } finally { clearTimeout(timeout); }
}

function points() {
  return {start: {latitude: parseFloat($('start-lat').value), longitude: parseFloat($('start-lon').value)}, end: {latitude: parseFloat($('end-lat').value), longitude: parseFloat($('end-lon').value)}};
}

function invalidateRoutes(message = 'Inputs changed. Compare routes again for updated advice.') {
  state.routeVersion++;
  clearTimeout(state.routeExpiry);
  for (const layer of [...state.layers, ...state.samples]) layer.remove();
  state.layers = []; state.samples = [];
  $('route-results').replaceChildren();
  $('route-status').textContent = message;
  $('route-status').className = 'status';
  $('plan-route').disabled = !navigator.onLine || !state.dataAvailable;
}

function updateMarkers(fit = false) {
  if (!state.map) return;
  state.markers.forEach(m => m.remove()); state.markers = [];
  const {start, end} = points();
  const coords = [start, end].map(p => [p.latitude, p.longitude]);
  if (!coords.flat().every(Number.isFinite)) return;
  coords.forEach((p, i) => {
    const marker = L.marker(p, {icon: L.divIcon({className: '', html: `<span class="marker-label ${i ? 'end' : ''}">${i ? 'B' : 'A'}</span>`, iconSize: [28,28], iconAnchor: [14,14]}), draggable: true}).addTo(state.map).bindPopup(i ? 'Destination · offshore' : 'Start · offshore');
    marker.on('dragend', () => {
      const pos = marker.getLatLng();
      $(i ? 'end-lat' : 'start-lat').value = pos.lat.toFixed(4);
      $(i ? 'end-lon' : 'start-lon').value = pos.lng.toFixed(4);
      invalidateRoutes(); previewBoundary();
    });
    state.markers.push(marker);
  });
  if (fit) state.map.fitBounds(coords, {padding: [55,55], maxZoom: 10});
  previewBoundary();
}

function setupMap() {
  if (!window.L) { $('map').textContent = 'Map library unavailable. Reload while online.'; return; }
  state.map = L.map('map', {zoomControl: true, minZoom: 6, maxZoom: 15}).setView([11, 79.8], 7);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19, attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'}).addTo(state.map);
  state.map.on('click', e => {
    if (!state.pick) return;
    if (e.latlng.lat < 7 || e.latlng.lat > 14 || e.latlng.lng < 77 || e.latlng.lng > 81) {
      $('map-mode').textContent = 'Choose a point within 7–14°N, 77–81°E.'; return;
    }
    const selected = state.pick;
    $(selected + '-lat').value = e.latlng.lat.toFixed(4);
    $(selected + '-lon').value = e.latlng.lng.toFixed(4);
    state.pick = null;
    $('pick-start').classList.remove('selected'); $('pick-end').classList.remove('selected');
    $('map-mode').textContent = 'Point selected';
    invalidateRoutes(); updateMarkers();
  });
}

function updateSafetyLocation() {
  const lat = parseFloat($('safety-lat').value), lon = parseFloat($('safety-lon').value);
  $('safety-location').textContent = Number.isFinite(lat) && Number.isFinite(lon)
    ? `${lat}°N • ${lon}°E` : 'Enter latitude and longitude';
}

function clearConditions(message) {
  $('wave-value').textContent = $('wind-value').textContent = $('prediction-value').textContent = $('prediction-wind-value').textContent = '—';
  $('data-stamp').textContent = message;
  badge('current-risk','unknown');badge('forecast-risk','unknown');
  document.querySelector('.safety-panels').setAttribute('aria-busy', 'false');
}

async function refreshConditions() {
  if (!$('safety-form').reportValidity()) return;
  const version = ++state.conditionVersion;
  const location = {latitude:parseFloat($('safety-lat').value),longitude:parseFloat($('safety-lon').value),model:$('prediction-model').value};
  updateSafetyLocation();
  clearConditions('Fetching live sea data for your coordinates…');
  $('predict-safety').disabled = true;
  $('predict-safety').textContent = 'Fetching…';
  document.querySelector('.safety-panels').setAttribute('aria-busy', 'true');
  try {
    const data=await api(`/api/conditions?latitude=${location.latitude}&longitude=${location.longitude}&model=${location.model}`);
    if(version!==state.conditionVersion)return;
    $('wave-value').textContent = number(data.current?.wave_m, 2);
    $('wind-value').textContent = number(data.current?.wind_ms);
    const messages = [];
    if(data.current_risk) badge('current-risk',data.current_risk.label);
    if (data.prediction?.available) {
      const pred = data.prediction;
      $('prediction-value').textContent = number(pred.wave_m, 2);
      if(pred.risk_prediction?.available) badge('forecast-risk',pred.risk_prediction.label);
      else messages.push(pred.risk_prediction?.reason || 'Risk prediction unavailable.');
    } else messages.push(data.prediction?.reason || 'Prediction unavailable.');
    // Display the existing weather forecast at exactly +6h; no wind ML model is added.
    const targetTime = data.prediction?.target_time || (data.current?.time ? new Date(new Date(data.current.time).getTime() + 6 * 3600000).toISOString() : null);
    const windForecast = targetTime && data.hourly?.find(h => new Date(h.time).getTime() === new Date(targetTime).getTime());
    $('prediction-wind-value').textContent = number(windForecast?.wind_ms);
    if (!Number.isFinite(windForecast?.wind_ms)) messages.push('Six-hour wind forecast unavailable.');
    $('data-stamp').textContent = messages.join(' ');
    $('safety-input-status').textContent = 'Live sea data fetched for your coordinates.';
  }catch(error){
    if(version!==state.conditionVersion)return;
    clearConditions(error.message);
    $('safety-input-status').textContent=error.message;
  }finally{
    if(version===state.conditionVersion){
      $('predict-safety').disabled=false;
      $('predict-safety').textContent='Fetch & Predict';
      document.querySelector('.safety-panels').setAttribute('aria-busy', 'false');
    }
  }
}

async function planRoute() {
  const fields = ['start-lat','start-lon','end-lat','end-lon','speed','hours','wave-limit','wind-limit'];
  if (fields.some(id => !$(id).reportValidity() || !Number.isFinite(parseFloat($(id).value)))) return;
  invalidateRoutes('Fetching forecasts across the route area and checking ocean paths…');
  const version = state.routeVersion;
  $('plan-route').disabled = true;
  try {
    const data = await api('/api/routes', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({...points(),model:$('model-choice').value,speed_knots:+$('speed').value,trip_hours:+$('hours').value,max_wave_m:+$('wave-limit').value,max_wind_ms:+$('wind-limit').value})});
    if (version !== state.routeVersion) return;
    $('route-status').textContent = `${data.message} · ${number(data.response_ms/1000)} s`;
    showRouteBoundary(data);
    (data.samples || []).forEach(p => {
      if (state.map) state.samples.push(L.circleMarker([p.latitude,p.longitude],{radius:5,color:p.blocked?'#bc563e':p.label==='moderate'?'#b27a22':'#33886c',weight:1,fillOpacity:.5}).addTo(state.map).bindPopup(`Project class: ${escapeHTML(p.label || 'unknown')}<br>Wave envelope: ${number(p.wave_m)} m<br>Wind envelope: ${number(p.wind_ms)} m/s<br>${p.blocked?'Excluded: danger or vessel limit':'Below selected limits'}`));
    });
    if (!data.routes.length) {
      $('route-results').innerHTML = `<div class="error-box">${escapeHTML(data.message)}</div>`; return;
    }
    data.routes.forEach((route,i) => {
      if (state.map) state.layers.push(L.polyline(route.coordinates,{color:i?'#087d79':'#dc8646',weight:i?4:6,opacity:.85,dashArray:i?null:'7 5'}).addTo(state.map));
    });
    if (state.layers.length) state.map.fitBounds(L.featureGroup(state.layers).getBounds(),{padding:[35,35]});
    $('route-results').innerHTML = data.routes.map((r,i) => `<button class="route-card ${i===data.routes.length-1?'selected':''}" data-route="${i}"><h3><i class="legend-line ${i?'teal':'orange'}"></i> ${escapeHTML(r.name)}</h3>${r.label?`<span class="risk-badge ${r.label}">${escapeHTML(r.label)}</span>`:''}<div class="route-distance">${number(r.distance_km)} <small>km · ${number(r.duration_hours)} h</small></div><p>Peak envelope: ${number(r.max_wave_m)} m waves · ${number(r.max_wind_ms)} m/s wind</p><p>Peak routing score: ${number(r.peak_hazard_score,3)} · Limit ratio: ${number(r.peak_risk_ratio*100,1)}%</p><p>Cumulative exposure index: ${number(r.exposure_index_km)}</p></button>`).join('');
    const same = data.routes.length===2 && JSON.stringify(data.routes[0].coordinates)===JSON.stringify(data.routes[1].coordinates);
    const note = document.createElement('p'); note.className='route-note';
    const detour=data.routes.length===2 ? `Recommended distance is ${number((data.routes[1].distance_km/data.routes[0].distance_km-1)*100)}% longer than shortest feasible. ` : '';
    note.textContent = `${same?'Both objectives found the same path. ':detour}Recommended route minimises peak estimated risk, then distance. Danger cells are excluded. Worst conditions over ${data.forecast_window_hours} hours include both models’ +6h classes and upper wave bands. Data retrieved ${stamp(data.fetched_at)}. Project-derived labels; approximate coastline and treaty boundary; no depth or other legal-zone checks.`;
    $('route-results').append(note);
    document.querySelectorAll('[data-route]').forEach(button => button.addEventListener('click', () => {
      document.querySelectorAll('[data-route]').forEach(b=>b.classList.toggle('selected',b===button));
      const layer = state.layers[+button.dataset.route]; if (layer) {layer.bringToFront();state.map.fitBounds(layer.getBounds(),{padding:[35,35]});}
    }));
    const remaining = 10*60*1000 - (Date.now() - new Date(data.fetched_at).getTime());
    state.routeExpiry = setTimeout(()=>invalidateRoutes('Forecast snapshot expired. Compare routes to refresh advice.'),Math.max(0,remaining));
  } catch(error) {
    if (version !== state.routeVersion) return;
    $('route-status').textContent = error.message; $('route-status').className='status error';
    $('route-results').innerHTML=`<div class="error-box">${escapeHTML(error.message)}</div>`;
  } finally { if(version===state.routeVersion) $('plan-route').disabled = !navigator.onLine || !state.dataAvailable; }
}

async function loadEvaluation() {
  if (!$('evaluation')) return;
  try {
    const e = await api('/api/evaluation');
    if (!e.test_metrics) { $('evaluation').textContent='Train the models to produce evaluation evidence.'; return; }
    const c=e.classification;
    let html=`<h3>Random Forest vs XGBoost</h3><p>2019–2023 training · 2024 validation · 2025 held-out test. Model selection uses validation data only.</p><h4>Wave regression · six hours ahead</h4><p>Auto selection: ${escapeHTML(modelName(e.selected_regressor || e.model))}</p><div class="table-scroll"><table><thead><tr><th>Model</th><th>Validation MAE</th><th>Test MAE</th><th>Test RMSE</th></tr></thead><tbody>${(e.candidates || []).map(r=>`<tr><td>${escapeHTML(modelName(r.name || 'Model'))}</td><td>${number(r.validation_mae_m,3)} m</td><td>${number(r.test_metrics?.mae_m,3)} m</td><td>${number(r.test_metrics?.rmse_m,3)} m</td></tr>`).join('')}<tr><td>Persistence</td><td>—</td><td>${number(e.persistence_baseline.mae_m,3)} m</td><td>${number(e.persistence_baseline.rmse_m,3)} m</td></tr></tbody></table></div>`;
    if(c?.models){
      html+=`<h4>Risk classification · six hours ahead</h4><p>Auto selection: ${escapeHTML(modelName(c.selected_classifier))}. Labels are <strong>project-derived from IMD sea-state guidance</strong>, not recorded government warnings.</p><div class="table-scroll"><table><thead><tr><th>Model</th><th>Val macro F1</th><th>Test accuracy</th><th>Test macro F1</th><th>Danger recall</th></tr></thead><tbody>${Object.entries(c.models).map(([name,r])=>`<tr><td>${escapeHTML(modelName(name))}</td><td>${number(r.validation.macro_f1,3)}</td><td>${number(r.test.accuracy*100)}%</td><td>${number(r.test.macro_f1,3)}</td><td>${number(r.test.per_class.danger.recall*100)}%</td></tr>`).join('')}<tr><td>Persistence</td><td>—</td><td>${number(c.persistence_baseline.accuracy*100)}%</td><td>${number(c.persistence_baseline.macro_f1,3)}</td><td>${number(c.persistence_baseline.per_class.danger.recall*100)}%</td></tr></tbody></table></div><h4>Confusion matrices · 2025</h4><p>Rows: actual derived class. Columns: predicted class.</p><div class="confusion-grid">${Object.entries(c.models).map(([name,r])=>`<div><strong>${escapeHTML(modelName(name))}</strong><table><thead><tr><th>Actual ↓</th>${c.labels.map(l=>`<th>${escapeHTML(l)}</th>`).join('')}</tr></thead><tbody>${r.test.confusion_matrix.map((row,i)=>`<tr><th>${escapeHTML(c.labels[i])}</th>${row.map(n=>`<td>${n.toLocaleString()}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`).join('')}</div><h4>Where the labels come from</h4><ul>${Object.entries(c.policy.mapping).map(([name,rule])=>`<li><strong>${escapeHTML(name)}:</strong> ${escapeHTML(rule)}</li>`).join('')}</ul><p>${escapeHTML(c.policy.meaning)}</p><p><a href="${escapeHTML(c.policy.source_url)}" target="_blank" rel="noopener">IMD source · Table 6.10 ↗</a> · <a href="/api/label-policy" target="_blank" rel="noopener">Exact policy and boundaries ↗</a> · <a href="/api/labeled-dataset">Download labelled dataset (.csv.gz)</a></p><h4>Interpretability</h4><p>Validation permutation importance: loss in macro F1 after shuffling each feature.</p><ul>${Object.entries(c.permutation_importance_macro_f1).sort((a,b)=>b[1]-a[1]).slice(0,5).map(([k,v])=>`<li>${escapeHTML(k.replaceAll('_',' '))}: ${number(v,3)}</li>`).join('')}</ul><h4>Limits of these results</h4><ul>${c.limitations.map(l=>`<li>${escapeHTML(l)}</li>`).join('')}</ul>`;
    }else html+='<p>Risk classification training is not yet available.</p>';
    html+='<p><a href="/api/evaluation" target="_blank" rel="noopener">Complete evaluation JSON ↗</a></p>';
    $('evaluation').innerHTML=html;
  } catch(error) {$('evaluation').textContent=error.message;}
}

function connectivity() {
  if (!navigator.onLine) state.dataAvailable=false;
  $('plan-route').disabled=!navigator.onLine||!state.dataAvailable;
  $('connection').textContent=!navigator.onLine?'○ Offline · forecasts paused':state.dataAvailable?'● Online · route planning ready':'○ Server unavailable';
  $('connection').classList.toggle('offline',!navigator.onLine||!state.dataAvailable);
  if(!navigator.onLine){state.conditionVersion++;clearConditions('Offline. Predictions require the server. Cached GPS boundary checks can continue.');invalidateRoutes('Offline. Fresh forecasts are required for route advice.');}
}

async function connectBackend(){
  try{
    const cfg=await api('/api/config');state.ports=cfg.ports;state.dataAvailable=navigator.onLine;
    const selected=$('port').value;
    $('port').innerHTML=cfg.ports.map(p=>`<option value="${escapeHTML(p.id)}">${escapeHTML(p.name)}</option>`).join('');
    if(selected)$('port').value=selected;
  }catch(error){state.dataAvailable=false;$('route-status').textContent='Backend unavailable: '+error.message;}
  connectivity();
}

function invalidateSafety(){
  state.conditionVersion++;clearConditions('Inputs changed. Run prediction for the new values.');
  updateSafetyLocation();
  $('predict-safety').disabled=false;
  $('predict-safety').textContent='Fetch & Predict';
}

function oneLocation(target){
  const status=target==='safety'?'safety-input-status':'route-status';
  if(!navigator.geolocation){$(status).textContent='GPS is unavailable.';return;}
  $(status).textContent='Locating you…';
  navigator.geolocation.getCurrentPosition(pos=>{
    const {latitude,longitude}=pos.coords;
    if(latitude<7||latitude>14||longitude<77||longitude>81){$(status).textContent='Location is outside the Tamil Nadu study region.';return;}
    if(target==='safety'){
      $('safety-lat').value=latitude.toFixed(5);$('safety-lon').value=longitude.toFixed(5);invalidateSafety();
    }else{
      $('start-lat').value=latitude.toFixed(5);$('start-lon').value=longitude.toFixed(5);invalidateRoutes();updateMarkers(true);
    }
    $(status).textContent='GPS coordinates selected. The point must be offshore.';
  },()=>{$(status).textContent='Location unavailable. Allow GPS over HTTPS or enter coordinates.';},{enableHighAccuracy:true,timeout:15000,maximumAge:0});
}

function routeBanner(message,severity='near'){
  $('route-border-warning').hidden=!message;$('route-border-warning').textContent=message;
  $('route-border-warning').className='boundary-banner '+severity;
}
async function loadBoundary(){
  try{
    const data=await api('/data/maritime-boundary.json');
    if(!Array.isArray(data.coordinates)||data.coordinates.length<2)throw new Error('Invalid boundary data');
    state.boundary=data;
    if(state.map)L.polyline(data.coordinates,{color:'#a94b55',weight:2,dashArray:'8 5',interactive:true}).addTo(state.map).bindPopup('India–Sri Lanka maritime boundary · treaty-derived approximation');
    $('start-tracking').disabled=false;previewBoundary();
  }catch(error){routeBanner('Boundary layer unavailable. Live border monitoring cannot start.','unknown');$('start-tracking').disabled=true;}
}
function previewBoundary(){
  if(state.directLine){state.directLine.remove();state.directLine=null;}
  if(!state.boundary)return;
  const p=points(),coords=[[p.start.latitude,p.start.longitude],[p.end.latitude,p.end.longitude]];
  if(!coords.flat().every(Number.isFinite)){routeBanner('Enter valid coordinates to check the border.','unknown');return;}
  const check=KadalBoundary.path(state.boundary,coords);
  if(check.crosses_boundary||check.has_foreign_side){
    routeBanner('Border warning: the requested direct connection crosses the maritime boundary or reaches the Sri Lankan side. The dashed red line is a request preview, not a navigation route.','outside');
    if(state.map)state.directLine=L.polyline(coords,{color:'#bd3f40',weight:3,dashArray:'4 7'}).addTo(state.map).bindPopup('Requested direct connection — not a recommended route');
  }else if(check.unknown)routeBanner('Outside boundary-monitor coverage. Border status is unknown.','unknown');
  else if(check.near)routeBanner('Selected point is near the maritime boundary. Route planning requires clearance from the boundary.','near');
  else routeBanner('');
}
function showRouteBoundary(data){
  if(data.status==='boundary_blocked'){routeBanner(data.message,'outside');return;}
  if(data.boundary?.crosses_boundary||data.boundary?.has_foreign_side){
    routeBanner(data.routes.length?'Direct connection crosses the boundary. Returned recommendations avoid the mapped border and its planning buffer.':data.boundary.message,'near');
  }
}
function trackingUnknown(message){
  $('gps-status').textContent=message;
  $('boat-warning').hidden=false;$('boat-warning').className='boundary-banner unknown';$('boat-warning').textContent=message;
  state.lastBorderState='unknown';
}
function soundWarning(){
  if(!$('alert-sound').checked)return;
  try{
    const context=state.audio;if(!context)return;
    const oscillator=context.createOscillator(),gain=context.createGain();
    oscillator.connect(gain);gain.connect(context.destination);oscillator.frequency.value=880;
    gain.gain.setValueAtTime(.16,context.currentTime);gain.gain.exponentialRampToValueAtTime(.001,context.currentTime+.7);
    oscillator.start();oscillator.stop(context.currentTime+.7);
  }catch(error){/* Visual alert remains available if audio is unsupported. */}
  navigator.vibrate?.([200,100,200]);
}
function receiveFix(position,epoch){
  if(epoch!==state.trackingEpoch||state.watchId===null)return;
  if(document.hidden){trackingUnknown('Monitoring suspended while app is hidden. Reopen for a fresh GPS fix.');return;}
  const {latitude,longitude,accuracy}=position.coords,time=position.timestamp;
  if(!Number.isFinite(time)||Date.now()-time>30000||time>Date.now()+5000){trackingUnknown('GPS fix is stale. Current border status is unknown.');return;}
  if(state.lastFix&&time<state.lastFix.timestamp)return;
  const point=[latitude,longitude];
  let report=KadalBoundary.assess(state.boundary,latitude,longitude,accuracy);
  if(!Number.isFinite(accuracy)||accuracy>1000)report={status:'unknown',side:'unknown',message:'GPS accuracy is too poor to confirm border status. Wait for a better fix.'};
  const previous=state.lastFix;
  const recent=previous&&time-previous.timestamp<=30000&&previous.accuracy<=1000&&accuracy<=1000;
  const crossed=recent&&previous.report.side==='india'&&report.side==='sri_lanka'&&report.status==='outside';
  const possible=recent&&report.status==='inside'&&previous.report.status==='inside'&&KadalBoundary.intersects(state.boundary,previous.point,point);
  let message=crossed?'Border crossing detected between GPS fixes: boat is now on the Sri Lankan side of the mapped boundary.':possible?'Possible border crossing between GPS fixes. The straight line between fixes intersects the mapped boundary.':report.message;
  state.lastFix={point,timestamp:time,accuracy,report};
  if(state.map&&point.every(Number.isFinite)){
    if(!state.boatMarker)state.boatMarker=L.circleMarker(point,{radius:8,color:'#204ecf',fillColor:'#fff',fillOpacity:1,weight:3}).addTo(state.map);
    else state.boatMarker.setLatLng(point);
    state.boatMarker.bindPopup('Live boat GPS');
  }
  const dangerous=crossed||possible||['outside','near','uncertain'].includes(report.status);
  $('boat-warning').hidden=false;$('boat-warning').className='boundary-banner '+(crossed||possible?'outside':report.status);
  $('boat-warning').textContent=message;
  $('gps-status').textContent=`GPS ${latitude.toFixed(5)}, ${longitude.toFixed(5)} · ±${Math.round(accuracy)} m · ${stamp(time)}${Number.isFinite(report.distance_km)?` · Border ${number(report.distance_km,2)} km away`:''}`;
  if(dangerous&&(state.lastBorderState!==report.status||crossed||possible)){
    $('last-border-event').textContent=`Last alert · ${stamp(time)}: ${message}`;soundWarning();
  }
  state.lastBorderState=report.status;
}
function startTracking(){
  if(!state.boundary||!navigator.geolocation){trackingUnknown('GPS or boundary data unavailable. Live monitoring cannot start.');return;}
  if(state.watchId!==null)return;
  try{const Audio=window.AudioContext||window.webkitAudioContext;if(Audio){state.audio=state.audio||new Audio();state.audio.resume().catch(()=>{});}}catch(error){}
  const epoch=++state.trackingEpoch;
  state.lastFix=null;state.lastBorderState=null;
  $('start-tracking').disabled=true;$('stop-tracking').disabled=false;trackingUnknown('Waiting for a fresh GPS fix…');
  state.watchId=navigator.geolocation.watchPosition(p=>receiveFix(p,epoch),error=>{
    if(epoch!==state.trackingEpoch)return;
    trackingUnknown(error.code===1?'GPS permission denied. Live border monitoring is unavailable.':'GPS signal unavailable. Current border status is unknown.');
    if(error.code===1){navigator.geolocation.clearWatch(state.watchId);state.watchId=null;$('start-tracking').disabled=false;$('stop-tracking').disabled=true;state.trackingEpoch++;}
  },{enableHighAccuracy:true,maximumAge:0,timeout:15000});
}
function stopTracking(){
  if(state.watchId!==null)navigator.geolocation.clearWatch(state.watchId);
  state.watchId=null;state.lastFix=null;state.trackingEpoch++;
  $('start-tracking').disabled=!state.boundary;$('stop-tracking').disabled=true;
  $('gps-status').textContent='Stopped · live boat monitoring is off.';
  $('boat-warning').hidden=true;
  if(state.boatMarker){state.boatMarker.remove();state.boatMarker=null;}
}
function restartTracking(){stopTracking();startTracking();}
setInterval(()=>{if(state.watchId!==null&&state.lastFix&&Date.now()-state.lastFix.timestamp>30000)trackingUnknown('GPS updates have stopped. Current border status is unknown.');},5000);

async function init(){
  setupMap();connectivity();
  document.querySelectorAll('[data-tab]').forEach(button=>button.addEventListener('click',()=>{
    document.querySelectorAll('[data-tab]').forEach(b=>{b.classList.toggle('active',b===button);b.setAttribute('aria-selected',String(b===button));});
    document.querySelectorAll('.view').forEach(v=>v.classList.toggle('active',v.id===button.dataset.tab));
    if(button.dataset.tab==='plan')setTimeout(()=>{state.map?.invalidateSize();if(state.layers.length)state.map.fitBounds(L.featureGroup(state.layers).getBounds(),{padding:[35,35]});else updateMarkers(true);},0);
  }));
  $('safety-form').addEventListener('submit',e=>{e.preventDefault();refreshConditions();});
  ['safety-lat','safety-lon','prediction-model'].forEach(id=>$(id).addEventListener('input',invalidateSafety));
  $('safety-gps').addEventListener('click',()=>oneLocation('safety'));
  $('gps').addEventListener('click',()=>oneLocation('route'));
  $('plan-route').addEventListener('click',planRoute);
  $('model-choice').addEventListener('change',()=>invalidateRoutes('Route model changed. Compare routes again.'));
  ['start-lat','start-lon','end-lat','end-lon','speed','hours','wave-limit','wind-limit'].forEach(id=>$(id).addEventListener('input',()=>{invalidateRoutes();updateMarkers();}));
  ['start','end'].forEach(p=>$('pick-'+p).addEventListener('click',()=>{
    state.pick=p;$('pick-start').classList.toggle('selected',p==='start');$('pick-end').classList.toggle('selected',p==='end');
    $('map-mode').textContent=`Tap the map to place your ${p} point`;$('map').scrollIntoView({behavior:'smooth',block:'center'});
  }));
  $('port').addEventListener('change',()=>{
    const p=state.ports.find(p=>p.id===$('port').value);if(!p)return;
    $('start-lat').value=p.latitude;$('start-lon').value=p.longitude;$('end-lat').value=p.destination[0];$('end-lon').value=p.destination[1];
    invalidateRoutes('Departure area selected. Adjust destination and compare routes.');updateMarkers(true);
  });
  $('start-tracking').addEventListener('click',startTracking);$('stop-tracking').addEventListener('click',stopTracking);
  loadBoundary();await connectBackend();updateMarkers(true);refreshConditions();loadEvaluation();
}

let installPrompt;
window.addEventListener('beforeinstallprompt',event=>{event.preventDefault();installPrompt=event;});
$('install').addEventListener('click',async()=>{if(installPrompt){await installPrompt.prompt();installPrompt=null;}else $('install-dialog').showModal();});
$('close-dialog').addEventListener('click',()=>$('install-dialog').close());
window.addEventListener('online',()=>{connectBackend();refreshConditions();if(!state.boundary)loadBoundary();});
window.addEventListener('offline',connectivity);
document.addEventListener('visibilitychange',()=>{if(!document.hidden){invalidateRoutes('Session resumed. Refresh route advice before using it.');refreshConditions();if(state.watchId!==null)restartTracking();}else if(state.watchId!==null)trackingUnknown('Monitoring suspended while app is hidden. Reopen for a fresh GPS fix.');});
setInterval(()=>{if(!document.hidden&&navigator.onLine)refreshConditions();},10*60*1000);
let resizeTimer;
window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{
  if(state.map&&state.layers.length)state.map.fitBounds(L.featureGroup(state.layers).getBounds(),{padding:[30,30]});
},200);});
if('serviceWorker'in navigator) navigator.serviceWorker.register('/sw.js').catch(()=>{});
init();
