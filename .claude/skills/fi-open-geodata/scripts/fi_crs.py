"""Coordinate reference system detection for Finnish spatial data.

Finnish open data carries a lot of legacy CRS baggage: KKJ zone coordinates
published without any CRS statement, ETRS-TM35FIN columns labelled "x"/"y" in
the wrong order, and files whose declared CRS contradicts the numbers inside
them. Guessing wrong silently relocates data by hundreds of kilometres, so this
module ranks candidate CRSs by how well the actual coordinate values fall inside
each CRS's Finnish envelope, and reports its confidence rather than asserting.

Used as a library by profile_source.py and transform_source.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

# Envelopes are deliberately a little wider than Finland's true extent so that
# offshore points, Aland, and the far north do not get rejected, but still tight
# enough that the candidates stay mutually exclusive by magnitude alone.
#
# `east` / `north` are ranges for the projected easting and northing (metres),
# or for lon/lat in degrees when `geographic` is True.


@dataclass(frozen=True)
class CrsCandidate:
    epsg: int
    name: str
    east: tuple[float, float]
    north: tuple[float, float]
    geographic: bool = False
    note: str = ""


CANDIDATES: list[CrsCandidate] = [
    CrsCandidate(
        3067,
        "ETRS-TM35FIN (EUREF-FIN)",
        east=(20_000, 800_000),
        north=(6_520_000, 7_900_000),
        note="National standard. Target CRS for all output of this skill.",
    ),
    CrsCandidate(
        4326,
        "WGS 84 geographic",
        east=(19.0, 31.7),
        north=(59.4, 70.2),
        geographic=True,
        note="Axis order in GeoJSON is lon, lat (RFC 7946).",
    ),
    CrsCandidate(
        3857,
        "WGS 84 / Pseudo-Mercator",
        east=(2_100_000, 3_560_000),
        north=(8_300_000, 11_150_000),
        note="Web map tiles. Never a good storage CRS for analysis.",
    ),
    CrsCandidate(
        2393,
        "KKJ / Finland Uniform Coordinate System (YKJ, zone 3)",
        east=(3_000_000, 3_800_000),
        north=(6_600_000, 7_800_000),
        note="Legacy national grid. Very common in pre-2010 municipal data.",
    ),
    CrsCandidate(
        2391, "KKJ zone 1", east=(1_000_000, 1_800_000), north=(6_600_000, 7_800_000)
    ),
    CrsCandidate(
        2392, "KKJ zone 2", east=(2_000_000, 2_800_000), north=(6_600_000, 7_800_000)
    ),
    CrsCandidate(
        2394, "KKJ zone 4", east=(4_000_000, 4_800_000), north=(6_600_000, 7_800_000)
    ),
]

# ETRS-GKn (Gauss-Kruger, 1-degree zones) are used by municipalities for
# detailed planning data. EPSG:3873 is GK19 and the codes run consecutively to
# EPSG:3885 for GK31. The false easting embeds the zone number, which makes them
# trivially separable from everything else by magnitude.
for _zone in range(19, 32):
    _epsg = 3873 + (_zone - 19)
    CANDIDATES.append(
        CrsCandidate(
            _epsg,
            f"ETRS-GK{_zone}FIN",
            east=(_zone * 1_000_000 + 300_000, _zone * 1_000_000 + 700_000),
            north=(6_520_000, 7_900_000),
            note="Municipal detail-plan CRS; zone number is embedded in the easting.",
        )
    )

BY_EPSG = {c.epsg: c for c in CANDIDATES}

#: Numerically identical aliases that people declare instead of the canonical code.
EQUIVALENT_TO_3067 = {
    3047,  # ETRS89 / TM35 - same projection parameters as ETRS-TM35FIN
    5048,  # ETRS89 / TM35FIN(N,E) - identical grid, EPSG-declared axis order N,E
    3067,
}


@dataclass
class CrsGuess:
    epsg: int
    name: str
    swap_xy: bool
    inside_fraction: float
    note: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def confident(self) -> bool:
        return self.inside_fraction >= 0.95

    def describe(self) -> str:
        order = "values are (northing, easting) - swap needed" if self.swap_xy else "values are (easting, northing)"
        return (
            f"EPSG:{self.epsg} {self.name} - {self.inside_fraction:.1%} of sampled "
            f"points fall inside the Finnish envelope, {order}"
        )


def _in_range(value: float, bounds: tuple[float, float]) -> bool:
    return bounds[0] <= value <= bounds[1]


def _score(pairs: Sequence[tuple[float, float]], cand: CrsCandidate, swap: bool) -> float:
    if not pairs:
        return 0.0
    hits = 0
    for a, b in pairs:
        east, north = (b, a) if swap else (a, b)
        if _in_range(east, cand.east) and _in_range(north, cand.north):
            hits += 1
    return hits / len(pairs)


def _clean(pairs: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    out = []
    for a, b in pairs:
        try:
            fa, fb = float(a), float(b)
        except (TypeError, ValueError):
            continue
        if math.isnan(fa) or math.isnan(fb) or math.isinf(fa) or math.isinf(fb):
            continue
        # A 0,0 pair is the classic "missing coordinate" sentinel in Finnish
        # registers; counting it would drag every candidate's score down.
        if fa == 0 and fb == 0:
            continue
        out.append((fa, fb))
    return out


def detect(
    pairs: Iterable[tuple[float, float]], declared_epsg: int | None = None
) -> list[CrsGuess]:
    """Rank CRS candidates for a sample of coordinate pairs, best first.

    `pairs` are taken in file order, i.e. (first column, second column). Both
    axis orders are tried for every candidate because the ordering convention is
    exactly what is most often wrong.

    If `declared_epsg` is given it is not trusted blindly: it is scored like any
    other candidate, and a warning is attached when the numbers disagree with the
    declaration. That contradiction is the single most useful thing to surface,
    because a mislabelled file converts without error and lands in the wrong
    country.
    """
    sample = _clean(pairs)
    guesses: list[CrsGuess] = []
    for cand in CANDIDATES:
        for swap in (False, True):
            # For a geographic CRS the two axes overlap in range, so a swapped
            # match is real information; for projected Finnish CRSs easting and
            # northing never overlap, so a swap score of 1.0 is unambiguous.
            frac = _score(sample, cand, swap)
            if frac > 0:
                guesses.append(
                    CrsGuess(cand.epsg, cand.name, swap, frac, cand.note)
                )
    guesses.sort(key=lambda g: (-g.inside_fraction, g.swap_xy, g.epsg))

    if declared_epsg is not None and guesses:
        best = guesses[0]
        declared_equiv = (
            EQUIVALENT_TO_3067 if declared_epsg in EQUIVALENT_TO_3067 else {declared_epsg}
        )
        if best.epsg not in declared_equiv:
            best.warnings.append(
                f"File declares EPSG:{declared_epsg} but the coordinate values fit "
                f"EPSG:{best.epsg} ({best.name}) far better. Treat the declaration as "
                f"suspect and confirm against a known landmark before publishing."
            )
        elif best.swap_xy:
            best.warnings.append(
                f"CRS declaration EPSG:{declared_epsg} looks right, but the axis order "
                f"is reversed relative to the stored values."
            )
    if guesses and not guesses[0].confident:
        guesses[0].warnings.append(
            "No candidate matched cleanly. The data may extend outside Finland, use a "
            "local municipal grid, or mix CRSs across rows - inspect before converting."
        )
    return guesses


def best_guess(
    pairs: Iterable[tuple[float, float]], declared_epsg: int | None = None
) -> CrsGuess | None:
    ranked = detect(pairs, declared_epsg)
    return ranked[0] if ranked else None


def is_target(epsg: int | None) -> bool:
    """True when `epsg` already is ETRS-TM35FIN under any of its aliases."""
    return epsg in EQUIVALENT_TO_3067
