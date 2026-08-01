"""Resolve a Finnish open data reference into local files plus a manifest.

The usual starting point is a URL somebody pasted, and it can be any of:
  * a dataset page on the national portal, e.g.
    https://avoindata.suomi.fi/data/fi/dataset/<name>
  * a CKAN resource page ending in /resource/<uuid>
  * a direct link to a file (CSV, GeoPackage, shapefile zip, ...)
  * an OGC API Features / WFS / WMS service endpoint

Rather than guessing from the file extension, this asks the portal's CKAN API
what the dataset actually contains, because that is also where the licence and
publisher live - and those decide whether the data may be republished at all,
which matters more than any format question.

Services are recorded but not bulk-downloaded: a WFS can be far larger than the
disk, so an explicit --bbox is required to pull features from one.

    python fetch_source.py <url-or-path> --out work/
    python fetch_source.py <wfs-url> --out work/ --bbox 385000,6670000,390000,6675000
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

import requests

from fi_common import classify_suffix, write_json

USER_AGENT = "fi-open-geodata/1.0 (+https://github.com/akaihola/geo-datasync-fi)"
TIMEOUT = 60

# Portals that speak CKAN. The national portal moved from avoindata.fi to
# avoindata.suomi.fi and both still answer, so both are probed; municipal
# portals running CKAN work through the same code path.
CKAN_API_SUFFIXES = ("/data/api/3/action", "/api/3/action")

SERVICE_HINTS = (
    "/features/v1",
    "service=wfs",
    "service=wms",
    "service=wmts",
    "/ogcapi",
    "/collections",
    "/geoserver",
)


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def looks_like_service(url: str) -> bool:
    low = url.lower()
    return any(hint in low for hint in SERVICE_HINTS)


def ckan_dataset_id(url: str) -> str | None:
    """Pull the dataset slug out of a CKAN dataset or resource page URL."""
    match = re.search(r"/dataset/([^/?#]+)", url)
    return match.group(1) if match else None


def find_ckan_api(session: requests.Session, url: str) -> str | None:
    """Probe a host for a working CKAN action API and return its base."""
    parsed = urlparse(url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    for suffix in CKAN_API_SUFFIXES:
        base = root + suffix
        try:
            response = session.get(f"{base}/status_show", timeout=TIMEOUT)
        except requests.RequestException:
            continue
        if response.ok:
            try:
                if response.json().get("success"):
                    return base
            except ValueError:
                continue
    return None


def ckan_package(session: requests.Session, api_base: str, dataset: str) -> dict:
    response = session.get(
        f"{api_base}/package_show", params={"id": dataset}, timeout=TIMEOUT
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success"):
        raise RuntimeError(f"CKAN refused package_show for {dataset!r}")
    return payload["result"]


def summarise_package(package: dict) -> dict:
    """Keep the metadata fields that decide whether and how data may be reused."""
    return {
        "id": package.get("id"),
        "name": package.get("name"),
        "title": package.get("title"),
        "notes": package.get("notes"),
        "licence_id": package.get("license_id"),
        "licence_title": package.get("license_title"),
        "licence_url": package.get("license_url"),
        "publisher": (package.get("organization") or {}).get("title"),
        "maintainer_email": package.get("maintainer_email") or package.get("author_email"),
        "keywords": [t.get("name") for t in package.get("tags", []) if t.get("name")],
        "last_modified": package.get("metadata_modified"),
        "landing_page": package.get("url"),
        "extras": {
            e.get("key"): e.get("value") for e in package.get("extras", []) if e.get("key")
        },
    }


def choose_resources(package: dict, prefer: list[str] | None = None) -> list[dict]:
    """Order a package's resources so the most usable formats come first.

    Preference runs from formats that carry their own geometry and CRS down to
    plain tables; a service endpoint sorts last because it needs a bbox before it
    yields anything.
    """
    ranking = prefer or [
        "gpkg", "geopackage", "geojson", "shp", "shapefile", "gml",
        "csv", "xlsx", "json", "wfs", "wms",
    ]

    def rank(resource: dict) -> tuple[int, str]:
        fmt = (resource.get("format") or "").strip().lower()
        for index, wanted in enumerate(ranking):
            if wanted in fmt:
                return index, fmt
        return len(ranking), fmt

    return sorted(package.get("resources", []), key=rank)


def download(session: requests.Session, url: str, target_dir: Path) -> Path | None:
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        with session.get(url, stream=True, timeout=TIMEOUT) as response:
            response.raise_for_status()
            name = Path(urlparse(url).path).name or "download"
            # Content-Disposition wins when present; portals often serve files
            # from an opaque UUID path.
            disposition = response.headers.get("content-disposition", "")
            match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', disposition)
            if match:
                name = Path(match.group(1)).name
            path = target_dir / name
            with path.open("wb") as handle:
                for chunk in response.iter_content(1 << 16):
                    handle.write(chunk)
        return path
    except requests.RequestException as error:
        print(f"  ! download failed: {error}", file=sys.stderr)
        return None


def fetch_features(
    session: requests.Session, url: str, bbox: str, target_dir: Path, api_key: str | None
) -> Path | None:
    """Pull a bbox worth of features from an OGC API Features endpoint.

    The NLS services accept the key either as HTTP basic auth or as an `api-key`
    query parameter; the query parameter is used here because it survives
    redirects and is easy to reproduce with curl when debugging.
    """
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query.setdefault("bbox", [bbox])
    query.setdefault("bbox-crs", ["http://www.opengis.net/def/crs/EPSG/0/3067"])
    query.setdefault("crs", ["http://www.opengis.net/def/crs/EPSG/0/3067"])
    query.setdefault("limit", ["1000"])
    query.setdefault("f", ["json"])
    if api_key:
        query["api-key"] = [api_key]
    service_url = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
    return download(session, service_url, target_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="dataset URL, file URL, service URL, or local path")
    parser.add_argument("--out", type=Path, default=Path("work"))
    parser.add_argument("--bbox", help="minx,miny,maxx,maxy in EPSG:3067, for services")
    parser.add_argument("--api-key", help="NLS API key, for avoin-paikkatieto endpoints")
    parser.add_argument(
        "--all-resources",
        action="store_true",
        help="download every resource instead of the best-ranked one",
    )
    args = parser.parse_args(argv)

    out = args.out
    source_dir = out / "source"
    manifest: dict = {"source": args.source, "files": [], "service": None, "dataset": None}

    local = Path(args.source)
    if local.exists():
        manifest["files"] = [
            {"path": str(local.resolve()), "kind": classify_suffix(local)}
        ]
        write_json(out / "manifest.json", manifest)
        print(f"local file registered: {local}")
        return 0

    session = _session()

    dataset_slug = ckan_dataset_id(args.source)
    if dataset_slug and not looks_like_service(args.source):
        api_base = find_ckan_api(session, args.source)
        if api_base:
            print(f"CKAN API: {api_base}")
            package = ckan_package(session, api_base, dataset_slug)
            manifest["dataset"] = summarise_package(package)
            licence = manifest["dataset"]["licence_title"] or manifest["dataset"]["licence_id"]
            print(f"dataset: {manifest['dataset']['title']}")
            print(f"licence: {licence or 'NOT STATED - check before republishing'}")

            resources = choose_resources(package)
            if not args.all_resources:
                resources = resources[:1]
            for resource in resources:
                url = resource.get("url")
                if not url:
                    continue
                if looks_like_service(url):
                    manifest["service"] = {
                        "url": url,
                        "format": resource.get("format"),
                        "note": "service endpoint - re-run with --bbox to extract features",
                    }
                    print(f"  service: {url}")
                    continue
                print(f"  downloading {resource.get('format')}: {url}")
                path = download(session, url, source_dir)
                if path:
                    manifest["files"].append(
                        {
                            "path": str(path),
                            "kind": classify_suffix(path),
                            "format": resource.get("format"),
                            "resource_name": resource.get("name"),
                            "url": url,
                        }
                    )
            write_json(out / "manifest.json", manifest)
            return 0
        print(
            "Could not reach a CKAN API on that host; treating the URL as a direct link.",
            file=sys.stderr,
        )

    if looks_like_service(args.source):
        manifest["service"] = {"url": args.source}
        if args.bbox:
            print(f"requesting features within {args.bbox}")
            path = fetch_features(session, args.source, args.bbox, source_dir, args.api_key)
            if path:
                manifest["files"].append(
                    {"path": str(path), "kind": "vector", "url": args.source}
                )
        else:
            print(
                "Service endpoint recorded. Re-run with --bbox to extract features, "
                "or point a GIS client at the URL."
            )
        write_json(out / "manifest.json", manifest)
        return 0

    print(f"downloading {args.source}")
    path = download(session, args.source, source_dir)
    if not path:
        return 1
    manifest["files"].append(
        {"path": str(path), "kind": classify_suffix(path), "url": args.source}
    )
    write_json(out / "manifest.json", manifest)
    print(f"saved {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
