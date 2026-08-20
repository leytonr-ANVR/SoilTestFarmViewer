# Farm Soil Nutrient Mapper

A browser-based whole-farm soil nutrient mapping app designed for paddock boundary files and laboratory soil-test spreadsheets.

## Features

- Interactive Leaflet farm map
- Esri World Imagery satellite basemap with OpenStreetMap fallback
- Import zipped ESRI Shapefiles, GeoJSON and KML paddock boundaries
- Import Excel (`.xlsx`, `.xls`) or CSV soil-test data
- Automatically maps common spreadsheet headings for paddock, coordinates, year, depth and nutrients
- Nutrient layers for pH, phosphorus, potassium, sulphur, organic carbon, CEC and zinc
- Paddock averages, sample points, ranges and nutrient status
- Filter by year and sample depth
- Adjustable interpretation thresholds
- Overlay opacity control
- Print / Save PDF reporting using the browser print dialog
- Responsive layout for desktop and mobile
- Demo farm included

## Run locally

Because the app loads JavaScript libraries and map tiles from CDNs, it is best opened through a small local web server rather than by double-clicking `index.html`.

### Python

```bash
python -m http.server 8000
```

Then open `http://localhost:8000`.

## Deploy to GitHub Pages

1. Create a new GitHub repository, for example `farm-soil-nutrient-mapper`.
2. Upload `index.html`, `styles.css`, `app.js` and this `README.md` to the repository root.
3. In GitHub, open **Settings → Pages**.
4. Under **Build and deployment**, choose **Deploy from a branch**.
5. Select your default branch (usually `main`) and `/ (root)`.
6. Save. GitHub will provide the public Pages URL once deployment is complete.

No backend or database is required for this version.

## Shapefile import

Zip the Shapefile components together before upload. The ZIP should normally contain at least:

- `.shp`
- `.shx`
- `.dbf`
- `.prj` recommended

Paddock names should ideally be stored in a field named `Paddock`, `Name` or `NAME`.

## Soil spreadsheet format

The app attempts to identify common heading names automatically. A recommended structure is:

| Sample ID | Paddock | Latitude | Longitude | Year | Depth | pH CaCl2 | Colwell P | Potassium | Sulphur | Organic Carbon | CEC | Zinc |
|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|
| S001 | River | -24.384 | 150.381 | 2026 | 0-10 cm | 5.6 | 28 | 0.42 | 8.2 | 1.21 | 11.8 | 1.2 |

Latitude and longitude should be decimal degrees, normally WGS84 / EPSG:4326.

## Notes

Interpretation thresholds in the demo are generic examples only. Set thresholds to match the crop, soil type, laboratory extraction method and agronomic guidelines you use.

Satellite imagery is supplied through Esri World Imagery and requires an internet connection. The app itself is otherwise static and can be hosted on GitHub Pages.

## Excel Soil Test Import Wizard

The app includes an interactive Excel/CSV import wizard designed around the supplied `Sample_Exported_Data_20260820091346.xlsx` laboratory export format.

It recognises the `Soil Samples Result` worksheet structure where:

- Row 1 contains result/test headings.
- Row 2 contains measurement units.
- Row 3 onward contains sample results.
- Duplicate laboratory result headings are retained by column position rather than being overwritten.
- The export placeholder value `248` can be treated as missing/no-result data (enabled by default).
- Results expressed as limits such as `<10` or `<0.10` can be converted to numeric limit values for mapping (enabled by default).

Default mappings include Sample ID, Sample Name, Grower Name, Customer Name, Paddock Name, Sampling Date, sample depth, crop, test code, latitude, longitude, pH (1:5 CaCl2), Phosphorus (Colwell), Potassium (Amm-acet.), Sulphur (KCl40), Organic Carbon (W&B), Cation Exchange Capacity and Zinc (DTPA).

Always review the mappings before importing, particularly where a laboratory export contains the same test name more than once using different extraction methods or units.
