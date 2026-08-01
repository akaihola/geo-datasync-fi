"""Classify a Finnish dataset by how it can be placed on a map, and report why.

The question that decides everything downstream is not "what format is this"
but "what is the strongest locational signal in it". A shapefile and a CSV of
municipality codes are both spatialisable, but one needs a reprojection and the
other needs a join to a reference polygon, and the accuracy they end up with
differs by kilometres. That distinction is what this script assigns.

Classes, strongest first:

  GEOMETRY     native geometry - reproject and go
  COORDINATES  coordinate columns - build points, CRS must be inferred
  ADDRESS      street addresses - geocodable to a point
  AREA_CODE    municipality or postal code - joinable to a reference polygon,
               accuracy is the area, not the object
  PLACE_NAME   a place name only - weakly geocodable, expect ambiguity
  NON_SPATIAL  no locational signal found

    python profile_source.py work/ --out work/profile.json --report work/report.md
"""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

import fi_crs
import fi_joinkeys as jk
from fi_common import classify_suffix, detect_roles, read_table, write_json

CLASS_ORDER = [
    "GEOMETRY", "COORDINATES", "ADDRESS", "AREA_CODE", "PLACE_NAME", "NON_SPATIAL"
]

CLASS_GUIDANCE = {
    "GEOMETRY": (
        "Reproject to EPSG:3067 and publish. Verify the declared CRS against the "
        "coordinate values before trusting it."
    ),
    "COORDINATES": (
        "Build point geometry from the coordinate columns. The CRS is the risk "
        "here: confirm the detection below before converting."
    ),
    "ADDRESS": (
        "Geocode with the NLS geocoding service. Expect a few percent of rows to "
        "fail on typos and former addresses; keep the failures visible."
    ),
    "AREA_CODE": (
        "Join to a reference polygon layer. The result locates the row to an area, "
        "not to a point - do not present it as a precise position."
    ),
    "PLACE_NAME": (
        "Geocode against the Geographic Names Register, but treat every hit as a "
        "candidate: Finnish place names repeat across the country."
    ),
    "NON_SPATIAL": (
        "No locational signal. Either it genuinely is not spatial data, or the key "
        "that would place it lives in a companion file."
    ),
}

JOIN_KEY_ROLES = {
    "municipality_code": "municipality_code",
    "postcode": "postcode",
    "property_id": "property_id",
    "building_id": "building_id",
}


def unpack_archives(paths: list[Path], workdir: Path) -> list[Path]:
    """Extract zips so a shapefile inside one can be profiled like any other file."""
    result: list[Path] = []
    for path in paths:
        if path.suffix.lower() != ".zip":
            result.append(path)
            continue
        target = workdir / f"{path.stem}_unzipped"
        target.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(path) as archive:
                archive.extractall(target)
        except zipfile.BadZipFile:
            result.append(path)
            continue
        for member in sorted(target.rglob("*")):
            if member.is_file() and classify_suffix(member) != "unknown":
                result.append(member)
    return result


def profile_vector(path: Path) -> dict:
    import geopandas as gpd

    frame = gpd.read_file(path)
    declared = None
    if frame.crs is not None:
        try:
            declared = frame.crs.to_epsg()
        except Exception:  # pragma: no cover - exotic or broken CRS definitions
            declared = None

    geometry = frame.geometry
    valid = geometry[~geometry.is_empty & geometry.notna()]
    sample = [
        (point.x, point.y)
        for point in valid.head(500).representative_point()
    ]
    ranked = fi_crs.detect(sample, declared_epsg=declared)
    best = ranked[0] if ranked else None

    info = {
        "kind": "vector",
        "class": "GEOMETRY" if len(valid) else "NON_SPATIAL",
        "features": int(len(frame)),
        "features_with_geometry": int(len(valid)),
        "geometry_types": sorted({str(t) for t in geometry.geom_type.dropna().unique()}),
        "declared_epsg": declared,
        "declared_crs": str(frame.crs) if frame.crs is not None else None,
        "columns": [c for c in frame.columns if c != frame.geometry.name],
        "bbox": [round(v, 3) for v in frame.total_bounds] if len(valid) else None,
        "warnings": [],
    }
    if best:
        info["crs_detection"] = {
            "epsg": best.epsg,
            "name": best.name,
            "swap_xy": best.swap_xy,
            "inside_fraction": round(best.inside_fraction, 4),
            "confident": best.confident,
        }
        info["warnings"].extend(best.warnings)
    if declared is None:
        info["warnings"].append(
            "The file carries no CRS definition. Nothing downstream can be trusted "
            "until one is established - the detection above is a proposal, not a fact."
        )
    if len(valid) < len(frame):
        info["warnings"].append(
            f"{len(frame) - len(valid)} of {len(frame)} features have empty geometry."
        )
    return info, frame


def profile_table(path: Path) -> dict:
    frame, dialect = read_table(path)
    roles = detect_roles(list(frame.columns))
    info = {
        "kind": "tabular",
        "rows": int(len(frame)),
        "columns": list(frame.columns),
        "detected_roles": roles,
        "dialect": dialect.as_dict() if dialect else None,
        "warnings": [],
    }

    # Strongest signal wins; the weaker ones are still reported because they are
    # useful as a cross-check on the strong one.
    if "geometry_wkt" in roles:
        info["class"] = "GEOMETRY"
    elif {"easting", "northing"} <= roles.keys() or {"longitude", "latitude"} <= roles.keys():
        info["class"] = "COORDINATES"
    elif "address" in roles:
        info["class"] = "ADDRESS"
    elif "property_id" in roles or "building_id" in roles:
        info["class"] = "AREA_CODE"
    elif "municipality_code" in roles or "postcode" in roles or "municipality_name" in roles:
        info["class"] = "AREA_CODE"
    elif "place_name" in roles:
        info["class"] = "PLACE_NAME"
    else:
        info["class"] = "NON_SPATIAL"

    if info["class"] == "COORDINATES":
        if {"longitude", "latitude"} <= roles.keys():
            first, second = roles["longitude"], roles["latitude"]
        else:
            first, second = roles["easting"], roles["northing"]
        pairs = list(
            zip(
                frame[first].head(500).tolist(),
                frame[second].head(500).tolist(),
            )
        )
        ranked = fi_crs.detect(pairs)
        if ranked:
            best = ranked[0]
            info["coordinate_columns"] = {"first": first, "second": second}
            info["crs_detection"] = {
                "epsg": best.epsg,
                "name": best.name,
                "swap_xy": best.swap_xy,
                "inside_fraction": round(best.inside_fraction, 4),
                "confident": best.confident,
            }
            info["warnings"].extend(best.warnings)
            if best.swap_xy:
                info["warnings"].append(
                    f"Column {first!r} holds the northing and {second!r} the easting, "
                    "despite their names. Magnitude decides this, not the header."
                )
            info["alternative_crs"] = [
                {"epsg": g.epsg, "name": g.name, "inside_fraction": round(g.inside_fraction, 4)}
                for g in ranked[1:4]
            ]
        else:
            info["warnings"].append(
                "Coordinate columns were found but no values could be parsed from them."
            )
            info["class"] = "NON_SPATIAL"

    # Join keys are worth normalising even when the dataset already has geometry:
    # they are what lets it be compared against a reference layer afterwards.
    join_keys = {}
    for role, key in JOIN_KEY_ROLES.items():
        if role in roles:
            _, report = jk.normalise_series(frame[roles[role]], key)
            report["column"] = roles[role]
            join_keys[key] = report
            if report["success_rate"] < 0.9 and report["rows"] > report["blank"]:
                info["warnings"].append(
                    f"Only {report['success_rate']:.0%} of {roles[role]!r} values are "
                    f"valid as {key}; the join will silently lose the rest."
                )
    info["join_keys"] = join_keys

    # Republishing a personal identity code would be a serious disclosure, and it
    # is cheap to check, so it is checked on every run rather than on request.
    pii_columns = []
    for column in frame.columns:
        sample = frame[column].dropna().astype(str).head(200)
        if any(jk.looks_like_personal_id(v) for v in sample):
            pii_columns.append(column)
    if pii_columns:
        info["personal_data_columns"] = pii_columns
        info["warnings"].append(
            f"Values matching a henkilotunnus were found in {pii_columns}. Do not "
            "publish these columns; remove or pseudonymise them first."
        )
    return info, frame


def render_report(profile: dict) -> str:
    lines = ["# Spatial fitness assessment", ""]
    dataset = profile.get("dataset")
    if dataset:
        lines += [
            f"**Dataset:** {dataset.get('title') or dataset.get('name')}",
            f"**Publisher:** {dataset.get('publisher') or 'not stated'}",
            f"**Licence:** {dataset.get('licence_title') or dataset.get('licence_id') or '**not stated**'}",
            f"**Last modified:** {dataset.get('last_modified') or 'unknown'}",
            "",
        ]
        if not (dataset.get("licence_id") or dataset.get("licence_title")):
            lines += [
                "> No licence is recorded for this dataset. Absence of a licence is not "
                "permission - confirm the terms with the publisher before redistributing.",
                "",
            ]

    lines += [f"**Overall class:** `{profile.get('overall_class', 'UNKNOWN')}`", ""]
    lines += [CLASS_GUIDANCE.get(profile.get("overall_class", ""), ""), ""]

    for entry in profile.get("files", []):
        lines += [f"## {Path(entry['path']).name}", ""]
        lines.append(f"- Class: `{entry.get('class')}` ({entry.get('kind')})")
        if entry.get("rows") is not None:
            lines.append(f"- Rows: {entry['rows']}")
        if entry.get("features") is not None:
            lines.append(
                f"- Features: {entry['features']} "
                f"({entry.get('features_with_geometry')} with geometry), "
                f"types {', '.join(entry.get('geometry_types') or []) or 'none'}"
            )
        detection = entry.get("crs_detection")
        if detection:
            declared = entry.get("declared_epsg")
            lines.append(
                f"- CRS: declared "
                f"{('EPSG:' + str(declared)) if declared else 'none'}, "
                f"values fit EPSG:{detection['epsg']} ({detection['name']}) at "
                f"{detection['inside_fraction']:.0%}"
                + (", axis order reversed" if detection.get("swap_xy") else "")
            )
        if entry.get("detected_roles"):
            roles = ", ".join(f"{k}=`{v}`" for k, v in entry["detected_roles"].items())
            lines.append(f"- Detected roles: {roles}")
        for key, report in (entry.get("join_keys") or {}).items():
            lines.append(
                f"- Join key `{key}` from `{report['column']}`: "
                f"{report['valid']}/{report['rows'] - report['blank']} valid "
                f"({report['success_rate']:.0%}), {report['blank']} blank"
            )
        if entry.get("warnings"):
            lines += ["", "**Warnings**", ""]
            lines += [f"- {w}" for w in entry["warnings"]]
        lines.append("")

    lines += ["## Recommended next step", "", profile.get("recommendation", ""), ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "target", type=Path, help="a work directory containing manifest.json, or a file"
    )
    parser.add_argument("--out", type=Path, help="path for profile.json")
    parser.add_argument("--report", type=Path, help="path for the markdown report")
    args = parser.parse_args(argv)

    workdir = args.target if args.target.is_dir() else args.target.parent
    manifest_path = workdir / "manifest.json"
    dataset_meta = None
    if args.target.is_dir() and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        paths = [Path(f["path"]) for f in manifest.get("files", [])]
        dataset_meta = manifest.get("dataset")
    else:
        paths = [args.target]

    paths = unpack_archives([p for p in paths if p.exists()], workdir)
    if not paths:
        print("nothing to profile - no readable files found")
        return 1

    profile = {"dataset": dataset_meta, "files": []}
    for path in paths:
        kind = classify_suffix(path)
        try:
            if kind == "vector":
                info, _ = profile_vector(path)
            elif kind == "tabular":
                info, _ = profile_table(path)
            else:
                info = {
                    "kind": kind,
                    "class": "NON_SPATIAL" if kind != "raster" else "GEOMETRY",
                    "warnings": [
                        "Raster data is outside this skill's scope; convert it with "
                        "gdalwarp -t_srs EPSG:3067 and describe it in metadata by hand."
                        if kind == "raster"
                        else f"Unrecognised file type {path.suffix!r}."
                    ],
                }
        except Exception as error:  # a broken file should not lose the whole run
            info = {
                "kind": kind,
                "class": "NON_SPATIAL",
                "warnings": [f"Could not read this file: {error}"],
            }
        info["path"] = str(path)
        profile["files"].append(info)

    classes = [f.get("class", "NON_SPATIAL") for f in profile["files"]]
    profile["overall_class"] = min(classes, key=CLASS_ORDER.index) if classes else "NON_SPATIAL"
    profile["recommendation"] = CLASS_GUIDANCE.get(profile["overall_class"], "")

    out = args.out or workdir / "profile.json"
    write_json(out, profile)
    report_text = render_report(profile)
    report_path = args.report or workdir / "report.md"
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    Path(report_path).write_text(report_text, encoding="utf-8")

    print(f"overall class: {profile['overall_class']}")
    for entry in profile["files"]:
        print(f"  {Path(entry['path']).name}: {entry['class']}")
        for warning in entry.get("warnings", []):
            print(f"    ! {warning}")
    print(f"\nprofile: {out}\nreport:  {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
