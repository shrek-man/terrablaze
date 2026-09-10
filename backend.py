
import os
from typing import Optional
import math
import io
import numpy as np
import pandas as pd
import requests
import geopandas as gpd
from shapely.geometry import LineString
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sklearn.ensemble import IsolationForest

APP_DIR = __import__("pathlib").Path(__file__).parent
BBOX = {"west":77.35,"south":12.75,"east":77.85,"north":13.20}
FIRMS_BASE="https://firms.modaps.eosdis.nasa.gov/api/area/csv"
OVERPASS="https://overpass-api.de/api/interpreter"
FIRMS_KEY = os.getenv("NASA_FIRMS_MAP_KEY", "").strip()

app=FastAPI(title="Thermal Intelligence API")
app.mount("/static", StaticFiles(directory=APP_DIR/"static"), name="static")

@app.get("/")
def index():
    return FileResponse(APP_DIR/"static/index.html")

def to_points(df):
    return gpd.GeoDataFrame(
        df.copy(),
        geometry=gpd.points_from_xy(df.longitude, df.latitude),
        crs=4326
    )

def demo_data():
    now=pd.Timestamp.utcnow().tz_localize(None)
    pts=[
      (12.9915,77.5500,38,329.8),(12.9930,77.5560,52,335.0),
      (12.9948,77.5625,71,340.0),(13.0045,77.5850,18,322.0),
      (12.9070,77.6240,31,331.0),(13.0400,77.7000,8,319.0),
      (12.8420,77.5000,64,346.0),(12.8460,77.5070,82,351.0)
    ]
    rows=[]
    for i,(lat,lon,frp,bt4) in enumerate(pts):
        dt=now-pd.Timedelta(hours=6*(len(pts)-i))
        rows.append({
          "latitude":lat,"longitude":lon,"frp":frp,
          "bright_ti4":bt4,"bright_ti5":bt4-24,
          "confidence":"h" if frp>=60 else "n","datetime_utc":dt.isoformat(),
          "satellite":"DEMO","instrument":"VIIRS"
        })
    return pd.DataFrame(rows)

def fetch_firms(key,source,days):
    url=f"{FIRMS_BASE}/{key}/{source}/{BBOX['west']},{BBOX['south']},{BBOX['east']},{BBOX['north']}/{days}"
    r=requests.get(url,timeout=40)
    r.raise_for_status()
    df=pd.read_csv(io.StringIO(r.text))
    if df.empty:return df
    for c in ["latitude","longitude","frp","bright_ti4","bright_ti5"]:
        if c in df.columns:df[c]=pd.to_numeric(df[c],errors="coerce")
    t=pd.to_datetime(df.get("acq_date"),errors="coerce")
    if "acq_time" in df.columns:
        mins=pd.to_numeric(df["acq_time"],errors="coerce").fillna(0).astype(int)
        t=t+pd.to_timedelta(mins//100,unit="h")+pd.to_timedelta(mins%100,unit="m")
    df["datetime_utc"]=t.astype(str)
    return df

def demo_facilities():
    return [
        {"name":"Peenya Industrial Area","kind":"industrial","lat":13.0282,"lon":77.5197,"osm_id":"demo-peenya","osm_type":"node"},
        {"name":"Whitefield Industrial Area","kind":"industrial","lat":12.9698,"lon":77.7499,"osm_id":"demo-whitefield","osm_type":"node"},
        {"name":"Electronic City Industrial Area","kind":"industrial","lat":12.8458,"lon":77.6600,"osm_id":"demo-ecity","osm_type":"node"},
        {"name":"Bidadi Industrial Area","kind":"industrial","lat":12.8006,"lon":77.3765,"osm_id":"demo-bidadi","osm_type":"node"},
    ]

def fetch_osm():
    q=f"""
    [out:json][timeout:60];
    (
      nwr["industrial"]({BBOX['south']},{BBOX['west']},{BBOX['north']},{BBOX['east']});
      nwr["landuse"="industrial"]({BBOX['south']},{BBOX['west']},{BBOX['north']},{BBOX['east']});
      nwr["power"~"plant|generator"]({BBOX['south']},{BBOX['west']},{BBOX['north']},{BBOX['east']});
      nwr["man_made"="works"]({BBOX['south']},{BBOX['west']},{BBOX['north']},{BBOX['east']});
    ); out center tags;
    """
    r=requests.post(OVERPASS,data=q,timeout=90);r.raise_for_status()
    rows=[]
    for el in r.json().get("elements",[]):
        tags=el.get("tags",{})
        if "lat" in el and "lon" in el: lat,lon=el["lat"],el["lon"]
        else:
            c=el.get("center",{});lat,lon=c.get("lat"),c.get("lon")
        if lat is None or lon is None:continue
        rows.append({
          "name":tags.get("name") or tags.get("industrial") or tags.get("landuse") or "Unnamed industrial feature",
          "kind":tags.get("industrial") or tags.get("power") or tags.get("landuse") or tags.get("man_made") or "industrial",
          "lat":lat,"lon":lon,"osm_id":el.get("id"),"osm_type":el.get("type")
        })
    return rows

def analyze(firms, facilities, buffer_km):
    fg=to_points(firms)
    if not facilities:
        fg["nearest_name"]=None;fg["distance_km"]=np.nan;fg["inside_industrial_buffer"]=False
        return fg,pd.DataFrame()
    osm_df=pd.DataFrame(facilities)
    og=gpd.GeoDataFrame(osm_df,geometry=gpd.points_from_xy(osm_df.lon,osm_df.lat),crs=4326)
    f=fg.to_crs(32643);o=og.to_crs(32643)
    near=gpd.sjoin_nearest(f,o[["name","kind","geometry"]],how="left",distance_col="nearest_m")
    buf=o.copy();buf["geometry"]=buf.geometry.buffer(buffer_km*1000);buf["buffer_id"]=np.arange(len(buf))
    joined=gpd.sjoin(f,buf[["buffer_id","name","kind","geometry"]],how="left",predicate="within")
    out=joined.copy()
    out["nearest_m"]=near["nearest_m"].values
    out["nearest_name"]=near["name"].values
    out["distance_km"]=out["nearest_m"]/1000
    out["inside_industrial_buffer"]=out["buffer_id"].notna()
    out["temp_anomaly"]=(out["bright_ti4"]-out["bright_ti5"]).fillna(0)
    out["inside_score"]=out["inside_industrial_buffer"].astype(int)
    prox=np.exp(-out["distance_km"].fillna(20)/3).clip(0,1)
    feats=out[["frp","temp_anomaly","distance_km","inside_score"]].replace([np.inf,-np.inf],np.nan).fillna(0)
    if len(out)>=8:
        model=IsolationForest(n_estimators=150,contamination="auto",random_state=42)
        model.fit(feats)
        raw=-model.score_samples(feats);out["anomaly_score"]=(raw-raw.min())/(raw.max()-raw.min()+1e-9)
    else:
        frp=out["frp"].fillna(0)/max(float(out["frp"].fillna(0).max()),1)
        out["anomaly_score"]=(.45*frp+.35*out["inside_score"]+.20*prox).clip(0,1)
    temp_norm=out["temp_anomaly"].fillna(0)/max(float(out["temp_anomaly"].fillna(0).max()),1)
    out["risk_score"]=(100*(.45*out["anomaly_score"]+.30*out["inside_score"]+.15*prox+.10*temp_norm)).clip(0,100)
    out["classification"]=np.where(
      (out["inside_industrial_buffer"])&(out["risk_score"]>=72),"Potential industrial anomaly",
      np.where(out["inside_industrial_buffer"],"Industrial-area thermal anomaly",
      np.where(out["risk_score"]>=55,"Elevated thermal anomaly","Background / non-industrial candidate"))
    )
    return out.to_crs(4326),og

def movement(out):
    if out.empty or "datetime_utc" not in out:return []
    d=out.copy();d["datetime_utc"]=pd.to_datetime(d["datetime_utc"],errors="coerce")
    d=d.dropna(subset=["datetime_utc"])
    if d.empty:return []
    d["group"]=d["nearest_name"].fillna("Unassigned")
    paths=[]
    for group,g in d.groupby("group"):
        g=g.sort_values("datetime_utc")
        if len(g)<2:continue
        gs=gpd.GeoSeries(g.geometry,crs=4326).to_crs(32643)
        line=LineString([(p.x,p.y) for p in gs])
        km=line.length/1000
        hours=max((g["datetime_utc"].iloc[-1]-g["datetime_utc"].iloc[0]).total_seconds()/3600,.001)
        line4326=gpd.GeoSeries([line],crs=32643).to_crs(4326).iloc[0]
        paths.append({"group":group,"points":len(g),"distance_km":km,"speed_kmh":km/hours,
                      "coordinates":[[y,x] for x,y in line4326.coords]})
    return paths

def clean_events(out):
    if out.empty:return []
    df=pd.DataFrame(out.drop(columns=["geometry"],errors="ignore")).copy()
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            df[c]=df[c].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            df[c]=df[c].apply(lambda x: None if pd.isna(x) else (x.item() if hasattr(x,"item") else x))
    return df.to_dict(orient="records")

@app.get("/api/auto")
def auto(source: str = "VIIRS_NOAA21_NRT", days: int = 2, buffer_km: float = 5):
    try:
        if FIRMS_KEY:
            firms = fetch_firms(FIRMS_KEY, source, days)
            try:
                fac = fetch_osm()
            except Exception:
                fac = demo_facilities()
            out, og = analyze(firms, fac, buffer_km)
            return {"mode":"LIVE", "events":clean_events(out), "facilities":fac, "paths":movement(out)}
        fac = demo_facilities()
        out, og = analyze(demo_data(), fac, buffer_km)
        return {"mode":"DEMO", "events":clean_events(out), "facilities":fac, "paths":movement(out)}
    except Exception as e:
        fac = demo_facilities()
        out, og = analyze(demo_data(), fac, buffer_km)
        return {"mode":"DEMO_FALLBACK", "error":str(e), "events":clean_events(out), "facilities":fac, "paths":movement(out)}

@app.get("/api/demo")
def demo():
    firms=demo_data();fac=demo_facilities()
    out,og=analyze(firms,fac,5)
    return {"events":clean_events(out),"facilities":fac,"paths":movement(out)}

@app.get("/api/analyze")
def analyze_api(map_key:str,source:str="VIIRS_SNPP_NRT",days:int=2,buffer_km:float=5):
    if not map_key:raise HTTPException(400,"NASA FIRMS MAP_KEY is required")
    try:
        firms=fetch_firms(map_key,source,days)
        fac=fetch_osm()
        out,og=analyze(firms,fac,buffer_km)
        return {"events":clean_events(out),"facilities":fac,"paths":movement(out)}
    except requests.RequestException as e:
        raise HTTPException(502,f"External API request failed: {e}")
    except Exception as e:
        raise HTTPException(500,str(e))
