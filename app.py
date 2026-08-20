import io
import json
import re
import zipfile
from pathlib import Path

import folium
import numpy as np
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPolygon, Patch
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak
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
    "Nitrate Nitrogen": {"field": "nitrate", "unit": "mg/kg", "low": 10.0, "high": 30.0, "aliases": ["Nitrate Nitrogen", "Nitrate Nitrogen (NO3)"]},
    "ESP": {"field": "esp", "unit": "%", "low": 3.0, "high": 6.0, "aliases": ["Sodium % of Cations (ESP)", "ESP", "Exchangeable Sodium Percentage"]},
    "Chloride": {"field": "chloride", "unit": "mg/kg", "low": 50.0, "high": 150.0, "aliases": ["Chloride"]},
    "EC (1:5 water)": {"field": "ec", "unit": "dS/m", "low": 0.2, "high": 0.8, "aliases": ["Electrical Conductivity (1:5 water)", "Electrical Conductivity (1:5)", "EC (1:5)"]},
    "ECse (Saturated Extract)": {"field": "ecse", "unit": "dS/m", "low": 2.0, "high": 4.0, "aliases": ["Elec. Cond. (Sat. Ext.)", "Elect. Conductivity on Sat. Extract", "Electrical Conductivity (Sat. Ext.)", "ECse"]},
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


def pdf_safe_text(value):
    text = str(value)
    replacements = {
        "₂": "2", "₃": "3", "₄": "4", "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9", "₀": "0",
        "–": "-", "—": "-", "−": "-", "≤": "<=", "≥": ">=", "µ": "u", "°": " deg ",
    }
    for a, b in replacements.items():
        text = text.replace(a, b)
    return text


def _iter_polygon_rings(geom):
    """Yield exterior XY coordinates from Polygon/MultiPolygon geometry."""
    if geom is None or geom.is_empty:
        return
    if geom.geom_type == "Polygon":
        yield list(geom.exterior.coords)
    elif geom.geom_type == "MultiPolygon":
        for part in geom.geoms:
            yield list(part.exterior.coords)


def make_static_map_png(filtered, boundaries, field, nutrient_label, unit, low, high, show_samples=True, show_labels=True):
    """Create a report-friendly static map image without requiring a browser screenshot."""
    fig, ax = plt.subplots(figsize=(10.5, 6.2))
    ax.set_facecolor("#f4f4f4")
    plotted = False

    paddock_stats = (
        filtered.groupby("paddock", dropna=True)[field].agg(["mean", "count"]).reset_index()
        if "paddock" in filtered.columns else pd.DataFrame()
    )
    stat_lookup = {str(r.paddock): r for _, r in paddock_stats.iterrows()}

    if boundaries and boundaries.get("features"):
        for feature in boundaries["features"]:
            try:
                geom = shape(feature.get("geometry"))
            except Exception:
                continue
            name = feature_name(feature)
            stats = stat_lookup.get(name)
            value = float(stats["mean"]) if stats is not None and pd.notna(stats["mean"]) else np.nan
            status = status_for(value, low, high)
            for coords in _iter_polygon_rings(geom):
                patch = MplPolygon(coords, closed=True, facecolor=STATUS_COLORS.get(status, "#999999"), edgecolor="white", linewidth=1.2, alpha=0.72)
                ax.add_patch(patch)
                plotted = True
            if show_labels and not geom.is_empty:
                c = geom.representative_point()
                ax.text(c.x, c.y, name, fontsize=7.5, fontweight="bold", ha="center", va="center", color="black",
                        bbox=dict(boxstyle="round,pad=0.15", facecolor="white", edgecolor="none", alpha=0.65))

    gps = filtered.dropna(subset=["latitude", "longitude"]) if {"latitude", "longitude"}.issubset(filtered.columns) else pd.DataFrame()
    if show_samples and not gps.empty:
        sample_colors = [STATUS_COLORS.get(status_for(v, low, high), "#999999") for v in gps[field]]
        ax.scatter(gps["longitude"], gps["latitude"], c=sample_colors, edgecolors="black", linewidths=0.45, s=22, zorder=5)
        plotted = True

    if plotted:
        ax.autoscale_view()
        ax.margins(0.06)
        ax.set_aspect("equal", adjustable="datalim")
    else:
        ax.text(0.5, 0.5, "No georeferenced paddock boundaries or GPS sample points available", ha="center", va="center", transform=ax.transAxes, fontsize=12)
        ax.set_xlim(0, 1); ax.set_ylim(0, 1)

    ax.set_title(f"{nutrient_label} nutrient map", fontsize=14, fontweight="bold")
    ax.set_xlabel("Longitude / map X")
    ax.set_ylabel("Latitude / map Y")
    legend_items = [Patch(facecolor=STATUS_COLORS[k], label=k) for k in ["Very low", "Low", "Adequate", "High", "Very high", "No data"]]
    ax.legend(handles=legend_items, loc="lower center", bbox_to_anchor=(0.5, -0.18), ncol=6, frameon=False, fontsize=8)
    ax.grid(alpha=0.15, linewidth=0.5)
    fig.tight_layout(rect=[0, 0.06, 1, 1])
    bio = io.BytesIO()
    fig.savefig(bio, format="png", dpi=180, bbox_inches="tight")
    plt.close(fig)
    bio.seek(0)
    return bio


def build_yoy_tables(data, field):
    """Return annual whole-farm and paddock year-on-year summaries for one nutrient."""
    if data is None or data.empty or "sampling_date" not in data.columns or field not in data.columns:
        return pd.DataFrame(), pd.DataFrame()
    work = data.copy()
    work = work[work["sampling_date"].notna() & work[field].notna()].copy()
    if work.empty:
        return pd.DataFrame(), pd.DataFrame()
    work["year"] = work["sampling_date"].dt.year.astype(int)
    annual = work.groupby("year")[field].agg(["mean", "count", "min", "max"]).reset_index().sort_values("year")
    if "paddock" in work.columns:
        paddock = work.dropna(subset=["paddock"]).groupby(["year", "paddock"])[field].agg(["mean", "count"]).reset_index()
    else:
        paddock = pd.DataFrame()
    return annual, paddock


def comparison_table(paddock_yearly, older_year, newer_year):
    if paddock_yearly is None or paddock_yearly.empty:
        return pd.DataFrame()
    old = paddock_yearly[paddock_yearly["year"] == older_year][["paddock", "mean", "count"]].rename(columns={"mean":"Older", "count":"Older samples"})
    new = paddock_yearly[paddock_yearly["year"] == newer_year][["paddock", "mean", "count"]].rename(columns={"mean":"Newer", "count":"Newer samples"})
    out = old.merge(new, on="paddock", how="outer")
    out["Change"] = out["Newer"] - out["Older"]
    out["Change %"] = np.where(out["Older"].notna() & (out["Older"] != 0), out["Change"] / out["Older"] * 100, np.nan)
    return out.sort_values("Change", na_position="last")


def mgkg_to_kgha(value, bulk_density, depth_cm):
    """Convert a soil concentration in mg/kg to kg/ha for a soil layer."""
    if pd.isna(value) or pd.isna(depth_cm) or float(depth_cm) <= 0:
        return np.nan
    return float(value) * float(bulk_density) * float(depth_cm) * 0.1


def build_mass_conversion(df, field, bulk_density):
    """Calculate kg/ha row-by-row using each test's depth_to - depth_from thickness."""
    needed = {field, "depth_from", "depth_to"}
    if df is None or df.empty or not needed.issubset(df.columns):
        return pd.DataFrame()
    cols = [c for c in ["sample_id", "paddock", "sampling_date", "latitude", "longitude", "depth_from", "depth_to", field] if c in df.columns]
    work = df[cols].copy()
    work[field] = pd.to_numeric(work[field], errors="coerce")
    work["depth_from"] = pd.to_numeric(work["depth_from"], errors="coerce")
    work["depth_to"] = pd.to_numeric(work["depth_to"], errors="coerce")
    work["layer_thickness_cm"] = work["depth_to"] - work["depth_from"]
    work.loc[work["layer_thickness_cm"] <= 0, "layer_thickness_cm"] = np.nan
    work["soil_mass_t_ha"] = float(bulk_density) * work["layer_thickness_cm"] * 100.0
    work["kg_ha"] = work.apply(lambda r: mgkg_to_kgha(r[field], bulk_density, r["layer_thickness_cm"]), axis=1)
    return work


def build_profile_totals(df, field, bulk_density):
    """Sum row-level kg/ha across all sampled depths for each sampling location on each day.

    GPS coordinates are preferred for defining a sampling location. If GPS is missing,
    sample_id is used as the fallback profile identifier. This prevents separate sites in
    the same paddock/date from being added together.
    """
    layers = build_mass_conversion(df, field, bulk_density)
    if layers.empty:
        return pd.DataFrame(), layers
    layers = layers.dropna(subset=["kg_ha", "sampling_date"]).copy()
    if layers.empty:
        return pd.DataFrame(), layers
    layers["sample_day"] = pd.to_datetime(layers["sampling_date"], errors="coerce").dt.normalize()
    layers["lat_key"] = pd.to_numeric(layers.get("latitude"), errors="coerce").round(6) if "latitude" in layers else np.nan
    layers["lon_key"] = pd.to_numeric(layers.get("longitude"), errors="coerce").round(6) if "longitude" in layers else np.nan

    def profile_key(r):
        if pd.notna(r.get("lat_key")) and pd.notna(r.get("lon_key")):
            return f"gps:{r['lat_key']:.6f},{r['lon_key']:.6f}"
        sid = str(r.get("sample_id", "")).strip()
        if sid and sid.lower() != "nan":
            return f"id:{sid}"
        return None

    layers["profile_key"] = layers.apply(profile_key, axis=1)
    layers = layers[layers["profile_key"].notna()].copy()
    if layers.empty:
        return pd.DataFrame(), layers

    group_cols = ["sample_day", "profile_key"]
    if "paddock" in layers.columns:
        group_cols.insert(1, "paddock")
    agg = {
        "kg_ha": "sum",
        "layer_thickness_cm": "sum",
        "depth_from": "min",
        "depth_to": "max",
        field: "mean",
    }
    for c in ["latitude", "longitude"]:
        if c in layers.columns:
            agg[c] = "mean"
    if "sample_id" in layers.columns:
        agg["sample_id"] = lambda x: ", ".join(dict.fromkeys(str(v) for v in x if pd.notna(v)))
    profiles = layers.groupby(group_cols, dropna=False).agg(agg).reset_index()
    profiles = profiles.rename(columns={
        "kg_ha": "total_kg_ha",
        "layer_thickness_cm": "total_sampled_depth_cm",
        "depth_from": "profile_depth_from",
        "depth_to": "profile_depth_to",
    })
    layer_counts = layers.groupby(group_cols, dropna=False).size().reset_index(name="depth_layers")
    profiles = profiles.merge(layer_counts, on=group_cols, how="left")
    return profiles, layers


MGKG_NUTRIENTS = {label: meta for label, meta in NUTRIENTS.items() if meta.get("unit") == "mg/kg"}


def generate_pdf_report(filtered, boundaries, nutrient_label, meta, field, low, high, year_choice, depth_choice, show_samples, show_labels, report_title, include_sample_rows=True, yoy_source=None, bulk_density=None):
    """Generate a landscape A4 PDF containing the map and current filtered data."""
    buf = io.BytesIO()
    report_title_pdf = pdf_safe_text(report_title)
    nutrient_label_pdf = pdf_safe_text(nutrient_label)
    year_pdf = pdf_safe_text(year_choice)
    depth_pdf = pdf_safe_text(depth_choice)
    unit_pdf = pdf_safe_text(meta["unit"])
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4), rightMargin=12*mm, leftMargin=12*mm, topMargin=12*mm, bottomMargin=12*mm,
        title=report_title_pdf, author="Farm Soil Nutrient Mapper"
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("ReportTitle", parent=styles["Title"], fontSize=19, leading=22, spaceAfter=5, alignment=TA_CENTER)
    small = ParagraphStyle("Small", parent=styles["BodyText"], fontSize=8, leading=10)
    heading = ParagraphStyle("Heading", parent=styles["Heading2"], fontSize=12, leading=14, spaceBefore=5, spaceAfter=5)
    story = [Paragraph(report_title_pdf, title_style)]
    story.append(Paragraph(
        f"Nutrient: <b>{nutrient_label_pdf}</b> &nbsp;&nbsp; | &nbsp;&nbsp; Year: <b>{year_pdf}</b> &nbsp;&nbsp; | &nbsp;&nbsp; Depth: <b>{depth_pdf}</b> &nbsp;&nbsp; | &nbsp;&nbsp; Thresholds: Low &lt; {low:g}, High &gt; {high:g} {unit_pdf}",
        small
    ))
    story.append(Spacer(1, 5*mm))

    valid = filtered[field].dropna()
    avg = valid.mean() if len(valid) else np.nan
    low_samples = int((valid < low).sum()) if len(valid) else 0
    gps_count = int(filtered[["latitude", "longitude"]].dropna().shape[0]) if {"latitude", "longitude"}.issubset(filtered.columns) else 0
    metrics = [
        ["Samples", "Average", "Low samples", "Paddocks", "GPS samples"],
        [f"{len(filtered):,}", "-" if pd.isna(avg) else f"{avg:.2f} {unit_pdf}", str(low_samples), str(filtered["paddock"].dropna().nunique()), str(gps_count)]
    ]
    mt = Table(metrics, colWidths=[48*mm]*5, rowHeights=[7*mm, 9*mm])
    mt.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#E9F3EC")),
        ("TEXTCOLOR", (0,0), (-1,-1), colors.HexColor("#1D3325")),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTNAME", (0,1), (-1,1), "Helvetica-Bold"),
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("BOX", (0,0), (-1,-1), 0.5, colors.HexColor("#B7CDBE")),
        ("INNERGRID", (0,0), (-1,-1), 0.25, colors.HexColor("#D5E2D8")),
    ]))
    story.append(mt)
    story.append(Spacer(1, 5*mm))

    conversion_active = meta.get("unit") == "mg/kg" and bulk_density is not None and {"depth_from", "depth_to"}.issubset(filtered.columns)
    conversion_work = build_mass_conversion(filtered, field, bulk_density) if conversion_active else pd.DataFrame()
    conversion_valid = conversion_work.dropna(subset=["kg_ha"]) if not conversion_work.empty else pd.DataFrame()
    if conversion_active and not conversion_valid.empty:
        avg_kgha = conversion_valid["kg_ha"].mean()
        story.append(Paragraph(
            f"Mass conversion: <b>{avg_kgha:.1f} kg/ha</b> mean sample nutrient mass for {nutrient_label_pdf}, using bulk density <b>{bulk_density:.2f} g/cm3</b>. Each test uses its own sampled layer thickness: <b>depth_to - depth_from</b>. Formula: kg/ha = mg/kg x bulk density x layer thickness x 0.1.",
            small
        ))
        story.append(Spacer(1, 3*mm))

    map_png = make_static_map_png(filtered, boundaries, field, nutrient_label, meta["unit"], low, high, show_samples, show_labels)
    story.append(Image(map_png, width=245*mm, height=133*mm))
    story.append(Paragraph("Map colours use the current editable thresholds. The PDF map is a report rendering of the paddock polygons and GPS sample points; the interactive satellite basemap remains available in the Streamlit map.", small))

    paddock_stats = filtered.groupby("paddock", dropna=True)[field].agg(["mean", "count", "min", "max"]).reset_index() if "paddock" in filtered.columns else pd.DataFrame()
    story.append(PageBreak())
    story.append(Paragraph("Paddock Nutrient Summary", heading))
    if paddock_stats.empty:
        story.append(Paragraph("No paddock names were available for the current filter.", small))
    else:
        if conversion_active and not conversion_valid.empty:
            paddock_mass = conversion_valid.groupby("paddock", dropna=True)["kg_ha"].agg(["mean", "min", "max"]).reset_index().rename(columns={"mean":"mass_mean", "min":"mass_min", "max":"mass_max"}) if "paddock" in conversion_valid.columns else pd.DataFrame()
            paddock_pdf = paddock_stats.merge(paddock_mass, on="paddock", how="left") if not paddock_mass.empty else paddock_stats.copy()
            rows = [["Paddock", "Samples", f"Average ({unit_pdf})", "Mean kg/ha", "Minimum", "Maximum", "Status"]]
            for _, r in paddock_pdf.sort_values("mean").iterrows():
                mass_text = "-" if pd.isna(r.get("mass_mean", np.nan)) else f"{r['mass_mean']:.1f}"
                rows.append([str(r["paddock"]), int(r["count"]), f"{r['mean']:.3g}", mass_text, f"{r['min']:.3g}", f"{r['max']:.3g}", status_for(r["mean"], low, high)])
            paddock_widths = [48*mm, 22*mm, 31*mm, 34*mm, 27*mm, 27*mm, 31*mm]
        else:
            rows = [["Paddock", "Samples", f"Average ({unit_pdf})" if unit_pdf else "Average", "Minimum", "Maximum", "Status"]]
            for _, r in paddock_stats.sort_values("mean").iterrows():
                rows.append([str(r["paddock"]), int(r["count"]), f"{r['mean']:.3g}", f"{r['min']:.3g}", f"{r['max']:.3g}", status_for(r["mean"], low, high)])
            paddock_widths = [55*mm, 25*mm, 35*mm, 30*mm, 30*mm, 35*mm]
        pt = Table(rows, repeatRows=1, colWidths=paddock_widths)
        pt.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#2E6B45")),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white),
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE", (0,0), (-1,-1), 8),
            ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#CCCCCC")),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#F6F8F6")]),
            ("ALIGN", (1,1), (-2,-1), "RIGHT"),
            ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
            ("TOPPADDING", (0,0), (-1,-1), 4), ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ]))
        story.append(pt)

    annual_yoy, paddock_yoy = build_yoy_tables(yoy_source, field)
    if not annual_yoy.empty and len(annual_yoy) >= 2:
        story.append(Spacer(1, 5*mm))
        story.append(Paragraph("Year-on-Year Analysis", heading))
        yoy_rows = [["Year", "Samples", f"Average ({unit_pdf})" if unit_pdf else "Average", "Minimum", "Maximum", "Change vs prior", "Change %"]]
        prior_mean = None
        for _, r in annual_yoy.iterrows():
            change = np.nan if prior_mean is None else r["mean"] - prior_mean
            pct = np.nan if prior_mean in (None, 0) or pd.isna(prior_mean) else change / prior_mean * 100
            yoy_rows.append([
                int(r["year"]), int(r["count"]), f"{r['mean']:.3g}", f"{r['min']:.3g}", f"{r['max']:.3g}",
                "-" if pd.isna(change) else f"{change:+.3g}", "-" if pd.isna(pct) else f"{pct:+.1f}%"
            ])
            prior_mean = r["mean"]
        yt = Table(yoy_rows, repeatRows=1, colWidths=[24*mm, 25*mm, 34*mm, 28*mm, 28*mm, 34*mm, 30*mm])
        yt.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#2E6B45")),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white),
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE", (0,0), (-1,-1), 8),
            ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#CCCCCC")),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#F6F8F6")]),
            ("ALIGN", (1,1), (-1,-1), "RIGHT"),
        ]))
        story.append(yt)

        years_available = annual_yoy["year"].astype(int).tolist()
        older_year, newer_year = years_available[-2], years_available[-1]
        comp = comparison_table(paddock_yoy, older_year, newer_year)
        if not comp.empty:
            story.append(Spacer(1, 4*mm))
            story.append(Paragraph(f"Paddock change: {older_year} to {newer_year}", heading))
            cr = [["Paddock", str(older_year), str(newer_year), "Change", "Change %", "Samples (old/new)"]]
            for _, r in comp.iterrows():
                cr.append([
                    str(r["paddock"]),
                    "-" if pd.isna(r["Older"]) else f"{r['Older']:.3g}",
                    "-" if pd.isna(r["Newer"]) else f"{r['Newer']:.3g}",
                    "-" if pd.isna(r["Change"]) else f"{r['Change']:+.3g}",
                    "-" if pd.isna(r["Change %"]) else f"{r['Change %']:+.1f}%",
                    f"{0 if pd.isna(r['Older samples']) else int(r['Older samples'])}/{0 if pd.isna(r['Newer samples']) else int(r['Newer samples'])}"
                ])
            ct = Table(cr, repeatRows=1, colWidths=[54*mm, 28*mm, 28*mm, 32*mm, 28*mm, 38*mm])
            ct.setStyle(TableStyle([
                ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#E9F3EC")),
                ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
                ("FONTSIZE", (0,0), (-1,-1), 7.5),
                ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#D0D0D0")),
                ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#F6F8F6")]),
                ("ALIGN", (1,1), (-1,-1), "RIGHT"),
            ]))
            story.append(ct)

    if include_sample_rows:
        story.append(Spacer(1, 5*mm))
        story.append(Paragraph("Soil Sample Data", heading))
        export_cols = [c for c in ["sample_id", "paddock", "sampling_date", "depth_from", "depth_to", field, "latitude", "longitude"] if c in filtered.columns]
        sample = filtered[export_cols].copy()
        if conversion_active and field in sample.columns and {"depth_from", "depth_to"}.issubset(sample.columns):
            sample["layer_thickness_cm"] = pd.to_numeric(sample["depth_to"], errors="coerce") - pd.to_numeric(sample["depth_from"], errors="coerce")
            sample.loc[sample["layer_thickness_cm"] <= 0, "layer_thickness_cm"] = np.nan
            sample["kg_ha"] = sample.apply(lambda r: mgkg_to_kgha(r[field], bulk_density, r["layer_thickness_cm"]), axis=1)
            export_cols = export_cols + ["layer_thickness_cm", "kg_ha"]
        if "sampling_date" in sample.columns:
            sample["sampling_date"] = sample["sampling_date"].dt.strftime("%d/%m/%Y").fillna("")
        sample = sample.head(250)
        headers = [
            {"sample_id":"Sample ID", "paddock":"Paddock", "sampling_date":"Date", "depth_from":"Depth From", "depth_to":"Depth To", "layer_thickness_cm":"Layer cm", field:nutrient_label_pdf, "latitude":"Latitude", "longitude":"Longitude", "kg_ha":"kg/ha"}.get(c,c)
            for c in export_cols
        ]
        rows = [headers] + [["" if pd.isna(v) else (f"{v:.4f}" if isinstance(v, (float, np.floating)) else str(v)) for v in row] for row in sample[export_cols].itertuples(index=False, name=None)]
        widths = ([27*mm, 30*mm, 21*mm, 18*mm, 18*mm, 28*mm, 22*mm, 24*mm, 24*mm, 22*mm] if conversion_active else [32*mm, 35*mm, 25*mm, 22*mm, 22*mm, 38*mm, 30*mm, 30*mm])[:len(export_cols)]
        dt = Table(rows, repeatRows=1, colWidths=widths)
        dt.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#E9F3EC")),
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE", (0,0), (-1,-1), 6.8),
            ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#D0D0D0")),
            ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
            ("TOPPADDING", (0,0), (-1,-1), 3), ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ]))
        story.append(dt)
        if len(filtered) > 250:
            story.append(Paragraph(f"PDF table limited to the first 250 filtered samples ({len(filtered):,} total). Use the CSV download for the complete filtered dataset.", small))

    def add_page_number(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawRightString(landscape(A4)[0]-12*mm, 7*mm, f"Page {doc.page}")
        canvas.drawString(12*mm, 7*mm, "Farm Soil Nutrient Mapper")
        canvas.restoreState()

    doc.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)
    buf.seek(0)
    return buf.getvalue()

# ---------- State ----------
for key, value in {
    "soil_df": None,
    "boundaries": None,
    "mapping_done": False,
    "pdf_report": None,
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

# Keep an all-years dataset at the same selected depth for historical comparisons.
yoy_source = soil.copy()
if depth_choice != "All depths":
    a, b = depth_choice.replace(" cm", "").split("–")
    yoy_source = yoy_source[(yoy_source["depth_from"] == float(a)) & (yoy_source["depth_to"] == float(b))]

st.markdown("#### Interpretation thresholds")
t1, t2 = st.columns(2)
low = t1.number_input("Low below", value=float(meta["low"]), step=0.1)
high = t2.number_input("High above", value=float(meta["high"]), step=0.1)

# Mass-map settings are shared with the Nutrient Mass Conversion section below.
map_value_mode = "Concentration"
bulk_density = float(st.session_state.get("bulk_density", 1.30))
profile_date_choice = "All dates"
profile_totals = pd.DataFrame()
profile_layers = pd.DataFrame()
if meta.get("unit") == "mg/kg":
    st.markdown("#### Interactive map value")
    mv1, mv2, mv3 = st.columns([1.4, 1, 1.2])
    map_value_mode = mv1.radio(
        "Display on map",
        ["Concentration", "Total kg/ha across sampled depths"],
        horizontal=False,
        help="Total kg/ha sums each depth interval sampled at the same location on the same day after converting every layer with depth_to - depth_from.",
    )
    bulk_density = mv2.number_input(
        "Bulk density (g/cm³)", min_value=0.50, max_value=2.50, value=bulk_density, step=0.05, format="%.2f", key="bulk_density"
    )
    if map_value_mode == "Total kg/ha across sampled depths":
        # Use the selected year but deliberately ignore the single-depth filter so the whole sampled profile can be summed.
        profile_source = soil.copy()
        if year_choice != "All years":
            profile_source = profile_source[profile_source["sampling_date"].dt.year == int(year_choice)]
        profile_totals, profile_layers = build_profile_totals(profile_source, field, bulk_density)
        available_profile_dates = []
        if not profile_totals.empty:
            available_profile_dates = sorted(profile_totals["sample_day"].dropna().dt.date.unique(), reverse=True)
        profile_date_choice = mv3.selectbox("Sampling date", ["All dates"] + available_profile_dates, key="profile_date_choice")
        if profile_date_choice != "All dates" and not profile_totals.empty:
            profile_totals = profile_totals[profile_totals["sample_day"].dt.date == profile_date_choice].copy()
        st.caption("Profile mode ignores the single-depth filter for the map and sums all valid depth intervals from each sampling location on the selected day. Separate GPS locations are kept separate; sample ID is used as a fallback when GPS is unavailable.")

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

if map_value_mode == "Total kg/ha across sampled depths" and meta.get("unit") == "mg/kg":
    map_points = profile_totals.copy()
    latlon = map_points[["latitude", "longitude"]].dropna() if {"latitude", "longitude"}.issubset(map_points.columns) else pd.DataFrame()
    map_field = "total_kg_ha"
    map_label = f"Total {nutrient_label} across sampled depths"
    map_unit = "kg/ha"
    # kg/ha is a mass measure rather than the concentration interpretation used above.
    # Use the observed profile distribution for map colouring and report the exact total in tooltips.
    observed = map_points[map_field].dropna() if map_field in map_points else pd.Series(dtype=float)
    if len(observed) >= 4 and observed.nunique() > 1:
        q20, q40, q60, q80 = observed.quantile([0.2, 0.4, 0.6, 0.8]).tolist()
    elif len(observed):
        base = float(observed.median())
        q20, q40, q60, q80 = base*0.6, base*0.85, base*1.15, base*1.4
    else:
        q20, q40, q60, q80 = 0, 0, 0, 0
    def profile_status(v):
        if pd.isna(v): return "No data"
        if v <= q20: return "Very low"
        if v <= q40: return "Low"
        if v <= q60: return "Adequate"
        if v <= q80: return "High"
        return "Very high"
    if not map_points.empty and "paddock" in map_points.columns:
        paddock_stats = map_points.groupby("paddock", dropna=True)[map_field].agg(["mean", "count", "min", "max"]).reset_index()
    else:
        paddock_stats = pd.DataFrame()
else:
    map_points = filtered.copy()
    latlon = filtered[["latitude", "longitude"]].dropna()
    map_field = field
    map_label = nutrient_label
    map_unit = meta["unit"]
    profile_status = lambda v: status_for(v, low, high)
    paddock_stats = filtered.groupby("paddock", dropna=True)[field].agg(["mean", "count", "min", "max"]).reset_index() if "paddock" in filtered else pd.DataFrame()

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

stat_lookup = {str(r.paddock): r for _, r in paddock_stats.iterrows()}

if boundaries and boundaries.get("features"):
    enriched = json.loads(json.dumps(boundaries))
    for f in enriched["features"]:
        name = feature_name(f)
        stats = stat_lookup.get(name)
        value = float(stats["mean"]) if stats is not None and pd.notna(stats["mean"]) else np.nan
        status = profile_status(value)
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
        tooltip=GeoJsonTooltip(fields=["_paddock", "_value", "_status", "_samples"], aliases=["Paddock", f"{map_label} ({map_unit})", "Relative class" if map_value_mode == "Total kg/ha across sampled depths" else "Status", "Profiles" if map_value_mode == "Total kg/ha across sampled depths" else "Samples"]),
    )
    gj.add_to(m)
    if show_labels:
        for f in enriched["features"]:
            c = polygon_centroid_feature(f)
            if c:
                folium.Marker(c, icon=folium.DivIcon(html=f'<div style="font-weight:700;color:white;text-shadow:0 1px 3px #000;white-space:nowrap">{feature_name(f)}</div>')).add_to(m)

if show_samples:
    point_rows = map_points.dropna(subset=["latitude", "longitude"]) if {"latitude", "longitude"}.issubset(map_points.columns) else pd.DataFrame()
    for _, r in point_rows.iterrows():
        value = r.get(map_field, np.nan)
        status = profile_status(value)
        if map_value_mode == "Total kg/ha across sampled depths" and meta.get("unit") == "mg/kg":
            date_text = pd.to_datetime(r.get("sample_day"), errors="coerce")
            date_text = date_text.strftime("%d %b %Y") if pd.notna(date_text) else "—"
            popup = (
                f"<b>{r.get('sample_id','')}</b><br>Paddock: {r.get('paddock','')}<br>Date: {date_text}"
                f"<br><b>{map_label}: {'—' if pd.isna(value) else round(float(value),1)} kg/ha</b>"
                f"<br>Depth layers: {int(r.get('depth_layers',0))}<br>Sampled profile: {r.get('profile_depth_from',np.nan):g}–{r.get('profile_depth_to',np.nan):g} cm"
                f"<br>Total sampled thickness: {r.get('total_sampled_depth_cm',np.nan):g} cm<br>Bulk density: {bulk_density:.2f} g/cm³"
            )
            radius = 7
        else:
            popup = f"<b>{r.get('sample_id','')}</b><br>Paddock: {r.get('paddock','')}<br>{nutrient_label}: {'—' if pd.isna(value) else round(float(value),3)} {meta['unit']}<br>Status: {status}"
            radius = 5
        folium.CircleMarker(
            [r["latitude"], r["longitude"]], radius=radius, color="#111111", weight=1,
            fill=True, fill_color=STATUS_COLORS[status], fill_opacity=0.95, popup=popup,
        ).add_to(m)

folium.LayerControl(collapsed=False).add_to(m)
st_folium(m, use_container_width=True, height=610, returned_objects=[])

if latlon.empty:
    st.warning("The imported soil sheet has no usable Latitude/Longitude values for the current map selection. Sample/profile points cannot be placed on the satellite map until GPS coordinates are present. Paddock polygons can still be displayed if you upload georeferenced boundaries.")

if map_value_mode == "Total kg/ha across sampled depths" and meta.get("unit") == "mg/kg":
    if profile_totals.empty:
        st.info("No complete profile totals could be calculated for the selected year/date. Valid nutrient results, sampling dates, depth_from and depth_to are required; GPS or Sample ID is required to identify a profile location.")
    else:
        pm1, pm2, pm3, pm4 = st.columns(4)
        pm1.metric("Profile locations", f"{len(profile_totals):,}")
        pm2.metric("Mean total mass", f"{profile_totals['total_kg_ha'].mean():,.1f} kg/ha")
        pm3.metric("Mean sampled thickness", f"{profile_totals['total_sampled_depth_cm'].mean():.1f} cm")
        pm4.metric("Depth layers summed", f"{int(profile_totals['depth_layers'].sum()):,}")
        with st.expander("View profile totals used on map"):
            cols = [c for c in ["sample_day","paddock","sample_id","latitude","longitude","profile_depth_from","profile_depth_to","total_sampled_depth_cm","depth_layers","total_kg_ha"] if c in profile_totals.columns]
            st.dataframe(profile_totals[cols].sort_values([c for c in ["sample_day","paddock"] if c in cols]), use_container_width=True, hide_index=True)

# ---------- Summary ----------
st.subheader("Paddock Total kg/ha Summary" if map_value_mode == "Total kg/ha across sampled depths" and meta.get("unit") == "mg/kg" else "Paddock Nutrient Summary")
if paddock_stats.empty:
    st.info("No paddock names were available in the filtered data.")
else:
    count_label = "Profiles" if map_value_mode == "Total kg/ha across sampled depths" and meta.get("unit") == "mg/kg" else "Samples"
    out = paddock_stats.rename(columns={"paddock": "Paddock", "mean": "Average", "count": count_label, "min": "Minimum", "max": "Maximum"})
    out["Relative class" if map_value_mode == "Total kg/ha across sampled depths" and meta.get("unit") == "mg/kg" else "Status"] = out["Average"].map(profile_status)
    status_col = "Relative class" if map_value_mode == "Total kg/ha across sampled depths" and meta.get("unit") == "mg/kg" else "Status"
    out = out[["Paddock", count_label, "Average", "Minimum", "Maximum", status_col]].sort_values("Average")
    if map_value_mode == "Total kg/ha across sampled depths" and meta.get("unit") == "mg/kg":
        out = out.rename(columns={"Average":"Average total kg/ha", "Minimum":"Minimum total kg/ha", "Maximum":"Maximum total kg/ha"})
        st.caption("Profile map colours are relative classes based on the current profile-total distribution, not agronomic sufficiency thresholds. Exact kg/ha values are shown in the table and map popups.")
    st.dataframe(out, use_container_width=True, hide_index=True)

# ---------- mg/kg to kg/ha conversion ----------
st.subheader("Nutrient Mass Conversion (mg/kg → kg/ha)")
with st.container(border=True):
    st.caption("Each soil test is converted using its own sampled layer thickness: depth_to − depth_from. Formula: kg/ha = mg/kg × bulk density (g/cm³) × layer thickness (cm) × 0.1.")
    mgkg_labels = list(MGKG_NUTRIENTS.keys())
    default_conv_index = mgkg_labels.index(nutrient_label) if nutrient_label in mgkg_labels else 0
    cv1, cv2 = st.columns([1.5, 1])
    conversion_nutrient = cv1.selectbox("Nutrient to convert", mgkg_labels, index=default_conv_index, key="conversion_nutrient")
    cv2.metric("Bulk density", f"{bulk_density:.2f} g/cm³")
    st.caption("Bulk density is set in the Interactive map value controls above for mg/kg nutrients and is shared by all mass calculations.")

    conv_meta = MGKG_NUTRIENTS[conversion_nutrient]
    conv_field = conv_meta["field"]
    conv_work = build_mass_conversion(filtered, conv_field, bulk_density)
    usable = conv_work.dropna(subset=[conv_field, "layer_thickness_cm", "kg_ha"]) if not conv_work.empty else pd.DataFrame()

    if usable.empty:
        st.info(f"No usable {conversion_nutrient} results with valid depth_from and depth_to values are available for the current filter.")
    else:
        conv_avg_mgkg = usable[conv_field].mean()
        conv_avg_kgha = usable["kg_ha"].mean()
        mean_thickness = usable["layer_thickness_cm"].mean()
        mean_soil_mass = usable["soil_mass_t_ha"].mean()
        q1, q2, q3, q4 = st.columns(4)
        q1.metric("Average concentration", f"{conv_avg_mgkg:.2f} mg/kg")
        q2.metric("Bulk density", f"{bulk_density:.2f} g/cm³")
        q3.metric("Mean sampled thickness", f"{mean_thickness:.1f} cm", help="Mean of depth_to − depth_from across usable tests")
        q4.metric("Mean nutrient mass", f"{conv_avg_kgha:,.1f} kg/ha")
        st.caption(f"Mean soil mass across the sampled layers: {mean_soil_mass:,.0f} t/ha. Every row is calculated separately before summaries are averaged.")

        if "paddock" in usable.columns:
            conv_paddock = usable.groupby("paddock", dropna=True).agg(
                Samples=("kg_ha", "count"),
                **{
                    "Average mg/kg": (conv_field, "mean"),
                    "Mean layer cm": ("layer_thickness_cm", "mean"),
                    "Mean kg/ha": ("kg_ha", "mean"),
                    "Minimum kg/ha": ("kg_ha", "min"),
                    "Maximum kg/ha": ("kg_ha", "max"),
                }
            ).reset_index().rename(columns={"paddock":"Paddock"})
            st.markdown("##### Paddock conversion summary")
            st.dataframe(conv_paddock.sort_values("Mean kg/ha"), use_container_width=True, hide_index=True)

        with st.expander("Sample-level converted values"):
            sample_display = usable.rename(columns={conv_field: "mg/kg", "layer_thickness_cm":"Layer thickness (cm)", "soil_mass_t_ha":"Soil mass (t/ha)", "kg_ha":"kg/ha"}).copy()
            st.dataframe(sample_display, use_container_width=True, hide_index=True)

st.subheader("Year-on-Year Analysis")
annual_yoy, paddock_yoy = build_yoy_tables(yoy_source, field)
if annual_yoy.empty or len(annual_yoy) < 2:
    st.info("At least two sampling years with results for this nutrient and depth are required for year-on-year analysis.")
else:
    chart_df = annual_yoy.set_index("year")[["mean"]].rename(columns={"mean": f"Average {nutrient_label}"})
    st.line_chart(chart_df, use_container_width=True)

    available_years = annual_yoy["year"].astype(int).tolist()
    y1, y2 = st.columns(2)
    older_year = y1.selectbox("Compare from", available_years[:-1], index=max(0, len(available_years)-2), key="yoy_older")
    newer_candidates = [y for y in available_years if y > older_year]
    newer_year = y2.selectbox("Compare to", newer_candidates, index=len(newer_candidates)-1, key="yoy_newer")

    older_row = annual_yoy[annual_yoy["year"] == older_year].iloc[0]
    newer_row = annual_yoy[annual_yoy["year"] == newer_year].iloc[0]
    change = newer_row["mean"] - older_row["mean"]
    pct_change = np.nan if older_row["mean"] == 0 else change / older_row["mean"] * 100
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(str(older_year), f"{older_row['mean']:.2f} {meta['unit']}".strip(), f"{int(older_row['count'])} samples")
    c2.metric(str(newer_year), f"{newer_row['mean']:.2f} {meta['unit']}".strip(), f"{int(newer_row['count'])} samples")
    c3.metric("Level change", f"{change:+.2f} {meta['unit']}".strip())
    c4.metric("Percentage change", "—" if pd.isna(pct_change) else f"{pct_change:+.1f}%")

    annual_display = annual_yoy.copy()
    annual_display["Change"] = annual_display["mean"].diff()
    annual_display["Change %"] = annual_display["mean"].pct_change() * 100
    annual_display = annual_display.rename(columns={"year":"Year", "mean":"Average", "count":"Samples", "min":"Minimum", "max":"Maximum"})
    st.markdown("##### Whole-farm annual levels")
    st.dataframe(annual_display[["Year", "Samples", "Average", "Minimum", "Maximum", "Change", "Change %"]], use_container_width=True, hide_index=True)

    comp = comparison_table(paddock_yoy, older_year, newer_year)
    if not comp.empty:
        comp_display = comp.rename(columns={"paddock":"Paddock", "Older":str(older_year), "Newer":str(newer_year)})
        st.markdown("##### Paddock change")
        st.dataframe(comp_display[["Paddock", "Older samples", "Newer samples", str(older_year), str(newer_year), "Change", "Change %"]], use_container_width=True, hide_index=True)

        paddock_options = sorted([str(p) for p in paddock_yoy["paddock"].dropna().unique()])
        if paddock_options:
            selected_yoy_paddock = st.selectbox(
                "Paddock trend",
                ["Whole farm", "All paddocks"] + paddock_options,
                help="Choose All paddocks to plot every paddock as a separate line across sampling years.",
            )
            if selected_yoy_paddock == "All paddocks":
                all_paddock_trends = (
                    paddock_yoy.assign(paddock=paddock_yoy["paddock"].astype(str))
                    .pivot_table(index="year", columns="paddock", values="mean", aggfunc="mean")
                    .sort_index()
                )
                if not all_paddock_trends.empty:
                    st.line_chart(all_paddock_trends, use_container_width=True)
                    st.caption("Each line represents one paddock. Gaps indicate years where that paddock has no matching result for the selected nutrient and depth.")
            elif selected_yoy_paddock != "Whole farm":
                pd_trend = paddock_yoy[paddock_yoy["paddock"].astype(str) == selected_yoy_paddock].set_index("year")[["mean"]].rename(columns={"mean": selected_yoy_paddock})
                st.line_chart(pd_trend, use_container_width=True)

st.subheader("Export Map & Data to PDF")
with st.container(border=True):
    r1, r2 = st.columns([2, 1])
    report_title = r1.text_input("PDF report title", value="Farm Soil Nutrient Report")
    include_sample_rows = r2.checkbox("Include sample data table", True, help="Includes up to 250 filtered samples in the PDF. The CSV export remains available for the complete dataset.")
    st.caption("The PDF includes the current nutrient layer, filters, thresholds, map, farm metrics, paddock summary, year-on-year analysis, row-level mg/kg → kg/ha conversion using depth_to − depth_from for mg/kg nutrients, and optional sample table.")
    if st.button("Prepare PDF report", type="primary"):
        try:
            with st.spinner("Building PDF report..."):
                st.session_state.pdf_report = generate_pdf_report(
                    filtered=filtered, boundaries=boundaries, nutrient_label=nutrient_label, meta=meta, field=field,
                    low=low, high=high, year_choice=year_choice, depth_choice=depth_choice, show_samples=show_samples,
                    show_labels=show_labels, report_title=report_title.strip() or "Farm Soil Nutrient Report",
                    include_sample_rows=include_sample_rows, yoy_source=yoy_source,
                    bulk_density=bulk_density if meta.get("unit") == "mg/kg" else None,
                )
            st.success("PDF report is ready to download.")
        except Exception as e:
            st.error(f"Could not build PDF report: {e}")
    if st.session_state.pdf_report:
        safe_nutrient = re.sub(r"[^A-Za-z0-9]+", "_", nutrient_label).strip("_").lower()
        st.download_button(
            "Download PDF report",
            data=st.session_state.pdf_report,
            file_name=f"farm_soil_{safe_nutrient}_report.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

st.subheader("Imported Soil Data")
st.dataframe(filtered, use_container_width=True, hide_index=True)

csv = filtered.to_csv(index=False).encode("utf-8")
st.download_button("Download filtered data as CSV", data=csv, file_name="filtered_soil_results.csv", mime="text/csv")
