# Farm Soil Nutrient Mapper — Streamlit

A Streamlit version of the Farm Soil Nutrient Mapper designed for paddock boundaries plus laboratory Excel soil-test exports.

## Run locally

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Community Cloud

1. Create a GitHub repository.
2. Upload `app.py`, `requirements.txt`, and this `README.md` to the repository root.
3. Sign in to Streamlit Community Cloud.
4. Choose **Create app** / **Deploy an app**.
5. Select the GitHub repository.
6. Set the main file path to `app.py`.
7. Deploy.

## Excel format

The import wizard is built around the supplied laboratory export format:

- worksheet: `Soil Samples Result`
- row 1: headings
- row 2: units
- row 3 onward: results
- Latitude and Longitude are supported when present
- values such as `<10` can be interpreted as half the detection limit, the detection limit, or zero
- `248` can be treated as a missing/no-result placeholder

The wizard automatically suggests mappings for Sample ID, Paddock Name, Sampling Date, depths, Latitude/Longitude, pH CaCl2, Colwell P, exchangeable K, Sulphur KCl40, Organic Carbon, CEC and Zinc DTPA.

## Paddock boundaries

Supported:

- GeoJSON / JSON
- zipped ESRI Shapefile

Shapefiles must include their CRS information and will be transformed to WGS84 for web mapping.

## Satellite map

The app uses Esri World Imagery as the satellite basemap. An internet connection is required for map tiles.

## Important note

The threshold values included in the app are editable defaults. Agronomic interpretation should be adjusted for crop, soil type, laboratory method and local recommendations.
## Additional nutrient fields

The Excel Import Wizard also supports and auto-maps these fields from the supplied laboratory export where present:

- Nitrate Nitrogen (mg/kg)
- ESP / Sodium % of Cations (%)
- Chloride (mg/kg)
- EC / Electrical Conductivity 1:5 water (dS/m)
- ECse / Saturated Extract Electrical Conductivity (dS/m)

All nutrient thresholds remain editable in the app because interpretation ranges can vary by crop, soil type, depth and laboratory method.


## PDF report export

The app can generate a downloadable landscape A4 PDF from the current analysis view. The report includes:

- selected nutrient, sampling year and depth
- current interpretation thresholds
- farm metrics and low-sample count
- paddock nutrient map and legend
- paddock nutrient summary table
- optional soil sample data table (up to 250 rows in the PDF)

The complete filtered dataset can still be downloaded separately as CSV. The PDF uses a report-friendly static rendering of paddock polygons and GPS sample points; the interactive Esri satellite basemap remains available in the web map.
## Year-on-year nutrient analysis

The app compares the selected nutrient across all available sampling years at the same selected depth. It includes:

- whole-farm average nutrient trend by year
- comparison between any earlier and later sampling year
- absolute and percentage change in nutrient level
- sample counts for each comparison year
- paddock-by-paddock changes
- selectable paddock trend charts
- year-on-year tables included in the PDF report when at least two years are available

This helps avoid mixing sampling depths when comparing historical soil results.


## mg/kg to kg/ha conversion

The app includes a **Nutrient Mass Conversion** section for nutrients reported in mg/kg. You choose the nutrient and specify soil bulk density in g/cm³. Soil layer thickness is calculated automatically for every individual test from the imported depth fields:

`layer thickness (cm) = depth_to - depth_from`

Each test is then converted separately using:

`kg/ha = mg/kg × bulk density (g/cm³) × layer thickness (cm) × 0.1`

For example, a 0–10 cm result uses a 10 cm layer, while a 10–30 cm result uses a 20 cm layer. The app does not apply one global depth to all tests.

The conversion section shows:

- average concentration in mg/kg
- specified bulk density
- mean sampled layer thickness
- mean soil mass per hectare
- mean nutrient mass in kg/ha
- paddock-by-paddock mean/minimum/maximum kg/ha
- sample-level `depth_from`, `depth_to`, calculated layer thickness and kg/ha

Rows without valid `depth_from` and `depth_to` values, or where `depth_to <= depth_from`, are excluded from the kg/ha calculation rather than assigned an assumed depth.

For a mapped nutrient reported in mg/kg, the same row-level depth calculation is also used in the PDF report.

The kg/ha value is an estimated mass within each sampled soil layer. It should not be interpreted automatically as plant-available nutrient or fertiliser requirement.

## Total kg/ha profile map

For nutrients reported in mg/kg, the interactive map now has a **Total kg/ha across sampled depths** mode.

The calculation is performed in two stages:

1. Every test layer is converted independently using `layer thickness = depth_to - depth_from` and `kg/ha = mg/kg × bulk density × layer thickness × 0.1`.
2. Layers from the same sampling location on the same sampling day are summed to give a total sampled-profile nutrient mass in kg/ha.

GPS latitude/longitude is preferred for identifying the same sampling location. If GPS is unavailable, Sample ID is used as the fallback profile identifier. Separate sample locations in the same paddock on the same date are therefore not intentionally combined.

The map allows a sampling date to be selected and shows one point per sampled profile. Paddock polygons display the mean total profile kg/ha for the mapped profiles in that paddock. Profile-map colour classes are relative to the current mapped profile distribution; the exact kg/ha values are shown in map popups and the profile summary table.


### Paddock trend comparison
The Year-on-Year Analysis includes an **All paddocks** option. This plots every paddock as a separate line on the same trend graph for the selected nutrient and sampling depth, making paddock-to-paddock changes easy to compare. Missing years are left as gaps rather than interpolated.
