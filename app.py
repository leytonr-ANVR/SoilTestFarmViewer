import io
import json
import re
import zipfile
from pathlib import Path

import folium
import numpy as np
import pandas as pd
import streamlit as st
from folium.features import GeoJson, GeoJsonTooltip
from shapely.geometry import shape, mapping, Point
from streamlit_folium import st_folium

st.set_page_config(page_title="Farm Soil Nutrient Mapper", page_icon="🌱", layout="wide")

# ---------- Styling ----------
st.markdown(
    """
    <style>
    .block-container {padding-top: 1.2rem; padding-bottom: 2rem;}
    .metric-card {border:1px solid rgba(128,128,128,.25); border-radius:14px; padding:12px 14px; background:rgba(128,128,128,.04);}
    .small-note {font-size:.85rem; opacity:.75;}
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------- Nutrient definitions ----------
NUTRIENTS = {
    "pH (CaCl₂)": {"field": "ph", "unit": "", "low": 5.5, "high": 6.5, "aliases": ["pH (1:5 CaCl2)", "pH CaCl2", "pH (CaCl2)"]},
    "Phosphorus (Colwell)": {"field": "p", "unit": "mg/kg", "low": 20, "high": 45, "aliases": ["Phosphorus (Colwell)", "Colwell P"]},
    "Potassium (Amm-acet.)": {"field": "k", "unit": "cmol(+)/kg", "low": 0.30, "high": 0.65, "aliases": ["Potassium (Amm-acet.)", "Exchangeable Potassium"]},
    "Sulphur (KCl40)": {"field": "s", "unit": "mg/kg", "low": 6, "high": 12, "aliases": ["Sulphur (KCl40)", "Sulfur (KCl40)"]},
    "Organic Carbon (W&B)": {"field": "oc", "unit": "%", "low": 1.0, "high": 2.0, "aliases": ["Organic Carbon (W&B)", "Organic Carbon"]},
    "CEC": {"field": "cec", "unit": "cmol(+)/kg", "low": 8, "high": 18, "aliases": ["Cation Exch. Cap.", "CEC"]},
    "Zinc (DTPA)": {"field": "zn", "unit": "mg/kg", "low": 0.8, "high": 2.0, "aliases": ["Zinc (DTPA)", "DTPA Zinc"]},
}

STATUS_COLORS = {
    "Very low": "#7f0000",
    "Low": "#d73027",
    "Adequate": "#1a9850",
    "High": "#4575b4",
    "Very high": "#313695",
    "No data": "#999999",
}


def clean_numeric(v, treat_248=True, less_than_mode="half"):
    if pd.isna(v):
        return np.nan
    if isinstance(v, (int, float, np.integer, np.floating)):
        val = float(v)
    else:
        s = str(v).strip()
        if not s:
            return np.nan
        if s.startswith("<"):
            try:
                val = float(re.sub(r"[^0-9.\-]", "", s))
                if less_than_mode == "half":
                    val /= 2
                elif less_than_mode == "zero":
                    val = 0.0
            except Exception:
                return np.nan
        else:
            try:
                val = float(re.sub(r"[^0-9.\-]", "", s))
            except Exception:
                return np.nan
    if treat_248 and val == 248:
        return np.nan
    return val


def status_for(v, low, high):
    if pd.isna(v):
        return "No data"
    spread = (high - low) * 0.55
    if v < low - spread:
        return "Very low"
    if v < low:
        return "Low"
    if v <= high:
        return "Adequate"
    if v <= high + spread:
        return "High"
    return "Very high"


def unique_headers(headers, units=None):
    seen = {}
    out = []
    for i, h in enumerate(headers):
        base = str(h).strip() if h not in (None, "") else f"Column {i+1}"
        seen[base] = seen.get(base, 0) + 1
        label = base if seen[base] == 1 else f"{base} [{seen[base]}]"
        unit = ""
        if units is not None and i < len(units) and units[i] not in (None, "") and not pd.isna(units[i]):
            unit = str(units[i]).strip()
        out.append(f"{label} — {unit}" if unit else label)
    return out


def read_lab_excel(uploaded):
    xl = pd.ExcelFile(uploaded)
    default_sheet = "Soil Samples Result" if "Soil Samples Result" in xl.sheet_names else xl.sheet_names[0]
    raw = pd.read_excel(uploaded, sheet_name=default_sheet, header=None, dtype=object)
    return xl.sheet_names, default_sheet, raw


def auto_find(options, aliases):
    normalized = [(o, o.lower()) for o in options]
    for alias in aliases:
        a = alias.lower()
        for original, lower in normalized:
            if lower == a or lower.startswith(a + " —") or lower.startswith(a + " ["):
                return original
        for original, lower in normalized:
            if a in lower:
                return original
    return None


def read_boundaries(uploaded):
    name = uploaded.name.lower()
    data = uploaded.getvalue()
    if name.endswith((".geojson", ".json")):
        return json.loads(data.decode("utf-8-sig"))
    if name.endswith(".zip"):
        try:
            import geopandas as gpd
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                zpath = Path(td) / "boundaries.zip"
                zpath.write_bytes(data)
                with zipfile.ZipFile(zpath) as zf:
                    zf.extractall(Path(td) / "shape")
                shp = next((Path(td) / "shape").rglob("*.shp"), None)
                if shp is None:
                    raise ValueError("ZIP does not contain a .shp file")
                gdf = gpd.read_file(shp)
                if gdf.crs is None:
                    raise ValueError("Shapefile has no CRS. Define a coordinate reference system before importing.")
                gdf = gdf.to_crs(4326)
                return json.loads(gdf.to_json())
        except Exception as e:
            raise ValueError(f"Could not read shapefile ZIP: {e}")
    raise ValueError("Supported boundary formats are GeoJSON/JSON and zipped Shapefile.")


def polygon_centroid_feature(feature):
    try:
        geom = shape(feature["geometry"])
        c = geom.representative_point()
        return c.y, c.x
    except Exception:
        return None


def feature_name(feature):
    props = feature.get("properties", {}) or {}
    for key in ["Paddock Name", "Paddock", "paddock", "PADDOCK", "Name", "NAME", "name"]:
        if key in props and props[key] not in (None, ""):
            return str(props[key])
    return "Unnamed paddock"

# ---------- State ----------
for key, value in {
    "soil_df": None,
    "boundaries": None,
    "mapping_done": False,
}.items():
    if key not in st.session_state:
        st.session_state[key] = value

# ---------- Header ----------
st.title("🌱 Farm Soil Nutrient Mapper")
st.caption("Import paddock boundaries and laboratory soil-test spreadsheets, then map and analyse nutrient status across the farm.")

with st.sidebar:
    st.header("Import data")
    boundary_file = st.file_uploader("Paddock boundaries", type=["zip", "geojson", "json"], help="Upload a zipped ESRI Shapefile or GeoJSON.")
    soil_file = st.file_uploader("Soil test Excel", type=["xlsx", "xls"], help="Built for the Soil Samples Result export format you supplied.")
    st.divider()
    st.header("Map")
    basemap = st.selectbox("Basemap", ["Satellite", "Street", "Light"], index=0)
    nutrient_overlay_opacity = st.slider("Nutrient overlay opacity", 0.15, 1.0, 0.65, 0.05)
    show_samples = st.checkbox("Show sample points", True)
    show_labels = st.checkbox("Show paddock labels", True)

if boundary_file is not None:
    try:
        st.session_state.boundaries = read_boundaries(boundary_file)
    except Exception as e:
        st.sidebar.error(str(e))

# ---------- Excel import wizard ----------
if soil_file is not None:
    st.subheader("Excel Import Wizard")
    try:
        sheets = pd.ExcelFile(soil_file).sheet_names
        sheet_default = sheets.index("Soil Samples Result") if "Soil Samples Result" in sheets else 0
        sheet = st.selectbox("Worksheet", sheets, index=sheet_default)
        raw = pd.read_excel(soil_file, sheet_name=sheet, header=None, dtype=object)

        c1, c2, c3 = st.columns(3)
        header_row = c1.number_input("Header row", min_value=1, max_value=max(1, len(raw)), value=1, step=1)
        units_row = c2.number_input("Units row", min_value=1, max_value=max(1, len(raw)), value=2, step=1)
        data_row = c3.number_input("First data row", min_value=1, max_value=max(1, len(raw)), value=3, step=1)

        headers = raw.iloc[int(header_row)-1].tolist()
        units = raw.iloc[int(units_row)-1].tolist() if int(units_row)-1 < len(raw) else [None] * len(headers)
        options = unique_headers(headers, units)
        option_to_idx = {opt: idx for idx, opt in enumerate(options)}

        st.markdown("#### Sample / location fields")
        left, right = st.columns(2)
        sample_id_opt = left.selectbox("Sample ID", options, index=option_to_idx.get(auto_find(options, ["Sample ID"]), 0))
        paddock_guess = auto_find(options, ["Paddock Name", "Paddock"])
        paddock_opt = left.selectbox("Paddock", ["— None —"] + options, index=(["— None —"] + options).index(paddock_guess) if paddock_guess else 0)
        date_guess = auto_find(options, ["Sampling Date"])
        date_opt = left.selectbox("Sampling Date", ["— None —"] + options, index=(["— None —"] + options).index(date_guess) if date_guess else 0)
        depth_from_guess = auto_find(options, ["Sample Depth From"])
        depth_from_opt = right.selectbox("Depth From", ["— None —"] + options, index=(["— None —"] + options).index(depth_from_guess) if depth_from_guess else 0)
        depth_to_guess = auto_find(options, ["Sample Depth To"])
        depth_to_opt = right.selectbox("Depth To", ["— None —"] + options, index=(["— None —"] + options).index(depth_to_guess) if depth_to_guess else 0)
        lat_guess = auto_find(options, ["Latitude"])
        lon_guess = auto_find(options, ["Longitude"])
        lat_opt = right.selectbox("Latitude", ["— None —"] + options, index=(["— None —"] + options).index(lat_guess) if lat_guess else 0)
        lon_opt = right.selectbox("Longitude", ["— None —"] + options, index=(["— None —"] + options).index(lon_guess) if lon_guess else 0)

        st.markdown("#### Nutrient fields")
        nutrient_mapping = {}
        cols = st.columns(2)
        for i, (label, meta) in enumerate(NUTRIENTS.items()):
            guess = auto_find(options, meta["aliases"])
            select_opts = ["— None —"] + options
            idx = select_opts.index(guess) if guess in select_opts else 0
            nutrient_mapping[label] = cols[i % 2].selectbox(label, select_opts, index=idx, key=f"map_{meta['field']}")

        st.markdown("#### Result handling")
        o1, o2 = st.columns(2)
        treat_248 = o1.checkbox("Treat 248 as missing/no result", True, help="Your supplied export contains many 248 placeholders.")
        less_than_mode = o2.selectbox("Values like <10", ["Use half detection limit", "Use detection limit", "Use zero"], index=0)
        less_mode_key = {"Use half detection limit": "half", "Use detection limit": "limit", "Use zero": "zero"}[less_than_mode]

        with st.expander("Preview source sheet"):
            st.dataframe(raw.head(8), use_container_width=True)

        if st.button("Import soil results", type="primary"):
            data = raw.iloc[int(data_row)-1:].copy().reset_index(drop=True)
            result = pd.DataFrame()
            result["sample_id"] = data.iloc[:, option_to_idx[sample_id_opt]].astype(str).str.strip()
            def pull(opt):
                if opt == "— None —":
                    return pd.Series([np.nan] * len(data))
                return data.iloc[:, option_to_idx[opt]]
            result["paddock"] = pull(paddock_opt).astype(str).replace("nan", np.nan)
            result["sampling_date"] = pd.to_datetime(pull(date_opt), dayfirst=True, errors="coerce")
            result["depth_from"] = pd.to_numeric(pull(depth_from_opt), errors="coerce")
            result["depth_to"] = pd.to_numeric(pull(depth_to_opt), errors="coerce")
            result["latitude"] = pd.to_numeric(pull(lat_opt), errors="coerce")
            result["longitude"] = pd.to_numeric(pull(lon_opt), errors="coerce")
            for label, opt in nutrient_mapping.items():
                field = NUTRIENTS[label]["field"]
                series = pull(opt)
                result[field] = series.map(lambda v: clean_numeric(v, treat_248=treat_248, less_than_mode=less_mode_key))
            result = result[result["sample_id"].notna() & (result["sample_id"].astype(str).str.strip() != "")]
            st.session_state.soil_df = result
            st.session_state.mapping_done = True
            st.success(f"Imported {len(result):,} soil samples.")
    except Exception as e:
        st.error(f"Could not read the Excel workbook: {e}")

# ---------- Analysis controls ----------
soil = st.session_state.soil_df
boundaries = st.session_state.boundaries

if soil is None:
    st.info("Upload your laboratory Excel sheet and complete the Import Wizard to begin. You can upload paddock boundaries before or after the soil results.")
    st.stop()

st.divider()
control1, control2, control3 = st.columns([1.4, 1, 1])
nutrient_label = control1.selectbox("Nutrient layer", list(NUTRIENTS.keys()))
meta = NUTRIENTS[nutrient_label]
field = meta["field"]

years = sorted([int(y) for y in soil["sampling_date"].dropna().dt.year.unique()], reverse=True)
year_choice = control2.selectbox("Sampling year", ["All years"] + years)

depths = soil[["depth_from", "depth_to"]].drop_duplicates().dropna(how="all")
depth_labels = [f"{r.depth_from:g}–{r.depth_to:g} cm" for _, r in depths.iterrows() if pd.notna(r.depth_from) and pd.notna(r.depth_to)]
depth_choice = control3.selectbox("Depth", ["All depths"] + depth_labels)

filtered = soil.copy()
if year_choice != "All years":
    filtered = filtered[filtered["sampling_date"].dt.year == int(year_choice)]
if depth_choice != "All depths":
    a, b = depth_choice.replace(" cm", "").split("–")
    filtered = filtered[(filtered["depth_from"] == float(a)) & (filtered["depth_to"] == float(b))]

st.markdown("#### Interpretation thresholds")
t1, t2 = st.columns(2)
low = t1.number_input("Low below", value=float(meta["low"]), step=0.1)
high = t2.number_input("High above", value=float(meta["high"]), step=0.1)

valid = filtered[field].dropna()
avg = valid.mean() if len(valid) else np.nan
low_alerts = int((valid < low).sum()) if len(valid) else 0

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Samples", f"{len(filtered):,}")
m2.metric(f"Average {nutrient_label}", "—" if pd.isna(avg) else f"{avg:.2f} {meta['unit']}".strip())
m3.metric("Low samples", low_alerts)
m4.metric("Paddocks", filtered["paddock"].dropna().nunique())
m5.metric("GPS samples", int(filtered[["latitude", "longitude"]].dropna().shape[0]))

# ---------- Map ----------
st.subheader("Farm Map")

if basemap == "Satellite":
    tiles = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
    attr = "Esri World Imagery"
elif basemap == "Street":
    tiles = "OpenStreetMap"
    attr = "OpenStreetMap"
else:
    tiles = "CartoDB positron"
    attr = "CartoDB"

latlon = filtered[["latitude", "longitude"]].dropna()
if not latlon.empty:
    center = [latlon["latitude"].mean(), latlon["longitude"].mean()]
elif boundaries and boundaries.get("features"):
    centers = [polygon_centroid_feature(f) for f in boundaries["features"]]
    centers = [c for c in centers if c]
    center = [sum(c[0] for c in centers)/len(centers), sum(c[1] for c in centers)/len(centers)] if centers else [-24.4, 150.5]
else:
    center = [-24.4, 150.5]

m = folium.Map(location=center, zoom_start=13 if (not latlon.empty or boundaries) else 5, tiles=None, control_scale=True)
folium.TileLayer(tiles=tiles, attr=attr, name=basemap, overlay=False, control=True).add_to(m)

paddock_stats = filtered.groupby("paddock", dropna=True)[field].agg(["mean", "count", "min", "max"]).reset_index() if "paddock" in filtered else pd.DataFrame()
stat_lookup = {str(r.paddock): r for _, r in paddock_stats.iterrows()}

if boundaries and boundaries.get("features"):
    enriched = json.loads(json.dumps(boundaries))
    for f in enriched["features"]:
        name = feature_name(f)
        stats = stat_lookup.get(name)
        value = float(stats["mean"]) if stats is not None and pd.notna(stats["mean"]) else np.nan
        status = status_for(value, low, high)
        f.setdefault("properties", {})["_paddock"] = name
        f["properties"]["_value"] = None if pd.isna(value) else round(value, 3)
        f["properties"]["_status"] = status
        f["properties"]["_samples"] = 0 if stats is None else int(stats["count"])
    def style_fn(feature):
        status = feature.get("properties", {}).get("_status", "No data")
        return {"fillColor": STATUS_COLORS.get(status, "#999999"), "color": "#ffffff", "weight": 2, "fillOpacity": nutrient_overlay_opacity}
    gj = GeoJson(
        enriched,
        name="Paddock nutrient status",
        style_function=style_fn,
        tooltip=GeoJsonTooltip(fields=["_paddock", "_value", "_status", "_samples"], aliases=["Paddock", nutrient_label, "Status", "Samples"]),
    )
    gj.add_to(m)
    if show_labels:
        for f in enriched["features"]:
            c = polygon_centroid_feature(f)
            if c:
                folium.Marker(c, icon=folium.DivIcon(html=f'<div style="font-weight:700;color:white;text-shadow:0 1px 3px #000;white-space:nowrap">{feature_name(f)}</div>')).add_to(m)

if show_samples:
    for _, r in filtered.dropna(subset=["latitude", "longitude"]).iterrows():
        value = r.get(field, np.nan)
        status = status_for(value, low, high)
        popup = f"<b>{r.get('sample_id','')}</b><br>Paddock: {r.get('paddock','')}<br>{nutrient_label}: {'—' if pd.isna(value) else round(float(value),3)} {meta['unit']}<br>Status: {status}"
        folium.CircleMarker(
            [r["latitude"], r["longitude"]], radius=5, color="#111111", weight=1,
            fill=True, fill_color=STATUS_COLORS[status], fill_opacity=0.95, popup=popup,
        ).add_to(m)

folium.LayerControl(collapsed=False).add_to(m)
st_folium(m, use_container_width=True, height=610, returned_objects=[])

if latlon.empty:
    st.warning("The imported soil sheet has no usable Latitude/Longitude values for the current filter. Sample points cannot be placed on the satellite map until GPS coordinates are present. Paddock polygons can still be displayed if you upload georeferenced boundaries.")

# ---------- Summary ----------
st.subheader("Paddock Nutrient Summary")
if paddock_stats.empty:
    st.info("No paddock names were available in the filtered data.")
else:
    out = paddock_stats.rename(columns={"paddock": "Paddock", "mean": "Average", "count": "Samples", "min": "Minimum", "max": "Maximum"})
    out["Status"] = out["Average"].map(lambda v: status_for(v, low, high))
    out = out[["Paddock", "Samples", "Average", "Minimum", "Maximum", "Status"]].sort_values("Average")
    st.dataframe(out, use_container_width=True, hide_index=True)

st.subheader("Imported Soil Data")
st.dataframe(filtered, use_container_width=True, hide_index=True)

csv = filtered.to_csv(index=False).encode("utf-8")
st.download_button("Download filtered data as CSV", data=csv, file_name="filtered_soil_results.csv", mime="text/csv")
