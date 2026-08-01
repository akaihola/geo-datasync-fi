"""Geocode Finnish addresses and place names via the NLS geocoding service.

Endpoint: https://avoin-paikkatieto.maanmittauslaitos.fi/geocoding/v2/pelias/search
It follows the Pelias API with Finnish extensions (`crs`, `lang`), returns GeoJSON,
and defaults to EPSG:3067. An API key is required; get one from
https://omatili.maanmittauslaitos.fi and pass it via --api-key or NLS_API_KEY.

Two design choices worth knowing about:

* Results are cached to disk by query string. Geocoding the same municipal
  register twice is common while iterating on a pipeline, and the cache turns the
  second run into seconds without hammering a public service.
* Nothing is silently dropped. Every input row appears in the output with a
  status, so a 4% failure rate is visible in the audit file instead of becoming
  4% fewer rows on the map.

    python geocode_fi.py addresses.csv --column osoite --out geocoded.csv
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

BASE_URL = "https://avoin-paikkatieto.maanmittauslaitos.fi/geocoding/v2/pelias/search"
USER_AGENT = "fi-open-geodata/1.0"

#: Ordered from most to least precise. `addresses` are surveyed address points;
#: `interpolated-road-addresses` estimate a position along a road segment and are
#: the service default; `geographic-names` covers place names.
SOURCES = (
    "addresses",
    "interpolated-road-addresses",
    "geographic-names",
    "cadastral-units",
    "mapsheets-tm35",
)


class Geocoder:
    def __init__(
        self,
        api_key: str,
        crs: str = "EPSG:3067",
        sources: str = "addresses,interpolated-road-addresses",
        lang: str = "fi",
        cache_path: Path | None = None,
        delay: float = 0.1,
    ):
        self.api_key = api_key
        self.crs = crs
        self.sources = sources
        self.lang = lang
        self.delay = delay
        self.cache_path = cache_path
        self.cache: dict[str, dict] = {}
        if cache_path and Path(cache_path).exists():
            try:
                self.cache = json.loads(Path(cache_path).read_text(encoding="utf-8"))
            except (ValueError, OSError):
                self.cache = {}
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.calls = 0

    def _request(self, text: str) -> dict:
        params = {
            "text": text,
            "sources": self.sources,
            "crs": self.crs,
            "lang": self.lang,
            "size": 1,
            "api-key": self.api_key,
        }
        last_error = None
        for attempt in range(4):
            try:
                response = self.session.get(BASE_URL, params=params, timeout=30)
                if response.status_code == 429:
                    # Backing off rather than failing keeps a long batch alive;
                    # the service rate-limits per key, not per request.
                    time.sleep(2**attempt)
                    continue
                response.raise_for_status()
                self.calls += 1
                return response.json()
            except requests.RequestException as error:
                last_error = error
                time.sleep(2**attempt)
        raise RuntimeError(f"geocoding failed for {text!r}: {last_error}")

    def geocode(self, text: str) -> dict:
        """Return {status, x, y, label, source, confidence} for one query string."""
        query = (text or "").strip()
        if not query:
            return {"status": "empty"}
        if query in self.cache:
            return self.cache[query]

        try:
            payload = self._request(query)
        except RuntimeError as error:
            return {"status": "error", "detail": str(error)}
        time.sleep(self.delay)

        features = payload.get("features") or []
        if not features:
            result = {"status": "no_match"}
        else:
            feature = features[0]
            coords = (feature.get("geometry") or {}).get("coordinates") or []
            props = feature.get("properties") or {}
            if len(coords) < 2:
                result = {"status": "no_geometry"}
            else:
                result = {
                    "status": "ok",
                    "x": coords[0],
                    "y": coords[1],
                    "label": props.get("label") or props.get("name"),
                    "source": props.get("source"),
                    "confidence": props.get("confidence"),
                    "match_type": props.get("match_type"),
                }
        self.cache[query] = result
        return result

    def save_cache(self) -> None:
        if not self.cache_path:
            return
        Path(self.cache_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.cache_path).write_text(
            json.dumps(self.cache, ensure_ascii=False, indent=1), encoding="utf-8"
        )


def build_query(row, address_col: str, postcode_col: str | None, municipality_col: str | None) -> str:
    """Assemble the most specific query string the row supports.

    A bare street address is ambiguous across Finland - `Koulukatu 1` exists in
    dozens of municipalities - so the municipality is appended whenever the
    dataset carries one. This single change is usually the difference between a
    60% and a 95% hit rate.
    """
    parts = [str(row.get(address_col, "") or "").strip()]
    if postcode_col:
        code = str(row.get(postcode_col, "") or "").strip()
        if code:
            parts.append(code.zfill(5) if code.isdigit() else code)
    if municipality_col:
        name = str(row.get(municipality_col, "") or "").strip()
        if name:
            parts.append(name)
    return ", ".join(p for p in parts if p)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--column", required=True, help="column holding the address or name")
    parser.add_argument("--postcode-column")
    parser.add_argument("--municipality-column")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--audit", type=Path, help="CSV listing every row's outcome")
    parser.add_argument("--api-key", default=os.environ.get("NLS_API_KEY"))
    parser.add_argument("--crs", default="EPSG:3067")
    parser.add_argument("--sources", default="addresses,interpolated-road-addresses")
    parser.add_argument("--cache", type=Path, default=Path(".geocode-cache.json"))
    parser.add_argument("--limit", type=int, help="stop after N rows, for a trial run")
    args = parser.parse_args(argv)

    if not args.api_key:
        print(
            "An NLS API key is required. Pass --api-key or set NLS_API_KEY.\n"
            "Register at https://omatili.maanmittauslaitos.fi",
            file=sys.stderr,
        )
        return 2

    from fi_common import read_table

    frame, _ = read_table(args.path)
    if args.column not in frame.columns:
        print(f"column {args.column!r} not in {list(frame.columns)}", file=sys.stderr)
        return 2
    if args.limit:
        frame = frame.head(args.limit)

    coder = Geocoder(
        args.api_key, crs=args.crs, sources=args.sources, cache_path=args.cache
    )
    statuses, xs, ys, labels, confidences = [], [], [], [], []
    for _, row in frame.iterrows():
        query = build_query(
            row, args.column, args.postcode_column, args.municipality_column
        )
        result = coder.geocode(query)
        statuses.append(result.get("status"))
        xs.append(result.get("x"))
        ys.append(result.get("y"))
        labels.append(result.get("label"))
        confidences.append(result.get("confidence"))
    coder.save_cache()

    frame["geocode_status"] = statuses
    frame["geocode_x"] = xs
    frame["geocode_y"] = ys
    frame["geocode_label"] = labels
    frame["geocode_confidence"] = confidences
    frame["geocode_crs"] = args.crs

    args.out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.out, index=False, encoding="utf-8")

    total = len(frame)
    matched = statuses.count("ok")
    print(
        f"{matched}/{total} rows geocoded ({matched / total:.1%} hit rate), "
        f"{coder.calls} API calls"
    )
    failures = {s: statuses.count(s) for s in set(statuses) if s != "ok"}
    for status, count in sorted(failures.items(), key=lambda kv: -kv[1]):
        print(f"  {status}: {count}")
    if args.audit:
        audit = frame[frame["geocode_status"] != "ok"]
        audit.to_csv(args.audit, index=False, encoding="utf-8")
        print(f"unmatched rows written to {args.audit}")
    if matched < total:
        print(
            "\nUnmatched rows are kept in the output with an empty position. Decide "
            "explicitly whether to drop them - dropping silently understates coverage."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
