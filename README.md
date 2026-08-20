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
