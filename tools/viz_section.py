"""Annotate a section sheet with what the extractor found, using the same
render matrix so markers land exactly on the source coordinates.
red = labelled stair RISE/RUN, blue = ceiling-height candidates (ranked).
"""

import os
import sys

import cv2
import fitz
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from permitcheck.extract import section  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "data", "viz")
RED, BLUE, GREEN, WHITE = (36, 36, 210), (200, 110, 0), (40, 150, 40), (255, 255, 255)


def _box_and_label(img, mat, bbox, text, color, font=0.65, thick=2, dy=-40):
    """Draw the transformed bbox around the exact source text, plus a labelled
    tag connected by a leader line so placement is unambiguous."""
    r = (fitz.Rect(bbox) * mat).irect
    # pad tiny (rotated) boxes so they are visible
    x0, y0, x1, y1 = r.x0 - 4, r.y0 - 4, r.x1 + 4, r.y1 + 4
    if x1 - x0 < 14:
        x0 -= 7
        x1 += 7
    cv2.rectangle(img, (x0, y0), (x1, y1), color, 3)
    anchor = ((x0 + x1) // 2, y0)
    lx, ly = anchor[0] + 16, anchor[1] + dy
    cv2.line(img, anchor, (lx, ly), color, 2, cv2.LINE_AA)
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font, thick)
    cv2.rectangle(img, (lx - 3, ly - th - 5), (lx + tw + 4, ly + 5), WHITE, -1)
    cv2.rectangle(img, (lx - 3, ly - th - 5), (lx + tw + 4, ly + 5), color, 1)
    cv2.putText(img, text, (lx, ly), cv2.FONT_HERSHEY_SIMPLEX, font, color, thick, cv2.LINE_AA)


def annotate(pdf_path, zoom=3.0):
    doc = fitz.open(pdf_path)
    page = doc[0]
    # get_text() returns coordinates in UNROTATED page space; get_pixmap()
    # renders the page with its /Rotate applied. Compose the page rotation
    # matrix with the zoom so text coordinates map onto the rendered pixels.
    mat = page.rotation_matrix * fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR) if pix.n >= 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

    data = section.extract(pdf_path)
    for key, item in data["stair"].items():
        if not item:
            continue
        _box_and_label(img, mat, item["bbox"],
                       "%s = %d mm  (from '%s')" % (key.replace("_mm", "").upper(),
                                                    item["value"], item["text"]),
                       RED, font=0.7, thick=2, dy=-46)
    for i, c in enumerate(data["ceiling_height_candidates"][:6]):
        tag = "CEIL? %.2fm" % c["value_m"] + ("" if not c["note"] else " (%s)" % c["note"])
        _box_and_label(img, mat, c["bbox"], tag, BLUE, font=0.6, thick=2,
                       dy=-30 - 26 * (i % 3))

    banner = "SECTION EXTRACTION  -  boxes wrap the EXACT source text; red = stair rise/run, blue = ceiling candidates"
    cv2.rectangle(img, (0, 0), (img.shape[1], 60), WHITE, -1)
    cv2.putText(img, banner, (20, 42), cv2.FONT_HERSHEY_SIMPLEX, 1.0, RED, 2, cv2.LINE_AA)

    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, os.path.splitext(os.path.basename(pdf_path))[0] + "_section_annotated.png")
    cv2.imwrite(out, img)
    return out, data["stair"]


if __name__ == "__main__":
    for p in sys.argv[1:]:
        path, stair = annotate(p)
        print("wrote", path)
