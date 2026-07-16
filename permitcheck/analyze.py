"""Unified, dynamic entry point for ANY drawing the user feeds in.

One function - analyze(path) - that adapts to the input instead of assuming a
case:

  1. Detect the file format: vector PDF (has a text layer) vs raster scan/image.
  2. Detect the sheet TYPE from the drawing's own title text - floor plan,
     section, elevation, schedule, detail - dynamically, no hardcoding.
  3. Route to the matching extractor (vector text / OCR scan / schedule table).
  4. Report which compliance inputs the sheet provides, and raise a dynamic
     Request for Information for the ones that require sheets not yet supplied
     (e.g. "no section sheet -> ceiling heights & stair rise still needed").

Feed it one sheet or many: analyze_set([...]) aggregates coverage across a
whole drawing set and asks only for what is still missing.
"""

import os
import re

# sheet-type signatures (keywords found in the sheet's title/labels)
SHEET_SIGNATURES = {
    "floor_plan": (r"floor\s*plan", r"\bplan\b"),
    "section": (r"\bsection\b", r"cross[-\s]?section", r"building section"),
    "elevation": (r"\belevation\b",),
    "schedule": (r"room\s*schedule", r"door\s*schedule", r"window\s*schedule",
                 r"\bschedule\b", r"\bsf\b.*\broom\b"),
    "detail": (r"\bdetail\b", r"\bdet\.\b"),
    "site_plan": (r"site\s*plan",),
}

# which compliance inputs each sheet type can supply
SHEET_PROVIDES = {
    "floor_plan": {"room_areas", "room_layout", "stair_run", "guard_locations"},
    "section": {"ceiling_heights", "stair_rise", "stair_headroom", "guard_heights"},
    "elevation": {"guard_heights", "window_heights"},
    "schedule": {"room_areas", "egress_windows", "door_sizes"},
    "detail": {"stair_rise", "stair_run", "guard_heights"},
    "site_plan": {"setbacks", "lot_coverage"},
}

# compliance inputs -> plain-language label + which sheet normally carries it
INPUT_INFO = {
    "room_areas": ("Room areas", "floor plan / room schedule"),
    "room_layout": ("Room layout & names", "floor plan"),
    "ceiling_heights": ("Ceiling heights (habitable rooms)", "building section"),
    "stair_rise": ("Stair riser height", "stair section / detail"),
    "stair_run": ("Stair tread run", "floor plan / stair detail"),
    "stair_headroom": ("Stair headroom", "stair section"),
    "guard_heights": ("Guard heights", "section / elevation / detail"),
    "egress_windows": ("Bedroom egress window sizes", "window schedule"),
    "fire_protection": ("Smoke/CO/fire-alarm provisions", "life-safety plan / specs"),
}
# inputs required for the current NBC rule set to reach a full verdict
REQUIRED_FOR_VERDICT = {"room_layout", "ceiling_heights", "stair_rise",
                        "stair_run", "guard_heights", "egress_windows"}


# --------------------------------------------------------------------- #
def _page_text(path):
    """Return (text, is_vector). Vector PDFs expose a real text layer."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        import fitz
        doc = fitz.open(path)
        txt = "\n".join(p.get_text() for p in doc)
        is_vector = len(txt.strip()) > 40
        if is_vector:
            return txt, True
        # scanned PDF: OCR the rendered page for the title text
        try:
            from .extract import planscan, ocr
            if ocr.HAVE_TESSERACT:
                img = planscan.load_image(path)
                words, _ = planscan._words(img)
                return " ".join(w["text"] for w in words), False
        except Exception:
            pass
        return "", False
    # image: OCR
    try:
        from .extract import ocr
        import cv2
        if ocr.HAVE_TESSERACT:
            lines = ocr.read_lines(path)
            return " ".join(l["text"] for l in lines), False
    except Exception:
        pass
    return "", False


def detect_sheet_types(text):
    low = text.lower()
    found = []
    for stype, patterns in SHEET_SIGNATURES.items():
        if any(re.search(p, low) for p in patterns):
            found.append(stype)
    return found or ["unknown"]


def detect_format(path):
    _, is_vector = _page_text(path)
    ext = os.path.splitext(path)[1].lower()
    if ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff"):
        return "raster_image"
    return "vector_pdf" if is_vector else "raster_pdf"


# --------------------------------------------------------------------- #
def analyze(path):
    """Analyze one sheet: detect format + type, extract, report coverage."""
    text, is_vector = _page_text(path)
    sheet_types = detect_sheet_types(text)
    fmt = "raster_image" if os.path.splitext(path)[1].lower() in (
        ".png", ".jpg", ".jpeg", ".tif", ".tiff") else (
        "vector_pdf" if is_vector else "raster_pdf")

    extraction = {}
    provided = set()
    try:
        if is_vector and path.lower().endswith(".pdf"):
            from .extract import vectorplan
            data = vectorplan.extract(path)
            extraction = {"kind": "vector",
                          "rooms": len(data["spaces"]), "stairs": len(data["stairs"]),
                          "dimensions": len(data["dimensions"]),
                          "dim_range_mm": data["summary"]["dim_range_mm"]}
            if data["spaces"]:
                provided |= {"room_layout", "stair_run"}
            if data["dimensions"]:
                provided |= {"stair_run"}
        else:
            from .extract import planscan
            sched = planscan.extract_room_schedule(planscan.load_image(path))
            extraction = {"kind": "raster", "rooms": len(sched["rooms"]),
                          "total_area_sf": sched["total_area_sf"]}
            if sched["rooms"]:
                provided |= {"room_areas", "room_layout"}
    except Exception as exc:  # pragma: no cover - defensive
        extraction = {"error": str(exc)}

    # what the DETECTED sheet types can, in principle, provide
    for stype in sheet_types:
        provided |= SHEET_PROVIDES.get(stype, set())

    missing = sorted(REQUIRED_FOR_VERDICT - provided)
    return {
        "source": os.path.basename(path),
        "format": fmt,
        "sheet_types": sheet_types,
        "extraction": extraction,
        "provides": sorted(provided & set(INPUT_INFO)),
        "missing_for_verdict": missing,
        "request_for_information": _rfi(missing),
    }


def _rfi(missing):
    out = []
    for item in missing:
        label, sheet = INPUT_INFO.get(item, (item, "?"))
        out.append({"item": item, "need": label, "provide_sheet": sheet})
    return out


def analyze_set(paths):
    """Aggregate coverage across several sheets and ask only for what is still
    missing after considering ALL of them together."""
    sheets = [analyze(p) for p in paths]
    provided = set()
    for s in sheets:
        provided |= set(s["provides"])
        for stype in s["sheet_types"]:
            provided |= SHEET_PROVIDES.get(stype, set())
    missing = sorted(REQUIRED_FOR_VERDICT - provided)
    return {
        "sheets": sheets,
        "combined_provides": sorted(provided & set(INPUT_INFO)),
        "still_missing_for_verdict": missing,
        "request_for_information": _rfi(missing),
        "ready_for_full_verdict": not missing,
        "message": ("All compliance inputs present - a full verdict can be produced."
                    if not missing else
                    "Provide the following to complete the check: "
                    + "; ".join("%s (on the %s)" % (INPUT_INFO[m][0], INPUT_INFO[m][1])
                                for m in missing)),
    }


def main():
    import json
    import sys
    if len(sys.argv) < 2:
        print("usage: python -m permitcheck.analyze <sheet.pdf> [more sheets...]")
        return 1
    if len(sys.argv) == 2:
        print(json.dumps(analyze(sys.argv[1]), indent=2, ensure_ascii=False))
    else:
        result = analyze_set(sys.argv[1:])
        for s in result["sheets"]:
            print("- %-24s format=%-11s type=%s  extracted=%s"
                  % (s["source"], s["format"], ",".join(s["sheet_types"]),
                     s["extraction"].get("rooms", s["extraction"])))
        print("\nCombined provides :", result["combined_provides"])
        print("Still missing     :", result["still_missing_for_verdict"])
        print("Ready for verdict :", result["ready_for_full_verdict"])
        print("\n" + result["message"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
