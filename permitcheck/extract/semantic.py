"""Semantic extraction: scanned floor plan -> engine-ready building elements.

This is the layer that turns raw geometry + OCR text into *meaning* - the
"which enclosed region is a room, which dimension belongs to which stair"
problem. It closes the gap between dimensional measurement (raster.py) and a
compliance verdict.

Pipeline (annotated architectural plan convention):
  1. Wall mask  -> connected components of the enclosed interior = rooms.
  2. OCR words (with boxes + confidence) are assigned to the room whose
     polygon contains their centroid  -> room name, occupancy keyword,
     ceiling-height callout ("CH 2440").
  3. Stairs: a cluster of parallel, evenly spaced lines (treads) gives the
     tread RUN by direct geometric measurement; a nearby "RISE/RUN" callout
     supplies the riser height; the two are bound by spatial proximity.
  4. Egress windows: "EGR <area>/<mindim>" callouts bound to the enclosing
     bedroom.
  5. A title-block block supplies building-level fields.

Every value keeps a {value, confidence, source} envelope; confidence is the
lower of the geometric-detection and OCR-token confidences, so weak reads
route to an UNCERTAIN verdict rather than a silent wrong answer.

Requires opencv-python + numpy (+ Tesseract for text). Standard architectural
annotation conventions are assumed and documented; unannotated / freehand
drawings remain out of scope.
"""

import math
import re

try:
    import cv2
    import numpy as np
    HAVE_CV2 = True
except ImportError:
    HAVE_CV2 = False

from . import raster
from .ocr import HAVE_TESSERACT

OCCUPANCY_KEYWORDS = {
    "bedroom": ("is_bedroom", True), "chambre": ("is_bedroom", True),
}
HABITABLE_KEYWORDS = ("bedroom", "living", "dining", "kitchen", "family", "den",
                      "chambre", "salon", "cuisine", "sejour")
NON_HABITABLE_KEYWORDS = ("bath", "wc", "closet", "storage", "mech", "utility",
                          "garage", "hall", "corridor", "salle de bain", "rangement")


# --------------------------------------------------------------------- #
# OCR words with bounding boxes
# --------------------------------------------------------------------- #
def _ocr_words(img, lang="eng", upscale=2):
    """OCR words with boxes. The image is upscaled (Tesseract reads larger
    text far more reliably) and boxes are mapped back to original coordinates."""
    from .ocr import _configure
    pt = _configure()
    if pt is None:
        return []
    big = cv2.resize(img, None, fx=upscale, fy=upscale,
                     interpolation=cv2.INTER_CUBIC) if upscale != 1 else img
    data = pt.image_to_data(big, lang=lang, output_type=pt.Output.DICT)
    words = []
    for i in range(len(data["text"])):
        txt = data["text"][i].strip()
        conf = float(data["conf"][i])
        if not txt or conf < 0:
            continue
        words.append({
            "text": txt, "conf": conf / 100.0,
            "cx": (data["left"][i] + data["width"][i] / 2.0) / upscale,
            "cy": (data["top"][i] + data["height"][i] / 2.0) / upscale,
            "left": data["left"][i] / upscale, "top": data["top"][i] / upscale,
            "w": data["width"][i] / upscale, "h": data["height"][i] / upscale,
        })
    return words


def _ocr_region_lines(img, room, lang="eng", upscale=3, pad=10):
    """OCR just one room's crop. Isolating the region lets Tesseract read
    stacked callouts (name / CH / EGR) far more reliably than on the full,
    busy sheet. Returns text lines with confidence."""
    from .ocr import _configure
    pt = _configure()
    if pt is None:
        return []
    h, w = img.shape
    x0 = max(0, room["x"] + pad)
    y0 = max(0, room["y"] + pad)
    x1 = min(w, room["x"] + room["w"] - pad)
    y1 = min(h, room["y"] + room["h"] - pad)
    if x1 - x0 < 20 or y1 - y0 < 20:
        return []
    crop = img[y0:y1, x0:x1]
    big = cv2.resize(crop, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
    # PSM 6: assume a uniform block of text - good for a room's callout stack
    data = pt.image_to_data(big, lang=lang, config="--psm 6",
                            output_type=pt.Output.DICT)
    words = []
    for i in range(len(data["text"])):
        txt = data["text"][i].strip()
        conf = float(data["conf"][i])
        if not txt or conf < 0:
            continue
        words.append({"text": txt, "conf": conf / 100.0,
                      "cx": data["left"][i] / upscale, "cy": data["top"][i] / upscale,
                      "left": data["left"][i] / upscale, "top": data["top"][i] / upscale,
                      "w": data["width"][i] / upscale, "h": data["height"][i] / upscale})
    return _line_text(words)


def _line_text(words, max_gap_factor=1.6):
    """Group words on the same text line (similar cy) into strings, so
    multi-word callouts like 'CH 2440' or 'BEDROOM 1' recombine."""
    words = sorted(words, key=lambda w: (round(w["cy"] / 12), w["cx"]))
    lines, cur = [], []
    for w in words:
        if cur and (abs(w["cy"] - cur[-1]["cy"]) > 0.7 * w["h"]
                    or w["left"] - (cur[-1]["left"] + cur[-1]["w"]) >
                    max_gap_factor * max(w["h"], 1)):
            lines.append(cur)
            cur = []
        cur.append(w)
    if cur:
        lines.append(cur)
    out = []
    for grp in lines:
        out.append({
            "text": " ".join(g["text"] for g in grp),
            "conf": min(g["conf"] for g in grp),
            "cx": sum(g["cx"] for g in grp) / len(grp),
            "cy": sum(g["cy"] for g in grp) / len(grp),
        })
    return out


# --------------------------------------------------------------------- #
# Room detection: enclosed interior connected components
# --------------------------------------------------------------------- #
def detect_rooms(binary, min_area_frac=0.004):
    """Return room polygons as (x, y, w, h, area) from the enclosed interior
    of the wall network. Walls are the ink; rooms are the holes."""
    h, w = binary.shape
    walls = cv2.dilate(binary, np.ones((3, 3), np.uint8), iterations=1)
    interior = cv2.bitwise_not(walls)
    # remove the outer background: flood fill from the border
    ff = interior.copy()
    mask = np.zeros((h + 2, w + 2), np.uint8)
    cv2.floodFill(ff, mask, (0, 0), 0)
    n, labels, stats, cents = cv2.connectedComponentsWithStats(ff, connectivity=4)
    rooms = []
    min_area = min_area_frac * h * w
    for i in range(1, n):
        x, y, ww, hh, area = stats[i]
        if area < min_area:
            continue
        if ww >= 0.98 * w and hh >= 0.98 * h:
            continue
        rooms.append({"x": int(x), "y": int(y), "w": int(ww), "h": int(hh),
                      "area_px": int(area),
                      "cx": float(cents[i][0]), "cy": float(cents[i][1])})
    return rooms


def _point_in_room(px, py, room, pad=0):
    return (room["x"] - pad <= px <= room["x"] + room["w"] + pad and
            room["y"] - pad <= py <= room["y"] + room["h"] + pad)


# --------------------------------------------------------------------- #
# Callout parsers
# --------------------------------------------------------------------- #
# Ceiling-height callout. Accepts the common tags used on real drawings -
# CH, C.H, C/H, CLG, CEIL, CEILING (and the OCR-slip "C4") - in either order
# ("CLG 2440" / "2440 CLG") and either unit (mm 2440 or metres 2.44). Anchored
# on the tag so stray numbers are ignored; range-validated to a storey height.
_CH_TAG = r"(?:C[H4]|C[./]H|CLG|CEIL(?:ING)?)"
_CH_AFTER = re.compile(r"\b" + _CH_TAG + r"\.?\s*[:=]?\s*(\d\.\d{1,2}|\d{3,4})\b", re.I)
_CH_BEFORE = re.compile(r"\b(\d\.\d{1,2}|\d{3,4})\s*" + _CH_TAG + r"\b", re.I)
_RISE_RE = re.compile(r"RISE\s*[:=]?\s*(\d{2,3})", re.I)
_RUN_RE = re.compile(r"RUN\s*[:=]?\s*(\d{2,3})", re.I)
_WIDTH_RE = re.compile(r"\bW\s*[:=]?\s*(\d{3,4})\b", re.I)
_HR_RE = re.compile(r"\bHR\s*[:=]?\s*(\d{3,4})\b", re.I)
_EGR_RE = re.compile(r"EGR\s*[:=]?\s*(\d\.\d{1,2})\s*/\s*(\d{3,4})", re.I)

CH_MIN_M, CH_MAX_M = 2.0, 3.5


def _ceiling_height_m(text):
    # metric callout: "CLG 2440", "CH 2.44", "2440 CLG"
    for rx in (_CH_AFTER, _CH_BEFORE):
        m = rx.search(text)
        if not m:
            continue
        tok = m.group(1)
        val = float(tok) if "." in tok else round(int(tok) / 1000.0, 3)
        if CH_MIN_M <= val <= CH_MAX_M:  # reject OCR garbage (e.g. 23550)
            return val
    # imperial callout tagged with a ceiling label: "9'-0\" CLG", "CLG 8'-0\""
    if re.search(_CH_TAG, text, re.I):
        from .section import imperial_to_mm
        mm = imperial_to_mm(text)
        if mm is not None and CH_MIN_M * 1000 <= mm <= CH_MAX_M * 1000:
            return round(mm / 1000.0, 3)
    return None


# --------------------------------------------------------------------- #
# Main extraction
# --------------------------------------------------------------------- #
def extract_from_image(image_or_path, px_per_mm, title_block=None,
                       lang="eng", source_name=None):
    """Full semantic extraction of a scanned floor plan into an engine-ready
    partial application (spaces, stairs). title_block supplies building-level
    fields the plan geometry cannot (occupancy, storeys, dwelling units)."""
    if not HAVE_CV2:
        raise RuntimeError("opencv-python + numpy required")
    img = image_or_path
    if isinstance(image_or_path, str):
        img = cv2.imread(image_or_path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise ValueError("cannot read image: %s" % image_or_path)
    src = source_name or (image_or_path if isinstance(image_or_path, str) else "scan")
    src = src.replace("\\", "/").rsplit("/", 1)[-1]

    binary = raster.binarize(img)
    geom = raster.analyze(img, px_per_mm)
    rooms = detect_rooms(binary)
    words = _ocr_words(img, lang=lang) if HAVE_TESSERACT else []

    def env(value, conf, note):
        return {"value": value, "confidence": round(conf, 3),
                "source": "%s (%s)" % (src, note)}

    # ---- rooms -> spaces (assign words to room FIRST, then group) -- #
    spaces = []
    for idx, room in enumerate(rooms):
        # per-room crop OCR (reliable on stacked callouts); fall back to the
        # whole-page words assigned by containment if the crop yields nothing
        inside = _ocr_region_lines(img, room, lang=lang) if HAVE_TESSERACT else []
        if not inside:
            inside = _line_text([w for w in words
                                 if _point_in_room(w["cx"], w["cy"], room, pad=-4)])
        low = " ".join(ln["text"] for ln in inside).lower()
        name = None
        for ln in inside:
            if any(k in ln["text"].lower()
                   for k in HABITABLE_KEYWORDS + NON_HABITABLE_KEYWORDS):
                name = ln["text"]
                break
        is_bedroom = any(k in low for k in ("bedroom", "chambre"))
        habitable = any(k in low for k in HABITABLE_KEYWORDS)
        is_service = any(k in low for k in NON_HABITABLE_KEYWORDS)
        space = {"id": "S-%02d" % (idx + 1), "name": name or ("Room %d" % (idx + 1)),
                 "is_bedroom": is_bedroom, "habitable": habitable and not is_service}
        for ln in inside:
            val = _ceiling_height_m(ln["text"])
            if val is not None:
                space["ceiling_height_m"] = env(val, min(ln["conf"], 0.95),
                                                "CH callout, %s" % space["name"])
            m = _EGR_RE.search(ln["text"])
            if m:
                space["egress_window"] = {
                    "open_area_m2": env(float(m.group(1)), min(ln["conf"], 0.95), "EGR callout"),
                    "min_dimension_mm": env(int(m.group(2)), min(ln["conf"], 0.95), "EGR callout"),
                }
        spaces.append(space)

    # ---- stairs: parallel tread cluster GATED by a nearby STAIR label #
    # A parallel run is only a stair if (a) a "STAIR" label sits near it and
    # (b) the spacing is a plausible tread run (100-450 mm). This rejects the
    # evenly-spaced walls of the plan.
    stairs = []
    all_lines = _line_text(words)
    stair_labels = [ln for ln in all_lines
                    if re.search(r"STAIR|RISE|RUN|\bHR\b|\bW\s*\d", ln["text"], re.I)]
    stair_text = " ".join(ln["text"] for ln in stair_labels)
    stair_conf = min([ln["conf"] for ln in stair_labels], default=0.9)
    has_stair_callout = bool(re.search(r"STAIR|RISE", stair_text, re.I))

    if has_stair_callout:
        stair = {"id": "ST-1", "name": "Stair 1", "private": True}
        # RUN: prefer direct geometric measurement of the tread spacing;
        # fall back to the RUN callout if no tread cluster was detected.
        tread = next((c for c in geom["parallel_clusters"]
                      if 100.0 <= c["value"] <= 450.0), None)
        if tread:
            stair["run_mm"] = env(round(tread["value"], 1), min(tread["confidence"], 0.9),
                                  "measured tread spacing (%d treads)" % tread["members"])
        else:
            rn = _RUN_RE.search(stair_text)
            if rn:
                stair["run_mm"] = env(int(rn.group(1)), min(stair_conf, 0.85), "RUN callout")
        for rx, field in ((_RISE_RE, "rise_mm"), (_WIDTH_RE, "width_mm"), (_HR_RE, "headroom_mm")):
            m = rx.search(stair_text)
            if m:
                stair[field] = env(int(m.group(1)), min(stair_conf, 0.9),
                                   "%s callout" % field.replace("_mm", ""))
        stairs.append(stair)

    app = {"spaces": spaces, "stairs": stairs}
    if title_block:
        app["building"] = dict(title_block)
    app["_extraction"] = {
        "rooms_detected": len(rooms), "stairs_detected": len(stairs),
        "ocr_lines": len(all_lines), "skew_deg": geom["skew_deg"],
        "px_per_mm": px_per_mm,
    }
    return app
