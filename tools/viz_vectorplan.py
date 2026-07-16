"""Annotate a real vector PDF plan with what the extractor found, using an
explicit render matrix so markers land exactly on the source coordinates.

green = room labels, red = stairs (with nearest dims), blue dots = dimensions.
"""

import os
import sys

import cv2
import fitz
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from permitcheck.extract import vectorplan  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "data", "viz")
GREEN, BLUE, RED, WHITE = (40, 150, 40), (200, 110, 0), (36, 36, 210), (255, 255, 255)


def _label(img, pt, text, color, font=0.9, thick=2):
    """Draw text with a white background box so it is readable, anchored so its
    left edge sits just right of the exact point (which gets a crosshair)."""
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font, thick)
    x, y = pt[0] + 14, pt[1] + th // 2
    cv2.rectangle(img, (x - 3, y - th - 4), (x + tw + 3, y + 6), WHITE, -1)
    cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, font, color, thick, cv2.LINE_AA)


def _cross(img, pt, color, r=9, thick=2):
    cv2.line(img, (pt[0] - r, pt[1]), (pt[0] + r, pt[1]), color, thick, cv2.LINE_AA)
    cv2.line(img, (pt[0], pt[1] - r), (pt[0], pt[1] + r), color, thick, cv2.LINE_AA)


def annotate(pdf_path, zoom=2.5):
    doc = fitz.open(pdf_path)
    page = doc[0]
    # respect any page /Rotate: get_text coords are unrotated, get_pixmap is
    # rendered rotated - compose the page rotation matrix with the zoom.
    mat = page.rotation_matrix * fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR) if pix.n >= 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

    def to_px(x, y):
        p = fitz.Point(x, y) * mat                # same matrix -> exact alignment
        return (int(round(p.x)), int(round(p.y)))

    data = vectorplan.extract(pdf_path)

    # dimensions: small blue dot exactly on the token (no clutter of 300 labels)
    for d in data["dimensions"]:
        cv2.circle(img, to_px(d["x"], d["y"]), 4, BLUE, -1)
    # rooms: green crosshair + label
    for sp in data["spaces"]:
        p = to_px(sp["x"], sp["y"])
        _cross(img, p, GREEN)
        _label(img, p, sp["label"], GREEN, font=0.8)
    # stairs: red crosshair + nearest measured dims
    for st in data["stairs"]:
        p = to_px(st["x"], st["y"])
        _cross(img, p, RED, r=12, thick=3)
        dims = "/".join("%dmm" % d for d in st.get("nearby_dims_mm", [])[:2])
        _label(img, p, "STAIR " + dims, RED, font=0.85)

    s = data["summary"]
    banner = "REAL VECTOR PLAN  -  %d rooms | %d stairs | %d dimensions (%d-%dmm)" % (
        s["spaces"], s["stairs"], s["dimensions"], s["dim_range_mm"][0], s["dim_range_mm"][1])
    cv2.rectangle(img, (0, 0), (img.shape[1], 64), (255, 255, 255), -1)
    cv2.putText(img, banner, (24, 44), cv2.FONT_HERSHEY_SIMPLEX, 1.1, RED, 3, cv2.LINE_AA)

    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, os.path.splitext(os.path.basename(pdf_path))[0] + "_vector_annotated.png")
    cv2.imwrite(out, img)
    return out, s


if __name__ == "__main__":
    for p in sys.argv[1:]:
        path, summ = annotate(p)
        print("wrote", path, summ)
