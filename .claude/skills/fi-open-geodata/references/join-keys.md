# Finnish join keys

Implemented in `scripts/fi_joinkeys.py`. Read this when a join produces fewer
rows than expected, or when deciding whether a flagged value is really bad.

## kuntanumero — municipality code

Three digits, zero-padded. Helsinki is `091`, Tampere `837`, Oulu `564`.

The failure mode is Excel: it reads `091` as the number 91 and writes it back
without the zero, after which every string join fails and every numeric join
silently succeeds against the wrong thing. Normalise before joining, always.

Codes are retired when municipalities merge and are **not** reissued, so an old
code that no longer matches is evidence of dataset age, not corruption. The set
of live codes is not hardcoded here — it changes each January, and a stale list
baked into a skill would reject valid data. Fetch the current classification from
Statistics Finland when membership needs checking.

## postinumero — postal code

Five digits, zero-padded; Helsinki centre is `00100`. Joins to the Paavo
`postialue` layer. Same leading-zero problem as above, one digit worse.

## kiinteistötunnus — real property identifier

Two forms, and they do not join to each other:

| Form | Example | Where used |
|---|---|---|
| Presentation | `91-1-1-1` | Printed documents, what people type |
| Database | `09100100010001` | NLS cadastral API, registers |

The structure is municipality (3) – area (3) – group (4) – unit (4), zero-padded
in the database form. `property_id()` converts either way and
`transform_source.py` emits both, because you generally need the database form to
join and the presentation form to show a human.

## rakennustunnus — permanent building identifier (VTJ-PRT)

Ten characters: nine digits plus a check character. The check character is
`"0123456789ABCDEFHJKLMNPRSTUVWXY"[number % 31]`, where `number` is the nine
digits read as an integer. G, I, O, Q and Z are absent from the alphabet because
they are confusable with digits.

A checksum mismatch usually means a transcription error, but this implementation
could not be verified against DVV's published algorithm from the authoring
environment. So a mismatch is **flagged, not fatal**: the value is still carried
into the output and reported. If you see a large fraction failing on data you
trust, suspect the checksum rule here before suspecting the data, and check
DVV's documentation.

## Y-tunnus — business identity code

Seven digits, a hyphen, and a check digit: `0201256-6`. Checksum uses weights
7, 9, 10, 5, 8, 4, 2 over the first seven digits; `remainder == 1` is invalid and
never issued. Not a spatial key, but it is the usual bridge to company registers.

## henkilötunnus — personal identity code

**Never republish.** Same check-character alphabet as the building identifier,
over `DDMMYY` plus the three-digit individual number.

`profile_source.py` scans every column for values of this shape on every run and
refuses to stay quiet about them. Finding one is not a curiosity — it means the
extract contains personal data and cannot be published as-is. Remove or
pseudonymise the column, and consider that the rest of the row may be
identifying too.

## Interpreting the normalisation report

Each key gets `rows`, `blank`, `valid`, `flagged` and a success rate over
non-blank values. The distinction that matters when debugging a lossy join:

- **blank** — the source had nothing there. Often legitimate.
- **flagged** — there was a value and it did not fit the format. Someone should
  look at it; the examples in the report show what it looked like.

A success rate below about 90% on a key you intend to join with is worth
resolving before converting, because the missing rows will vanish from the map
without any error being raised.
