"""BCF (BIM Collaboration Format) 2.1 export.

Turns every non-passing rule result into a BCF topic so findings flow back
into openBIM authoring tools (Revit, Archicad, Solibri, BIMcollab…). Topic
GUIDs are deterministic (UUIDv5 of run + rule) so re-exports are stable.
"""

import io
import re
import uuid
import zipfile
from xml.sax.saxutils import escape

_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

_STATUS = {"DOES_NOT_MEET": "Error", "UNCERTAIN": "Warning", "INFO_NOT_AVAILABLE": "Info"}


def _slug(s):
    return re.sub(r"[^A-Za-z0-9_-]", "_", s)


def _markup(run, result, guid, lang="en"):
    checks = []
    for inst in result["instances"]:
        for c in inst.get("checks", []):
            if c["verdict"] == "MEETS":
                continue
            unit = c.get("unit") or ""
            checks.append("- %s [%s]: observed %s %s, limit %s %s (%s)" % (
                inst["element"], c["verdict"], c.get("observed"), unit,
                c.get("limit"), unit, c.get("reason") or c.get("message", {}).get(lang, "")))
    description = "%s\n\nCode reference: %s\nRule: %s\nRun: %s\nInput: %s\n\n%s" % (
        result["requirement_text"][lang], result["reference"], result["rule_id"],
        run["run_id"], run["input_hash"], "\n".join(checks))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Markup xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
        '  <Topic Guid="%s" TopicType="Issue" TopicStatus="%s">\n'
        "    <Title>%s</Title>\n"
        "    <Priority>%s</Priority>\n"
        "    <CreationDate>%s</CreationDate>\n"
        "    <CreationAuthor>PermitCheck %s</CreationAuthor>\n"
        "    <Description>%s</Description>\n"
        "  </Topic>\n"
        "</Markup>\n"
    ) % (guid, _STATUS.get(result["verdict"], "Info"),
         escape("%s - %s" % (result["rule_id"], result["title"][lang])),
         escape(result["severity"]), run["timestamp"], run["engine_version"],
         escape(description))


def to_bcf(run, lang="en") -> bytes:
    """Build a .bcfzip with one topic per non-passing applicable rule."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bcf.version",
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<Version VersionId="2.1" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                    "<DetailedVersion>2.1</DetailedVersion></Version>\n")
        for result in run["results"]:
            if not result.get("applicable") or result["verdict"] == "MEETS":
                continue
            guid = str(uuid.uuid5(_NAMESPACE, run["run_id"] + "/" + result["rule_id"]))
            zf.writestr("%s/markup.bcf" % guid, _markup(run, result, guid, lang))
    return buf.getvalue()


def suggested_filename(run):
    return "%s_findings.bcfzip" % _slug(run["run_id"])
