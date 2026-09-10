# Thermal Intelligence Website — SIH 2026

This is an actual browser-based website, not a Streamlit-only app.

### Stack
Frontend:
- HTML/CSS/JavaScript
- Leaflet
- OpenStreetMap tiles

Python backend:
- FastAPI
- pandas
- GeoPandas
- Shapely
- scikit-learn

External data:
- NASA FIRMS
- OpenStreetMap via Overpass API

### GIS features
- NASA FIRMS thermal anomalies
- OSM industrial infrastructure
- Industrial buffer analysis
- Spatial join: thermal points within industrial buffer
- Nearest industrial feature and distance
- Time-ordered movement paths
- Prototype anomaly/risk score
- Interactive map and detection table
- CSV export

### Run on Windows
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn backend:app --reload
```

Then open:
http://127.0.0.1:8000

The website starts in DEMO mode so it can be shown without a FIRMS key. Enter a NASA FIRMS MAP_KEY in the sidebar for live data.

### Important
The anomaly score is a prototype signal. It is not a validated industrial-fire classifier. FIRMS thermal anomalies can have multiple causes, and OSM coverage is not guaranteed to be complete.



just click the .bat file and refresh if any errors
