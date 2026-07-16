"""Generate annotated PNGs of drawings on demand (for the web viewer).

One entry point - annotate_to_png(path) - auto-detects the sheet kind and
overlays what the extractor found, with rotation-aware placement so boxes land
on the exact source text:

  * section / detail  -> stair RISE/RUN (red boxes) + ceiling candidates (blue)
  * vector floor plan -> room labels (green) + stairs (red) + dimension dots
  * schedule / scan    -> detected 'SF' room-schedule anchors (green)

Requires opencv + PyMuPDF. Returns PNG bytes.
"""

import os

_IMPORT_ERROR = None
try:
    import cv2
    import fitz
    import numpy as np
    HAVE = True
except Exception as _exc:  # capture the real cause (DLL load, version, etc.)
    HAVE = False
    _IMPORT_ERROR = "%s: %s" % (type(_exc).__name__, _exc)

from . import analyze

RED, BLUE, GREEN, WHITE = (36, 36, 210), (200, 110, 0), (40, 150, 40), (255, 255, 255)


def _render(page, zoom):
    """Return (bgr image, text->pixel matrix) with page rotation respected."""
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR) if pix.n >= 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    mat = page.rotation_matrix * fitz.Matrix(zoom, zoom)
    return img, mat


def _box_label(img, mat, bbox, text, color, font=0.62, thick=2, dy=-34):
    r = (fitz.Rect(bbox) * mat).irect
    x0, y0, x1, y1 = r.x0 - 4, r.y0 - 4, r.x1 + 4, r.y1 + 4
    if x1 - x0 < 14:
        x0 -= 7
        x1 += 7
    if y1 - y0 < 14:
        y0 -= 7
        y1 += 7
    cv2.rectangle(img, (x0, y0), (x1, y1), color, 3)
    anchor = ((x0 + x1) // 2, y0)
    lx, ly = anchor[0] + 14, anchor[1] + dy
    cv2.line(img, anchor, (lx, ly), color, 2, cv2.LINE_AA)
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font, thick)
    cv2.rectangle(img, (lx - 3, ly - th - 5), (lx + tw + 4, ly + 5), WHITE, -1)
    cv2.rectangle(img, (lx - 3, ly - th - 5), (lx + tw + 4, ly + 5), color, 1)
    cv2.putText(img, text, (lx, ly), cv2.FONT_HERSHEY_SIMPLEX, font, color, thick, cv2.LINE_AA)


def _banner(img, text):
    cv2.rectangle(img, (0, 0), (img.shape[1], 58), WHITE, -1)
    cv2.putText(img, text, (18, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.95, RED, 2, cv2.LINE_AA)


def _annotate_section(page, path, zoom):
    from .extract import section
    img, mat = _render(page, zoom)
    data = section.extract(path)
    for key, item in data["stair"].items():
        if item:
            _box_label(img, mat, item["bbox"],
                       "%s = %d mm (from '%s')" % (key.replace("_mm", "").upper(),
                                                   item["value"], item["text"]),
                       RED, font=0.66, dy=-44)
    for i, c in enumerate(data["ceiling_height_candidates"][:6]):
        tag = "CEIL? %.2fm" % c["value_m"] + ("" if not c["note"] else " (%s)" % c["note"])
        _box_label(img, mat, c["bbox"], tag, BLUE, dy=-28 - 24 * (i % 3))
    _banner(img, "SECTION  -  stair rise/run (red) + ceiling-height candidates (blue)")
    return img


def _annotate_vector(page, path, zoom):
    from .extract import vectorplan
    img, mat = _render(page, zoom)
    data = vectorplan.extract(path)

    def to_px(x, y):
        p = fitz.Point(x, y) * mat
        return (int(round(p.x)), int(round(p.y)))

    for d in data["dimensions"]:
        cv2.circle(img, to_px(d["x"], d["y"]), 4, BLUE, -1)
    for sp in data["spaces"]:
        p = to_px(sp["x"], sp["y"])
        cv2.circle(img, p, 7, GREEN, -1)
        cv2.putText(img, sp["label"], (p[0] + 12, p[1] + 5), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, GREEN, 2, cv2.LINE_AA)
    for st in data["stairs"]:
        p = to_px(st["x"], st["y"])
        cv2.circle(img, p, 11, RED, 3)
        # prefer the meaningful label (e.g. "STAIR (UP 15R)"); fall back to dims
        label = st.get("label")
        if not label or label.upper() in ("STAIR", "STAIRS"):
            label = "STAIR " + "/".join("%dmm" % d for d in st.get("nearby_dims_mm", [])[:2])
        cv2.putText(img, label, (p[0] + 14, p[1] + 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.72, RED, 2, cv2.LINE_AA)
    s = data["summary"]
    _banner(img, "VECTOR PLAN  -  %d rooms | %d stairs | %d dimensions"
            % (s["spaces"], s["stairs"], s["dimensions"]))
    return img


def _annotate_schedule(page, path, zoom):
    from .extract import planscan
    img, mat = _render(page, zoom)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    words, scale = planscan._words(gray)
    # words are in the (already-rendered) gray image space; scale back
    for w in words:
        if w["text"].upper() in ("SF", "SE", "SFE"):
            x, y = int(w["x"]), int(w["y"])
            cv2.rectangle(img, (x - 4, y - 4), (x + int(w["w"]) + 4, y + int(w["h"]) + 4),
                          GREEN, 2)
    sched = planscan.extract_room_schedule(gray)
    _banner(img, "SCHEDULE  -  %d room records detected (green = SF anchors)"
            % len(sched["rooms"]))
    return img


def annotate_to_png(path, kind=None, zoom=2.5):
    """Auto-detect the sheet kind and return an annotated PNG as bytes."""
    if not HAVE:
        raise RuntimeError("opencv-python + PyMuPDF required (%s)" % _IMPORT_ERROR)
    info = analyze.analyze(path)
    types = set(info["sheet_types"])
    fmt = info["format"]
    doc = fitz.open(path)
    page = doc[0]

    if kind is None:
        if "section" in types or "detail" in types:
            kind = "section"
        elif fmt == "vector_pdf":
            kind = "vector"
        else:
            kind = "schedule"

    if kind == "section":
        img = _annotate_section(page, path, zoom)
    elif kind == "vector":
        img = _annotate_vector(page, path, zoom)
    else:
        img = _annotate_schedule(page, path, zoom)

    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError("failed to encode PNG")
    return buf.tobytes(), kind
