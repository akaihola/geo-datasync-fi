"""Normalisation and validation of Finnish join keys.

A dataset becomes interoperable the moment its identifiers match the reference
layers it should be joined against. In practice they almost never do out of the
box: municipality codes lose their leading zeros in Excel, property identifiers
appear in the hyphenated presentation form while the cadastral API serves the
14-digit database form, and postal codes arrive as integers.

Every normaliser here is total - it returns a Normalised result carrying both the
value and whether it could be trusted - so that a bad row is reported rather than
silently dropped.

Run directly to normalise a column in a file:
    python fi_joinkeys.py data.csv --column kuntanumero --key municipality_code
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Check-character alphabet shared by the Finnish personal identity code and the
# permanent building identifier. Letters G, I, O, Q and Z are omitted because
# they are easy to confuse with digits.
CHECK_CHARS = "0123456789ABCDEFHJKLMNPRSTUVWXY"


@dataclass
class Normalised:
    value: str | None
    valid: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.valid


def _digits(raw) -> str:
    return re.sub(r"\D", "", str(raw or ""))


def municipality_code(raw) -> Normalised:
    """Zero-pad a kuntanumero to the canonical three digits.

    Statistics Finland and the NLS both key municipalities on a three-character
    string. Spreadsheets store them as integers, so Helsinki (091) arrives as 91
    and fails every join until it is padded back.
    """
    digits = _digits(raw)
    if not digits:
        return Normalised(None, False, "no digits present")
    if len(digits) > 3:
        return Normalised(None, False, f"too long for a municipality code: {digits!r}")
    code = digits.zfill(3)
    if code == "000":
        return Normalised(None, False, "000 is not a municipality")
    # The set of live codes changes with every municipal merger, so membership is
    # checked against a fetched classification rather than a list frozen here.
    return Normalised(code, True)


def postcode(raw) -> Normalised:
    """Zero-pad a postinumero to five digits (Paavo postal-area join key)."""
    digits = _digits(raw)
    if not digits:
        return Normalised(None, False, "no digits present")
    if len(digits) > 5:
        return Normalised(None, False, f"too long for a postal code: {digits!r}")
    code = digits.zfill(5)
    if code == "00000":
        return Normalised(None, False, "00000 is not a postal code")
    return Normalised(code, True)


def property_id(raw, form: str = "database") -> Normalised:
    """Normalise a kiinteistotunnus (real property identifier).

    Two forms are in circulation and they do not join to each other:
      presentation  91-1-1-1          - what people type and print
      database      09100100010001    - 3+3+4+4 zero-padded, what the NLS
                                        cadastral API serves

    `form` selects the output; conversion is lossless in both directions.
    """
    text = str(raw or "").strip()
    if not text:
        return Normalised(None, False, "empty")
    if "-" in text:
        parts = text.split("-")
        if len(parts) != 4 or not all(p.isdigit() for p in parts):
            return Normalised(None, False, f"not four numeric parts: {text!r}")
        widths = (3, 3, 4, 4)
        if any(len(p) > w for p, w in zip(parts, widths)):
            return Normalised(None, False, f"a part is too long: {text!r}")
        nums = [int(p) for p in parts]
    else:
        digits = _digits(text)
        if len(digits) != 14:
            return Normalised(
                None, False, f"expected 14 digits in database form, got {len(digits)}"
            )
        nums = [
            int(digits[0:3]), int(digits[3:6]), int(digits[6:10]), int(digits[10:14])
        ]
    if form == "database":
        return Normalised(
            f"{nums[0]:03d}{nums[1]:03d}{nums[2]:04d}{nums[3]:04d}", True
        )
    if form == "presentation":
        return Normalised("-".join(str(n) for n in nums), True)
    raise ValueError(f"unknown form {form!r}; use 'database' or 'presentation'")


def _check_char(number: int) -> str:
    return CHECK_CHARS[number % 31]


def building_id(raw, verify_checksum: bool = True) -> Normalised:
    """Normalise a pysyva rakennustunnus (permanent building identifier, VTJ-PRT).

    The identifier is nine digits plus a check character drawn from CHECK_CHARS,
    indexed by the nine-digit number modulo 31. Verifying it catches transcription
    damage before it turns into a silently empty join.
    """
    text = re.sub(r"[^0-9A-Za-z]", "", str(raw or "")).upper()
    if not text:
        return Normalised(None, False, "empty")
    if len(text) != 10:
        return Normalised(None, False, f"expected 10 characters, got {len(text)}")
    body, check = text[:9], text[9]
    if not body.isdigit():
        return Normalised(None, False, "first nine characters must be digits")
    if verify_checksum:
        expected = _check_char(int(body))
        if check != expected:
            return Normalised(
                text, False, f"check character is {check!r}, expected {expected!r}"
            )
    return Normalised(text, True)


def business_id(raw) -> Normalised:
    """Normalise a Y-tunnus (business identity code) as 1234567-8.

    Not a spatial key, but it is the usual bridge from a municipal dataset to
    company registers, and its checksum makes bad rows cheap to spot.
    """
    digits = _digits(raw)
    if len(digits) != 8:
        return Normalised(None, False, f"expected 8 digits, got {len(digits)}")
    weights = (7, 9, 10, 5, 8, 4, 2)
    total = sum(int(d) * w for d, w in zip(digits[:7], weights))
    remainder = total % 11
    if remainder == 1:
        return Normalised(None, False, "checksum remainder of 1 is never issued")
    expected = 0 if remainder == 0 else 11 - remainder
    if int(digits[7]) != expected:
        return Normalised(
            None, False, f"check digit is {digits[7]}, expected {expected}"
        )
    return Normalised(f"{digits[:7]}-{digits[7]}", True)


HETU_RE = re.compile(r"\b\d{6}[-+ABCDEFUVWXY]\d{3}[0-9A-Z]\b")


def looks_like_personal_id(value) -> bool:
    """True when a value has the shape of a henkilotunnus.

    Personal identity codes must never be republished. They turn up in municipal
    extracts more often than anyone would like, so scanning for them before
    writing output is worth the cost.
    """
    text = str(value or "").strip().upper()
    if not HETU_RE.fullmatch(text):
        return False
    # Layout is DDMMYY + century sign + a three-digit individual number + check
    # character; the checksum is taken over the nine digits either side of the
    # sign, so the sign itself has to be dropped rather than sliced around.
    digits = text[:6] + text[7:10]
    if not digits.isdigit():
        return False
    return text[10] == _check_char(int(digits))


NORMALISERS = {
    "municipality_code": municipality_code,
    "postcode": postcode,
    "property_id": property_id,
    "building_id": building_id,
    "business_id": business_id,
}


def normalise_series(values, key: str):
    """Apply a normaliser across an iterable, returning (values, report)."""
    func = NORMALISERS[key]
    out: list[str | None] = []
    failures: list[dict] = []
    blank = valid = 0
    for index, raw in enumerate(values):
        if not str(raw or "").strip():
            out.append(None)
            blank += 1
            continue
        result = func(raw)
        # A value that failed validation is still carried through when the
        # normaliser could make sense of its shape, so that the caller can decide
        # whether a checksum mismatch is fatal. It is always reported either way.
        out.append(result.value)
        if result.valid:
            valid += 1
        else:
            failures.append({"row": index, "value": str(raw), "reason": result.reason})
    total = len(out)
    present = total - blank
    report = {
        "key": key,
        "rows": total,
        "blank": blank,
        "valid": valid,
        "flagged": len(failures),
        "success_rate": (valid / present) if present else 0.0,
        "examples_of_failure": failures[:20],
    }
    return out, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--column", required=True)
    parser.add_argument("--key", required=True, choices=sorted(NORMALISERS))
    parser.add_argument("--output", type=Path, help="write the normalised table here")
    parser.add_argument("--report", type=Path, help="write the JSON report here")
    args = parser.parse_args(argv)

    from fi_common import read_table, write_json

    frame, _ = read_table(args.path)
    if args.column not in frame.columns:
        print(f"column {args.column!r} not in {list(frame.columns)}", file=sys.stderr)
        return 2
    values, report = normalise_series(frame[args.column], args.key)
    frame[f"{args.key}_normalised"] = values
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(args.output, index=False, encoding="utf-8")
        report["output"] = str(args.output)
    if args.report:
        write_json(args.report, report)
    print(
        f"{report['valid']}/{report['rows'] - report['blank']} non-blank values valid "
        f"({report['success_rate']:.1%}); {report['blank']} blank, "
        f"{report['flagged']} flagged"
    )
    for failure in report["examples_of_failure"][:5]:
        print(f"  row {failure['row']}: {failure['value']!r} - {failure['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
