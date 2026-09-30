import io
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from shapely.geometry import LineString, Point, shape
from shapely.ops import polygonize, unary_union
from pyproj import Geod
from sklearn.cluster import DBSCAN
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import BallTree
from sklearn.preprocessing import StandardScaler


APP_DIR = Path(__file__).parent
BBOX = {"west": 68.0, "south": 6.0, "east": 97.5, "north": 37.5}
FIRMS_BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
OFFLINE_INDIA_BOUNDARY = APP_DIR / "data" / "india_boundary.geojson"
GEOD = Geod(ellps="WGS84")
NEARBY_SITE_LIMIT_KM = 100.0
INCIDENT_RADIUS_KM = 1.5

RECENT_SOURCES = {
    "VIIRS_NOAA21_NRT": {"satellite": "NOAA-21", "sensor": "VIIRS"},
    "VIIRS_NOAA20_NRT": {"satellite": "NOAA-20", "sensor": "VIIRS"},
    "VIIRS_SNPP_NRT": {"satellite": "Suomi-NPP", "sensor": "VIIRS"},
    "MODIS_NRT": {"satellite": "Terra/Aqua", "sensor": "MODIS"},
}
HISTORICAL_SOURCES = {
    "MODIS_SP": {"satellite": "Terra/Aqua", "sensor": "MODIS"},
    "VIIRS_NOAA20_SP": {"satellite": "NOAA-20", "sensor": "VIIRS"},
    "VIIRS_SNPP_SP": {"satellite": "Suomi-NPP", "sensor": "VIIRS"},
}
SOURCE_NAMES = {
    "VIIRS_NOAA21_NRT": "NOAA-21 · VIIRS",
    "VIIRS_NOAA20_NRT": "NOAA-20 · VIIRS",
    "VIIRS_SNPP_NRT": "Suomi-NPP · VIIRS",
    "MODIS_NRT": "Terra/Aqua · MODIS",
    "MODIS_SP": "Terra/Aqua · MODIS archive",
    "VIIRS_NOAA20_SP": "NOAA-20 · VIIRS archive",
    "VIIRS_SNPP_SP": "Suomi-NPP · VIIRS archive",
}


def load_firms_key():
    """Read the optional ignored .env file without requiring another package."""
    key = os.getenv("NASA_FIRMS_MAP_KEY", "").strip()
    if key:
        return key
    env_file = APP_DIR / ".env"
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() == "NASA_FIRMS_MAP_KEY":
                value = value.strip().strip("\"'")
                if value and not value.startswith("YOUR_"):
                    return value
    except OSError:
        pass
    return ""


FIRMS_KEY = load_firms_key()
app = FastAPI(title="ThermalTrace API")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(APP_DIR / "static" / "index.html")


def _empty_firms():
    return pd.DataFrame(columns=[
        "latitude", "longitude", "frp", "bright_ti4", "bright_ti5",
        "datetime_utc", "confidence", "satellite", "instrument", "source_id",
        "satellite_name", "sensor_name", "fire_type",
    ])


def normalize_firms(df):
    """Normalize FIRMS CSV variants and discard rows without usable coordinates."""
    if df is None:
        return _empty_firms()
    df = pd.DataFrame(df).copy()
    if df.empty and len(df.columns) == 0:
        return _empty_firms()
    df.columns = [str(column).strip().lstrip("\ufeff").lower() for column in df.columns]

    # MODIS uses brightness / bright_t31 where VIIRS uses bright_ti4 / bright_ti5.
    aliases = {
        "latitude": ("lat",),
        "longitude": ("lon", "lng"),
        "frp": ("fire_radiative_power",),
        "bright_ti4": ("brightness", "bright_t4"),
        "bright_ti5": ("bright_t31", "bright_t5"),
    }
    for target, alternatives in aliases.items():
        if target not in df:
            for candidate in alternatives:
                if candidate in df:
                    df[target] = df[candidate]
                    break
    for column in ("latitude", "longitude", "frp", "bright_ti4", "bright_ti5"):
        if column not in df:
            df[column] = np.nan
        df[column] = pd.to_numeric(df[column], errors="coerce")
    if "fire_type" not in df:
        df["fire_type"] = df["type"] if "type" in df else np.nan
    df["fire_type"] = pd.to_numeric(df["fire_type"], errors="coerce")

    if "datetime_utc" not in df or df["datetime_utc"].isna().all():
        if "acq_date" in df:
            date = pd.to_datetime(df["acq_date"], errors="coerce", utc=True)
            if "acq_time" in df:
                minutes = pd.to_numeric(df["acq_time"], errors="coerce").fillna(0).astype(int)
                hours_part = minutes // 100
                minutes_part = minutes % 100
                valid_time = minutes_part < 60
                date = date + pd.to_timedelta(hours_part.where(valid_time, 0), unit="h")
                date = date + pd.to_timedelta(minutes_part.where(valid_time, 0), unit="m")
            df["datetime_utc"] = date
        else:
            df["datetime_utc"] = pd.NaT
    else:
        df["datetime_utc"] = pd.to_datetime(df["datetime_utc"], errors="coerce", utc=True)

    valid_coordinates = (
        df["latitude"].between(-90, 90)
        & df["longitude"].between(-180, 180)
    )
    return df.loc[valid_coordinates].reset_index(drop=True)


def to_points(df):
    return gpd.GeoDataFrame(
        df.copy(),
        geometry=gpd.points_from_xy(df.longitude, df.latitude),
        crs=4326,
    )


def demo_data():
    now = pd.Timestamp.now(tz="UTC")
    points = [
        (28.6139, 77.2090, 38, 329.8, 0, "D", 60),
        (19.0760, 72.8777, 52, 335.0, 2, "N", 72),
        (19.0760, 72.8777, 44, 333.0, 2, "N", 36),
        (19.0760, 72.8777, 49, 334.0, 2, "N", 4),
        (13.0827, 80.2707, 7, 322.0, 0, "D", 18),
        (22.5726, 88.3639, 18, 322.0, 0, "N", 24),
        (17.3850, 78.4867, 31, 331.0, 0, "D", 42),
        (26.1445, 91.7362, 8, 319.0, None, "D", 30),
        (23.0225, 72.5714, 64, 346.0, 2, "N", 48),
        (12.2780, 93.8580, 27, 340.0, 1, "N", 6),
        (21.1458, 79.0882, 4, 315.0, None, "N", 12),
    ]
    rows = []
    for lat, lon, frp, bt4, fire_type, daynight, age_hours in points:
        detected_at = now - pd.Timedelta(hours=age_hours)
        rows.append({
            "latitude": lat, "longitude": lon, "frp": frp,
            "bright_ti4": bt4, "bright_ti5": bt4 - 24,
            "confidence": "h" if frp >= 60 else "n",
            "datetime_utc": detected_at,
            "satellite": "DEMO", "instrument": "VIIRS",
            "source_id": "DEMO", "satellite_name": "Demo satellite", "sensor_name": "VIIRS",
            "fire_type": fire_type, "daynight": daynight,
        })
    return normalize_firms(pd.DataFrame(rows))


def normalize_source(source):
    """Validate and normalize a NASA FIRMS source ID or display alias."""
    raw = str(source or "VIIRS_NOAA21_NRT").strip().upper().replace("-", "_").replace(" ", "_")
    if raw == "VERS_SNPP_NRT":
        raw = "VIIRS_SNPP_NRT"
    if raw not in RECENT_SOURCES and raw not in HISTORICAL_SOURCES:
        raise ValueError(f"Unsupported FIRMS source '{source}'.")
    return raw


def resolve_sources(selection, period):
    catalog = RECENT_SOURCES if period == "recent" else HISTORICAL_SOURCES
    if selection in (None, "ALL"):
        return list(catalog)
    source = normalize_source(selection)
    if source not in catalog:
        raise ValueError(f"{source} is not available for the selected data period.")
    return [source]


def _parse_firms_csv(text):
    try:
        df = pd.read_csv(io.StringIO(text))
    except pd.errors.EmptyDataError:
        return _empty_firms()
    if len(df.columns) == 0:
        return _empty_firms()
    if "latitude" not in [str(column).strip().lstrip("\ufeff").lower() for column in df.columns]:
        # A few FIRMS errors are returned as a text/CSV body with HTTP 200.
        raise ValueError("NASA FIRMS returned an error instead of fire detection data.")
    return normalize_firms(df)


def _safe_request_error(exc, secret=""):
    message = str(exc)
    if secret:
        message = message.replace(secret, "[redacted]")
        message = message.replace(quote(secret, safe=""), "[redacted]")
    # NASA puts the MAP key in the URL path, so retain only the service host
    # when reporting request failures. This also keeps Overpass errors useful.
    message = re.sub(r"(https?://[^/\s?#]+)[^\s]*", r"\1/[redacted]", message)
    return message[:400] or "The external data request failed."


def fetch_firms(key, source, days, start_date=None):
    """Fetch up to 30 contiguous days in NASA's five-day request windows."""
    source = normalize_source(source)
    days = max(1, min(30, int(days)))
    area = f"{BBOX['west']},{BBOX['south']},{BBOX['east']},{BBOX['north']}"
    today = datetime.now(timezone.utc).date()
    if start_date:
        first_date = datetime.strptime(str(start_date), "%Y-%m-%d").date()
        if first_date + timedelta(days=days - 1) > today:
            raise ValueError("Selected date range cannot extend into the future.")
    else:
        first_date = None
    frames = []
    remaining = days
    consumed = 0

    while remaining > 0:
        window = min(5, remaining)
        encoded_key = quote(str(key).strip(), safe="")
        url = f"{FIRMS_BASE}/{encoded_key}/{source}/{area}/{window}"
        if first_date is not None:
            # A dated request returns DATE through DATE + window - 1.
            window_date = first_date + timedelta(days=consumed)
            url += f"/{window_date.isoformat()}"
        elif consumed:
            window_date = today - timedelta(days=consumed + window - 1)
            url += f"/{window_date.isoformat()}"
        response = requests.get(url, timeout=45)
        if response.status_code != 200:
            body = (response.text or "").strip().replace("\n", " ")
            if len(body) > 200:
                body = body[:200] + "…"
            raise requests.HTTPError(
                f"NASA FIRMS returned HTTP {response.status_code}: {body}",
                response=response,
            )
        frames.append(_parse_firms_csv(response.text))
        remaining -= window
        consumed += window

    if not frames:
        return _empty_firms()
    out = pd.concat(frames, ignore_index=True)
    dedupe = [
        column for column in (
            "latitude", "longitude", "acq_date", "acq_time", "satellite", "instrument",
        ) if column in out.columns
    ]
    if dedupe:
        out = out.drop_duplicates(subset=dedupe)
    out = normalize_firms(out)
    source_info = {**RECENT_SOURCES, **HISTORICAL_SOURCES}[source]
    out["source_id"] = source
    out["sensor_name"] = source_info["sensor"]
    raw_satellite = out.get("satellite", pd.Series("", index=out.index)).astype(str).str.strip()
    if source_info["sensor"] == "MODIS":
        raw_lower = raw_satellite.str.lower()
        platform = pd.Series(np.where(
            raw_lower.str.contains("aqua|^a$", regex=True), "Aqua",
            np.where(raw_lower.str.contains("terra|^t$", regex=True), "Terra", source_info["satellite"]),
        ), index=out.index)
    else:
        # A scalar string is iterable character by character. Make the
        # platform column row-aligned so VIIRS feeds keep one name per record.
        platform = pd.Series(source_info["satellite"], index=out.index, dtype="object")
    out["satellite_name"] = platform.astype(str) + f" · {source_info['sensor']}"
    return out


def demo_facilities():
    return [
        {"name": "Delhi industrial context", "kind": "industrial", "lat": 28.6139, "lon": 77.2090, "osm_id": "demo-delhi", "osm_type": "node"},
        {"name": "Mumbai industrial context", "kind": "industrial", "lat": 19.0760, "lon": 72.8777, "osm_id": "demo-mumbai", "osm_type": "node"},
        {"name": "Chennai industrial context", "kind": "industrial", "lat": 13.0827, "lon": 80.2707, "osm_id": "demo-chennai", "osm_type": "node"},
        {"name": "Kolkata industrial context", "kind": "industrial", "lat": 22.5726, "lon": 88.3639, "osm_id": "demo-kolkata", "osm_type": "node"},
        {"name": "Hyderabad industrial context", "kind": "industrial", "lat": 17.3850, "lon": 78.4867, "osm_id": "demo-hyderabad", "osm_type": "node"},
        {"name": "Guwahati industrial context", "kind": "industrial", "lat": 26.1445, "lon": 91.7362, "osm_id": "demo-guwahati", "osm_type": "node"},
        {"name": "Ahmedabad industrial context", "kind": "industrial", "lat": 23.0225, "lon": 72.5714, "osm_id": "demo-ahmedabad", "osm_type": "node"},
    ]


def _boundary_from_relation(element):
    """Rebuild India's outer and inner rings from the OSM country relation."""
    outer_lines = []
    inner_lines = []
    for member in element.get("members", []):
        coordinates = member.get("geometry") or []
        if len(coordinates) < 2:
            continue
        line = LineString([(point["lon"], point["lat"]) for point in coordinates])
        (inner_lines if member.get("role") == "inner" else outer_lines).append(line)
    if not outer_lines:
        return None
    outer_rings = list(polygonize(unary_union(outer_lines)))
    if not outer_rings:
        return None
    boundary = unary_union(outer_rings)
    if inner_lines:
        inner_rings = list(polygonize(unary_union(inner_lines)))
        if inner_rings:
            boundary = boundary.difference(unary_union(inner_rings))
    return boundary if not boundary.is_empty else None


def _load_offline_india_boundary():
    """Load the bundled Natural Earth country outline for Overpass outages."""
    with OFFLINE_INDIA_BOUNDARY.open("r", encoding="utf-8") as boundary_file:
        geometry = shape(json.load(boundary_file))
    if geometry.is_empty or not geometry.is_valid:
        raise ValueError("The bundled India boundary is empty or invalid.")
    return geometry


def _post_overpass(query, timeout):
    """Try multiple global Overpass instances in sequence."""
    errors = []
    for endpoint in OVERPASS_ENDPOINTS:
        host = endpoint.split("/")[2]
        try:
            response = requests.post(
                endpoint,
                data=query,
                headers={"User-Agent": "ThermalTrace/1.0"},
                timeout=(15, timeout),
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("remark"):
                raise ValueError(f"Overpass returned an error: {payload['remark']}")
            if len(errors):
                logging.warning("Overpass retry succeeded via %s after %s failed endpoint(s)", host, len(errors))
            return response, host
        except (requests.RequestException, ValueError) as exc:
            error = _safe_request_error(exc)
            logging.warning("Overpass request failed via %s: %s", host, error)
            errors.append(f"{host}: {error}")
    raise requests.RequestException("All Overpass servers failed: " + " | ".join(errors))


def fetch_osm():
    boundary_query = """
    [out:json][timeout:120];
    relation["ISO3166-1"="IN"][admin_level=2]->.india_boundary;
    .india_boundary out geom;
    """
    boundary_error = ""
    boundary_source = "OSM"
    india_boundary = None
    try:
        boundary_response, _ = _post_overpass(boundary_query, timeout=150)
        boundary_element = next((
            element for element in boundary_response.json().get("elements", [])
            if element.get("type") == "relation" and element.get("tags", {}).get("ISO3166-1") == "IN"
        ), None)
        india_boundary = _boundary_from_relation(boundary_element) if boundary_element else None
        if india_boundary is None:
            raise ValueError("OpenStreetMap did not return India's national boundary.")
    except Exception as exc:
        boundary_error = _safe_request_error(exc)
        try:
            india_boundary = _load_offline_india_boundary()
            boundary_source = "NATURAL_EARTH_OFFLINE"
            logging.warning("Overpass India boundary unavailable; using bundled Natural Earth outline: %s", boundary_error)
        except Exception as fallback_exc:
            raise ValueError(
                "OpenStreetMap's India boundary is unavailable and the bundled fallback could not be loaded: "
                f"{_safe_request_error(fallback_exc)}"
            ) from exc

    sites_query = """
    [out:json][timeout:180];
    area["ISO3166-1"="IN"][admin_level=2]->.india;
    (
      nwr["industrial"](area.india);
      nwr["landuse"="industrial"](area.india);
      nwr["power"~"plant|generator"](area.india);
      nwr["man_made"="works"](area.india);
    ); out center tags;
    """
    site_endpoint = ""
    try:
        sites_response, site_endpoint = _post_overpass(sites_query, timeout=210)
        site_elements = sites_response.json().get("elements", [])
    except Exception as exc:
        error = _safe_request_error(exc)
        if boundary_error:
            error = f"India boundary fallback in use ({boundary_error}); site lookup failed ({error})"
        logging.warning("OpenStreetMap industrial site layer unavailable: %s", error)
        return [], india_boundary, False, error, boundary_source, site_endpoint
    rows = []
    seen = set()
    for element in site_elements:
        tags = element.get("tags", {})
        identity = (element.get("type"), element.get("id"))
        if identity in seen:
            continue
        seen.add(identity)
        if "lat" in element and "lon" in element:
            lat, lon = element["lat"], element["lon"]
        else:
            center_point = element.get("center", {})
            lat, lon = center_point.get("lat"), center_point.get("lon")
        if lat is None or lon is None:
            continue
        rows.append({
            "name": tags.get("name") or tags.get("industrial") or tags.get("landuse") or "Unnamed industrial feature",
            "kind": tags.get("industrial") or tags.get("power") or tags.get("landuse") or tags.get("man_made") or "industrial",
            "lat": lat, "lon": lon, "osm_id": element.get("id"),
            "osm_type": element.get("type"),
        })
    logging.warning(
        "OpenStreetMap site query via %s returned %s tagged features; %s had usable coordinates",
        site_endpoint, len(site_elements), len(rows),
    )
    return rows, india_boundary, True, boundary_error, boundary_source, site_endpoint


def _empty_analysis():
    columns = [
        "latitude", "longitude", "frp", "bright_ti4", "bright_ti5", "satellite",
        "satellite_name", "sensor_name", "source_id", "confidence", "instrument",
        "datetime_utc", "fire_type", "nearest_name", "nearest_kind", "distance_km",
        "nearby_features", "inside_industrial_buffer", "temp_anomaly", "anomaly_score",
        "risk_score", "classification", "classification_basis", "persistence_days",
        "incident_id", "incident_detections", "incident_peak_frp", "incident_satellites", "incident_first_seen",
        "incident_last_seen",
    ]
    return gpd.GeoDataFrame({column: pd.Series(dtype="object") for column in columns}, geometry=[], crs=4326)


def _classify_hotspot(row):
    fire_type = row.get("fire_type")
    try:
        fire_type = int(fire_type) if pd.notna(fire_type) else None
    except (TypeError, ValueError):
        fire_type = None
    distance = row.get("distance_km")
    distance = float(distance) if pd.notna(distance) else np.inf
    persistence = int(row.get("persistence_days") or 0)
    frp = float(row.get("frp") or 0)
    daynight = str(row.get("daynight") or "").upper()
    satellite_confidence = str(row.get("confidence") or "").lower()
    if fire_type == 1:
        return "Volcanic or geothermal source", "FIRMS type 1: active volcano source"
    if fire_type == 2 and distance <= 3 and persistence >= 3:
        return "Persistent industrial heat / gas flare", "FIRMS type 2 static land source near a mapped site on multiple dates"
    if fire_type == 2 and distance <= 10:
        return "Suspected industrial incident", "FIRMS type 2 static land source near a mapped industrial or power site"
    if fire_type == 0:
        if frp <= 8 and daynight == "D":
            return "Agricultural or open burn", "Low-FRP daytime vegetation detection; open or agricultural burning is a heuristic"
        return "Vegetation fire", "FIRMS type 0: presumed vegetation fire"
    if fire_type == 3:
        return "Unknown / needs review", "FIRMS type 3 offshore detection; no land-fire cause assigned"
    if fire_type == 2:
        return "Unknown / needs review", "FIRMS type 2 static land source; no nearby mapped site confirms its cause"
    if distance <= 3 and persistence >= 3:
        return "Persistent industrial heat / gas flare", "Repeated hotspot near a mapped site; candidate requires review"
    if distance <= 5 and frp >= 20:
        return "Suspected industrial incident", "High-FRP hotspot near a mapped site; candidate requires review"
    if frp <= 8 and daynight == "D":
        return "Agricultural or open burn", "Low-FRP daytime hotspot; open or agricultural burning is a heuristic"
    if frp >= 8 and satellite_confidence in {"h", "high", "n", "nominal"}:
        return "Vegetation fire", "FIRMS confidence and FRP suggest a vegetation fire; cause is not field-verified"
    return "Unknown / needs review", "FIRMS source type is unavailable or evidence is inconclusive"


def _assign_incidents(events):
    """Group nearby multi-satellite detections into one location history."""
    if events.empty:
        return events
    coordinates = np.radians(events[["latitude", "longitude"]].to_numpy(dtype=float))
    labels = DBSCAN(
        eps=INCIDENT_RADIUS_KM / 6371.0088,
        min_samples=1,
        metric="haversine",
        algorithm="ball_tree",
    ).fit_predict(coordinates)
    events["_cluster"] = labels
    timestamps = pd.to_datetime(events["datetime_utc"], errors="coerce", utc=True)
    events["_date"] = timestamps.dt.strftime("%Y-%m-%d")
    summaries = {}
    for cluster, indices in events.groupby("_cluster").groups.items():
        group = events.loc[indices]
        center_lat = float(group["latitude"].mean())
        center_lon = float(group["longitude"].mean())
        digest = hashlib.sha1(f"{center_lat:.3f},{center_lon:.3f}".encode("ascii")).hexdigest()[:10]
        satellite_names = sorted({str(name) for name in group["satellite_name"].dropna() if str(name).strip()})
        dates = group["_date"].dropna()
        first_seen = timestamps.loc[indices].min()
        last_seen = timestamps.loc[indices].max()
        summaries[cluster] = {
            "incident_id": f"IN-{digest}",
            "incident_detections": int(len(group)),
            "incident_peak_frp": float(group["frp"].max()) if group["frp"].notna().any() else np.nan,
            "incident_satellites": satellite_names,
            "incident_first_seen": first_seen,
            "incident_last_seen": last_seen,
            "persistence_days": int(dates.nunique()),
        }
    for column in ("incident_id", "incident_detections", "incident_peak_frp", "incident_satellites", "incident_first_seen", "incident_last_seen", "persistence_days"):
        events[column] = events["_cluster"].map({cluster: value[column] for cluster, value in summaries.items()})
    return events.drop(columns=["_cluster", "_date"], errors="ignore")


def analyze(firms, facilities, buffer_km, india_boundary=None):
    firms = normalize_firms(firms)
    if india_boundary is not None and not firms.empty:
        inside_india = [india_boundary.covers(Point(lon, lat)) for lat, lon in zip(firms.latitude, firms.longitude)]
        firms = firms.loc[inside_india].reset_index(drop=True)
    if firms.empty:
        empty_facilities = pd.DataFrame(facilities or [])
        return _empty_analysis(), empty_facilities

    events = to_points(firms).reset_index(drop=True)
    events["_event_id"] = np.arange(len(events))
    osm = pd.DataFrame(facilities or []).copy()
    if osm.empty:
        industrial = gpd.GeoDataFrame(columns=["name", "kind", "lat", "lon", "geometry"], geometry="geometry", crs=4326)
        events["nearest_name"] = None
        events["nearest_kind"] = None
        events["distance_km"] = np.nan
        events["nearby_features"] = [[] for _ in range(len(events))]
        events["inside_industrial_buffer"] = False
    else:
        osm["lat"] = pd.to_numeric(osm["lat"], errors="coerce")
        osm["lon"] = pd.to_numeric(osm["lon"], errors="coerce")
        osm = osm.loc[
            osm["lat"].between(-90, 90) & osm["lon"].between(-180, 180)
        ].reset_index(drop=True)
        if osm.empty:
            return analyze(firms, [], buffer_km, india_boundary)
        if "name" not in osm:
            osm["name"] = "Unnamed mapped site"
        if "kind" not in osm:
            osm["kind"] = "industrial"
        industrial = gpd.GeoDataFrame(osm, geometry=gpd.points_from_xy(osm.lon, osm.lat), crs=4326)
        site_lats = industrial["lat"].to_numpy(dtype=float)
        site_lons = industrial["lon"].to_numpy(dtype=float)
        site_names = industrial["name"].fillna("Unnamed mapped site").astype(str).to_numpy()
        site_kinds = industrial["kind"].fillna("industrial").astype(str).to_numpy()
        site_coordinates = np.radians(np.column_stack([site_lats, site_lons]))
        site_index = BallTree(site_coordinates, metric="haversine")
        nearest_names = []
        nearest_kinds = []
        distances = []
        nearby_features = []
        inside_buffer = []
        radius = NEARBY_SITE_LIMIT_KM
        for lat, lon in zip(events.latitude.to_numpy(dtype=float), events.longitude.to_numpy(dtype=float)):
            event_coordinate = np.radians(np.array([[lat, lon]], dtype=float))
            # A small margin covers the difference between a spherical candidate
            # search and the exact WGS84 ellipsoid distance calculated below.
            candidate_ids = site_index.query_radius(
                event_coordinate, r=(radius + 2.0) / 6371.0088
            )[0]
            if not len(candidate_ids):
                nearest_names.append(None)
                nearest_kinds.append(None)
                distances.append(np.nan)
                nearby_features.append([])
                inside_buffer.append(False)
                continue
            _, _, meters = GEOD.inv(
                np.full(len(candidate_ids), lon), np.full(len(candidate_ids), lat),
                site_lons[candidate_ids], site_lats[candidate_ids],
            )
            km = np.asarray(meters, dtype=float) / 1000
            order = np.argsort(km)
            ordered_ids = candidate_ids[order]
            ordered_km = km[order]
            nearby = [
                {"name": site_names[index], "kind": site_kinds[index], "distance_km": round(float(distance), 2)}
                for index, distance in zip(ordered_ids, ordered_km)
                if distance <= 50
            ][:5]
            nearest_id = int(ordered_ids[0])
            nearest_distance = float(ordered_km[0])
            if nearest_distance <= radius:
                nearest_names.append(site_names[nearest_id])
                nearest_kinds.append(site_kinds[nearest_id])
                distances.append(nearest_distance)
                inside_buffer.append(nearest_distance <= max(0, float(buffer_km)))
            else:
                nearest_names.append(None)
                nearest_kinds.append(None)
                distances.append(np.nan)
                inside_buffer.append(False)
            nearby_features.append(nearby)
        events["nearest_name"] = nearest_names
        events["nearest_kind"] = nearest_kinds
        events["distance_km"] = distances
        events["nearby_features"] = nearby_features
        events["inside_industrial_buffer"] = inside_buffer

    events["temp_anomaly"] = (events["bright_ti4"] - events["bright_ti5"]).fillna(0)
    events["persistence_days"] = 1
    if "datetime_utc" in events:
        event_dates = pd.to_datetime(events["datetime_utc"], errors="coerce", utc=True).dt.strftime("%Y-%m-%d")
        location_cells = events["latitude"].round(2).astype(str) + "," + events["longitude"].round(2).astype(str)
        persistence = pd.DataFrame({"cell": location_cells, "date": event_dates}).groupby("cell")["date"].nunique()
        events["persistence_days"] = location_cells.map(persistence).fillna(1).astype(int)
    events = _assign_incidents(events)

    events["inside_score"] = events["inside_industrial_buffer"].astype(int)
    proximity = np.exp(-events["distance_km"].fillna(20) / 3).clip(0, 1)
    features = events[["frp", "temp_anomaly", "distance_km", "inside_score"]]
    features = features.replace([np.inf, -np.inf], np.nan).fillna(0)
    if len(events) >= 8:
        scaled = StandardScaler().fit_transform(features)
        model = IsolationForest(n_estimators=150, contamination="auto", random_state=42)
        model.fit(scaled)
        raw = -model.score_samples(scaled)
        events["anomaly_score"] = (raw - raw.min()) / (raw.max() - raw.min() + 1e-9)
    else:
        frp = events["frp"].fillna(0) / max(float(events["frp"].fillna(0).max()), 1)
        events["anomaly_score"] = (.45 * frp + .35 * events["inside_score"] + .20 * proximity).clip(0, 1)

    temp_normalized = events["temp_anomaly"].fillna(0) / max(float(events["temp_anomaly"].fillna(0).max()), 1)
    events["risk_score"] = (
        100 * (.45 * events["anomaly_score"] + .30 * events["inside_score"] + .15 * proximity + .10 * temp_normalized)
    ).clip(0, 100)
    classifications = events.apply(_classify_hotspot, axis=1)
    events["classification"] = [value[0] for value in classifications]
    events["classification_basis"] = [value[1] for value in classifications]
    events = events.drop(columns=["_event_id", "inside_score"], errors="ignore")
    return events, industrial


def movement(out):
    if out.empty or "datetime_utc" not in out:
        return []
    data = out.copy()
    data["datetime_utc"] = pd.to_datetime(data["datetime_utc"], errors="coerce", utc=True)
    data = data.dropna(subset=["datetime_utc"])
    data = data.loc[data["nearest_name"].notna()]
    if data.empty:
        return []
    data["group"] = data["nearest_name"].fillna("Unassigned") + " · " + data["satellite_name"].fillna("Unknown satellite")
    paths = []
    for group, rows in data.groupby("group"):
        if group == "Unassigned":
            continue
        rows = rows.sort_values("datetime_utc")
        if len(rows) < 2:
            continue
        segments = []
        current = []
        previous = None
        for _, row in rows.iterrows():
            point = row.geometry
            if previous is not None:
                _, _, gap_m = GEOD.inv(previous.geometry.x, previous.geometry.y, point.x, point.y)
                gap_hours = (row.datetime_utc - previous.datetime_utc).total_seconds() / 3600
                if gap_m > 50000 or gap_hours > 24:
                    if len(current) >= 2:
                        segments.append(current)
                    current = []
            current.append((point.x, point.y, row.datetime_utc))
            previous = row
        if len(current) >= 2:
            segments.append(current)
        for segment in segments:
            distance_km = sum(
                GEOD.inv(left[0], left[1], right[0], right[1])[2] / 1000
                for left, right in zip(segment, segment[1:])
            )
            elapsed_hours = max((segment[-1][2] - segment[0][2]).total_seconds() / 3600, .001)
            paths.append({
                "group": group, "points": len(segment), "distance_km": distance_km,
                "speed_kmh": distance_km / elapsed_hours,
                "coordinates": [[lat, lon] for lon, lat, _ in segment],
            })
    return paths


def clean_events(out):
    if out.empty:
        return []
    frame = pd.DataFrame(out.drop(columns=["geometry"], errors="ignore")).copy()
    display_columns = [
        "latitude", "longitude", "datetime_utc", "frp", "bright_ti4", "bright_ti5",
        "confidence", "daynight", "satellite", "satellite_name", "sensor_name", "source_id", "instrument",
        "fire_type", "nearest_name", "nearest_kind", "distance_km", "nearby_features",
        "inside_industrial_buffer", "temp_anomaly", "anomaly_score", "risk_score",
        "classification", "classification_basis", "persistence_days", "incident_id",
        "incident_detections", "incident_peak_frp", "incident_satellites", "incident_first_seen", "incident_last_seen",
    ]
    frame = frame[[column for column in display_columns if column in frame.columns]]
    def json_safe(value):
        if value is None or value is pd.NA:
            return None
        if isinstance(value, dict):
            return {str(key): json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set, np.ndarray)):
            return [json_safe(item) for item in value]
        if isinstance(value, (pd.Timestamp, datetime)):
            return value.strftime("%Y-%m-%dT%H:%M:%SZ") if pd.notna(value) else None
        if isinstance(value, np.generic):
            return json_safe(value.item())
        if isinstance(value, float) and not np.isfinite(value):
            return None
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
        return value

    # Sanitize the Python records after pandas conversion. Assigning None into a
    # float-typed column first makes pandas coerce it straight back to NaN.
    return [
        {column: json_safe(value) for column, value in record.items()}
        for record in frame.to_dict(orient="records")
    ]


def fetch_selected_feeds(key, selection, period, days, start_date=None):
    sources = resolve_sources(selection, period)
    frames = []
    feed_status = []
    catalog = RECENT_SOURCES if period == "recent" else HISTORICAL_SOURCES
    for source in sources:
        info = catalog[source]
        try:
            events = fetch_firms(key, source, days, start_date=start_date)
            frames.append(events)
            feed_status.append({
                "source_id": source, "label": SOURCE_NAMES[source], "ok": True,
                "count": len(events), "error": "",
            })
        except Exception as exc:
            feed_status.append({
                "source_id": source, "label": SOURCE_NAMES[source], "ok": False,
                "count": 0, "error": _safe_request_error(exc, key),
            })
    if frames:
        firms = normalize_firms(pd.concat(frames, ignore_index=True))
        if "datetime_utc" in firms:
            firms = firms.sort_values("datetime_utc", ascending=False, na_position="last").reset_index(drop=True)
    else:
        firms = _empty_firms()
    return firms, feed_status


def _run_analysis(key, selection, period, days, buffer_km, start_date=None):
    firms, feed_status = fetch_selected_feeds(key, selection, period, days, start_date)
    try:
        facilities, india_boundary, osm_sites_available, osm_error, boundary_source, osm_endpoint = fetch_osm()
        osm_mode = "LIVE" if osm_sites_available else "SITE_CONTEXT_UNAVAILABLE"
    except Exception as exc:
        raise ValueError(f"India boundary and industrial context could not be loaded, so detections were not shown: {_safe_request_error(exc)}")
    events, _ = analyze(firms, facilities, buffer_km, india_boundary=india_boundary)
    actual_start = start_date or (datetime.now(timezone.utc).date() - timedelta(days=days - 1)).isoformat()
    actual_end = (datetime.strptime(actual_start, "%Y-%m-%d").date() + timedelta(days=days - 1)).isoformat()
    return {
        "mode": "LIVE", "period": period, "start_date": actual_start, "end_date": actual_end,
        "selected_sources": list(RECENT_SOURCES if period == "recent" else HISTORICAL_SOURCES)
        if selection in (None, "ALL") else [normalize_source(selection)],
        "source_status": feed_status, "osm_mode": osm_mode, "osm_error": osm_error,
        "boundary_source": boundary_source, "osm_endpoint": osm_endpoint,
        "osm_feature_count": len(facilities),
        "excluded_outside_india": max(0, len(firms) - len(events)),
        "events": clean_events(events), "facilities": facilities, "paths": movement(events),
    }


@app.get("/api/auto")
def auto(source: str = "VIIRS_NOAA21_NRT", days: int = 2, buffer_km: float = 5):
    if not FIRMS_KEY:
        events, _ = analyze(demo_data(), demo_facilities(), buffer_km)
        return {"mode": "DEMO", "events": clean_events(events), "facilities": demo_facilities(), "paths": movement(events)}
    try:
        return _run_analysis(FIRMS_KEY, source, "recent", days, buffer_km)
    except Exception as exc:
        events, _ = analyze(demo_data(), demo_facilities(), buffer_km)
        return {
            "mode": "DEMO_FALLBACK", "error": _safe_request_error(exc, FIRMS_KEY),
            "events": clean_events(events), "facilities": demo_facilities(), "paths": movement(events),
        }


@app.get("/api/demo")
def demo(buffer_km: float = 5):
    buffer_km = max(1.0, min(15.0, float(buffer_km)))
    try:
        events, _ = analyze(demo_data(), demo_facilities(), buffer_km)
        return {"mode": "DEMO", "events": clean_events(events), "facilities": demo_facilities(), "paths": movement(events)}
    except Exception as exc:
        logging.exception("Demo analysis failed")
        raise HTTPException(500, "Demo analysis failed. Check the RUN_WEBSITE console for the traceback.") from exc


@app.post("/api/analyze")
async def analyze_api_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON request.")
    if not isinstance(body, dict):
        raise HTTPException(400, "The request body must be a JSON object.")

    key = str(body.get("map_key") or FIRMS_KEY).strip()
    if not key:
        raise HTTPException(400, "NASA FIRMS MAP_KEY is required. Paste it into the key field, choose a key file, or configure it in .env.")
    if len(key) > 256 or any(character.isspace() for character in key):
        raise HTTPException(400, "The NASA FIRMS MAP_KEY format looks invalid. Check the value and try again.")
    try:
        period = str(body.get("period") or "recent").strip().lower()
        if period not in {"recent", "historical"}:
            raise ValueError("Choose a supported data period.")
        source = body.get("source") or "ALL"
        days = max(1, min(30, int(body.get("days") or 7)))
        buffer_km = max(1.0, min(15.0, float(body.get("buffer_km") or 5)))
        sources = resolve_sources(source, period)
        start_date = None
        if period == "historical":
            start_date = str(body.get("start_date") or "").strip()
            if not start_date:
                raise ValueError("Choose a start date for historical data.")
            parsed_start = datetime.strptime(start_date, "%Y-%m-%d").date()
            if parsed_start + timedelta(days=days - 1) > datetime.now(timezone.utc).date():
                raise ValueError("The selected historical date range cannot extend into the future.")
    except (TypeError, ValueError):
        raise HTTPException(400, "Choose a valid recent or historical period, satellite feed, date, 1-30 days, and 1-15 km buffer.")
    try:
        return _run_analysis(key, source, period, days, buffer_km, start_date)
    except requests.RequestException as exc:
        raise HTTPException(502, f"NASA FIRMS/OSM request failed: {_safe_request_error(exc, key)}")
    except ValueError as exc:
        raise HTTPException(502, _safe_request_error(exc, key))
    except Exception as exc:
        # Keep server internals and credentials out of the browser response while
        # retaining the traceback in the local RUN_WEBSITE console for diagnosis.
        logging.exception("FIRMS analysis failed")
        raise HTTPException(500, "The analysis could not be completed. Check the RUN_WEBSITE console for the traceback.") from exc
