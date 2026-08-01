---
name: fi-open-geodata
description: >
  Make Finnish open data spatially interoperable - assess whether a dataset can be
  put on a map, convert it to EUREF-FIN / ETRS-TM35FIN (EPSG:3067), normalise its
  join keys against National Land Survey and Statistics Finland reference layers,
  geocode Finnish addresses, and generate INSPIRE-conformant ISO 19139 metadata.
  Use this skill whenever the user mentions avoindata.suomi.fi, avoindata.fi,
  Paikkatietoikkuna, Maanmittauslaitos, EPSG:3067, ETRS-TM35FIN, EUREF-FIN, KKJ,
  YKJ, kuntanumero, kiinteistotunnus, rakennustunnus, or paikkatieto - and also
  whenever they have any Finnish dataset (CSV, Excel, shapefile, GeoPackage, WFS
  or OGC API endpoint) that they want mapped, reprojected, geocoded, joined to
  Finnish administrative areas, or published as INSPIRE metadata, even if they do
  not name any of those terms. Finnish coordinates, municipality codes, or
  addresses in a file are enough of a signal to use it.
---

# Finnish open data spatial interoperability

## What this is for

Getting a Finnish dataset from "a file somebody linked" to "a layer that joins
correctly to national reference data and has metadata a catalogue will accept".

Three deliverables, produced together:

1. **Data** — EPSG:3067 GeoPackage (the copy to keep) plus WGS84 GeoJSON (for web maps)
2. **Report** — what the data is, what was assumed, and what is not trustworthy
3. **Join keys** — normalised identifiers that actually match NLS and Statistics
   Finland reference layers

## The thing that goes wrong

Finnish spatial data converts *without erroring* into the wrong place. Legacy KKJ
coordinates look like plausible numbers; a column named `x` holds the northing
about as often as the easting; a municipality code loses its leading zero in
Excel and joins to nothing. None of these raise an exception — they produce a
clean-looking file that is silently wrong.

So the working principle here is: **state what was assumed, and refuse to guess
about the CRS**. A pipeline that stops and asks is more useful than one that
always produces output. Carry that into how you report results too — say which
decisions were inferred, and how confident the inference was.

## Setup

```bash
pip install -r <skill>/requirements.txt      # geopandas, pyproj, shapely, pandas, requests
export NLS_API_KEY=...                       # only for geocoding and NLS services
```

The National Land Survey key is self-service from <https://omatili.maanmittauslaitos.fi>.
Everything except geocoding and NLS feature queries works without it.

Scripts live in `scripts/` and import each other, so run them from that directory
or add it to `PYTHONPATH`.

## Workflow

### 1. Fetch

```bash
python fetch_source.py "<url-or-path>" --out work/
```

Accepts a portal dataset page, a CKAN resource link, a direct file URL, an OGC
API Features or WFS endpoint, or a local path. For a CKAN dataset it queries the
portal API for the real resources plus the licence and publisher.

**Read the licence line it prints.** If no licence is recorded, that is not
permission to republish — flag it and ask before going further.

Service endpoints are recorded but not bulk-downloaded; pass
`--bbox minx,miny,maxx,maxy` (EPSG:3067) to pull an extract.

### 2. Profile

```bash
python profile_source.py work/ --out work/profile.json --report work/report.md
```

Assigns a **spatial fitness class** — the strongest locational signal present,
which is what determines the transformation path and the accuracy you can honestly
claim:

| Class | Means | Path |
|---|---|---|
| `GEOMETRY` | Native geometry | Reproject |
| `COORDINATES` | Coordinate columns | Build points; CRS must be established |
| `ADDRESS` | Street addresses | Geocode |
| `AREA_CODE` | Municipality/postal/property code | Join to a reference polygon |
| `PLACE_NAME` | A name only | Weak geocode, expect ambiguity |
| `NON_SPATIAL` | No locational signal | Stop, or find the companion file |

It also detects the CRS from coordinate magnitudes, checks that against what the
file declares, normalises every join key it finds, and scans for personal identity
codes. Read `work/report.md` before converting — the warnings there are the point
of this step, not a formality.

### 3. Geocode (only for `ADDRESS`)

```bash
python geocode_fi.py work/source/data.csv --column osoite \
    --municipality-column kunta --out work/geocoded.csv --audit work/unmatched.csv
```

Pass `--municipality-column` whenever the data has one. `Koulukatu 1` exists in
dozens of municipalities, and adding the municipality is typically the difference
between a 60% and a 95% hit rate.

Results are cached by query, and unmatched rows stay in the output with an empty
position rather than disappearing. Report the hit rate — a map built from 88% of
the rows is a different claim than one built from all of them.

### 4. Transform

```bash
python transform_source.py work/ --out-dir out/
```

Writes `out/data/<name>_3067.gpkg`, `out/data/<name>_wgs84.geojson`, and
`out/transform.json` recording every assumption. Appends normalised join-key
columns; for property identifiers it emits both the database and presentation
forms, since you need one to join and the other to show a person.

If the CRS could not be determined confidently it stops. Resolve it from the
publisher's documentation and re-run with `--source-epsg` (plus `--swap-xy` if
the first column holds the northing) — see `references/crs.md`.

### 5. Metadata

```bash
python make_metadata.py out/transform.json \
    --title "..." --abstract "..." --theme "Buildings" \
    --organisation "..." --email "..." --access-url "..." \
    --out out/metadata/dataset_iso19139.xml
```

`--list-themes` shows the 34 INSPIRE themes with their topic categories. The
lineage is filled from what actually happened during the transform. Conformance
is asserted, not proven — say so, and point at
<https://inspire.ec.europa.eu/validator/> for a verdict.

## Joining to reference layers

For `AREA_CODE` data, the normalised key joins to:

- **kuntanumero** → Statistics Finland `tilastointialueet:kunta*` (open, no key)
- **postinumero** → Statistics Finland `postialue` (open, no key)
- **kiinteistötunnus** → NLS cadastral index (needs a key)
- **rakennustunnus** → NLS topographic `rakennus` (needs a key)

Match the reference year to the data year — municipal boundaries change every
January, and old codes are retired rather than reissued.

An area join gives a row the accuracy of *the area*. A centroid taken from it is
a convenience, not a measurement, and presenting one as a precise location is the
most common way this work misleads people. Say which it is.

## Reference material

Read these when the situation calls for it rather than up front:

- `references/crs.md` — the Finnish CRS zoo, magnitude ranges, KKJ datum shift,
  what to do when detection is unconfident
- `references/apis-and-layers.md` — CKAN, NLS and Statistics Finland endpoints,
  collection discovery, choosing a reference layer, Paikkatietoikkuna
- `references/join-keys.md` — formats and checksums for every key, and how to read
  the normalisation report
- `references/inspire-metadata.md` — what the validator checks and why records fail

Endpoints in those files were documented but could not be verified live when this
skill was written. Treat a 404 as "this moved" and rediscover, rather than
assuming the data is gone.

## Working notes

- Store in EPSG:3067; publish GeoJSON in WGS84 lon/lat because RFC 7946 requires
  it. Both outputs exist for that reason.
- A personal identity code in the data stops publication. Remove or pseudonymise
  it, and consider whether the rest of the row is identifying too.
- Never settle a CRS by eyeballing a country-zoom map — YKJ and TM35FIN both put
  Finland roughly in the right place and differ by hundreds of metres. Check a
  landmark: Helsinki Cathedral is 385 200 E / 6 672 200 N in EPSG:3067.
- When a step is skipped or a rate is below 100%, put the number in the summary
  you give the user. The failure rates are the informative part.
