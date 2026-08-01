"""Shared helpers for reading Finnish open data files.

Finnish CSV exports are rarely plain UTF-8 comma-separated files. They come out
of Excel and municipal registers as cp1252 with semicolon separators and comma
decimal marks, and their column names are in Finnish, Swedish, English, or an
abbreviation of one of those. Getting this wrong turns coordinates into strings
and silently drops the fractional part, so the sniffing here is deliberately
explicit about what it concluded.
"""

from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
DELIMITERS = (";", ",", "\t", "|")

VECTOR_SUFFIXES = {
    ".gpkg", ".shp", ".geojson", ".json", ".gml", ".kml", ".kmz",
    ".gpx", ".fgb", ".tab", ".mif", ".dgn", ".dxf", ".parquet",
}
TABULAR_SUFFIXES = {".csv", ".tsv", ".txt", ".xlsx", ".xls", ".ods"}
RASTER_SUFFIXES = {".tif", ".tiff", ".jp2", ".png", ".asc", ".ecw"}


def normalise_name(name: str) -> str:
    """Fold a column name to a comparable key.

    Scandinavian letters are folded rather than stripped so that `pituusaste`
    and `pituusaste ` and `Pituusaste (WGS84)` all collapse together, and so
    that a column called `Ita` matches one called `Itä`.
    """
    text = str(name).strip().lower()
    text = text.replace("ä", "a").replace("ö", "o").replace("å", "a")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


# Column role synonyms, keyed by role. Matching is done on normalised names,
# exact match first and then substring, so that `x` does not accidentally match
# `max_korkeus` before `x_koordinaatti` gets a chance.
ROLE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "easting": (
        "e", "x", "ita", "itainen", "itakoordinaatti", "ita_koordinaatti",
        "x_koordinaatti", "koordinaatti_x", "x_koord", "xkoord", "east",
        "easting", "x_etrs", "etrs_x", "i_koordinaatti", "p_koordinaatti_x",
        "x_tm35fin", "tm35fin_x", "x_ykj", "ykj_x", "x_kkj", "kkj_x",
    ),
    "northing": (
        "n", "y", "pohjoinen", "pohjoiskoordinaatti", "pohjois_koordinaatti",
        "y_koordinaatti", "koordinaatti_y", "y_koord", "ykoord", "north",
        "northing", "y_etrs", "etrs_y", "p_koordinaatti", "y_tm35fin",
        "tm35fin_y", "y_ykj", "ykj_y", "y_kkj", "kkj_y",
    ),
    "latitude": (
        "lat", "latitude", "leveysaste", "leveyspiiri", "wgs84_lat", "lat_wgs84",
        "y_wgs84", "wgs_lat", "gps_lat", "leveys",
    ),
    "longitude": (
        "lon", "lng", "long", "longitude", "pituusaste", "pituuspiiri",
        "wgs84_lon", "lon_wgs84", "x_wgs84", "wgs_lon", "gps_lon", "pituus",
    ),
    "geometry_wkt": (
        "wkt", "geom", "geometry", "the_geom", "geometria", "sijainti", "shape",
    ),
    "address": (
        "osoite", "katuosoite", "lahiosoite", "kayntiosoite", "address",
        "street_address", "gatuadress", "adress", "toimipaikan_osoite",
    ),
    "postcode": (
        "postinumero", "postinro", "postnummer", "postal_code", "postcode",
        "zip", "posti_nro", "pno",
    ),
    "municipality_code": (
        "kuntanumero", "kuntakoodi", "kuntatunnus", "kunta_nro", "kuntanro",
        "kommunkod", "municipality_code", "kuntaid",
    ),
    "municipality_name": (
        "kunta", "kunnan_nimi", "kommun", "municipality", "kuntanimi", "paikkakunta",
    ),
    "property_id": (
        "kiinteistotunnus", "kiinteistorekisteritunnus", "kiinteisto_tunnus",
        "property_identifier", "fastighetsbeteckning", "ktunnus",
    ),
    "building_id": (
        "rakennustunnus", "prt", "vtj_prt", "rakennus_tunnus", "building_id",
        "pysyva_rakennustunnus", "byggnadsbeteckning",
    ),
    "place_name": (
        "paikannimi", "nimi", "kohteen_nimi", "name", "namn", "kohde", "sijaintinimi",
    ),
}


def detect_roles(columns: list[str]) -> dict[str, str]:
    """Map role -> original column name for whatever roles are present.

    Only the first plausible column per role is returned. Exact matches beat
    substring matches so that a file with both `x` and `x_koordinaatti` picks the
    one that was named on purpose.

    Matching runs in two passes - every role gets its exact matches first, then
    the leftovers compete for substring matches - and a column can only be
    claimed once. Without that, `Kuntanumero` exactly matches `municipality_code`
    and then also gets grabbed by `municipality_name` on the substring `kunta`,
    which would send the join down the wrong path.
    """
    normalised = {col: normalise_name(col) for col in columns}
    roles: dict[str, str] = {}
    claimed: set[str] = set()

    for role, synonyms in ROLE_SYNONYMS.items():
        exact = [
            c for c, n in normalised.items() if n in synonyms and c not in claimed
        ]
        if exact:
            roles[role] = exact[0]
            claimed.add(exact[0])

    for role, synonyms in ROLE_SYNONYMS.items():
        if role in roles:
            continue
        # Substring matching is restricted to the longer synonyms; matching on a
        # one-letter synonym like "x" as a substring would hit almost anything.
        partial = [
            c
            for c, n in normalised.items()
            if c not in claimed and any(s in n for s in synonyms if len(s) > 3)
        ]
        if partial:
            roles[role] = partial[0]
            claimed.add(partial[0])
    return roles


@dataclass
class CsvDialect:
    encoding: str
    delimiter: str
    decimal: str
    header: list[str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def sniff_csv(path: Path, sample_bytes: int = 262_144) -> CsvDialect:
    """Work out encoding, delimiter and decimal mark for a Finnish CSV file."""
    raw = Path(path).read_bytes()[:sample_bytes]
    text = None
    encoding = ENCODINGS[-1]
    for enc in ENCODINGS:
        try:
            text = raw.decode(enc)
            encoding = enc
            break
        except UnicodeDecodeError:
            continue
    if text is None:  # latin-1 cannot fail, so this is unreachable in practice
        text = raw.decode("latin-1", errors="replace")

    # Trim a trailing partial line so the counts below are not skewed.
    lines = text.splitlines()
    if len(lines) > 2:
        lines = lines[:-1]
    head = "\n".join(lines[:50])

    def count(delim: str) -> int:
        try:
            rows = list(csv.reader(io.StringIO(head), delimiter=delim))
        except csv.Error:
            return 0
        rows = [r for r in rows if r]
        if len(rows) < 2:
            return 0
        widths = {len(r) for r in rows}
        # A good delimiter splits every row into the same number of fields, and
        # into more than one field.
        return max(widths) if len(widths) == 1 and max(widths) > 1 else 0

    delimiter = max(DELIMITERS, key=count)
    if count(delimiter) == 0:
        delimiter = ";" if head.count(";") > head.count(",") else ","

    rows = list(csv.reader(io.StringIO(head), delimiter=delimiter))
    header = rows[0] if rows else []
    body = rows[1:]

    # A comma decimal mark only makes sense when the comma is not the delimiter.
    # Look for values that are digits-comma-digits in at least one column.
    decimal = "."
    if delimiter != ",":
        comma_numeric = sum(
            1
            for row in body
            for cell in row
            if re.fullmatch(r"-?\d{1,12},\d+", cell.strip())
        )
        plain_numeric = sum(
            1
            for row in body
            for cell in row
            if re.fullmatch(r"-?\d{1,12}\.\d+", cell.strip())
        )
        if comma_numeric > plain_numeric:
            decimal = ","
    return CsvDialect(encoding, delimiter, decimal, header)


def read_table(path: Path):
    """Read a CSV/TSV/Excel file into a DataFrame, handling Finnish conventions."""
    import pandas as pd

    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls", ".ods"}:
        return pd.read_excel(path), None
    dialect = sniff_csv(path)
    frame = pd.read_csv(
        path,
        sep=dialect.delimiter,
        encoding=dialect.encoding,
        decimal=dialect.decimal,
        dtype=str,
        keep_default_na=False,
        na_values=["", "NA", "N/A", "null", "NULL", "-"],
        low_memory=False,
    )
    # Re-parse the columns that look numeric, now that the decimal mark is known.
    # Everything was read as text so the decimal mark could be handled uniformly;
    # recent pandas returns StringDtype rather than object for that, so the guard
    # tests for "not already numeric" instead of comparing against object.
    for col in frame.columns:
        series = frame[col]
        if pd.api.types.is_numeric_dtype(series):
            continue
        cleaned = series.astype(str).str.strip()
        if dialect.decimal == ",":
            cleaned = cleaned.str.replace(" ", "", regex=False).str.replace(
                ",", ".", regex=False
            )
        numeric = pd.to_numeric(cleaned, errors="coerce")
        non_null = series.notna().sum()
        if non_null and numeric.notna().sum() >= 0.95 * non_null:
            frame[col] = numeric
    return frame, dialect


def orient_pair(a_values, b_values) -> bool:
    """Return True when (a, b) is really (northing, easting) and needs swapping.

    Column names cannot settle this: Finnish datasets label ETRS-TM35FIN
    northings as "x" about as often as they label them "y", because the EPSG
    definition of the CRS puts northing first while GIS software puts easting
    first. Magnitude does settle it - inside Finland a TM35FIN northing is always
    in the millions and an easting never reaches one million.
    """
    import pandas as pd

    a = pd.to_numeric(pd.Series(list(a_values)), errors="coerce").dropna()
    b = pd.to_numeric(pd.Series(list(b_values)), errors="coerce").dropna()
    if a.empty or b.empty:
        return False
    return bool(a.median() > b.median() and a.median() > 1_000_000)


def write_json(path: Path, payload: Any) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )


def classify_suffix(path: Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in VECTOR_SUFFIXES:
        return "vector"
    if suffix in TABULAR_SUFFIXES:
        return "tabular"
    if suffix in RASTER_SUFFIXES:
        return "raster"
    return "unknown"
