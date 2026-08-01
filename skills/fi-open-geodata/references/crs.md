# Coordinate reference systems in Finnish data

Read this when a CRS detection is unconfident, when a conversion produces
coordinates that land in the wrong place, or before overriding a declared CRS.

## The target

**EUREF-FIN / ETRS-TM35FIN, EPSG:3067.** The national standard since 2010 and the
CRS Paikkatietoikkuna, the NLS services and Statistics Finland all serve. Every
output of this skill is stored in it.

Numerically identical codes that mean the same grid, so a file declaring one of
these is *not* mislabelled:

| Code | Note |
|---|---|
| EPSG:3067 | ETRS-TM35FIN(E,N) — the canonical code, easting first |
| EPSG:3047 | ETRS89 / TM35 — same projection parameters |
| EPSG:5048 | ETRS89 / TM35FIN(N,E) — same grid, EPSG-declared axis order northing first |

That last row is the origin of most axis-order confusion: the EPSG registry
defines the CRS with northing first, while GIS software, GeoJSON and almost every
CSV put easting first. Files therefore disagree with each other in good faith.

## What you will actually encounter

Detection in `fi_crs.py` works purely on coordinate magnitude, because the ranges
below do not overlap. That makes it reliable even when the file says nothing, and
it is why a declared CRS is scored rather than trusted.

| CRS | EPSG | Easting range | Northing range | Where it comes from |
|---|---|---|---|---|
| ETRS-TM35FIN | 3067 | 20 000 – 800 000 | 6 520 000 – 7 900 000 | Current national standard |
| KKJ / YKJ zone 3 | 2393 | 3 000 000 – 3 800 000 | 6 600 000 – 7 800 000 | Legacy national grid; ubiquitous in pre-2010 municipal data |
| KKJ zones 1, 2, 4 | 2391, 2392, 2394 | 1/2/4 000 000 + | same | Older regional data; the leading digit is the zone |
| ETRS-GK19…GK31 | 3873–3885 | zone × 1 000 000 + ~500 000 | 6 520 000 – 7 900 000 | Municipal detail plans; zone embedded in the easting |
| WGS 84 | 4326 | lon 19.0 – 31.7 | lat 59.4 – 70.2 | GPS exports, web APIs |
| Web Mercator | 3857 | 2 100 000 – 3 560 000 | 8 300 000 – 11 150 000 | Tile services; never a storage CRS |

Rules of thumb that follow from the table:

- A seven-digit easting beginning with 3 is **YKJ**, not TM35FIN.
- A TM35FIN northing is always in the millions and an easting never reaches one
  million, so a pair can be oriented by magnitude alone regardless of the column
  names. `fi_common.orient_pair` does exactly this.
- An easting near 25 500 000 is **ETRS-GK25** (EPSG:3879), Helsinki's planning CRS
  — not corrupt data.

## KKJ to EUREF-FIN is not a simple parameter shift

The KKJ family (EPSG:239x) sits on a different datum, and the difference is not
constant across the country — it varies by roughly 100 m in a way that a
three-parameter shift cannot capture. `pyproj` applies the national
transformation when it has the grid available, which the pipeline relies on.

If accuracy matters at the metre level, say so in the metadata lineage and note
which transformation was used. For most open data the difference is well inside
the source's own accuracy, but that is a judgement to state, not to assume.

## When detection is unconfident

`profile_source.py` reports the fraction of sampled points landing inside each
candidate's envelope. Below 95% it refuses to pick one silently. Usual causes:

- **The data extends outside Finland.** Legitimate for shipping, air quality, or
  cross-border datasets. Override with `--source-epsg` once you have established
  the CRS from the publisher's documentation.
- **Mixed CRSs in one file.** Municipal registers merged from several sources do
  this. There is no automatic fix; split the file by source and convert each part.
- **The coordinates are not coordinates.** A column called `x` might be an
  identifier. Check `report.md` for what else was detected in the row.

Never resolve this by trying codes until the output looks right in a viewer at
country zoom — YKJ and TM35FIN both put Finland in roughly the right place and
differ by hundreds of metres. Check a known landmark instead: Helsinki Cathedral
is 60.1703 N, 24.9522 E, which is 385 200 E / 6 672 200 N in EPSG:3067.
