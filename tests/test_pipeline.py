"""End-to-end tests for the fi-open-geodata pipeline scripts.

These run profile -> transform -> metadata on synthetic data that reproduces the
failure modes the skill exists to catch: legacy YKJ coordinates, an axis order
that contradicts the column names, a file whose declared CRS is wrong, and a
personal identity code that must block publication.

No network access is required.

Run: python tests/test_pipeline.py
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/fi-open-geodata/scripts"
PYTHON = sys.executable

CHECK_CHARS = "0123456789ABCDEFHJKLMNPRSTUVWXY"


def run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, str(SCRIPTS / script), *args],
        capture_output=True,
        text=True,
        cwd=SCRIPTS,
    )


def check(label, condition, detail=""):
    print(("ok   " if condition else "FAIL ") + label + (f" - {detail}" if detail else ""))
    return bool(condition)


def make_ykj_csv(path: Path) -> None:
    """Municipal export in cp1252/semicolon/decimal-comma with YKJ coordinates.

    The northing sits in the column called X, which is the orientation trap.
    """
    rows = [
        ("Rautatieasema", 6675000.4, 3386000.2, 91, 100),
        ("Kirjasto Oodi", 6675300.9, 3385800.5, 91, 100),
        ("Tuomiokirkko", 6674800.1, 3386200.7, 91, 170),
        ("Tampere-talo", 6821000.0, 3327500.0, 837, 33100),
        ("Oulun linna", 7213000.0, 3428000.0, 564, 90100),
    ]
    text = "Kohteen nimi;X-koordinaatti;Y-koordinaatti;Kuntanumero;Postinumero;Lisätieto\r\n"
    for name, x, y, kunta, pno in rows:
        text += (
            f"{name};{str(x).replace('.', ',')};{str(y).replace('.', ',')};"
            f"{kunta};{pno};Käytössä\r\n"
        )
    path.write_bytes(text.encode("cp1252"))


def test_coordinate_pipeline(tmp: Path):
    results = []
    source = tmp / "kohteet.csv"
    make_ykj_csv(source)

    proc = run("profile_source.py", str(source), "--out", str(tmp / "profile.json"),
               "--report", str(tmp / "report.md"))
    results.append(check("profile exits cleanly", proc.returncode == 0, proc.stderr[-200:]))

    profile = json.loads((tmp / "profile.json").read_text(encoding="utf-8"))
    entry = profile["files"][0]
    results.append(
        check("classified as COORDINATES", entry["class"] == "COORDINATES", entry["class"])
    )
    results.append(
        check(
            "legacy YKJ detected rather than assumed TM35FIN",
            entry["crs_detection"]["epsg"] == 2393,
            str(entry["crs_detection"]),
        )
    )
    results.append(
        check("reversed axis order detected", entry["crs_detection"]["swap_xy"] is True)
    )
    results.append(
        check(
            "municipality codes normalised with leading zeros",
            entry["join_keys"]["municipality_code"]["valid"] == 5,
            str(entry["join_keys"]["municipality_code"]),
        )
    )

    proc = run("transform_source.py", str(source), "--out-dir", str(tmp / "out"),
               "--name", "kohteet")
    results.append(check("transform exits cleanly", proc.returncode == 0, proc.stderr[-300:]))

    gpkg = tmp / "out/data/kohteet_3067.gpkg"
    geojson = tmp / "out/data/kohteet_wgs84.geojson"
    results.append(check("GeoPackage written", gpkg.exists()))
    results.append(check("GeoJSON written", geojson.exists()))

    import geopandas as gpd

    frame = gpd.read_file(gpkg)
    results.append(check("GeoPackage is EPSG:3067", frame.crs.to_epsg() == 3067))
    results.append(
        check(
            "kuntanumero 91 became 091",
            set(frame["municipality_code_normalised"]) == {"091", "837", "564"},
            str(sorted(set(frame["municipality_code_normalised"]))),
        )
    )

    # The real test of the conversion: the first three rows must land within a
    # kilometre of Helsinki Cathedral, not merely somewhere inside Finland.
    web = json.loads(geojson.read_text(encoding="utf-8"))
    helsinki = [
        f for f in web["features"] if f["properties"]["Kohteen nimi"] == "Tuomiokirkko"
    ][0]
    lon, lat = helsinki["geometry"]["coordinates"]
    results.append(
        check(
            "Helsinki row lands in Helsinki after the KKJ conversion",
            abs(lon - 24.95) < 0.05 and abs(lat - 60.17) < 0.05,
            f"lon={lon:.4f} lat={lat:.4f}",
        )
    )

    summary = json.loads((tmp / "out/transform.json").read_text(encoding="utf-8"))
    results.append(
        check(
            "assumptions are recorded in the transform summary",
            any("EPSG:2393" in n for n in summary["notes"])
            and any("swapped" in n for n in summary["notes"]),
            str(summary["notes"][:2]),
        )
    )

    proc = run(
        "make_metadata.py", str(tmp / "out/transform.json"),
        "--title", "Testikohteet", "--abstract", "Synteettinen testiaineisto.",
        "--theme", "Buildings", "--organisation", "Testikunta",
        "--email", "test@example.fi", "--access-url", "https://example.fi/d.gpkg",
        "--out", str(tmp / "out/metadata/test.xml"),
    )
    results.append(check("metadata exits cleanly", proc.returncode == 0, proc.stderr[-200:]))

    from xml.etree import ElementTree as ET

    ns = {"gmd": "http://www.isotc211.org/2005/gmd", "gco": "http://www.isotc211.org/2005/gco"}
    root = ET.parse(tmp / "out/metadata/test.xml").getroot()
    west = float(root.find(".//gmd:westBoundLongitude/gco:Decimal", ns).text)
    north = float(root.find(".//gmd:northBoundLatitude/gco:Decimal", ns).text)
    results.append(
        check(
            "bounding box is WGS84 degrees, not national-grid metres",
            19 < west < 32 and 59 < north < 71,
            f"west={west} north={north}",
        )
    )
    results.append(
        check(
            "theme maps to its INSPIRE topic category",
            root.find(".//gmd:MD_TopicCategoryCode", ns).text == "structure",
        )
    )
    results.append(
        check(
            "lineage records the CRS decision",
            "2393" in root.find(".//gmd:LI_Lineage/gmd:statement/gco:CharacterString", ns).text,
        )
    )
    return results


def test_mislabelled_vector(tmp: Path):
    """A file declaring EPSG:4326 while holding TM35FIN metres must be challenged."""
    import geopandas as gpd
    from shapely.geometry import Point

    path = tmp / "liar.gpkg"
    gpd.GeoDataFrame(
        {"nimi": ["a", "b", "c"]},
        geometry=[Point(385000, 6672000), Point(327500, 6821000), Point(428000, 7213000)],
        crs="EPSG:4326",
    ).to_file(path, driver="GPKG")

    run("profile_source.py", str(path), "--out", str(tmp / "p.json"),
        "--report", str(tmp / "r.md"))
    profile = json.loads((tmp / "p.json").read_text(encoding="utf-8"))
    entry = profile["files"][0]
    return [
        check("mislabelled vector still classifies as GEOMETRY", entry["class"] == "GEOMETRY"),
        check(
            "contradiction between declared and actual CRS is reported",
            any("suspect" in w for w in entry["warnings"]),
            "; ".join(entry["warnings"])[:120],
        ),
    ]


def test_personal_data_blocks(tmp: Path):
    """A henkilotunnus anywhere in the file has to surface before publication."""
    path = tmp / "osoitteet.csv"
    hetu = "010101-900" + CHECK_CHARS[int("010101900") % 31]
    path.write_text(
        "osoite,kunta,henkilotunnus\n"
        f"Mannerheimintie 100,Helsinki,{hetu}\n",
        encoding="utf-8",
    )
    run("profile_source.py", str(path), "--out", str(tmp / "p.json"),
        "--report", str(tmp / "r.md"))
    profile = json.loads((tmp / "p.json").read_text(encoding="utf-8"))
    entry = profile["files"][0]
    return [
        check("address data classified as ADDRESS", entry["class"] == "ADDRESS", entry["class"]),
        check(
            "personal identity code detected",
            entry.get("personal_data_columns") == ["henkilotunnus"],
            str(entry.get("personal_data_columns")),
        ),
    ]


def test_transform_refuses_addresses(tmp: Path):
    """Address rows have no coordinates; transform must say so, not invent them."""
    path = tmp / "a.csv"
    path.write_text("osoite,kunta\nKoulukatu 1,Tampere\n", encoding="utf-8")
    proc = run("transform_source.py", str(path), "--out-dir", str(tmp / "out"))
    message = proc.stdout + proc.stderr
    return [
        check("transform fails rather than guessing", proc.returncode != 0),
        check("failure points at the geocoding step", "geocode_fi.py" in message, message[-120:]),
    ]


def main():
    results = []
    cases = [
        ("coordinate pipeline (YKJ, swapped axes, join keys, metadata)",
         test_coordinate_pipeline),
        ("mislabelled vector CRS", test_mislabelled_vector),
        ("personal data guard", test_personal_data_blocks),
        ("refusal to guess a position", test_transform_refuses_addresses),
    ]
    with tempfile.TemporaryDirectory() as raw:
        for index, (label, func) in enumerate(cases):
            case_dir = Path(raw) / f"case{index}"
            case_dir.mkdir(parents=True, exist_ok=True)
            print(f"{'' if index == 0 else chr(10)}== {label}")
            results += func(case_dir)

    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
