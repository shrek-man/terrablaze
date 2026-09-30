let map;
let markersLayer;
let facilityLayer;
let pathLayer;
let data = { events: [], facilities: [], paths: [] };
let queryInfo = { period: 'recent', days: 7 };
let firmsSessionKey = sessionStorage.getItem('firms_map_key') || '';
let markersByIncident = new Map();
let selectionMarker = null;

const INDIA_BOUNDS = [[6, 68], [37.5, 97.5]];
const MAP_EVENT_LIMIT = 15000;
const TABLE_ROW_LIMIT = 1000;
const SATELLITE_COLORS = {
  'NOAA-21 · VIIRS': '#ff755e',
  'NOAA-20 · VIIRS': '#ffd166',
  'Suomi-NPP · VIIRS': '#8ed081',
  'Terra · MODIS': '#68b9ff',
  'Aqua · MODIS': '#c18cff',
  'Terra/Aqua · MODIS': '#c18cff',
  'Demo satellite': '#ff9d45'
};
const CLASSIFICATION_COLORS = {
  'Persistent industrial heat / gas flare': '#3d9365',
  'Suspected industrial incident': '#e98a4d',
  'Vegetation fire': '#e5b83f',
  'Agricultural or open burn': '#a77b47',
  'Volcanic or geothermal source': '#3288bd',
  'Unknown / needs review': '#77818c'
};

const keyInput = document.getElementById('firmsKey');
const keyStatus = document.getElementById('keyStatus');
const periodSelect = document.getElementById('dataPeriod');
const sourceSelect = document.getElementById('firmsSource');
const historicalStart = document.getElementById('historicalStart');

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[character]);
}

function setStatus(text, ok = false) {
  document.getElementById('statusText').textContent = text;
  document.getElementById('statusDot').style.background = ok ? '#54d18a' : '#ffc857';
}

function initMap() {
  map = L.map('map', { preferCanvas: true }).fitBounds(INDIA_BOUNDS);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© OpenStreetMap contributors'
  }).addTo(map);
  markersLayer = L.layerGroup().addTo(map);
  facilityLayer = L.layerGroup().addTo(map);
  pathLayer = L.layerGroup().addTo(map);
}

function markerColor(event) {
  return CLASSIFICATION_COLORS[event.classification] || CLASSIFICATION_COLORS['Unknown / needs review'];
}

function satelliteColor(name) {
  return SATELLITE_COLORS[name] || '#9aa8b6';
}

function num(value, digits = 1) {
  return value === null || value === undefined || Number.isNaN(Number(value))
    ? '—' : Number(value).toFixed(digits);
}

function incidentKey(event, index = 0) {
  return event.incident_id || `detection-${index}`;
}

function incidentGroups(events) {
  const groups = new Map();
  events.forEach((event, index) => {
    const key = incidentKey(event, index);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(event);
  });
  return [...groups.entries()].map(([key, records]) => {
    records.sort((left, right) => String(left.datetime_utc || '').localeCompare(String(right.datetime_utc || '')));
    const latest = records[records.length - 1];
    const satellites = [...new Set(records.flatMap(record => [
      ...(Array.isArray(record.incident_satellites) ? record.incident_satellites : []),
      record.satellite_name
    ]).filter(Boolean))].sort();
    const firstSeen = records.map(record => record.incident_first_seen || record.datetime_utc).filter(Boolean).sort()[0] || null;
    const lastSeen = records.map(record => record.incident_last_seen || record.datetime_utc).filter(Boolean).sort().at(-1) || null;
    const peakFrpValues = records.flatMap(record => [record.frp, record.incident_peak_frp]
      .filter(value => value !== null && value !== undefined).map(Number)).filter(Number.isFinite);
    return {
      ...latest,
      incident_id: key,
      incident_detections: Math.max(records.length, ...records.map(record => Number(record.incident_detections) || 0)),
      incident_peak_frp: peakFrpValues.length ? Math.max(...peakFrpValues) : null,
      incident_satellites: satellites,
      incident_first_seen: firstSeen,
      incident_last_seen: lastSeen,
      persistence_days: Math.max(...records.map(record => Number(record.persistence_days) || 1)),
      memberEvents: records
    };
  });
}

function popup(event) {
  const satellites = Array.isArray(event.incident_satellites) && event.incident_satellites.length
    ? event.incident_satellites.join(', ') : event.satellite_name || 'Unknown';
  const siteLabel = queryInfo.mode === 'DEMO' ? 'Nearest demo reference point' : 'Nearest mapped site';
  return `<div style="min-width:220px"><b>${escapeHtml(event.classification || 'Unknown / needs review')}</b><br>
    Hotspot group: ${escapeHtml(event.incident_id || '—')}<br>
    Detections: ${Number(event.incident_detections || 1).toLocaleString()}<br>
    Satellites: ${escapeHtml(satellites)}<br>
    Latest time (UTC): ${escapeHtml(event.datetime_utc || '—')}<br>
    Peak FRP: ${num(event.incident_peak_frp ?? event.frp)} MW<br>${siteLabel}: ${escapeHtml(event.nearest_name || (queryInfo.osm_mode === 'SITE_CONTEXT_UNAVAILABLE' ? 'OSM site layer unavailable' : 'None within 100 km'))}<br>
    Distance: ${num(event.distance_km, 2)} km<br>
    Inside buffer: ${event.inside_industrial_buffer ? 'YES' : 'NO'}</div>`;
}

function render() {
  const onlyIndustrial = document.getElementById('onlyIndustrial').checked;
  const minimumRisk = Number(document.getElementById('minRisk').value);
  const showMovement = document.getElementById('showMovement').checked;
  const filteredEvents = data.events.filter(event =>
    (!onlyIndustrial || event.inside_industrial_buffer) && Number(event.risk_score || 0) >= minimumRisk
  );
  const filtered = incidentGroups(filteredEvents);

  markersLayer.clearLayers();
  facilityLayer.clearLayers();
  pathLayer.clearLayers();
  markersByIncident = new Map();
  if (selectionMarker) {
    markersLayer.removeLayer(selectionMarker);
    selectionMarker = null;
  }

  data.facilities.slice(0, 5000).forEach(facility => {
    L.circleMarker([facility.lat, facility.lon], {
      radius: 4, color: '#b887ff', fillColor: '#b887ff', fillOpacity: .8, weight: 1
    }).bindPopup(`<b>${escapeHtml(facility.name || 'Unnamed feature')}</b><br>
      ${queryInfo.mode === 'DEMO' ? 'Illustrative demo context' : 'OSM type'}: ${escapeHtml(facility.kind || 'industrial')}`).addTo(facilityLayer);
  });

  const mapEvents = filtered.slice(0, MAP_EVENT_LIMIT);
  mapEvents.forEach(event => {
    const color = markerColor(event);
    const marker = L.circleMarker([event.latitude, event.longitude], {
      radius: Math.min(9, 4 + Math.log2(Number(event.incident_detections || 1))),
      color, fillColor: color, fillOpacity: .88, weight: 1
    }).bindPopup(popup(event)).on('click', () => showDetail(event)).addTo(markersLayer);
    markersByIncident.set(event.incident_id, marker);
  });

  if (showMovement) {
    data.paths.forEach(path => {
      if (!path.coordinates || path.coordinates.length < 2) return;
      L.polyline(path.coordinates, { color: '#ff7a45', weight: 3, opacity: .75 })
        .bindTooltip(`${escapeHtml(path.group)}: ${num(path.distance_km, 2)} km · ${num(path.speed_kmh, 2)} km/h`)
        .addTo(pathLayer);
    });
  }

  document.getElementById('countDetections').textContent = filtered.length.toLocaleString();
  document.getElementById('countRawDetections').textContent = filteredEvents.length.toLocaleString();
  document.getElementById('countOsm').textContent = data.facilities.length.toLocaleString();
  document.getElementById('countOsmLabel').textContent = queryInfo.mode === 'DEMO'
    ? 'Illustrative demo points'
    : queryInfo.osm_mode === 'SITE_CONTEXT_UNAVAILABLE' ? 'OSM sites unavailable'
      : queryInfo.osm_mode === 'CACHED' ? 'Cached OSM features' : 'OSM industrial features';
  document.getElementById('facilityLegendLabel').textContent = queryInfo.mode === 'DEMO'
    ? 'Illustrative demo reference point'
    : queryInfo.osm_mode === 'SITE_CONTEXT_UNAVAILABLE' ? 'OSM sites unavailable' : 'OSM industrial / power site';
  document.getElementById('countBuffer').textContent = filtered.filter(event => event.inside_industrial_buffer).length.toLocaleString();
  document.getElementById('countHigh').textContent = filtered.filter(event => Number(event.risk_score) >= 72).length.toLocaleString();
  document.getElementById('countPaths').textContent = showMovement ? data.paths.length.toLocaleString() : '0';
  const mapNote = document.getElementById('mapPointNote');
  const excluded = Number(queryInfo.excluded_outside_india || 0);
  const osmLoaded = Number(queryInfo.osm_feature_count ?? data.facilities.length);
  const history = queryInfo.history_context || {};
  const baselineStatus = document.getElementById('baselineStatus');
  baselineStatus.textContent = queryInfo.mode === 'DEMO'
    ? 'Demo does not create historical baselines. Load live or historical FIRMS data to build a local baseline.'
    : history.available === false
      ? 'The local history database could not be read or updated; historical anomaly comparisons are unavailable.'
      : `Local history: ${Number(history.stored_observations || 0).toLocaleString()} observations across ${Number(history.stored_days || 0).toLocaleString()} dates. Baselines compare prior same-satellite detections within 1.5 km, same day/night, and within 2 hours of the overpass; at least 3 prior dates are needed. FIRMS contains hotspot detections, not confirmed no-fire days.`;
  const osmStatus = queryInfo.mode === 'DEMO' ? ''
    : queryInfo.osm_mode === 'SITE_CONTEXT_UNAVAILABLE' ? ' OSM industrial-site distances and site-based classifications are unavailable.'
      : queryInfo.osm_mode === 'CACHED'
        ? ` Overpass is timing out; using ${osmLoaded.toLocaleString()} previously cached OpenStreetMap sites${queryInfo.osm_cache_updated_at ? ` (cache updated ${escapeHtml(String(queryInfo.osm_cache_updated_at).slice(0, 10))})` : ''}. This is partial coverage.`
        : ` OpenStreetMap loaded ${osmLoaded.toLocaleString()} mapped industrial/power sites${queryInfo.osm_endpoint ? ` via ${escapeHtml(queryInfo.osm_endpoint)}` : ''}; nearest-site search covers 100 km.`
          + (queryInfo.osm_mode === 'PARTIAL' ? ' Some nearby area queries timed out; cached and returned sites are shown.' : '')
          + (osmLoaded === 0 ? ' No site-based classification can be made until mapped sites are returned.' : '');
  const osmDiagnostic = queryInfo.osm_mode === 'SITE_CONTEXT_UNAVAILABLE' && queryInfo.osm_error
    ? `<details class="detail-note"><summary>OpenStreetMap service error details</summary><small>${escapeHtml(queryInfo.osm_error)}</small></details>`
    : '';
  mapNote.innerHTML = `Showing ${mapEvents.length.toLocaleString()} of ${filtered.length.toLocaleString()} hotspot groups. Groups combine nearby detections; each list entry shows the satellites and dates observed.${queryInfo.mode === 'DEMO' ? ' Demo only: these are synthetic examples, not NASA FIRMS observations. Enter a MAP_KEY and click Load live data for satellite detections.' : ''}${excluded ? ` ${excluded.toLocaleString()} detections outside India's boundary were excluded.` : ''}${queryInfo.boundary_source === 'NATURAL_EARTH_OFFLINE' ? ' Using the bundled Natural Earth India boundary because the live OSM boundary service is unavailable.' : ''}${osmStatus}${osmDiagnostic}`;

  const body = document.getElementById('eventsBody');
  const tableRows = filtered.slice(0, TABLE_ROW_LIMIT);
  body.innerHTML = tableRows.map((event, index) => {
    const classification = String(event.classification || '');
    const badgeClass = classification === 'Persistent industrial heat / gas flare' ? 'class-persistent'
      : classification === 'Suspected industrial incident' ? 'class-industrial'
        : classification === 'Vegetation fire' ? 'class-vegetation'
          : classification === 'Agricultural or open burn' ? 'class-openburn'
            : classification === 'Volcanic or geothermal source' ? 'class-volcanic' : 'class-unknown';
    const first = event.incident_first_seen ? new Date(event.incident_first_seen).toLocaleString() : '—';
    const last = event.incident_last_seen ? new Date(event.incident_last_seen).toLocaleString() : '—';
    const satellites = event.incident_satellites.join(', ') || event.satellite_name || 'Unknown';
    return `<tr data-event-index="${index}" tabindex="0" aria-label="Open hotspot group ${escapeHtml(event.incident_id || '')}">
      <td><b>${escapeHtml(event.incident_id || '—')}</b><small class="subcell">${escapeHtml(first)} – ${escapeHtml(last)}</small></td>
      <td>${escapeHtml(satellites)}<small class="subcell">${Number(event.incident_detections || 1).toLocaleString()} detections · ${Number(event.persistence_days || 1)} day(s)</small></td>
      <td>${num(event.incident_peak_frp ?? event.frp)} MW</td>
      <td>${escapeHtml(event.nearest_name || (queryInfo.osm_mode === 'SITE_CONTEXT_UNAVAILABLE' ? 'OSM unavailable' : '—'))}<small class="subcell">${escapeHtml(event.nearest_kind || (queryInfo.mode === 'DEMO' ? 'Illustrative demo context' : 'Mapped industrial/site feature'))}</small></td>
      <td>${queryInfo.osm_mode === 'SITE_CONTEXT_UNAVAILABLE' ? 'OSM context unavailable' : event.distance_km !== null && event.distance_km !== undefined && Number.isFinite(Number(event.distance_km)) ? `${num(event.distance_km, 2)} km` : 'No mapped site within 100 km'}</td>
      <td>${event.inside_industrial_buffer ? 'YES' : 'NO'}</td>
      <td>${num(event.risk_score, 1)}<small class="subcell">History anomaly ${Number(event.historical_sample_days || 0) >= 3 ? num(event.historical_anomaly_score, 2) : 'building baseline'}</small></td>
      <td><span class="badge ${badgeClass}">${escapeHtml(classification)}</span></td>
    </tr>`;
  }).join('');
  body.querySelectorAll('tr[data-event-index]').forEach(row => {
    const selectRow = () => selectEvent(tableRows[Number(row.dataset.eventIndex)], true);
    row.addEventListener('click', selectRow);
    row.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        selectRow();
      }
    });
  });
  document.getElementById('tableSummary').textContent = filtered.length > TABLE_ROW_LIMIT
    ? `Showing newest ${TABLE_ROW_LIMIT.toLocaleString()} of ${filtered.length.toLocaleString()} groups. Click a row to zoom; CSV export includes every matching detection.`
    : `${filtered.length.toLocaleString()} hotspot groups · click a row to zoom · CSV export includes every matching detection.`;

  renderHistory();
}

function selectEvent(event, zoomToEvent = false) {
  if (!event) return;
  showDetail(event);
  if (!zoomToEvent || !map) return;
  const point = [Number(event.latitude), Number(event.longitude)];
  if (!point.every(Number.isFinite)) return;
  map.flyTo(point, Math.max(map.getZoom(), 12), { duration: .8 });
  let marker = markersByIncident.get(event.incident_id);
  if (!marker) {
    const color = markerColor(event);
    selectionMarker = L.circleMarker(point, {
      radius: 9, color: '#ffffff', fillColor: color, fillOpacity: 1, weight: 3
    }).bindPopup(popup(event)).addTo(markersLayer);
    marker = selectionMarker;
    markersByIncident.set(event.incident_id, marker);
  }
  map.once('moveend', () => marker.openPopup());
}

function showDetail(event) {
  if (!event) return;
  const members = event.incident_id
    ? data.events.filter(record => record.incident_id === event.incident_id)
    : (event.memberEvents || [event]);
  const satelliteNames = event.incident_satellites || [...new Set(members.map(record => record.satellite_name).filter(Boolean))];
  const observations = [...members].sort((left, right) => String(right.datetime_utc || '').localeCompare(String(left.datetime_utc || '')));
  const nearby = Array.isArray(event.nearby_features) ? event.nearby_features : [];
  const nearbyHtml = nearby.length
    ? `<ul class="nearby-list">${nearby.map(site => `<li><b>${escapeHtml(site.name || 'Mapped site')}</b> · ${escapeHtml(site.kind || 'industrial')} · ${num(site.distance_km, 2)} km</li>`).join('')}</ul>`
    : queryInfo.osm_mode === 'SITE_CONTEXT_UNAVAILABLE'
      ? `<p class="detail-note">${queryInfo.boundary_source === 'NATURAL_EARTH_OFFLINE' ? 'A bundled India boundary was used, but' : 'The India boundary was available, but'} the OpenStreetMap site query failed. Reload live data to retry distances.</p>`
      : queryInfo.mode === 'DEMO'
        ? '<p class="detail-note">Demo reference points are illustrative examples, not a complete industrial-site map.</p>'
        : '<p class="detail-note">No mapped industrial or power site within 50 km. Nearest-site search covers 100 km.</p>';
  const observationHtml = observations.slice(0, 12).map(record => {
    const time = record.datetime_utc ? new Date(record.datetime_utc).toLocaleString() : 'Date unavailable';
    return `<li><b>${escapeHtml(record.satellite_name || 'Unknown satellite')}</b> · ${escapeHtml(time)} · FRP ${num(record.frp)} MW · ${escapeHtml(record.daynight || 'D/N unknown')} · confidence ${escapeHtml(record.confidence || '—')} · FIRMS type ${record.fire_type === null || record.fire_type === undefined ? '—' : escapeHtml(record.fire_type)} · ${escapeHtml(record.source_id || 'feed unknown')}</li>`;
  }).join('');
  const start = event.incident_first_seen ? new Date(event.incident_first_seen).toLocaleString() : '—';
  const end = event.incident_last_seen ? new Date(event.incident_last_seen).toLocaleString() : '—';
  const historyDays = Number(event.historical_sample_days || 0);
  const baselineDetails = historyDays >= 3 && event.historical_baseline_frp !== null && event.historical_baseline_frp !== undefined
    ? `${historyDays} earlier dates · FRP median ${num(event.historical_baseline_frp)} MW · current FRP change ${num(event.historical_frp_delta, 1)} MW (${num(event.historical_frp_change_pct, 0)}%) · brightness-difference change ${num(event.historical_temp_delta, 1)} K · anomaly score ${num(event.historical_anomaly_score, 2)}/1.00.`
    : `${historyDays} earlier active dates match this satellite and overpass. At least 3 are needed for a baseline. Load historical date ranges to seed local history.`;
  document.getElementById('detail').innerHTML = `<div class="detail-grid">
    <div class="detail-item"><small>Hotspot group</small><b>${escapeHtml(event.incident_id || '—')}</b></div>
    <div class="detail-item"><small>Detection records / satellites</small><b>${Number(event.incident_detections || members.length || 1).toLocaleString()} · ${escapeHtml(satelliteNames.join(', ') || 'Unknown')}</b></div>
    <div class="detail-item"><small>FIRMS feed</small><b>${escapeHtml(event.source_id || '—')}</b></div>
    <div class="detail-item"><small>Classification</small><b>${escapeHtml(event.classification)}</b></div>
    <div class="detail-item"><small>Risk score</small><b>${num(event.risk_score, 1)}/100</b></div>
    <div class="detail-item"><small>Historical anomaly score</small><b>${historyDays >= 3 ? `${num(event.historical_anomaly_score, 2)}/1.00` : 'Building baseline'}</b></div>
    <div class="detail-item"><small>Latest FRP / FIRMS type</small><b>${num(event.frp, 1)} MW · ${event.fire_type === null || event.fire_type === undefined ? 'unavailable' : escapeHtml(event.fire_type)}</b></div>
    <div class="detail-item"><small>Brightness / day-night</small><b>${num(event.bright_ti4, 1)} K · ${num(event.bright_ti5, 1)} K · ${escapeHtml(event.daynight || '—')}</b></div>
    <div class="detail-item"><small>Confidence</small><b>${escapeHtml(event.confidence || '—')}</b></div>
    <div class="detail-item"><small>${queryInfo.mode === 'DEMO' ? 'Distance to illustrative demo point' : 'Distance to nearest mapped site'}</small><b>${queryInfo.osm_mode === 'SITE_CONTEXT_UNAVAILABLE' ? 'OSM context unavailable' : Number.isFinite(Number(event.distance_km)) && event.distance_km !== null ? `${num(event.distance_km, 2)} km` : 'No site within 100 km'}</b></div>
    <div class="detail-item"><small>Industrial buffer</small><b>${event.inside_industrial_buffer ? 'Inside' : 'Outside'}</b></div>
    <div class="detail-item"><small>${queryInfo.mode === 'DEMO' ? 'Illustrative reference point' : 'Nearest mapped feature'}</small><b>${escapeHtml(event.nearest_name || (queryInfo.osm_mode === 'SITE_CONTEXT_UNAVAILABLE' ? 'OSM site layer unavailable' : '—'))} · ${escapeHtml(event.nearest_kind || '—')}</b></div>
    <div class="detail-item"><small>Latitude / Longitude</small><b>${num(event.latitude, 5)}, ${num(event.longitude, 5)}</b></div>
    <div class="detail-item"><small>Observed span / active days</small><b>${escapeHtml(start)} – ${escapeHtml(end)} · ${Number(event.persistence_days || 1)} day(s)</b></div>
  </div>
  <div class="detail-section"><b>Why it received this label</b><p>${escapeHtml(event.classification_basis || 'Rule-based candidate label; review the source measurements.')}</p></div>
  <div class="detail-section"><b>Historical heat baseline</b><p>${escapeHtml(baselineDetails)}</p><small class="detail-note">Comparison uses prior hotspot detections from the same satellite near this location, at a similar overpass time and day/night. It does not measure a factory’s complete operating temperature or treat missing detections as zero activity.</small></div>
  <div class="detail-section"><b>${queryInfo.mode === 'DEMO' ? 'Illustrative demo context' : 'Nearby mapped sites · straight-line distance'}</b>${nearbyHtml}</div>
  <div class="detail-section"><b>Satellite observations (${observations.length.toLocaleString()})</b><p class="detail-note">FIRMS reports the approximate center of a satellite pixel; the hotspot point is not a surveyed fire perimeter.</p><ul class="observation-list">${observationHtml || '<li>No observation details available.</li>'}</ul>${observations.length > 12 ? `<small class="detail-note">Showing 12 of ${observations.length.toLocaleString()} observations. CSV export contains every detection.</small>` : ''}</div>`;
}

function updateKeyStatus() {
  const hasKey = Boolean(firmsSessionKey);
  keyStatus.textContent = hasKey ? 'Session key saved' : 'Not set';
  keyInput.value = '';
}

function setHistoricalDefault() {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() - 180);
  historicalStart.value = date.toISOString().slice(0, 10);
  historicalStart.max = new Date().toISOString().slice(0, 10);
}

function updatePeriodControls() {
  const historical = periodSelect.value === 'historical';
  document.getElementById('historicalDateGroup').classList.toggle('hidden', !historical);
  document.getElementById('historicalNote').classList.toggle('hidden', !historical);
  sourceSelect.innerHTML = '';
  const options = historical
    ? [
        ['ALL', 'All historical feeds · MODIS, NOAA-20, Suomi-NPP'],
        ['MODIS_SP', 'MODIS historical archive · Terra/Aqua'],
        ['VIIRS_NOAA20_SP', 'VIIRS historical archive · NOAA-20'],
        ['VIIRS_SNPP_SP', 'VIIRS historical archive · Suomi-NPP'],
      ]
    : [
        ['ALL', 'All recent feeds · all satellites'],
        ['VIIRS_NOAA21_NRT', 'VIIRS · NOAA-21'],
        ['VIIRS_NOAA20_NRT', 'VIIRS · NOAA-20'],
        ['VIIRS_SNPP_NRT', 'VIIRS · Suomi-NPP'],
        ['MODIS_NRT', 'MODIS · Terra/Aqua'],
      ];
  options.forEach(([value, label]) => {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    sourceSelect.append(option);
  });
  if (historical && !historicalStart.value) setHistoricalDefault();
}

document.getElementById('saveKey').addEventListener('click', () => {
  const value = keyInput.value.trim();
  if (!value) {
    alert('Paste your NASA FIRMS MAP_KEY first.');
    keyInput.focus();
    return;
  }
  firmsSessionKey = value;
  sessionStorage.setItem('firms_map_key', value);
  updateKeyStatus();
});

document.getElementById('clearKey').addEventListener('click', () => {
  sessionStorage.removeItem('firms_map_key');
  firmsSessionKey = '';
  updateKeyStatus();
});

document.getElementById('keyFile').addEventListener('change', async event => {
  const file = event.target.files && event.target.files[0];
  event.target.value = '';
  if (!file) return;
  if (file.size > 4096) {
    alert('Choose a small .txt file containing only the NASA FIRMS MAP_KEY.');
    return;
  }
  try {
    const value = (await file.text()).trim();
    if (!value || /\s/.test(value)) {
      alert('The selected file should contain only the NASA FIRMS MAP_KEY.');
      return;
    }
    keyInput.value = value;
    keyInput.focus();
  } catch {
    alert('Could not read that key file. Paste the MAP_KEY into the field instead.');
  }
});

function requestPeriod() {
  const days = Math.max(1, Math.min(30, parseInt(document.getElementById('days').value || '7', 10)));
  document.getElementById('days').value = days;
  return {
    period: periodSelect.value,
    source: sourceSelect.value,
    days,
    start_date: periodSelect.value === 'historical' ? historicalStart.value : '',
  };
}

async function loadData(mode) {
  try {
    setStatus('Loading…');
    const query = requestPeriod();
    const buffer = Math.max(1, Math.min(15, parseFloat(document.getElementById('bufferKm').value || '5')));
    document.getElementById('bufferKm').value = buffer;
    let response;
    if (mode === 'live') {
      const enteredKey = keyInput.value.trim();
      if (enteredKey) {
        firmsSessionKey = enteredKey;
        sessionStorage.setItem('firms_map_key', enteredKey);
        updateKeyStatus();
      }
      response = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...query, map_key: firmsSessionKey, buffer_km: buffer })
      });
    } else {
      response = await fetch(`/api/demo?buffer_km=${encodeURIComponent(buffer)}`);
    }

    const text = await response.text();
    let result;
    try {
      result = JSON.parse(text);
    } catch {
      throw new Error(`Server returned ${response.status} without valid JSON.`);
    }
    if (!response.ok) throw new Error(result.detail || result.error || `Request failed (${response.status})`);
    data = {
      events: Array.isArray(result.events) ? result.events : [],
      facilities: Array.isArray(result.facilities) ? result.facilities : [],
      paths: Array.isArray(result.paths) ? result.paths : [],
    };
    queryInfo = result.mode === 'DEMO' ? { period: 'demo', days: 7, ...result } : { ...query, ...result };
    render();
    fitMapToData();
    renderFeedStatus(result.source_status || []);

    const feedStatus = result.source_status || [];
    const succeeded = feedStatus.filter(feed => feed.ok).length;
    if (result.mode === 'DEMO') {
      setStatus('Demo only · synthetic data');
    } else if (feedStatus.length && succeeded === 0) {
      setStatus('No satellite feeds loaded');
      alert(`No NASA satellite feeds could be loaded.\n\n${feedStatus.map(feed => `${feed.label}: ${feed.error}`).join('\n')}`);
    } else if (feedStatus.length && succeeded < feedStatus.length) {
      setStatus(`Partial feed · ${succeeded}/${feedStatus.length} loaded`, true);
      alert(`Some satellite feeds were unavailable. Loaded ${succeeded} of ${feedStatus.length}. Check the feed status below the history chart.`);
    } else if (mode === 'live' || result.mode === 'LIVE') {
      const dataLabel = query.period === 'historical' ? 'Historical NASA FIRMS data' : 'Live NASA FIRMS data';
      const osmUnavailable = result.osm_mode === 'SITE_CONTEXT_UNAVAILABLE';
      const osmPartial = result.osm_mode === 'PARTIAL';
      setStatus(
        `${dataLabel}${osmUnavailable ? ' · OSM unavailable' : osmPartial ? ' · OSM partial' : ''}`,
        !osmUnavailable && !osmPartial,
      );
    } else {
      setStatus('Demo only · synthetic data');
    }
  } catch (error) {
    console.error(error);
    alert(error instanceof Error ? error.message : String(error));
    setStatus('Error');
  }
}

function fitMapToData() {
  if (!map || !data.events.length) return;
  const bounds = data.events.reduce((value, event) => {
    const lat = Number(event.latitude);
    const lon = Number(event.longitude);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return value;
    value.minLat = Math.min(value.minLat, lat);
    value.minLon = Math.min(value.minLon, lon);
    value.maxLat = Math.max(value.maxLat, lat);
    value.maxLon = Math.max(value.maxLon, lon);
    return value;
  }, { minLat: 90, minLon: 180, maxLat: -90, maxLon: -180 });
  if (bounds.minLat <= bounds.maxLat) {
    map.fitBounds([[bounds.minLat, bounds.minLon], [bounds.maxLat, bounds.maxLon]], { padding: [20, 20], maxZoom: 7 });
  }
}

function renderFeedStatus(feeds) {
  const container = document.getElementById('feedStatus');
  if (!feeds.length) {
    container.innerHTML = queryInfo.mode === 'DEMO' ? '<span class="feed-chip">Demo only · synthetic sample points, not NASA FIRMS detections. Save your MAP_KEY and click Load live data to fetch satellite observations.</span>' : '';
    return;
  }
  container.innerHTML = feeds.map(feed =>
    `<span class="feed-chip ${feed.ok ? '' : 'failed'}">${escapeHtml(feed.label)}: ${feed.ok ? `${Number(feed.count).toLocaleString()} detections` : escapeHtml(feed.error || 'unavailable')}</span>`
  ).join('');
}

function renderHistory() {
  const chart = document.getElementById('historyChart');
  const events = data.events;
  const requestedDays = Math.max(1, Math.min(30, Number(queryInfo.days) || 7));
  const endingDate = queryInfo.end_date || new Date().toISOString().slice(0, 10);
  const startDate = queryInfo.start_date || new Date(Date.parse(`${endingDate}T00:00:00Z`) - (requestedDays - 1) * 86400000).toISOString().slice(0, 10);
  const dates = [];
  for (let offset = 0; offset < requestedDays; offset++) {
    dates.push(new Date(Date.parse(`${startDate}T00:00:00Z`) + offset * 86400000).toISOString().slice(0, 10));
  }

  const platformNames = [...new Set(events.map(event => event.satellite_name || 'Unknown satellite'))].sort();
  const counts = Object.fromEntries(dates.map(date => [date, Object.fromEntries(platformNames.map(name => [name, 0]))]));
  events.forEach(event => {
    if (!event.datetime_utc) return;
    const date = String(event.datetime_utc).slice(0, 10);
    const name = event.satellite_name || 'Unknown satellite';
    if (counts[date] && Object.hasOwn(counts[date], name)) counts[date][name]++;
  });
  const dailyTotals = dates.map(date => Object.values(counts[date]).reduce((sum, value) => sum + value, 0));
  const total = dailyTotals.reduce((sum, value) => sum + value, 0);
  const dailyAverage = (total / requestedDays).toFixed(1);
  const activeDays = dailyTotals.filter(value => value > 0).length;
  const peakCount = Math.max(0, ...dailyTotals);
  const peakDate = peakCount ? dates[dailyTotals.indexOf(peakCount)] : '';
  const periodLabel = queryInfo.period === 'historical' ? 'standard archive' : queryInfo.period === 'demo' ? 'demo' : 'recent NRT';
  document.getElementById('historySummary').textContent = total
    ? `${total.toLocaleString()} detections · avg ${dailyAverage}/day · ${activeDays}/${dates.length} active days · peak ${peakCount.toLocaleString()} on ${peakDate} · ${startDate} to ${endingDate}`
    : `No detections in this ${periodLabel} window (${startDate} to ${endingDate}).`;

  if (!total) {
    chart.innerHTML = '<div class="empty">No detections are available for this period. The selected dates and feed status are shown above.</div>';
  } else {
    const width = 960;
    const height = 230;
    const left = 48;
    const right = 16;
    const top = 12;
    const bottom = 38;
    const plotHeight = height - top - bottom;
    const plotWidth = width - left - right;
    const slot = plotWidth / dates.length;
    const barWidth = Math.max(2, Math.min(24, slot * .62));
    const maxTotal = Math.max(peakCount, 1);
    let svg = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Daily detections by satellite from ${escapeHtml(startDate)} to ${escapeHtml(endingDate)}">`;
    for (let step = 0; step <= 3; step++) {
      const value = Math.round(maxTotal * step / 3);
      const y = top + plotHeight - plotHeight * step / 3;
      svg += `<line class="grid-line" x1="${left}" y1="${y}" x2="${width - right}" y2="${y}"></line>`;
      svg += `<text x="${left - 8}" y="${y + 3}" text-anchor="end">${value}</text>`;
    }
    dates.forEach((date, index) => {
      let stackHeight = 0;
      const x = left + index * slot + (slot - barWidth) / 2;
      platformNames.forEach(name => {
        const value = counts[date][name] || 0;
        const barHeight = value / maxTotal * plotHeight;
        if (barHeight > 0) {
          const y = top + plotHeight - stackHeight - barHeight;
          svg += `<rect x="${x}" y="${y}" width="${barWidth}" height="${Math.max(barHeight, 1)}" fill="${satelliteColor(name)}"><title>${escapeHtml(date)} · ${escapeHtml(name)}: ${value}</title></rect>`;
          stackHeight += barHeight;
        }
      });
      const labelEvery = Math.max(1, Math.ceil(dates.length / 10));
      if (index % labelEvery === 0 || index === dates.length - 1) {
        const label = date.slice(5);
        svg += `<text x="${x + barWidth / 2}" y="${height - 12}" text-anchor="middle">${escapeHtml(label)}</text>`;
      }
    });
    svg += '</svg>';
    chart.innerHTML = svg;
  }

  document.getElementById('historyLegend').innerHTML = platformNames.map(name =>
    `<span><i style="background:${satelliteColor(name)}"></i>${escapeHtml(name)}</span>`
  ).join('');
}

document.getElementById('dataPeriod').addEventListener('change', updatePeriodControls);
document.getElementById('loadDemo').addEventListener('click', () => loadData('demo'));
document.getElementById('loadLive').addEventListener('click', () => loadData('live'));
document.getElementById('onlyIndustrial').addEventListener('change', render);
document.getElementById('showMovement').addEventListener('change', render);
document.getElementById('minRisk').addEventListener('input', event => {
  document.getElementById('riskValue').textContent = event.target.value;
  render();
});

function csvCell(value) {
  let text = value === null || value === undefined ? '' : String(value);
  if (/^\s*[=+\-@]/.test(text)) text = `'${text}`;
  return `"${text.replace(/"/g, '""')}"`;
}

document.getElementById('exportBtn').addEventListener('click', () => {
  const onlyIndustrial = document.getElementById('onlyIndustrial').checked;
  const minimumRisk = Number(document.getElementById('minRisk').value);
  const rows = data.events.filter(event =>
    (!onlyIndustrial || event.inside_industrial_buffer) && Number(event.risk_score || 0) >= minimumRisk
  );
  const columns = [
    'incident_id', 'incident_detections', 'incident_peak_frp', 'incident_satellites', 'incident_first_seen', 'incident_last_seen',
    'latitude', 'longitude', 'datetime_utc', 'satellite_name', 'source_id', 'sensor_name',
    'satellite', 'instrument', 'confidence', 'daynight', 'fire_type', 'frp', 'bright_ti4', 'bright_ti5', 'persistence_days',
    'nearest_name', 'nearest_kind', 'distance_km', 'nearby_features', 'inside_industrial_buffer',
    'historical_sample_count', 'historical_sample_days', 'historical_baseline_frp', 'historical_baseline_temp',
    'historical_frp_delta', 'historical_frp_change_pct', 'historical_temp_delta', 'historical_anomaly_score',
    'classification', 'classification_basis', 'risk_score'
  ];
  const csv = ['\ufeff' + columns.map(csvCell).join(','), ...rows.map(row => columns.map(column => {
    const value = Array.isArray(row[column]) || (row[column] && typeof row[column] === 'object')
      ? JSON.stringify(row[column]) : row[column];
    return csvCell(value);
  }).join(','))].join('\r\n');
  const objectUrl = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = 'india_thermal_analysis.csv';
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
});

initMap();
updateKeyStatus();
setHistoricalDefault();
updatePeriodControls();
loadData('demo');
