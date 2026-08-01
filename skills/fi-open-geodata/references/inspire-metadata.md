# INSPIRE metadata for Finnish datasets

Read this when filling in `make_metadata.py` arguments, or when the validator
rejects a record.

## What conformance means here

`make_metadata.py` produces an ISO/TS 19139 record shaped to satisfy INSPIRE
Technical Guidance for dataset metadata. It **asserts** conformance; only the
official validator establishes it:

<https://inspire.ec.europa.eu/validator/> — upload the XML, run the
"INSPIRE data sets and data set series" suite.

Conformance classes are cumulative — Conformance Class 2 (interoperability)
requires Conformance Class 1 (baseline) to pass first, so fix baseline failures
before looking at anything else.

## Elements the generator fills, and where they come from

| Element | Source |
|---|---|
| `fileIdentifier` | generated UUID, or `--identifier` |
| `referenceSystemInfo` | `--epsg`, as an OGC def URI |
| `EX_GeographicBoundingBox` | reprojected from the transform's EPSG:3067 bbox |
| `descriptiveKeywords` | `--theme`, cited against the GEMET INSPIRE themes thesaurus |
| `topicCategory` | derived from the theme, overridable with `--topic-category` |
| `resourceConstraints` | `--access-limitation` and `--licence` |
| `lineage` | the actual processing notes from `transform.json` |

The bounding box is always WGS84 decimal degrees regardless of the dataset's own
CRS — this is a fixed INSPIRE requirement and a common validator failure when
people copy their national-grid extent into it.

## Elements you must supply

These are editorial and the generator will not invent them:

- `--title` — what the dataset is, not what the file is called.
- `--abstract` — what it contains, how it was collected, and what it is *not*
  suitable for. The limitation sentence is the most useful one and the most
  frequently omitted.
- `--theme` — one of the 34 INSPIRE themes; run `--list-themes` to see them with
  their default topic categories. The spelling must match the register exactly.
- `--organisation` and `--email` — a real, monitored contact.
- `--access-url` — where the data can actually be obtained. A record with no
  distribution link fails the discovery requirement.

## Access constraints

`--access-limitation` defaults to `noLimitations`, which is correct for open
data. The alternatives are the Article 13 exceptions
(`INSPIRE_Directive_Article13_1a` … `_1h`), and they are legal determinations —
do not select one to describe a licence condition. A licence goes in
`useConstraints` via `--licence`; the two are different things and validators
check that they are used correctly.

For Finnish open data `CC-BY-4.0` is the usual answer. If the source portal
recorded no licence at all, say so in the report and get it confirmed before
publishing — absence of a stated licence is not permission.

## Common validator failures

| Symptom | Cause |
|---|---|
| Keyword not recognised | Theme spelling differs from the GEMET register |
| Missing mandatory element | Usually `--access-url` or the resource identifier |
| Bounding box out of range | National-grid metres placed in the WGS84 element |
| Invalid code list value | A code list URL or value edited by hand |
| Conformity result missing | Warned about at CC2; add a `DQ_ConformanceResult` citing the INSPIRE implementing rules once the dataset genuinely conforms to a data specification |

The generator does not emit a `DQ_ConformanceResult` on purpose. Claiming
conformity to a data specification is a substantive assertion about the data
model — that the schema matches the INSPIRE application schema for the theme —
and it would be wrong to assert it automatically for an arbitrary converted CSV.
Add it by hand when it is true.

## Finnish discovery

Finland's INSPIRE discovery service is Paikkatietohakemisto
(<https://www.paikkatietohakemisto.fi>), which harvests records from producers'
own catalogues. Getting a record listed there is an organisational step —
registering as a producer — not something a conversion pipeline can do.
