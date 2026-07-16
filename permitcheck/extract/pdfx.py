"""PDF permit-form extraction adapter.

Extracts text from uncompressed PDF content streams (Tj / TJ operators) and
maps ``LABEL: VALUE`` lines to application fields via a deterministic
dictionary. Handles the vector-text PDFs produced by e-permitting portals;
a Phase 2 adapter adds FlateDecode streams, AcroForm fields and OCR (with the
OCR engine's own per-token confidence feeding the UNCERTAIN verdict).
"""

import re
from . import CONFIDENCE

# label (uppercased) -> dotted application field path
PDF_FIELDS = {
    "APPLICATION NO": "application.id",
    "MUNICIPALITY": "application.municipality",
    "APPLICANT": "application.applicant",
    "LEGAL DESCRIPTION": "application.legal_description",
    "DWELLING UNITS": "building.dwelling_units",
    "SECONDARY SUITE": "building.has_secondary_suite",
    "SMOKE ALARM EACH STOREY": "fire_safety.smoke_alarm_each_storey",
    "SMOKE ALARM EACH BEDROOM": "fire_safety.smoke_alarm_each_bedroom",
    "SMOKE ALARMS INTERCONNECTED BETWEEN SUITES": "fire_safety.smoke_alarms_interconnected",
    "CO ALARM PROVIDED": "fire_safety.co_alarm_provided",
    "FUEL BURNING APPLIANCE": "building.fuel_burning_appliance",
    "ATTACHED GARAGE": "building.attached_garage",
}

_UNESCAPE = {r"\(": "(", r"\)": ")", r"\\": "\\"}


def _stream_text(data: bytes) -> str:
    """Concatenate text shown by Tj/TJ operators in uncompressed streams."""
    chunks = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        stream = m.group(1).decode("latin-1")
        for tm in re.finditer(r"\((?:[^()\\]|\\.)*\)\s*Tj|\[(?:[^\[\]\\]|\\.)*\]\s*TJ", stream):
            for sm in re.finditer(r"\(((?:[^()\\]|\\.)*)\)", tm.group(0)):
                text = sm.group(1)
                for esc, ch in _UNESCAPE.items():
                    text = text.replace(esc, ch)
                chunks.append(text)
            chunks.append("\n")
    return "".join(chunks)


def _coerce(raw: str):
    up = raw.strip().upper()
    if up in ("YES", "TRUE", "OUI", "X"):
        return True
    if up in ("NO", "FALSE", "NON"):
        return False
    try:
        return float(raw) if "." in raw else int(raw)
    except ValueError:
        return raw.strip()


def extract_text(path):
    """All text lines from a PDF. Prefers pdfplumber (handles FlateDecode and
    other real-world encodings); falls back to the stdlib content-stream
    parser for uncompressed PDFs when pdfplumber is not installed."""
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        if text.strip():
            return text, "pdfplumber"
    except ImportError:
        pass
    with open(path, "rb") as fh:
        return _stream_text(fh.read()), "builtin"


def extract(path):
    """Extract application/form fields; returns nested dicts with envelopes."""
    text, _parser = extract_text(path)
    fname = path.replace("\\", "/").rsplit("/", 1)[-1]

    result = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        label, raw = line.split(":", 1)
        target = PDF_FIELDS.get(label.strip().upper())
        if not target or not raw.strip():
            continue
        node = result
        parts = target.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        value = _coerce(raw)
        if parts[0] == "application":
            # administrative identity fields are taken verbatim, no envelope
            node[parts[-1]] = value
        else:
            node[parts[-1]] = {
                "value": value,
                "confidence": CONFIDENCE["pdf"],
                "source": "%s (form line \"%s\")" % (fname, line.strip()),
            }
    return result
