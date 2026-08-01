"""Offline tests for the fi-open-geodata helper modules.

These deliberately avoid the network: everything they check (CRS envelopes,
Finnish CSV conventions, join-key formats) has to work on a laptop with no API
key, and that is also the part most likely to break silently.

Run: python -m pytest tests/ -q   (or: python tests/test_fi_modules.py)
"""

import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1] / ".claude/skills/fi-open-geodata/scripts"
sys.path.insert(0, str(SKILL))

import fi_common  # noqa: E402
import fi_crs  # noqa: E402
import fi_joinkeys as jk  # noqa: E402

# Helsinki Senate Square, converted with pyproj so the fixtures cannot drift
# away from what the projection actually does.
from pyproj import Transformer  # noqa: E402

HKI_LON, HKI_LAT = 24.9522, 60.1699
_to3067 = Transformer.from_crs(4326, 3067, always_xy=True)
_to2393 = Transformer.from_crs(4326, 2393, always_xy=True)
_to3857 = Transformer.from_crs(4326, 3857, always_xy=True)
HKI_3067 = _to3067.transform(HKI_LON, HKI_LAT)
HKI_2393 = _to2393.transform(HKI_LON, HKI_LAT)
HKI_3857 = _to3857.transform(HKI_LON, HKI_LAT)

# A spread of points so a detection cannot pass on one lucky coordinate.
CITIES_4326 = [
    (24.9522, 60.1699),  # Helsinki
    (23.7610, 61.4978),  # Tampere
    (25.4651, 65.0121),  # Oulu
    (27.0280, 69.9080),  # Utsjoki, the far north
    (19.9348, 60.0971),  # Maarianhamina, the far south-west
]
CITIES_3067 = [_to3067.transform(*p) for p in CITIES_4326]


def check(label, condition, detail=""):
    status = "ok  " if condition else "FAIL"
    print(f"{status} {label}" + (f" - {detail}" if detail else ""))
    return bool(condition)


def test_crs():
    results = []
    guess = fi_crs.best_guess(CITIES_3067)
    results.append(
        check(
            "TM35FIN points detected as EPSG:3067",
            guess.epsg == 3067 and not guess.swap_xy and guess.confident,
            guess.describe(),
        )
    )

    swapped = [(n, e) for e, n in CITIES_3067]
    guess = fi_crs.best_guess(swapped)
    results.append(
        check(
            "reversed axis order is caught",
            guess.epsg == 3067 and guess.swap_xy,
            guess.describe(),
        )
    )

    guess = fi_crs.best_guess([HKI_2393] * 5)
    results.append(
        check("YKJ points detected as EPSG:2393", guess.epsg == 2393, guess.describe())
    )

    guess = fi_crs.best_guess(CITIES_4326)
    results.append(
        check("lon/lat detected as EPSG:4326", guess.epsg == 4326, guess.describe())
    )

    guess = fi_crs.best_guess([HKI_3857] * 5)
    results.append(
        check("web mercator detected as EPSG:3857", guess.epsg == 3857, guess.describe())
    )

    # A file that says 4326 but holds TM35FIN metres is the failure mode that
    # matters most, because it converts without raising and lands off Africa.
    guess = fi_crs.best_guess(CITIES_3067, declared_epsg=4326)
    results.append(
        check(
            "declared CRS contradicting the values raises a warning",
            guess.epsg == 3067 and any("suspect" in w for w in guess.warnings),
            "; ".join(guess.warnings),
        )
    )

    # ETRS-GK25 (EPSG:3879), a common municipal planning CRS.
    gk = Transformer.from_crs(4326, 3879, always_xy=True).transform(HKI_LON, HKI_LAT)
    guess = fi_crs.best_guess([gk] * 5)
    results.append(
        check("ETRS-GK25 detected as EPSG:3879", guess.epsg == 3879, guess.describe())
    )

    results.append(
        check("3047 and 5048 recognised as TM35FIN aliases",
              fi_crs.is_target(3047) and fi_crs.is_target(5048) and not fi_crs.is_target(2393))
    )
    return results


def test_csv(tmp: Path):
    results = []
    # The archetypal Finnish municipal export: cp1252, semicolons, decimal
    # commas, Scandinavian column names, northing in the column called "x".
    content = (
        "Kohteen nimi;X-koordinaatti;Y-koordinaatti;Kuntanumero;Postinumero;Lisätieto\r\n"
        "Rautatieasema;6672841,5;385165,2;91;100;Käytössä\r\n"
        "Kirjasto Oodi;6673120,8;385000,1;91;100;Käytössä\r\n"
        "Tuomiokirkko;6672500,0;385400,9;91;170;Ei käytössä\r\n"
    )
    path = tmp / "kohteet.csv"
    path.write_bytes(content.encode("cp1252"))

    dialect = fi_common.sniff_csv(path)
    results.append(
        check(
            "cp1252 encoding, semicolon delimiter and comma decimal sniffed",
            dialect.delimiter == ";"
            and dialect.decimal == ","
            and dialect.encoding == "cp1252",
            f"{dialect.delimiter!r} / {dialect.decimal!r} / {dialect.encoding}",
        )
    )
    results.append(
        check(
            "Scandinavian characters survive the decode",
            "Lisätieto" in dialect.header,
            str(dialect.header),
        )
    )

    frame, _ = fi_common.read_table(path)
    results.append(
        check(
            "decimal commas parsed as floats",
            abs(float(frame["X-koordinaatti"].iloc[0]) - 6672841.5) < 0.01,
            repr(frame["X-koordinaatti"].iloc[0]),
        )
    )

    roles = fi_common.detect_roles(list(frame.columns))
    results.append(
        check(
            "column roles detected",
            roles.get("easting") == "X-koordinaatti"
            and roles.get("northing") == "Y-koordinaatti"
            and roles.get("municipality_code") == "Kuntanumero"
            and roles.get("postcode") == "Postinumero",
            str(roles),
        )
    )

    # The columns are named x/y but hold northing/easting - magnitude must win
    # over the names.
    swap = fi_common.orient_pair(frame["X-koordinaatti"], frame["Y-koordinaatti"])
    results.append(check("mislabelled x/y detected by magnitude", swap is True))

    # Plain UTF-8 comma CSV must not be mangled by the same sniffer.
    plain = tmp / "plain.csv"
    plain.write_text("name,lon,lat\nA,24.95,60.17\nB,23.76,61.50\n", encoding="utf-8")
    d2 = fi_common.sniff_csv(plain)
    results.append(
        check(
            "plain comma CSV still sniffed correctly",
            d2.delimiter == "," and d2.decimal == ".",
            f"{d2.delimiter!r} / {d2.decimal!r}",
        )
    )
    f2, _ = fi_common.read_table(plain)
    roles2 = fi_common.detect_roles(list(f2.columns))
    results.append(
        check(
            "lon/lat roles detected",
            roles2.get("longitude") == "lon" and roles2.get("latitude") == "lat",
            str(roles2),
        )
    )
    return results


def test_joinkeys():
    results = []
    results.append(
        check("kuntanumero zero-padded", jk.municipality_code(91).value == "091")
    )
    results.append(
        check("kuntanumero rejects overlong", not jk.municipality_code("9199").valid)
    )
    results.append(check("postinumero zero-padded", jk.postcode(100).value == "00100"))

    results.append(
        check(
            "kiinteistotunnus presentation -> database",
            jk.property_id("91-1-1-1").value == "09100100010001",
        )
    )
    results.append(
        check(
            "kiinteistotunnus database -> presentation",
            jk.property_id("09100100010001", form="presentation").value == "91-1-1-1",
        )
    )
    results.append(
        check(
            "kiinteistotunnus round-trips",
            jk.property_id(
                jk.property_id("853-416-3-33", form="database").value,
                form="presentation",
            ).value
            == "853-416-3-33",
        )
    )
    results.append(
        check("kiinteistotunnus rejects junk", not jk.property_id("91-1-1").valid)
    )

    # Build a self-consistent PRT and confirm a single-character corruption is
    # caught. The algorithm itself is documented in references/join-keys.md.
    body = "100012345"
    good = body + jk.CHECK_CHARS[int(body) % 31]
    bad = body + ("0" if good[-1] != "0" else "1")
    results.append(check("valid rakennustunnus accepted", jk.building_id(good).valid))
    results.append(
        check("corrupted rakennustunnus flagged", not jk.building_id(bad).valid)
    )
    results.append(
        check(
            "corrupted rakennustunnus still returns its value",
            jk.building_id(bad).value == bad,
        )
    )

    # Y-tunnus with a known-good checksum: 0201256-6 is a widely published test
    # value for the algorithm.
    results.append(
        check("y-tunnus checksum accepted", jk.business_id("02012566").value == "0201256-6")
    )
    results.append(
        check("y-tunnus bad checksum rejected", not jk.business_id("02012565").valid)
    )

    hetu_body = "010101" + "900"
    hetu = "010101-900" + jk.CHECK_CHARS[int(hetu_body) % 31]
    results.append(check("henkilotunnus shape detected", jk.looks_like_personal_id(hetu)))
    results.append(
        check(
            "ordinary text is not mistaken for a henkilotunnus",
            not jk.looks_like_personal_id("Mannerheimintie 100"),
        )
    )

    values, report = jk.normalise_series([91, "091", "", "9999", 49], "municipality_code")
    results.append(
        check(
            "series report counts blanks and failures separately",
            report["rows"] == 5
            and report["blank"] == 1
            and report["valid"] == 3
            and report["flagged"] == 1,
            str({k: report[k] for k in ("rows", "blank", "valid", "flagged")}),
        )
    )
    return results


def main():
    import tempfile

    all_results = []
    print("== CRS detection")
    all_results += test_crs()
    print("\n== Finnish CSV handling")
    with tempfile.TemporaryDirectory() as tmp:
        all_results += test_csv(Path(tmp))
    print("\n== Join keys")
    all_results += test_joinkeys()

    failed = all_results.count(False)
    print(f"\n{len(all_results) - failed}/{len(all_results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
