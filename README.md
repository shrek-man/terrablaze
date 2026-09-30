# ThermalTrace — India-wide satellite fire monitoring

## Start the app

1. Extract the ZIP.
2. Double-click `RUN_WEBSITE.bat` and leave its terminal open.
3. Wait for the browser to open at `http://127.0.0.1:8000`.
4. The app starts with clearly marked synthetic India-wide demo points. For NASA data, get a free [NASA FIRMS MAP_KEY](https://firms.modaps.eosdis.nasa.gov/api/map_key/), paste it into the key field or choose a `.txt` file containing only the key, then select **Load live data**.

The key is sent by POST to this app running on your computer and kept in the current browser tab's `sessionStorage` until you choose **Clear saved key** or close the tab. It is not stored in the project files. To use a key from `.env`, copy `.env.example` to `.env` and replace the placeholder; `.env` is ignored by Git.

## Satellite and history controls

- **Recent · NRT** + **All available satellite feeds** loads the NOAA-21, NOAA-20, Suomi-NPP VIIRS and Terra/Aqua MODIS feeds. Choose one feed to narrow the query.
- **Historical · standard archive** lets you select a UTC start date and a 1–30 day window. The all-satellite archive currently includes MODIS, NOAA-20 VIIRS and Suomi-NPP VIIRS. NASA's published archive list does not currently include a NOAA-21 standard-processing feed.
- The historical context chart shows daily counts by satellite for the requested period. Nearby detections within 1.5 km are grouped into a hotspot history; clicking its table row zooms the map and lists each satellite observation, date, FRP, confidence, and FIRMS type. CSV export retains every individual detection.
- Live and historical detections are saved in a local SQLite database ignored by Git. ThermalTrace compares a hotspot with prior detections from the same satellite within 1.5 km, at the same day/night period and within two hours of the overpass. A baseline needs at least three earlier active dates; it reports FRP and brightness-difference changes and contributes to risk scoring. This is a baseline of FIRMS detections only: FIRMS does not provide complete factory operating temperatures or confirm that a day with no detection had zero heat.
- NASA data availability varies by feed and date. Some archive date ranges can return partial results when one satellite's product is not available for that period.

NASA FIRMS' area endpoint accepts rectangles, so returned points are clipped to an India boundary before they are analyzed or shown. The app uses the live OpenStreetMap boundary when available. If Overpass is unreachable, it falls back to the bundled Natural Earth 1:110m country outline in `data/india_boundary.geojson`, and the map labels that fallback. The fallback is generalized and may omit small islands or fine boundary detail; reconnect to Overpass for the more detailed country outline. Recent and historical requests are split into contiguous windows because each FIRMS area request is limited to five days.

## Stack and analysis

NASA FIRMS · OpenStreetMap/Overpass API · GeoPandas · Shapely · scikit-learn · FastAPI · Leaflet

Each detection is assigned one of these candidate labels: persistent industrial heat / gas flare, suspected industrial incident, vegetation fire, agricultural or open burn, volcanic or geothermal source, or unknown / needs review. NASA's [VIIRS](https://firms.modaps.eosdis.nasa.gov/content/descriptions/FIRMS_VIIRS_Firehotspots.html) and [MODIS](https://firms.modaps.eosdis.nasa.gov/content/descriptions/FIRMS_MODIS_Firehotspots.html) FIRMS type codes identify presumed vegetation fires (0), active volcanoes (1), other static land sources (2), and offshore detections (3), where the chosen standard product provides the type field. Agricultural/open-burn and industrial labels use FRP, day/night, persistence, and nearby mapped sites as heuristics. The app shows the evidence used for each label; none is a confirmed cause.

The app adds OpenStreetMap industrial/power context, exact WGS84 straight-line distance to the nearest mapped site, up to five nearby mapped features within 50 km, and a configurable buffer check. A nearest site is searched for within 100 km. To avoid the nationwide Overpass query timing out, site lookups cover only areas around current India hotspots, split into smaller batches, and try multiple Overpass endpoints. Successful results are cached locally for use when Overpass is unavailable. Cached coverage is partial; site distances and facility names depend on OpenStreetMap coverage and are context, not proof of a fire source. Historical FIRMS dates still use today's OSM context. FIRMS points are approximate satellite-pixel centers, not surveyed fire locations or fire perimeters.

## Troubleshooting

- For an authentication error, confirm the MAP_KEY on the NASA FIRMS website and enter it again.
- The app opens in **Demo only** mode. Demo points and reference sites are synthetic examples. Enter a MAP_KEY and click **Load live data** to fetch NASA FIRMS detections.
- An unavailable feed is shown in the status chips below the historical chart; other feeds can still load.
- The app queries OSM only around detected hotspots, in smaller batches, and uses previously cached OSM sites when Overpass is unavailable. If there is no local cache and all Overpass services time out, live detections still load using the bundled, generalized India outline, but site distances remain unavailable.
- Historical baselines build as you load more FIRMS date ranges. The database stores detections locally in `data/thermaltrace_history.sqlite3`; it is excluded from Git. A useful FRP comparison needs three or more earlier active dates from a matching satellite overpass. FIRMS hotspot data does not include all clear/no-fire observations.
- The bundled India outline is Natural Earth Admin 0 Countries, 1:110m GeoJSON, public domain: https://www.naturalearthdata.com/downloads/110m-cultural-vectors/ and https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_110m_admin_0_countries.geojson.
- Keep the app on `127.0.0.1`; it is designed for local use.
