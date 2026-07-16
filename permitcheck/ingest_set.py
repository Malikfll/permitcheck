"""Multi-document (multi-'Fiche') submission ingestion.

Real permit submissions - e.g. Montreal's per-'Fiche' breakdown - spread the
compliance data across many separate PDFs: one for the floor plan, one for the
window schedule, one for the sections, a permit form, etc. This module accepts
a whole folder (or an explicit list) of such files, auto-classifies each one
(no manifest required), routes it to the right extractor, and MERGES every
contribution into a single application - highest-confidence value wins, and
collection items (spaces, stairs...) merge by id across documents.

The merged application then goes to the deterministic engine exactly like a
single-file submission; whatever no Fiche supplied is surfaced as
INFO_NOT_AVAILABLE and can be entered manually.

    from permitcheck.ingest_set import ingest_folder
    app = ingest_folder("submissions/montreal_1234")   # any set of PDFs/images
    run = RulesEngine.from_file(RULES_PATH).run(app)
"""

import hashlib
import os

from . import analyze
from .extract import pipeline

SUPPORTED = (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".ifc", ".dxf")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _partial_from_document(path, sheet_types):
    """Turn one Fiche into an engine-ready partial application, chosen by its
    detected sheet type / format."""
    ext = os.path.splitext(path)[1].lower()

    # native BIM / CAD adapters
    if ext == ".ifc":
        from .extract import ifc
        return ifc.extract(path)
    if ext == ".dxf":
        from .extract import dxf
        return dxf.extract(path)

    # a room/area schedule Fiche -> spaces with areas (+ egress if noted)
    if "schedule" in sheet_types:
        from .extract import planscan
        sched = planscan.extract_room_schedule(planscan.load_image(path))
        spaces = []
        for r in sched["rooms"]:
            if not r.get("name"):
                continue
            sp = {"id": r.get("number") or r["name"], "name": r["name"]}
            if r.get("area_sf"):
                sp["area_m2"] = {"value": round(r["area_sf"] * 0.092903, 2),
                                 "confidence": r.get("confidence", 0.8),
                                 "source": "%s (schedule row)" % os.path.basename(path)}
            spaces.append(sp)
        return {"spaces": spaces}

    # a SECTION / detail Fiche -> labelled stair rise/run (high confidence) and
    # ceiling-height candidates (surfaced for confirmation, not asserted)
    if ("section" in sheet_types or "detail" in sheet_types) and ext == ".pdf":
        from .extract import section
        sec = section.extract(path)
        out = {}
        rise, run = sec["stair"]["rise_mm"], sec["stair"]["run_mm"]
        if rise or run:
            stair = {"id": "ST-1", "name": "Stair (from section)", "private": True}
            if rise:
                stair["rise_mm"] = rise
            if run:
                stair["run_mm"] = run
            out["stairs"] = [stair]
        if sec["ceiling_height_candidates"]:
            # keep the ranked candidates on the application so the reviewer can
            # confirm which is the room ceiling height (human-in-the-loop)
            out["_ceiling_height_candidates"] = sec["ceiling_height_candidates"]
        if out:
            return out

    # a vector floor-plan / detail Fiche -> stairs (measured run) + room labels
    text_is_vector = analyze.detect_format(path) == "vector_pdf"
    if text_is_vector and ext == ".pdf":
        from .extract import vectorplan
        data = vectorplan.extract(path)
        stairs = []
        for i, st in enumerate(data["stairs"]):
            entry = {"id": "ST-%d" % (i + 1), "name": st["label"], "private": True}
            dims = st.get("nearby_dims_mm", [])
            run = next((d for d in dims if 200 <= d <= 400), None)
            if run:
                entry["run_mm"] = {"value": run, "confidence": 0.9,
                                   "source": "%s (stair-adjacent dimension)" % os.path.basename(path)}
            stairs.append(entry)
        return {"stairs": stairs}

    # a scanned floor-plan Fiche -> full semantic extraction (rooms + stairs)
    if "floor_plan" in sheet_types:
        try:
            from .extract import semantic
            # px_per_mm unknown for an arbitrary scan; semantic still yields
            # room typing + callouts where present
            return {}  # requires a scale; handled via analyze/manual entry
        except Exception:
            return {}

    # a permit-form Fiche -> building metadata + fire-safety answers
    from .extract import pdfx
    try:
        return pdfx.extract(path)
    except Exception:
        return {}


def _infer_building(paths):
    """Infer building-level metadata (for typology classification) from the
    set of sheet names/titles - e.g. an 'upper floor plan' implies >=2 storeys,
    a residential set implies major occupancy C. Low-confidence, so any value
    is easily overridden by a form Fiche or manual entry."""
    import fitz
    titles = ""
    storeys = 1
    residential = False
    for p in paths:
        if not p.lower().endswith(".pdf"):
            continue
        try:
            titles += fitz.open(p)[0].get_text().lower() + "\n"
        except Exception:
            continue
    if "upper floor plan" in titles or "second floor plan" in titles:
        storeys = 2
    if any(k in titles for k in ("bedroom", "dwelling", "residence", "new home",
                                 "single family", "house")):
        residential = True
    b = {}
    src = "inferred from sheet titles"
    b["storeys"] = {"value": storeys, "confidence": 0.6, "source": src}
    if residential:
        b["major_occupancy"] = {"value": "C", "confidence": 0.6, "source": src}
        b["dwelling_units"] = {"value": 1, "confidence": 0.5, "source": src}
        b["has_secondary_suite"] = {"value": False, "confidence": 0.5, "source": src}
    return b


def ingest_documents(paths):
    """Merge an explicit list of Fiche files into one application."""
    app = {"application": {"documents": []}}
    for path in paths:
        info = analyze.analyze(path)
        partial = _partial_from_document(path, info["sheet_types"])
        pipeline._merge_dict(app, partial)
        app["application"]["documents"].append({
            "name": os.path.basename(path),
            "format": info["format"],
            "sheet_types": info["sheet_types"],
            "sha256": _sha256(path),
            "contributed": sorted(k for k in partial if k != "application"),
        })
    if paths:
        pipeline._merge_dict(app, {"building": _infer_building(paths)})
    return app


def ingest_folder(folder):
    """Merge every supported document in a folder into one application."""
    paths = [os.path.join(folder, n) for n in sorted(os.listdir(folder))
             if os.path.splitext(n)[1].lower() in SUPPORTED]
    app = ingest_documents(paths)
    app["application"].setdefault("id", os.path.basename(os.path.normpath(folder)))
    return app


def coverage(paths):
    """Report, across the whole Fiche set, what is present and still missing -
    the dynamic Request for Information for a multi-document submission."""
    return analyze.analyze_set(paths)
