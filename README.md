# geo-datasync-fi

An agent skill for making Finnish open data spatially interoperable.

It takes a dataset — a link to [avoindata.suomi.fi](https://avoindata.suomi.fi),
a CSV somebody emailed, a shapefile, a WFS endpoint — and produces three things:

1. **Data** converted to EUREF-FIN / ETRS-TM35FIN (EPSG:3067) as a GeoPackage,
   plus WGS84 GeoJSON for web maps
2. **A report** classifying how the dataset can be placed on a map at all, and
   recording every assumption made on the way
3. **Join keys** normalised so they actually match National Land Survey and
   Statistics Finland reference layers

Plus INSPIRE-conformant ISO 19139 metadata for the result.

## Why

Finnish spatial data converts *without erroring* into the wrong place. Legacy KKJ
coordinates look like plausible numbers. A column named `x` holds the northing
about as often as the easting, because the EPSG definition of ETRS-TM35FIN puts
northing first while every GIS tool puts easting first. A municipality code loses
its leading zero in Excel and silently joins to nothing.

None of these raise an exception. They produce a clean-looking file that is
quietly wrong — sometimes by hundreds of metres, sometimes by a country.

So the skill infers from coordinate magnitudes rather than from labels, scores
the declared CRS instead of trusting it, and stops and asks when it is not
confident rather than emitting something plausible.

## Layout

```
.claude/skills/fi-open-geodata/
├── SKILL.md                     the workflow
├── requirements.txt
├── scripts/
│   ├── fi_crs.py                CRS detection from coordinate magnitudes
│   ├── fi_common.py             Finnish CSV conventions, column role detection
│   ├── fi_joinkeys.py           kuntanumero, kiinteistötunnus, PRT, Y-tunnus
│   ├── fetch_source.py          CKAN / direct file / OGC service resolution
│   ├── profile_source.py        spatial fitness classification + report
│   ├── geocode_fi.py            NLS geocoding with caching and an audit trail
│   ├── transform_source.py      EPSG:3067 GeoPackage + WGS84 GeoJSON
│   └── make_metadata.py         INSPIRE ISO 19139
├── references/                  CRS zoo, APIs, join keys, INSPIRE details
└── assets/inspire-themes.json   the 34 themes and their topic categories
tests/                           offline tests, no network or API key needed
```

## Spatial fitness classes

The question that decides everything downstream is not what format a file is, but
what the strongest locational signal in it is — because that sets both the
conversion path and the accuracy you can honestly claim.

| Class | Means | Path |
|---|---|---|
| `GEOMETRY` | Native geometry | Reproject |
| `COORDINATES` | Coordinate columns | Build points; CRS must be established |
| `ADDRESS` | Street addresses | Geocode |
| `AREA_CODE` | Municipality/postal/property code | Join to a reference polygon |
| `PLACE_NAME` | A name only | Weak geocode, expect ambiguity |
| `NON_SPATIAL` | No locational signal | Stop |

## Usage

```bash
pip install -r .claude/skills/fi-open-geodata/requirements.txt
export NLS_API_KEY=...        # only needed for geocoding and NLS feature queries

cd .claude/skills/fi-open-geodata/scripts
python fetch_source.py "https://avoindata.suomi.fi/data/fi/dataset/<slug>" --out work/
python profile_source.py work/ --report work/report.md
python transform_source.py work/ --out-dir out/
python make_metadata.py out/transform.json --title ... --theme "Buildings" ... --out out/metadata/d.xml
```

With Claude Code the skill triggers on its own for Finnish spatial data; the
commands above are what it runs.

## Tests

```bash
python tests/test_fi_modules.py    # CRS detection, CSV sniffing, join keys
python tests/test_pipeline.py      # profile -> transform -> metadata, end to end
```

Both run offline. They assert on behaviour that matters rather than on output
shape — for instance that a row of KKJ coordinates with reversed axes ends up
within a kilometre of Helsinki Cathedral, and that a personal identity code in
any column is surfaced before anything gets published.

## Caveats

- **Endpoints were not verified live.** The environment this was authored in
  blocked `avoindata.suomi.fi` and `maanmittauslaitos.fi` at the network policy,
  so the URLs in `references/apis-and-layers.md` come from documentation rather
  than from a successful call. The scripts discover rather than assume where they
  can. Treat a 404 as "this moved".
- **The VTJ-PRT checksum is unverified** against DVV's published algorithm, so a
  mismatch is flagged rather than treated as fatal.
- **INSPIRE conformance is asserted, not proven.** Upload the generated record to
  <https://inspire.ec.europa.eu/validator/> for an authoritative verdict.
- Raster data is out of scope.
