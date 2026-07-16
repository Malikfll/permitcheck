"""Extraction from real vector (CAD-exported) architectural PDFs.

Unlike scans, vector PDFs carry text and geometry with exact coordinates, so
no OCR is needed and confidence is high. This adapter, tuned on real
institutional floor plans, extracts:

  * room / space labels with positions (STAIR, OFFICE, CORRIDOR, ...)
  * dimension strings in imperial feet-inches ("22'-6\"", "3'-8\"") converted
    to millimetres - the real measured data on the sheet
  * stair references (STAIR labels + the dimensions near them)

Requires PyMuPDF (fitz). Everything carries a {value, confidence, source}
envelope; vector text is high-confidence (0.97) because coordinates are exact.
"""

import re

CONF_VECTOR = 0.97

# imperial dimension: 12'-6"  or  3'-8 1/2"
_FT_IN = re.compile(r"(\d{1,3})'\s*-\s*(\d{1,2})(?:\s+(\d)/(\d))?\s*\"?")
# room/space vocabulary seen on real plans
SPACE_WORDS = {
    "stair", "stairs", "corridor", "office", "toilet", "vestibule", "vest",
    "lobby", "kitchen", "mech", "mechanical", "elect", "electrical", "stor",
    "storage", "conf", "conference", "seminar", "reading", "typing", "closet",
    "clos", "men", "women", "lav", "chase", "circulation", "admin", "canopy",
}
STAIR_WORDS = {"stair", "stairs"}
# stairs are often shown with riser-count notation rather than the word
# "STAIR": "UP 15R", "DN 15R", "15 RISERS". Detect those too.
_RISER_RE = re.compile(r"^(\d{1,2})\s*R$", re.I)          # 15R
_UPDN_RE = re.compile(r"^(UP|DN|DOWN)$", re.I)


def feet_inches_to_mm(text):
    """Parse an imperial dimension token to millimetres; None if not a dim."""
    m = _FT_IN.search(text)
    if not m:
        return None
    feet = int(m.group(1))
    inches = int(m.group(2))
    if m.group(3):
        inches += int(m.group(3)) / int(m.group(4))
    return round(feet * 304.8 + inches * 25.4, 1)


def _words(path):
    import fitz
    doc = fitz.open(path)
    page = doc[0]
    out = []
    for x0, y0, x1, y1, word, *_ in page.get_text("words"):
        out.append({"text": word, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                    "cx": (x0 + x1) / 2, "cy": (y0 + y1) / 2})
    return out, page.rect.width, page.rect.height


def extract(path):
    """Extract labelled spaces and imperial dimensions from a vector PDF plan."""
    words, w, h = _words(path)
    src = path.replace("\\", "/").rsplit("/", 1)[-1]

    spaces, stairs, dimensions = [], [], []
    for wd in words:
        low = wd["text"].strip(".,").lower()
        if low in SPACE_WORDS:
            entry = {"label": wd["text"].strip(".,").upper(),
                     "x": wd["cx"], "y": wd["cy"],
                     "confidence": CONF_VECTOR,
                     "source": "%s (vector text @%.0f,%.0f)" % (src, wd["cx"], wd["cy"])}
            (stairs if low in STAIR_WORDS else spaces).append(entry)

    # riser-count notation ("15R", "UP 15R", "DN 15R") - a stair flight marker
    for wd in words:
        m = _RISER_RE.match(wd["text"].strip())
        if not m:
            continue
        risers = int(m.group(1))
        if not (2 <= risers <= 30):        # plausible flight
            continue
        # look for an adjacent UP/DN token to label direction
        direction = ""
        for other in words:
            if _UPDN_RE.match(other["text"].strip()) and \
                    abs(other["cy"] - wd["cy"]) < 30 and abs(other["cx"] - wd["cx"]) < 120:
                direction = other["text"].strip().upper() + " "
                break
        stairs.append({
            "label": "STAIR (%s%dR)" % (direction, risers),
            "risers": risers, "x": wd["cx"], "y": wd["cy"],
            "confidence": CONF_VECTOR,
            "source": "%s (riser-count notation '%s%dR')" % (src, direction, risers),
        })

    for wd in words:
        mm = feet_inches_to_mm(wd["text"])
        if mm is not None and 100 <= mm <= 120000:  # plausible building dim
            dimensions.append({"text": wd["text"], "mm": mm,
                               "x": wd["cx"], "y": wd["cy"]})

    # associate the nearest dimension(s) to each stair (real "which dimension
    # belongs to which stair" step, exact via coordinates)
    for st in stairs:
        near = sorted(dimensions,
                      key=lambda d: (d["x"] - st["x"]) ** 2 + (d["y"] - st["y"]) ** 2)[:3]
        st["nearby_dims_mm"] = [d["mm"] for d in near]

    return {
        "source": src, "page_pts": [round(w), round(h)],
        "spaces": spaces, "stairs": stairs,
        "dimensions": dimensions,
        "summary": {"spaces": len(spaces), "stairs": len(stairs),
                    "dimensions": len(dimensions),
                    "dim_range_mm": [min((d["mm"] for d in dimensions), default=0),
                                     max((d["mm"] for d in dimensions), default=0)]},
    }
