# TerraBlaze / Thermal Intelligence — SIH 2026

## Run
Double-click `RUN_WEBSITE.bat`. The first run installs the Python packages and opens the site at `http://127.0.0.1:8000`.

### NASA FIRMS key
You have two options:
1. Let `RUN_WEBSITE.bat` ask for the MAP_KEY at startup.
2. Leave it blank and click **Load live data** on the website; a secure-looking browser modal asks for the key.

The website sends the key to the local FastAPI backend and stores it only in `sessionStorage` for the current browser session. `.env` is ignored by Git.

## Stack
NASA FIRMS · OpenStreetMap/Overpass API · GeoPandas · Shapely · scikit-learn · FastAPI · Leaflet

## GIS features
- FIRMS thermal anomaly detections
- OSM industrial context
- Industrial buffers and spatial join
- Nearest industrial feature and distance
- Time-based movement paths
- Prototype anomaly/risk score
- Interactive Leaflet map and detection table
- CSV export

The risk/classification output is a prototype signal, not a validated operational fire-type classifier.
