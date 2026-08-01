"""Generate INSPIRE-conformant ISO 19139 metadata for a converted dataset.

Most of what a validator checks is mechanical - a bounding box in WGS84 decimal
degrees, a reference system identifier, a keyword drawn from the GEMET INSPIRE
themes register, a lineage statement - and most of it is already known by the
time transform_source.py has run. This script assembles that into an
ISO/TS 19139 record and tells you what it could not fill in.

What it cannot do is invent the parts that are editorial judgement: the abstract,
the responsible organisation, the theme. Those are prompted for as arguments, and
the record is marked incomplete rather than filled with plausible filler, because
a metadata record that looks complete and is wrong is worse than one that is
visibly unfinished.

    python make_metadata.py out/transform.json \\
        --title "Public toilets in Helsinki" \\
        --abstract "Locations of publicly accessible toilets ..." \\
        --theme "Utility and governmental services" \\
        --organisation "City of Helsinki" --email opendata@hel.fi \\
        --licence CC-BY-4.0 --out out/metadata/toilets_iso19139.xml

Conformance is asserted, not proven. Upload the result to the official validator
at https://inspire.ec.europa.eu/validator/ to get an authoritative verdict.
"""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

ASSETS = Path(__file__).resolve().parent.parent / "assets"

NS = (
    'xmlns:gmd="http://www.isotc211.org/2005/gmd" '
    'xmlns:gco="http://www.isotc211.org/2005/gco" '
    'xmlns:gml="http://www.opengis.net/gml/3.2" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    'xsi:schemaLocation="http://www.isotc211.org/2005/gmd '
    'http://schemas.opengis.net/csw/2.0.2/profiles/apiso/1.0.0/apiso.xsd"'
)


def load_themes() -> dict:
    return json.loads((ASSETS / "inspire-themes.json").read_text(encoding="utf-8"))


def esc(value) -> str:
    return escape(str(value if value is not None else ""))


def code(list_name: str, value: str, indent: str = "") -> str:
    url = f"http://standards.iso.org/ittf/PubliclyAvailableStandards/ISO_19139_Schemas/resources/Codelist/gmxCodelists.xml#{list_name}"
    return (
        f'{indent}<gmd:{list_name} codeList="{url}" '
        f'codeListValue="{esc(value)}">{esc(value)}</gmd:{list_name}>'
    )


def responsible_party(name: str, email: str, role: str, indent: str) -> str:
    pad = indent + "  "
    return f"""{indent}<gmd:CI_ResponsibleParty>
{pad}<gmd:organisationName><gco:CharacterString>{esc(name)}</gco:CharacterString></gmd:organisationName>
{pad}<gmd:contactInfo>
{pad}  <gmd:CI_Contact>
{pad}    <gmd:address>
{pad}      <gmd:CI_Address>
{pad}        <gmd:electronicMailAddress><gco:CharacterString>{esc(email)}</gco:CharacterString></gmd:electronicMailAddress>
{pad}      </gmd:CI_Address>
{pad}    </gmd:address>
{pad}  </gmd:CI_Contact>
{pad}</gmd:contactInfo>
{pad}<gmd:role>
{code("CI_RoleCode", role, pad + "  ")}
{pad}</gmd:role>
{indent}</gmd:CI_ResponsibleParty>"""


def build(args, summary: dict, themes: dict) -> tuple[str, list[str]]:
    gaps: list[str] = []
    theme_info = themes["themes"].get(args.theme)
    if theme_info is None:
        gaps.append(
            f"Theme {args.theme!r} is not an INSPIRE theme; the keyword will not "
            "match the GEMET register and Conformance Class 2 will fail."
        )
        topic = args.topic_category or "location"
    else:
        topic = args.topic_category or theme_info["topic"]

    bbox = args.bbox_wgs84 or summary.get("bbox_wgs84")
    if not bbox:
        # The geographic extent must be WGS84 decimal degrees regardless of the
        # dataset's own CRS, so it is derived rather than copied from bbox_3067.
        bbox_3067 = summary.get("bbox_3067")
        if bbox_3067:
            from pyproj import Transformer

            transformer = Transformer.from_crs(3067, 4326, always_xy=True)
            west, south = transformer.transform(bbox_3067[0], bbox_3067[1])
            east, north = transformer.transform(bbox_3067[2], bbox_3067[3])
            bbox = [west, east, south, north]
        else:
            gaps.append("No spatial extent available; gmd:EX_GeographicBoundingBox omitted.")

    identifier = args.identifier or f"urn:uuid:{uuid.uuid4()}"
    stamp = datetime.now(timezone.utc).date().isoformat()
    resource_date = args.date or summary.get("source_date") or stamp
    epsg = args.epsg or 3067
    licence_url = themes["common_finnish_licences"].get(args.licence, args.licence_url or "")
    if not licence_url:
        gaps.append(
            f"No URL known for licence {args.licence!r}; gmd:useConstraints will carry "
            "the bare identifier. Add --licence-url so downstream users can resolve it."
        )

    extent = ""
    if bbox:
        extent = f"""
      <gmd:extent>
        <gmd:EX_Extent>
          <gmd:geographicElement>
            <gmd:EX_GeographicBoundingBox>
              <gmd:westBoundLongitude><gco:Decimal>{bbox[0]:.6f}</gco:Decimal></gmd:westBoundLongitude>
              <gmd:eastBoundLongitude><gco:Decimal>{bbox[1]:.6f}</gco:Decimal></gmd:eastBoundLongitude>
              <gmd:southBoundLatitude><gco:Decimal>{bbox[2]:.6f}</gco:Decimal></gmd:southBoundLatitude>
              <gmd:northBoundLatitude><gco:Decimal>{bbox[3]:.6f}</gco:Decimal></gmd:northBoundLatitude>
            </gmd:EX_GeographicBoundingBox>
          </gmd:geographicElement>
        </gmd:EX_Extent>
      </gmd:extent>"""

    distribution = ""
    if args.access_url:
        distribution = f"""
        <gmd:transferOptions>
          <gmd:MD_DigitalTransferOptions>
            <gmd:onLine>
              <gmd:CI_OnlineResource>
                <gmd:linkage><gmd:URL>{esc(args.access_url)}</gmd:URL></gmd:linkage>
                <gmd:function>
{code("CI_OnLineFunctionCode", "download", "                  ")}
                </gmd:function>
              </gmd:CI_OnlineResource>
            </gmd:onLine>
          </gmd:MD_DigitalTransferOptions>
        </gmd:transferOptions>"""
    else:
        gaps.append(
            "No --access-url given; the record does not say where the data can be "
            "obtained, which fails the INSPIRE discovery requirement."
        )

    lineage_notes = summary.get("notes") or []
    lineage = args.lineage or (
        "Derived from the publisher's original distribution. Processing steps: "
        + " ".join(lineage_notes)
        if lineage_notes
        else "Derived from the publisher's original distribution."
    )

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<gmd:MD_Metadata {NS}>
  <gmd:fileIdentifier><gco:CharacterString>{esc(identifier)}</gco:CharacterString></gmd:fileIdentifier>
  <gmd:language>
{code("LanguageCode", args.language, "    ")}
  </gmd:language>
  <gmd:characterSet>
{code("MD_CharacterSetCode", "utf8", "    ")}
  </gmd:characterSet>
  <gmd:hierarchyLevel>
{code("MD_ScopeCode", "dataset", "    ")}
  </gmd:hierarchyLevel>
  <gmd:contact>
{responsible_party(args.organisation, args.email, "pointOfContact", "    ")}
  </gmd:contact>
  <gmd:dateStamp><gco:Date>{stamp}</gco:Date></gmd:dateStamp>
  <gmd:metadataStandardName><gco:CharacterString>ISO 19115</gco:CharacterString></gmd:metadataStandardName>
  <gmd:metadataStandardVersion><gco:CharacterString>2003/Cor.1:2006</gco:CharacterString></gmd:metadataStandardVersion>
  <gmd:referenceSystemInfo>
    <gmd:MD_ReferenceSystem>
      <gmd:referenceSystemIdentifier>
        <gmd:RS_Identifier>
          <gmd:code><gco:CharacterString>http://www.opengis.net/def/crs/EPSG/0/{epsg}</gco:CharacterString></gmd:code>
          <gmd:codeSpace><gco:CharacterString>EPSG</gco:CharacterString></gmd:codeSpace>
        </gmd:RS_Identifier>
      </gmd:referenceSystemIdentifier>
    </gmd:MD_ReferenceSystem>
  </gmd:referenceSystemInfo>
  <gmd:identificationInfo>
    <gmd:MD_DataIdentification>
      <gmd:citation>
        <gmd:CI_Citation>
          <gmd:title><gco:CharacterString>{esc(args.title)}</gco:CharacterString></gmd:title>
          <gmd:date>
            <gmd:CI_Date>
              <gmd:date><gco:Date>{esc(resource_date)}</gco:Date></gmd:date>
              <gmd:dateType>
{code("CI_DateTypeCode", "publication", "                ")}
              </gmd:dateType>
            </gmd:CI_Date>
          </gmd:date>
          <gmd:identifier>
            <gmd:MD_Identifier>
              <gmd:code><gco:CharacterString>{esc(identifier)}</gco:CharacterString></gmd:code>
            </gmd:MD_Identifier>
          </gmd:identifier>
        </gmd:CI_Citation>
      </gmd:citation>
      <gmd:abstract><gco:CharacterString>{esc(args.abstract)}</gco:CharacterString></gmd:abstract>
      <gmd:pointOfContact>
{responsible_party(args.organisation, args.email, "custodian", "        ")}
      </gmd:pointOfContact>
      <gmd:descriptiveKeywords>
        <gmd:MD_Keywords>
          <gmd:keyword><gco:CharacterString>{esc(args.theme)}</gco:CharacterString></gmd:keyword>
          <gmd:thesaurusName>
            <gmd:CI_Citation>
              <gmd:title><gco:CharacterString>{esc(themes['thesaurus']['title'])}</gco:CharacterString></gmd:title>
              <gmd:date>
                <gmd:CI_Date>
                  <gmd:date><gco:Date>{themes['thesaurus']['date']}</gco:Date></gmd:date>
                  <gmd:dateType>
{code("CI_DateTypeCode", themes['thesaurus']['date_type'], "                    ")}
                  </gmd:dateType>
                </gmd:CI_Date>
              </gmd:date>
            </gmd:CI_Citation>
          </gmd:thesaurusName>
        </gmd:MD_Keywords>
      </gmd:descriptiveKeywords>
      <gmd:resourceConstraints>
        <gmd:MD_LegalConstraints>
          <gmd:accessConstraints>
{code("MD_RestrictionCode", "otherRestrictions", "            ")}
          </gmd:accessConstraints>
          <gmd:otherConstraints>
            <gco:CharacterString>{esc(args.access_limitation)}</gco:CharacterString>
          </gmd:otherConstraints>
        </gmd:MD_LegalConstraints>
      </gmd:resourceConstraints>
      <gmd:resourceConstraints>
        <gmd:MD_LegalConstraints>
          <gmd:useConstraints>
{code("MD_RestrictionCode", "otherRestrictions", "            ")}
          </gmd:useConstraints>
          <gmd:otherConstraints>
            <gco:CharacterString>{esc(licence_url or args.licence)}</gco:CharacterString>
          </gmd:otherConstraints>
        </gmd:MD_LegalConstraints>
      </gmd:resourceConstraints>
      <gmd:language>
{code("LanguageCode", args.language, "        ")}
      </gmd:language>
      <gmd:topicCategory>
        <gmd:MD_TopicCategoryCode>{esc(topic)}</gmd:MD_TopicCategoryCode>
      </gmd:topicCategory>{extent}
    </gmd:MD_DataIdentification>
  </gmd:identificationInfo>
  <gmd:distributionInfo>
    <gmd:MD_Distribution>
      <gmd:distributionFormat>
        <gmd:MD_Format>
          <gmd:name><gco:CharacterString>{esc(args.format)}</gco:CharacterString></gmd:name>
          <gmd:version><gco:CharacterString>{esc(args.format_version)}</gco:CharacterString></gmd:version>
        </gmd:MD_Format>
      </gmd:distributionFormat>{distribution}
    </gmd:MD_Distribution>
  </gmd:distributionInfo>
  <gmd:dataQualityInfo>
    <gmd:DQ_DataQuality>
      <gmd:scope>
        <gmd:DQ_Scope>
          <gmd:level>
{code("MD_ScopeCode", "dataset", "            ")}
          </gmd:level>
        </gmd:DQ_Scope>
      </gmd:scope>
      <gmd:lineage>
        <gmd:LI_Lineage>
          <gmd:statement><gco:CharacterString>{esc(lineage)}</gco:CharacterString></gmd:statement>
        </gmd:LI_Lineage>
      </gmd:lineage>
    </gmd:DQ_DataQuality>
  </gmd:dataQualityInfo>
</gmd:MD_Metadata>
"""
    return xml, gaps


def main(argv: list[str] | None = None) -> int:
    themes = load_themes()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path, help="transform.json from transform_source.py")
    parser.add_argument("--title", required=True)
    parser.add_argument("--abstract", required=True)
    parser.add_argument(
        "--theme", required=True, help="INSPIRE theme; see assets/inspire-themes.json"
    )
    parser.add_argument("--organisation", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--licence", default="CC-BY-4.0")
    parser.add_argument("--licence-url")
    parser.add_argument(
        "--access-limitation",
        default="noLimitations",
        choices=themes["limitations_on_public_access"]["values"],
    )
    parser.add_argument("--access-url", help="where the dataset can be downloaded")
    parser.add_argument("--identifier", help="defaults to a generated urn:uuid")
    parser.add_argument("--language", default="fin", help="ISO 639-2/B code, e.g. fin, swe, eng")
    parser.add_argument("--topic-category", help="override the theme's default")
    parser.add_argument("--epsg", type=int, default=3067)
    parser.add_argument("--date", help="resource publication date, YYYY-MM-DD")
    parser.add_argument("--lineage", help="how the dataset came to be")
    parser.add_argument("--format", default="GeoPackage")
    parser.add_argument("--format-version", default="1.3")
    parser.add_argument("--bbox-wgs84", nargs=4, type=float, metavar=("W", "E", "S", "N"))
    parser.add_argument("--list-themes", action="store_true")
    args = parser.parse_args(argv)

    if args.list_themes:
        for name, info in themes["themes"].items():
            print(f"  Annex {info['annex']}: {name}  ->  {info['topic']}")
        return 0

    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    xml, gaps = build(args, summary, themes)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(xml, encoding="utf-8")
    print(f"wrote {args.out}")

    if gaps:
        print("\nThis record is not yet complete:")
        for gap in gaps:
            print(f"  ! {gap}")
    print(
        "\nThis asserts conformance rather than proving it. Upload the file to "
        "https://inspire.ec.europa.eu/validator/ and run the "
        "'INSPIRE data sets and data set series' suite for a verdict."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
