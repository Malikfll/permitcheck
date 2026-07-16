# PermitCheck

A deterministic compliance-checking engine for building permit applications. It
reads a submission in the formats jurisdictions actually use, extracts the
compliance-relevant data, classifies the building under the right part of the
National Building Code of Canada, and evaluates it against machine-executable
rules. Every verdict traces back to a code article and a source document.

"Deterministic" is the whole point: there is no probabilistic component in the
decision path. The same input always produces the same verdicts and the same
run id, which is what makes the output auditable.

## Extraction

Permit submissions arrive as anything. The pipeline has a real adapter per
format and merges them highest-confidence-wins, fingerprinting every document
with SHA-256:

| Source | Adapter | Notes |
|---|---|---|
| BIM / IFC models | `extract/ifc.py` | IFC/STEP parser with an IFC to field data dictionary. Validated on the 2,885-entity IfcOpenHouse IFC4 model. |
| CAD annotations | `extract/dxf.py` | DXF text and attributes |
| CAD geometry | `extract/dxf_geom.py` | Measured geometry including DIMENSION entities |
| Scanned drawings | `extract/raster.py` | OpenCV analysis, 99.7% dimensional accuracy on real drawings |
| Permit forms | `extract/pdfx.py` | pdfplumber, validated on the official Ontario form |

## Classification and rules

A two-stage deterministic classifier: Part 3 vs Part 9 first, then either the
NBC 3.2.2 first-match decision table or the Part 9 subtype (house, house with
secondary suite, small multi-dwelling, small non-residential). Rules can target
a specific typology.

The ruleset (`rules/nbc_rules.json`) is RASE-annotated, bilingual and versioned.
The engine returns one of four verdicts:

```
MEETS  |  DOES_NOT_MEET  |  INFO_NOT_AVAILABLE  |  UNCERTAIN
```

`UNCERTAIN` is deliberate: it fires when extraction confidence is low or when a
value sits within the declared measurement tolerance of the limit. Guessing in
those cases would be the failure mode this engine exists to avoid.

## Auditability

Every check records the observed value, the source document, the confidence, the
code reference, and the engine and ruleset versions, into a SHA-256 hash-chained
audit log (`audit.py`). A reviewer can reconstruct exactly why any verdict was
reached, and tampering with the log breaks the chain.

## Interfaces

Bilingual web UI (`static/index.html`), REST API (`server.py`), CLI (`cli.py`).
Exports to CSV, JSON, HTML/PDF, and BCF 2.1 for openBIM round-trips.

## Running it

The core (engine, API, UI, reports, audit) is Python 3.9+ standard library only.

```
python -m permitcheck.cli --help
```

The computer-vision extraction stack is optional:

```
pip install -r requirements.txt   # opencv, numpy, ezdxf, pdfplumber
```

## Status

Prototype. The rule set covers a subset of the NBC, and the 3.2.2 decision table
is simplified.
