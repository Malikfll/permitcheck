"""Dynamic, resolution-independent analysis of real architectural plan sheets.

Handles arbitrary PDF/image plans (any page size, any scan resolution) - no
hardcoded dimensions. Two capabilities:

1. Room-schedule extraction. Real plans carry a room/area schedule as a dense
   multi-column table. We anchor on the "SF" (square-feet) tokens: every
   schedule row has one, so their x-positions reveal the column groups and
   their y-positions reveal the rows - no fixed crop, no fixed column count.
   Each row parses to {area_sf, name, number} with OCR confidence.

2. Missing-information detection ("ask what's not on the plan"). A single plan
   sheet rarely carries every compliance datum (ceiling heights and stair
   geometry live on section sheets). analyze_completeness() reports which
   compliance inputs are present vs absent, so the system can raise a
   structured Request for Information instead of guessing - this is the
   human-in-the-loop side of the challenge's "Information Not Available"
   verdict.

Requires opencv + numpy (+ Tesseract for text, PyMuPDF for PDF input).
"""

import os
import re

try:
    import cv2
    import numpy as np
    HAVE_CV2 = True
except ImportError:
    HAVE_CV2 = False


# --------------------------------------------------------------------- #
# Dynamic image loading - native resolution, any source
# --------------------------------------------------------------------- #
def load_image(path, min_long_edge=4000):
    """Load a plan as a grayscale array at the best available resolution.
    For PDFs, the embedded raster is used at native size; if that is small,
    the page is re-rendered at higher DPI. Fully resolution-adaptive."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        import fitz
        doc = fitz.open(path)
        page = doc[0]
        best = None
        for img in page.get_images():
            base = doc.extract_image(img[0])
            arr = cv2.imdecode(np.frombuffer(base["image"], np.uint8), cv2.IMREAD_GRAYSCALE)
            if arr is not None and (best is None or arr.size > best.size):
                best = arr
        if best is not None and max(best.shape) >= min_long_edge:
            return best
        # embedded image too small (or none): render the page at scaled DPI
        scale = max(1.0, min_long_edge / max(page.rect.width, page.rect.height))
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csGRAY)
        return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError("cannot read image: %s" % path)
    return img


# --------------------------------------------------------------------- #
# OCR words with boxes (dynamic upscale so small text is legible)
# --------------------------------------------------------------------- #
def _words(img, target_text_px=34):
    from .ocr import _configure
    pt = _configure()
    if pt is None:
        return [], 1.0
    # scale so the sheet's long edge is generous; Tesseract likes larger glyphs
    scale = max(1.0, min(4.0, 9000.0 / max(img.shape)))
    big = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    big = cv2.threshold(big, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    data = pt.image_to_data(big, config="--psm 11", output_type=pt.Output.DICT)
    words = []
    for i in range(len(data["text"])):
        t = data["text"][i].strip()
        c = float(data["conf"][i])
        if not t or c < 0:
            continue
        words.append({"text": t, "conf": c / 100.0,
                      "x": data["left"][i] / scale, "y": data["top"][i] / scale,
                      "w": data["width"][i] / scale, "h": data["height"][i] / scale})
    return words, scale


# --------------------------------------------------------------------- #
# Room-schedule extraction anchored on "SF" tokens
# --------------------------------------------------------------------- #
_AREA_RE = re.compile(r"^[\d,]{2,7}$")
_NUM_RE = re.compile(r"^\d{2,4}$")


def _cluster_1d(values, gap):
    """Group sorted scalars into clusters separated by gaps > `gap`."""
    if not values:
        return []
    values = sorted(values)
    groups = [[values[0]]]
    for v in values[1:]:
        if v - groups[-1][-1] > gap:
            groups.append([])
        groups[-1].append(v)
    return groups


def extract_room_schedule(img):
    """Return room records parsed from the schedule table, plus diagnostics.
    Works for any column count / resolution by anchoring on SF tokens."""
    words, _ = _words(img)
    if not words:
        return {"rooms": [], "note": "no OCR text (Tesseract unavailable?)"}

    row_h = np.median([w["h"] for w in words]) if words else 20.0
    sf = [w for w in words if re.fullmatch(r"SF|SE|SFE", w["text"], re.I)]
    # column groups from the x-spread of SF anchors
    col_groups = _cluster_1d([w["x"] for w in sf], gap=6 * row_h)
    col_centers = [np.mean(g) for g in col_groups] if col_groups else []

    rooms = []
    for anchor in sf:
        row_words = [w for w in words
                     if abs((w["y"] + w["h"] / 2) - (anchor["y"] + anchor["h"] / 2)) < 0.7 * row_h]
        row_words.sort(key=lambda w: w["x"])
        # which column group this anchor belongs to
        gi = min(range(len(col_centers)), key=lambda i: abs(col_centers[i] - anchor["x"])) \
            if col_centers else 0
        lo = col_centers[gi] - 4 * row_h if col_centers else -1e9
        hi = col_centers[gi] + 22 * row_h if col_centers else 1e9
        band = [w for w in row_words if lo <= w["x"] <= hi]
        # area = numeric just left of SF; name/number to the right
        left = [w for w in band if w["x"] < anchor["x"]]
        right = [w for w in band if w["x"] > anchor["x"]]
        area = None
        if left and _AREA_RE.match(left[-1]["text"].replace(",", "")):
            try:
                area = int(left[-1]["text"].replace(",", ""))
            except ValueError:
                area = None
        name_toks, number = [], None
        for w in right:
            if _NUM_RE.match(w["text"]) and len(w["text"]) == 3:
                number = w["text"]
                break
            name_toks.append(w["text"])
        if area is None and not name_toks:
            continue
        conf = min([w["conf"] for w in band], default=0.0)
        rooms.append({"area_sf": area, "name": " ".join(name_toks).strip() or None,
                      "number": number, "confidence": round(conf, 2)})

    # keep plausible rows
    rooms = [r for r in rooms if r["area_sf"] or (r["name"] and r["number"])]
    return {
        "rooms": rooms,
        "column_groups": len(col_groups),
        "total_area_sf": sum(r["area_sf"] for r in rooms if r["area_sf"]),
        "resolution": list(img.shape[::-1]),
    }


# --------------------------------------------------------------------- #
# Occupancy typing of extracted rooms (real, from the schedule)
# --------------------------------------------------------------------- #
ROOM_TYPE_KEYWORDS = {
    "classroom": ("cr", "classroom", "kindergarten", "educ", "computers", "music", "art"),
    "assembly": ("gymnasium", "gym", "multi-purpose", "multipurpose", "media center",
                 "cafeteria", "auditorium"),
    "office": ("office", "principal", "secretary", "reception", "conference",
               "teacher work", "staff", "counselor", "nurse"),
    "service": ("toilet", "jan", "mech", "elec", "stor", "boiler", "kitchen",
                "cooler", "freezer", "vault", "cust", "chase", "vest", "corridor",
                "hall", "entry", "closet"),
}


def classify_rooms(rooms):
    out = {"classroom": 0, "assembly": 0, "office": 0, "service": 0, "unknown": 0}
    for r in rooms:
        name = (r.get("name") or "").lower()
        hit = "unknown"
        for typ, kws in ROOM_TYPE_KEYWORDS.items():
            if any(k in name for k in kws):
                hit = typ
                break
        out[hit] += 1
    return out


# --------------------------------------------------------------------- #
# Missing-information detection -> Request for Information
# --------------------------------------------------------------------- #
# Compliance inputs and where they normally appear on a drawing set.
COMPLIANCE_INPUTS = {
    "room_areas": {"sheet": "floor plan / room schedule",
                   "label": {"en": "Room areas", "fr": "Aires des pièces"}},
    "ceiling_heights": {"sheet": "building sections",
                        "label": {"en": "Ceiling heights (per habitable room)",
                                  "fr": "Hauteurs sous plafond (par pièce habitable)"}},
    "stair_geometry": {"sheet": "stair sections / details",
                       "label": {"en": "Stair rise, run, width, headroom",
                                 "fr": "Contremarche, giron, largeur, échappée d'escalier"}},
    "egress_windows": {"sheet": "window schedule",
                       "label": {"en": "Bedroom egress window openable area & dimensions",
                                 "fr": "Aire et dimensions ouvrantes des fenêtres de secours"}},
    "guards": {"sheet": "sections / details",
               "label": {"en": "Guard heights at raised surfaces",
                         "fr": "Hauteur des garde-corps aux surfaces surélevées"}},
    "fire_protection": {"sheet": "life-safety plan / specifications",
                        "label": {"en": "Smoke/CO alarm and fire-alarm provisions",
                                  "fr": "Avertisseurs de fumée/CO et système d'alarme incendie"}},
}


def analyze_completeness(schedule):
    """Given what a plan sheet yielded, report present vs missing compliance
    inputs and build a Request for Information for the missing ones."""
    present = set()
    if schedule.get("rooms"):
        present.add("room_areas")
    missing = [k for k in COMPLIANCE_INPUTS if k not in present]
    rfi = [{
        "item": k,
        "needed_for_compliance": True,
        "label": COMPLIANCE_INPUTS[k]["label"],
        "usual_location": COMPLIANCE_INPUTS[k]["sheet"],
    } for k in missing]
    return {
        "present": sorted(present),
        "missing": missing,
        "request_for_information": rfi,
        "message": {
            "en": "This sheet provided: %s. To complete the compliance check, "
                  "please provide the following (usually on other sheets): %s."
                  % (", ".join(sorted(present)) or "no compliance inputs",
                     "; ".join(COMPLIANCE_INPUTS[m]["label"]["en"] for m in missing)),
            "fr": "Cette feuille fournit : %s. Pour compléter la vérification, "
                  "veuillez fournir (généralement sur d'autres feuilles) : %s."
                  % (", ".join(sorted(present)) or "aucune donnée",
                     "; ".join(COMPLIANCE_INPUTS[m]["label"]["fr"] for m in missing)),
        },
    }


# NBC 2020 Div. B, Table 3.1.17.1 occupant-load area factors (m2/person).
# A real compliance computation the room schedule CAN feed directly.
OCCUPANT_LOAD_FACTORS_M2 = {
    "classroom": 1.85, "assembly": 0.75, "office": 9.30,
}
SF_TO_M2 = 0.092903


def compute_occupant_load(rooms):
    """Derive occupant load per NBC 3.1.17 area factors from real room areas.
    Returns per-type and total occupant load - a real, defensible number
    computed entirely from data extracted off the plan."""
    by_type = {}
    for r in rooms:
        if not r.get("area_sf"):
            continue
        name = (r.get("name") or "").lower()
        typ = None
        for t, kws in ROOM_TYPE_KEYWORDS.items():
            if t in OCCUPANT_LOAD_FACTORS_M2 and any(k in name for k in kws):
                typ = t
                break
        if not typ:
            continue
        area_m2 = r["area_sf"] * SF_TO_M2
        by_type.setdefault(typ, {"rooms": 0, "area_m2": 0.0, "occupants": 0})
        by_type[typ]["rooms"] += 1
        by_type[typ]["area_m2"] += area_m2
        by_type[typ]["occupants"] += area_m2 / OCCUPANT_LOAD_FACTORS_M2[typ]
    for v in by_type.values():
        v["area_m2"] = round(v["area_m2"], 1)
        v["occupants"] = int(round(v["occupants"]))
    total = sum(v["occupants"] for v in by_type.values())
    return {"by_type": by_type, "total_occupant_load": total}


def analyze_plan(path):
    """Full dynamic analysis of a real plan sheet: schedule extraction, room
    typing, and a missing-information request."""
    if not HAVE_CV2:
        raise RuntimeError("opencv-python + numpy required")
    img = load_image(path)
    schedule = extract_room_schedule(img)
    schedule["room_types"] = classify_rooms(schedule["rooms"])
    completeness = analyze_completeness(schedule)
    occupant_load = compute_occupant_load(schedule["rooms"])
    return {"source": os.path.basename(path), "schedule": schedule,
            "occupant_load": occupant_load, "completeness": completeness}
