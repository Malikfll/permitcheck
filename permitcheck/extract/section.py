"""Extraction from building SECTION sheets (vector PDF).

Section sheets rarely tag a value "CEILING HEIGHT = 2440". Instead they carry:
  * explicitly labelled stair geometry ("7 1/4\" RISE", "10\" RUN") - extracted
    directly and with high confidence because the label removes ambiguity;
  * vertical clearance dimensions in feet-inches ("9'-0\"", "8'-1\"", "6'-8\"
    UNDER BEAM") - these are ceiling-height CANDIDATES. We do not silently
    assert which one is "the" ceiling height (that needs section geometry
    comprehension); we surface the plausible candidates, ranked, so the
    reviewer confirms - CV extraction feeding the human-in-the-loop, and an
    UNCERTAIN verdict until confirmed.

Includes a robust imperial parser handling  9'-0"  |  7 1/4"  |  10"  |
9'-0 3/4"  |  9'-0¾"  and the span-split forms real PDFs produce.
"""

import re
import unicodedata

CONF_LABELLED = 0.92     # value carries an explicit RISE/RUN label
CONF_CANDIDATE = 0.55    # height candidate, needs human confirmation

CEILING_MIN_MM, CEILING_MAX_MM = 2000.0, 3500.0
RISE_MIN_MM, RISE_MAX_MM = 100.0, 250.0
RUN_MIN_MM, RUN_MAX_MM = 200.0, 400.0

# feet-inches with optional fraction, tolerant of the split forms PDFs emit:
#   9'-0"   9'-0 3/4"   9'-03/4"   7 1/4" (inches only)   10"
_FT = r"(\d{1,3})\s*['′]\s*-?\s*(\d{1,2})?"          # 9'-0 or 9'
_FRAC = r"(?:\s*(\d)\s*/\s*(\d))?"                          # optional 3/4
_IN_ONLY = r"(\d{1,2})\s*" + _FRAC + r"\s*[\"″]"       # 7 1/4" or 10"
_FT_IN = re.compile(_FT + _FRAC + r"\s*[\"″]?")
_IN = re.compile(_IN_ONLY)


def _norm(text):
    # turn unicode fractions (¾) into ascii ( 3/4)
    out = []
    for ch in text:
        if unicodedata.category(ch) == "No":  # numeric fraction char
            try:
                frac = unicodedata.numeric(ch)
                out.append(" %d/%d" % (round(frac * 4), 4) if frac == 0.75 else
                           " %d/%d" % (1, round(1 / frac)) if frac else "")
                continue
            except (ValueError, ZeroDivisionError):
                pass
        out.append(ch)
    return "".join(out)


def imperial_to_mm(text):
    """Parse a feet-inches (optionally fractional) dimension to mm; None if not
    a length. Tries feet-inches first, then inches-only."""
    t = _norm(text)
    m = _FT_IN.search(t)
    if m and m.group(1) and (m.group(2) is not None or "'" in t or "′" in t):
        feet = int(m.group(1))
        inches = int(m.group(2)) if m.group(2) else 0
        if m.group(3) and m.group(4):
            inches += int(m.group(3)) / int(m.group(4))
        return round(feet * 304.8 + inches * 25.4, 1)
    m = _IN.search(t)
    if m:
        inches = int(m.group(1))
        if m.group(2) and m.group(3):
            inches += int(m.group(2)) / int(m.group(3))
        return round(inches * 25.4, 1)
    return None


def _spans(pdf_path):
    import fitz
    doc = fitz.open(pdf_path)
    out = []
    for page in doc:
        d = page.get_text("dict")
        for b in d["blocks"]:
            for l in b.get("lines", []):
                vert = abs(l.get("dir", (1, 0))[1]) > abs(l.get("dir", (1, 0))[0])
                for s in l["spans"]:
                    bb = s["bbox"]
                    out.append({"text": s["text"].strip(),
                                "x": bb[0], "y": bb[1], "x1": bb[2], "y1": bb[3],
                                "bbox": [bb[0], bb[1], bb[2], bb[3]],
                                "vertical": vert})
    return out


def extract(pdf_path):
    """Return stair rise/run (labelled) and ceiling-height candidates."""
    import os
    spans = _spans(pdf_path)
    src = os.path.basename(pdf_path)

    rise = run = None
    candidates = []
    for s in spans:
        txt = s["text"]
        up = txt.upper()
        mm = imperial_to_mm(txt)
        if mm is None:
            continue
        if "RISE" in up and RISE_MIN_MM <= mm <= RISE_MAX_MM and rise is None:
            rise = {"value": round(mm), "confidence": CONF_LABELLED,
                    "text": txt, "x": round(s["x"]), "y": round(s["y"]),
                    "bbox": s["bbox"], "vertical": s["vertical"],
                    "source": "%s (labelled '%s')" % (src, txt)}
        elif "RUN" in up and RUN_MIN_MM <= mm <= RUN_MAX_MM and run is None:
            run = {"value": round(mm), "confidence": CONF_LABELLED,
                   "text": txt, "x": round(s["x"]), "y": round(s["y"]),
                   "bbox": s["bbox"], "vertical": s["vertical"],
                   "source": "%s (labelled '%s')" % (src, txt)}
        elif CEILING_MIN_MM <= mm <= CEILING_MAX_MM:
            note = re.sub(r"[\d'\"′″/ .-]+", " ", txt).strip()
            candidates.append({
                "value_mm": round(mm), "value_m": round(mm / 1000.0, 3),
                "text": txt, "note": note or None,
                "x": round(s["x"]), "y": round(s["y"]),
                "bbox": s["bbox"], "vertical": s["vertical"],
                "confidence": CONF_CANDIDATE,
                "source": "%s (section dimension '%s')" % (src, txt),
            })

    # rank ceiling candidates: prefer clean "N'-M"" storey heights without a
    # qualifying note like "UNDER BEAM" (which is a local clearance, not the room)
    candidates.sort(key=lambda c: (c["note"] is not None, -c["value_mm"]))
    return {"source": src, "stair": {"rise_mm": rise, "run_mm": run},
            "ceiling_height_candidates": candidates}
