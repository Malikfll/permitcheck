"""OCR adapter for scanned permit forms and drawing text (Tesseract 5).

Reads text off raster images with *per-token confidence* from Tesseract's
image_to_data. Form lines are mapped to application fields using the same
label dictionary as the PDF adapter; each value's envelope carries the OCR
confidence, so anything Tesseract is unsure about surfaces downstream as an
UNCERTAIN verdict instead of a silent wrong answer.
"""

import os

from .pdfx import PDF_FIELDS, _coerce

_DEFAULT_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
]


def _configure():
    try:
        import pytesseract
    except ImportError:
        return None
    from shutil import which
    if not which("tesseract"):
        for path in _DEFAULT_PATHS:
            if os.path.exists(path):
                pytesseract.pytesseract.tesseract_cmd = path
                break
    return pytesseract


HAVE_TESSERACT = _configure() is not None


def read_lines(image_or_path, lang="eng"):
    """OCR an image; returns [{"text", "confidence"}] per line, confidence
    being the minimum token confidence on that line (0..1) - the weakest
    token bounds trust in the whole line."""
    pytesseract = _configure()
    if pytesseract is None:
        raise RuntimeError("pytesseract + Tesseract are required: pip install pytesseract "
                           "and winget install UB-Mannheim.TesseractOCR")
    import cv2
    img = image_or_path
    if isinstance(image_or_path, str):
        img = cv2.imread(image_or_path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise ValueError("cannot read image: %s" % image_or_path)
    data = pytesseract.image_to_data(img, lang=lang,
                                     output_type=pytesseract.Output.DICT)
    lines = {}
    for i in range(len(data["text"])):
        token = data["text"][i].strip()
        conf = float(data["conf"][i])
        if not token or conf < 0:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        entry = lines.setdefault(key, {"tokens": [], "confs": []})
        entry["tokens"].append(token)
        entry["confs"].append(conf / 100.0)
    return [{"text": " ".join(e["tokens"]),
             "confidence": round(min(e["confs"]), 3)}
            for _, e in sorted(lines.items())]


def extract(path, lang="eng"):
    """Extract permit-form fields from a scanned image, with OCR confidence
    per value. Same field dictionary as the PDF adapter."""
    fname = path.replace("\\", "/").rsplit("/", 1)[-1]
    result = {}
    for line in read_lines(path, lang=lang):
        if ":" not in line["text"]:
            continue
        label, raw = line["text"].split(":", 1)
        target = PDF_FIELDS.get(label.strip().upper())
        if not target or not raw.strip():
            continue
        node = result
        parts = target.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        value = _coerce(raw)
        if parts[0] == "application":
            node[parts[-1]] = value
        else:
            node[parts[-1]] = {
                "value": value,
                "confidence": line["confidence"],
                "source": "%s (OCR line \"%s\", Tesseract)" % (fname, line["text"]),
            }
    return result
