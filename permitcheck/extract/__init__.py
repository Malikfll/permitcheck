"""Document/BIM extraction adapters (essential outcome: process 2D drawings
(PDF/CAD) and BIM/IFC models plus permit application data).

Each adapter turns a real file into engine-ready fields wrapped in
{"value", "confidence", "source"} envelopes. Confidence is assigned by a
deterministic policy per source type - structured BIM data is more trustworthy
than CAD text annotations, which beat form text:

    IFC/STEP property sets   0.98
    DXF text annotations     0.90
    PDF form text            0.85

The pipeline merges adapters with highest-confidence-wins precedence, so the
UNCERTAIN verdict downstream is grounded in real extraction provenance.
"""

CONFIDENCE = {"ifc": 0.98, "dxf": 0.90, "pdf": 0.85}
