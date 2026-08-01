"""Turn a profiled Finnish dataset into interoperable output.

Produces, from whatever the profile found:
  <name>_3067.gpkg     EPSG:3067 GeoPackage - the archival, analysis-ready copy
  <name>_wgs84.geojson EPSG:4326 lon/lat GeoJSON - what web maps and RFC 7946 want
  transform.json       what was done, including every assumption made

The CRS is the one thing this refuses to guess quietly. If the profile's
detection was not confident and no --source-epsg was given, the run stops rather
than producing a plausible-looking file in the wrong place on Earth.

    python transform_source.py work/ --out-dir out/
    python transform_source.py work/source/points.csv --source-epsg 2393 --out-dir out/
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import fi_crs
import fi_joinkeys as jk
from fi_common import classify_suffix, detect_roles, read_table, write_json

TARGET_EPSG = 3067
WEB_EPSG = 4326

JOIN_KEY_ROLES = {
    "municipality_code": "municipality_code",
    "postcode": "postcode",
    "property_id": "property_id",
    "building_id": "building_id",
}


def load_vector(path: Path, source_epsg: int | None, notes: list[str]):
    import geopandas as gpd

    frame = gpd.read_file(path)
    if frame.crs is None:
        if source_epsg is None:
            raise SystemExit(
                f"{path.name} has no CRS and none was supplied. Re-run with "
                "--source-epsg once you have established it (profile.json proposes one)."
            )
        frame = frame.set_crs(epsg=source_epsg)
        notes.append(f"No CRS in the file; EPSG:{source_epsg} applied as instructed.")
    elif source_epsg is not None:
        declared = None
        try:
            declared = frame.crs.to_epsg()
        except Exception:
            pass
        if declared != source_epsg:
            # Overriding is the whole point of the flag - a mislabelled file is
            # the common case - but it must be stated in the output, because the
            # numbers do not change and nothing else records the decision.
            frame = frame.set_crs(epsg=source_epsg, allow_override=True)
            notes.append(
                f"CRS overridden: file declared EPSG:{declared}, treated as "
                f"EPSG:{source_epsg} on request."
            )
    return frame


def load_table(
    path: Path, source_epsg: int | None, swap_xy: bool | None, notes: list[str]
):
    import geopandas as gpd
    import pandas as pd
    from shapely import wkt as shapely_wkt

    frame, _ = read_table(path)
    roles = detect_roles(list(frame.columns))

    if "geometry_wkt" in roles:
        column = roles["geometry_wkt"]
        geometry = frame[column].map(
            lambda v: shapely_wkt.loads(v) if isinstance(v, str) and v.strip() else None
        )
        notes.append(f"Geometry parsed from WKT in column {column!r}.")
        if source_epsg is None:
            raise SystemExit(
                "WKT carries no CRS. Re-run with --source-epsg to state which one it is."
            )
        return gpd.GeoDataFrame(frame, geometry=geometry, crs=f"EPSG:{source_epsg}")

    # Output of geocode_fi.py takes precedence: it is the most recent, most
    # deliberate positioning of the rows.
    if {"geocode_x", "geocode_y"} <= set(frame.columns):
        first, second = "geocode_x", "geocode_y"
        declared = frame.get("geocode_crs")
        if source_epsg is None and declared is not None and len(declared):
            text = str(declared.dropna().iloc[0]) if declared.notna().any() else ""
            if text.upper().startswith("EPSG:"):
                source_epsg = int(text.split(":")[1])
                notes.append(f"CRS taken from the geocoder output: EPSG:{source_epsg}.")
        swap_xy = False if swap_xy is None else swap_xy
        matched = frame["geocode_status"].eq("ok") if "geocode_status" in frame else None
        if matched is not None and not matched.all():
            notes.append(
                f"{int((~matched).sum())} rows had no geocoding match and carry no "
                "geometry in the output."
            )
    elif {"longitude", "latitude"} <= roles.keys():
        first, second = roles["longitude"], roles["latitude"]
        source_epsg = source_epsg or 4326
    elif {"easting", "northing"} <= roles.keys():
        first, second = roles["easting"], roles["northing"]
    else:
        raise SystemExit(
            f"No coordinate columns found in {path.name}. Detected roles: {roles}. "
            "If the rows are addresses, geocode them first with geocode_fi.py."
        )

    xs = pd.to_numeric(frame[first], errors="coerce")
    ys = pd.to_numeric(frame[second], errors="coerce")

    if source_epsg is None or swap_xy is None:
        ranked = fi_crs.detect(list(zip(xs.head(500), ys.head(500))))
        if not ranked or not ranked[0].confident:
            raise SystemExit(
                "The CRS of these coordinates could not be determined with confidence. "
                "Inspect work/report.md and re-run with an explicit --source-epsg "
                "(add --swap-xy if the first column holds the northing)."
            )
        best = ranked[0]
        source_epsg = source_epsg or best.epsg
        swap_xy = best.swap_xy if swap_xy is None else swap_xy
        notes.append(
            f"CRS inferred as EPSG:{source_epsg} from coordinate magnitudes "
            f"({best.inside_fraction:.0%} of sampled points inside its Finnish envelope)."
        )

    if swap_xy:
        xs, ys = ys, xs
        notes.append(
            f"Columns {first!r}/{second!r} swapped: the first holds the northing."
        )

    geometry = gpd.points_from_xy(xs, ys)
    result = gpd.GeoDataFrame(frame, geometry=geometry, crs=f"EPSG:{source_epsg}")
    # Rows without usable coordinates keep their attributes but lose geometry, so
    # the row count stays comparable with the source.
    result.loc[xs.isna() | ys.isna(), result.geometry.name] = None
    missing = int((xs.isna() | ys.isna()).sum())
    if missing:
        notes.append(f"{missing} rows had unusable coordinates and carry no geometry.")
    return result


def add_join_keys(frame, notes: list[str]) -> dict:
    """Append normalised join-key columns and report how well they normalised."""
    roles = detect_roles([c for c in frame.columns if c != frame.geometry.name])
    reports = {}
    for role, key in JOIN_KEY_ROLES.items():
        if role not in roles:
            continue
        column = roles[role]
        values, report = jk.normalise_series(frame[column], key)
        report["column"] = column
        target = f"{key}_normalised"
        frame[target] = values
        reports[key] = report
        notes.append(
            f"Added {target!r} from {column!r}: {report['valid']} valid, "
            f"{report['flagged']} flagged, {report['blank']} blank."
        )
        if key == "property_id":
            # Both forms are carried because the cadastral API keys on the
            # database form while humans and printed documents use the other.
            presentation = [
                jk.property_id(v, form="presentation").value if v else None
                for v in values
            ]
            frame["property_id_presentation"] = presentation
    return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="work directory or a single file")
    parser.add_argument("--out-dir", type=Path, default=Path("out"))
    parser.add_argument("--name", help="basename for the outputs")
    parser.add_argument(
        "--source-epsg", type=int, help="state the source CRS instead of inferring it"
    )
    parser.add_argument(
        "--swap-xy",
        action="store_true",
        default=None,
        help="the first coordinate column holds the northing",
    )
    parser.add_argument(
        "--layer", help="layer name inside the GeoPackage (defaults to --name)"
    )
    args = parser.parse_args(argv)

    if args.target.is_dir():
        profile_path = args.target / "profile.json"
        if not profile_path.exists():
            print(
                f"no profile.json in {args.target}; run profile_source.py first",
                file=sys.stderr,
            )
            return 2
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        candidates = [
            Path(f["path"])
            for f in profile.get("files", [])
            if f.get("class") in {"GEOMETRY", "COORDINATES"}
        ]
        if not candidates:
            print(
                "The profile found no file that can be placed on a map directly.\n"
                "For ADDRESS data run geocode_fi.py first; for AREA_CODE data join to a "
                "reference layer (see references/reference-layers.md).",
                file=sys.stderr,
            )
            return 2
        source = candidates[0]
        # A confident detection in the profile is a fine default; an unconfident
        # one is left unset so the load path stops and asks.
        if args.source_epsg is None:
            for entry in profile["files"]:
                if Path(entry["path"]) == source:
                    detection = entry.get("crs_detection") or {}
                    if detection.get("confident"):
                        args.source_epsg = detection["epsg"]
                        if args.swap_xy is None and detection.get("swap_xy"):
                            args.swap_xy = True
                    break
    else:
        source = args.target
        profile = {}

    notes: list[str] = []
    kind = classify_suffix(source)
    if kind == "vector":
        frame = load_vector(source, args.source_epsg, notes)
    elif kind == "tabular":
        frame = load_table(source, args.source_epsg, args.swap_xy, notes)
    else:
        print(f"cannot transform {kind} input {source}", file=sys.stderr)
        return 2

    join_reports = add_join_keys(frame, notes)

    invalid = int((~frame.geometry.is_valid & frame.geometry.notna()).sum())
    if invalid:
        notes.append(
            f"{invalid} geometries are invalid (self-intersections or similar). They "
            "are written as-is; run make_valid before spatial joins."
        )

    name = args.name or source.stem
    out_dir = args.out_dir
    data_dir = out_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    target = frame.to_crs(epsg=TARGET_EPSG)
    gpkg = data_dir / f"{name}_3067.gpkg"
    target.to_file(gpkg, layer=args.layer or name, driver="GPKG")

    web = frame.to_crs(epsg=WEB_EPSG)
    geojson = data_dir / f"{name}_wgs84.geojson"
    web.to_file(geojson, driver="GeoJSON")
    notes.append(
        "GeoJSON is written in EPSG:4326 lon/lat as RFC 7946 requires; the "
        "GeoPackage in EPSG:3067 is the copy to keep."
    )

    summary = {
        "source": str(source),
        "rows": int(len(frame)),
        "with_geometry": int(frame.geometry.notna().sum()),
        "source_crs": str(frame.crs),
        "outputs": {"geopackage": str(gpkg), "geojson": str(geojson)},
        "geometry_types": sorted({str(t) for t in frame.geom_type.dropna().unique()}),
        "bbox_3067": [round(v, 2) for v in target.total_bounds]
        if frame.geometry.notna().any()
        else None,
        "join_keys": join_reports,
        "notes": notes,
    }
    write_json(out_dir / "transform.json", summary)

    print(f"wrote {gpkg}")
    print(f"wrote {geojson}")
    print(f"{summary['with_geometry']}/{summary['rows']} rows have geometry")
    for note in notes:
        print(f"  - {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
