# Finnish data sources, APIs and reference layers

Endpoints move and collection names change. Everything here was correct as
documented at the time of writing but was **not** verifiable live from the
authoring environment, so treat a 404 as "this moved", not "the skill is broken",
and confirm against the linked documentation. Prefer discovery — ask a service
what it holds — over trusting a hardcoded name.

## The national portal (CKAN)

Suomi.fi Open Data, `https://avoindata.suomi.fi` (the older `www.avoindata.fi`
still answers). It is CKAN, so the standard action API applies:

```
https://avoindata.suomi.fi/data/api/3/action/package_search?q=<terms>&rows=10
https://avoindata.suomi.fi/data/api/3/action/package_show?id=<dataset-slug>
https://avoindata.suomi.fi/data/api/3/action/organization_list
```

`fetch_source.py` probes both `/data/api/3/action` and `/api/3/action` and uses
whichever answers `status_show`, which also makes it work against municipal CKAN
portals such as Helsinki Region Infoshare.

The portal holds **metadata**; the data itself lives on the publisher's server,
so a resource URL can be dead while the dataset record looks healthy.

Fields worth reading before anything else: `license_id`/`license_title` (absent
means *ask*, not *free*), `organization.title`, and `metadata_modified`.

## National Land Survey (Maanmittauslaitos) — needs an API key

Register at <https://omatili.maanmittauslaitos.fi>. The key goes in either an
`api-key` query parameter or the username of HTTP basic auth. Set `NLS_API_KEY`
and the scripts here pick it up.

| Service | Base URL |
|---|---|
| Topographic database (OGC API Features) | `https://avoin-paikkatieto.maanmittauslaitos.fi/maastotiedot/features/v1/` |
| Geographic names (OGC API Features) | `https://avoin-paikkatieto.maanmittauslaitos.fi/geographic-names/features/v1/` |
| Cadastral index (OGC API Features) | `https://avoin-paikkatieto.maanmittauslaitos.fi/kiinteisto-avoin/features/v1/` |
| Geocoding (Pelias-style REST) | `https://avoin-paikkatieto.maanmittauslaitos.fi/geocoding/v2/pelias/search` |

Discover collections rather than assuming them:

```bash
curl "https://avoin-paikkatieto.maanmittauslaitos.fi/maastotiedot/features/v1/collections?f=json&api-key=$NLS_API_KEY" \
  | python -c "import json,sys; [print(c['id'], '-', c.get('title')) for c in json.load(sys.stdin)['collections']]"
```

Requesting features in EPSG:3067, which avoids a needless round trip through
WGS84:

```bash
curl "https://avoin-paikkatieto.maanmittauslaitos.fi/maastotiedot/features/v1/collections/rakennus/items\
?bbox=385000,6670000,390000,6675000\
&bbox-crs=http://www.opengis.net/def/crs/EPSG/0/3067\
&crs=http://www.opengis.net/def/crs/EPSG/0/3067\
&limit=1000&api-key=$NLS_API_KEY"
```

Collections commonly used as reference geometry include `rakennus` (buildings),
`tieviiva` (road centrelines) and `suo` (mires) — confirm the current list with
the discovery call above.

The geocoding service takes `text`, `sources`, `crs`, `lang` and `size`, defaults
to EPSG:3067, and returns GeoJSON. Sources, most precise first: `addresses`,
`interpolated-road-addresses`, `geographic-names`, `cadastral-units`,
`mapsheets-tm35`.

## Statistics Finland — no API key

`https://geo.stat.fi/geoserver/` serves WFS and WMS openly, which makes it the
path of least resistance for administrative geometry.

```
https://geo.stat.fi/geoserver/tilastointialueet/wfs?service=WFS&version=2.0.0&request=GetCapabilities
```

Layers follow a `name_YYYY` pattern — `tilastointialueet:kunta4500k_2022` is the
generalised municipal boundary set for 2022. **Always read GetCapabilities for
the current year** rather than assuming; boundaries change on 1 January whenever
municipalities merge.

Other useful workspaces: `postialue` (Paavo postal-code areas, carries population
and demographic attributes), `vaestoruutu` (1 km and 5 km population grids).

## Choosing a reference layer to join against

| Your key | Join to | Result accuracy |
|---|---|---|
| `municipality_code` (3 digits) | Statistics Finland `kunta*` | Municipality polygon — coarse |
| `postcode` (5 digits) | Statistics Finland `postialue` | Postal area polygon |
| `property_id` (14-digit form) | NLS cadastral index | Parcel boundary — precise |
| `building_id` (VTJ-PRT) | NLS topographic `rakennus` | Building footprint — precise |
| Geocoded address | already a point | Point, quality depends on the source used |

Two things to state in the report whenever an area join is used: the join gives
the row the accuracy of *the area*, not of a point, and a centroid derived from
it is a convenience, not a measurement. Presenting municipality-joined data as
precise points is the most common way this kind of work misleads people.

## Boundary vintages

A dataset from 2018 keyed on municipality codes will not join cleanly to 2024
boundaries, because merged municipalities lose their codes. Match the reference
year to the data year, and say which year you used in the metadata lineage.

## Paikkatietoikkuna

The national geoportal (<https://kartta.paikkatietoikkuna.fi>) runs on Oskari and
can add user layers from WMS, WMTS, WFS and OGC API Features endpoints. It
consumes *services*, not uploaded files — so the route for data produced here is
to publish the GeoPackage through a service (GeoServer, pygeoapi, or the
publisher's own infrastructure) and then add that endpoint as a layer.

For a service to work there, it needs to advertise EPSG:3067 among its supported
CRSs and serve valid capabilities. Check the current import options in the NLS
user guide before promising a particular route to anyone.
