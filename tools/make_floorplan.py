"""Render annotated architectural floor plans as raster 'scans', each paired
with ground-truth semantics AND ground-truth compliance verdicts.

Purpose: validate the full scanned-drawing -> compliance-verdict pipeline.
The renderer knows the true rooms/heights/stairs, so it can emit both the
degraded image the pipeline sees and the answer key it is graded against.

A plan spec is a dict:
  {
    "id": "...", "px_per_mm": 0.5, "title_block": {...engine building fields},
    "rooms": [{"name","x","y","w","h","ceiling_mm","bedroom","egress":(area,mindim)}],
    "stairs": [{"x","y","treads","run_mm","rise_mm","width_mm"}],
  }
Coordinates are in millimetres (drawing units); px_per_mm sets the scan scale.

Run:  python tools/make_floorplan.py   -> writes data/scans/<id>.png + .truth.json
"""

import json
import math
import os

import cv2
import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "data", "scans")

WALL_PX = 6
FONT = cv2.FONT_HERSHEY_SIMPLEX


def _render(spec, rotate_deg=0.6, noise_sigma=4.0, blur=1):
    ppm = spec["px_per_mm"]
    margin_mm = 400
    W = int((max(r["x"] + r["w"] for r in spec["rooms"]) + margin_mm) * ppm)
    H = int((max(r["y"] + r["h"] for r in spec["rooms"]) + margin_mm + 300) * ppm)
    img = np.full((H, W), 255, np.uint8)

    def to_px(xmm, ymm):
        return int(round((xmm + 200) * ppm)), int(round((ymm + 200) * ppm))

    # rooms: rectangles (walls) + labels
    for r in spec["rooms"]:
        p1 = to_px(r["x"], r["y"])
        p2 = to_px(r["x"] + r["w"], r["y"] + r["h"])
        cv2.rectangle(img, p1, p2, 0, WALL_PX)
        # place labels centred, well clear of the walls, generously spaced
        lx, ly = to_px(r["x"] + 350, r["y"] + 800)
        scale = 0.9 * ppm / 0.5
        th = 2
        cv2.putText(img, r["name"], (lx, ly), FONT, scale, 0, th, cv2.LINE_AA)
        ch_txt = "CH %d" % r["ceiling_mm"]
        cv2.putText(img, ch_txt, (lx, ly + int(750 * ppm)), FONT, scale, 0, th, cv2.LINE_AA)
        if r.get("egress"):
            area, mindim = r["egress"]
            eg = "EGR %.2f/%d" % (area, mindim)
            cv2.putText(img, eg, (lx, ly + int(1500 * ppm)), FONT, scale, 0, th, cv2.LINE_AA)

    # stairs: parallel tread lines + RISE/RUN callout
    for s in spec["stairs"]:
        for i in range(s["treads"]):
            x = s["x"] + i * s["run_mm"]
            p1 = to_px(x, s["y"])
            p2 = to_px(x, s["y"] + s["width_mm"])
            cv2.line(img, p1, p2, 0, 3)
        # stringer lines
        cv2.line(img, to_px(s["x"], s["y"]),
                 to_px(s["x"] + (s["treads"] - 1) * s["run_mm"], s["y"]), 0, 3)
        cv2.line(img, to_px(s["x"], s["y"] + s["width_mm"]),
                 to_px(s["x"] + (s["treads"] - 1) * s["run_mm"], s["y"] + s["width_mm"]), 0, 3)
        lx, ly = to_px(s["x"], s["y"] + s["width_mm"] + 350)
        cv2.putText(img, "STAIR RISE %d RUN %d" % (s["rise_mm"], s["run_mm"]),
                    (lx, ly), FONT, 0.7 * ppm / 0.5, 0, 2, cv2.LINE_AA)
        cv2.putText(img, "W %d HR %d" % (s["width_mm"], s.get("headroom_mm", 2000)),
                    (lx, ly + int(500 * ppm)), FONT, 0.7 * ppm / 0.5, 0, 2, cv2.LINE_AA)

    # degrade into a 'scan'
    if rotate_deg:
        m = cv2.getRotationMatrix2D((W / 2, H / 2), rotate_deg, 1.0)
        img = cv2.warpAffine(img, m, (W, H), borderValue=255)
    if noise_sigma:
        rng = np.random.default_rng(11)
        img = np.clip(img.astype(np.float32) + rng.normal(0, noise_sigma, img.shape),
                      0, 255).astype(np.uint8)
    if blur:
        img = cv2.GaussianBlur(img, (blur | 1, blur | 1), 0)
    return img


def _ground_truth_app(spec):
    """The engine-ready application the plan encodes (the answer key)."""
    spaces = []
    for i, r in enumerate(spec["rooms"]):
        space = {"id": "S-%02d" % (i + 1), "name": r["name"],
                 "is_bedroom": bool(r.get("bedroom")),
                 "habitable": bool(r.get("habitable", not r.get("service"))),
                 "ceiling_height_m": round(r["ceiling_mm"] / 1000.0, 3)}
        if r.get("egress"):
            space["egress_window"] = {"open_area_m2": r["egress"][0],
                                      "min_dimension_mm": r["egress"][1]}
        spaces.append(space)
    stairs = [{"id": "ST-%d" % (i + 1), "name": "Stair %d" % (i + 1), "private": True,
               "rise_mm": s["rise_mm"], "run_mm": s["run_mm"], "width_mm": s["width_mm"],
               "headroom_mm": s.get("headroom_mm", 2000)}
              for i, s in enumerate(spec["stairs"])]
    app = {"application": {"id": spec["id"], "municipality": spec.get("municipality", ""),
                           "legal_description": spec.get("legal_description", "on file"),
                           "documents": [{"name": spec["id"] + ".png", "type": "scan"}]},
           "building": dict(spec["title_block"]), "spaces": spaces, "stairs": stairs}
    app.setdefault("fire_safety", spec.get("fire_safety", {}))
    return app


SPECS = [
    {
        "id": "SCAN-2026-0301", "px_per_mm": 0.5,
        "municipality": "Kingston, ON",
        "title_block": {"name": "Bungalow (scanned plan)", "major_occupancy": "C",
                        "storeys": 1, "building_area_m2": 150.0, "dwelling_units": 1,
                        "has_secondary_suite": False, "sprinklered": False,
                        "fuel_burning_appliance": True, "attached_garage": False},
        "fire_safety": {"smoke_alarm_each_storey": True, "smoke_alarm_each_bedroom": True,
                        "co_alarm_provided": True},
        "rooms": [
            {"name": "LIVING", "x": 0, "y": 0, "w": 5000, "h": 4000, "ceiling_mm": 2440,
             "habitable": True},
            {"name": "KITCHEN", "x": 5000, "y": 0, "w": 3500, "h": 4000, "ceiling_mm": 2440,
             "habitable": True},
            {"name": "BEDROOM 1", "x": 0, "y": 4000, "w": 4000, "h": 3500, "ceiling_mm": 2440,
             "bedroom": True, "habitable": True, "egress": (0.46, 550)},
            {"name": "BEDROOM 2", "x": 4000, "y": 4000, "w": 3500, "h": 3500, "ceiling_mm": 2440,
             "bedroom": True, "habitable": True, "egress": (0.42, 520)},
            {"name": "BATH", "x": 7500, "y": 4000, "w": 2000, "h": 3500, "ceiling_mm": 2350,
             "service": True},
        ],
        "stairs": [{"x": 9000, "y": 500, "treads": 6, "run_mm": 260, "rise_mm": 185,
                    "width_mm": 900, "headroom_mm": 2050}],
    },
    {
        "id": "SCAN-2026-0302", "px_per_mm": 0.5,
        "municipality": "Sudbury, ON",
        "title_block": {"name": "Duplex (scanned plan, deficiencies)", "major_occupancy": "C",
                        "storeys": 2, "building_area_m2": 240.0, "dwelling_units": 2,
                        "has_secondary_suite": False, "sprinklered": False,
                        "fuel_burning_appliance": True, "attached_garage": False},
        "fire_safety": {"smoke_alarm_each_storey": True, "smoke_alarm_each_bedroom": True,
                        "co_alarm_provided": True, "smoke_alarms_interconnected": False},
        "rooms": [
            {"name": "LIVING", "x": 0, "y": 0, "w": 4800, "h": 4000, "ceiling_mm": 2420,
             "habitable": True},
            {"name": "BEDROOM 1", "x": 4800, "y": 0, "w": 3600, "h": 4000, "ceiling_mm": 2240,
             "bedroom": True, "habitable": True, "egress": (0.30, 350)},
            {"name": "BEDROOM 2", "x": 0, "y": 4000, "w": 3600, "h": 3500, "ceiling_mm": 2440,
             "bedroom": True, "habitable": True, "egress": (0.44, 520)},
        ],
        "stairs": [{"x": 5000, "y": 4200, "treads": 6, "run_mm": 250, "rise_mm": 210,
                    "width_mm": 880, "headroom_mm": 1980}],
    },
]


def build():
    os.makedirs(OUT, exist_ok=True)
    for spec in SPECS:
        img = _render(spec)
        cv2.imwrite(os.path.join(OUT, spec["id"] + ".png"), img)
        truth = _ground_truth_app(spec)
        with open(os.path.join(OUT, spec["id"] + ".truth.json"), "w", encoding="utf-8") as fh:
            json.dump({"px_per_mm": spec["px_per_mm"], "title_block": spec["title_block"],
                       "fire_safety": spec.get("fire_safety", {}),
                       "application": truth}, fh, indent=2)
    print("Wrote %d scans + ground truth to %s" % (len(SPECS), OUT))


if __name__ == "__main__":
    build()
