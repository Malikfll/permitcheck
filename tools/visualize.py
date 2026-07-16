"""Visual proof: overlay what the pipeline detected and decided onto the
drawing image, so a reviewer can SEE it working (not just read test output).

  python tools/visualize.py            -> writes annotated PNGs to data/viz/
"""

import json
import os

import cv2
import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "data", "viz")

GREEN = (40, 160, 40)
RED = (40, 40, 210)
ORANGE = (0, 140, 235)
BLUE = (200, 120, 0)
GREY = (120, 120, 120)
VCOLOR = {"MEETS": GREEN, "DOES_NOT_MEET": RED, "UNCERTAIN": ORANGE,
          "INFO_NOT_AVAILABLE": BLUE}


def _bgr(img):
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR) if img.ndim == 2 else img


def annotate_scan(scan_id):
    """Overlay detected rooms, the values read, and per-rule verdicts on a
    residential floor-plan scan."""
    from permitcheck.extract import semantic, raster
    from permitcheck.engine import RulesEngine

    png = os.path.join(BASE, "data", "scans", scan_id + ".png")
    truth = json.load(open(os.path.join(BASE, "data", "scans", scan_id + ".truth.json"),
                           encoding="utf-8"))
    gray = cv2.imread(png, cv2.IMREAD_GRAYSCALE)
    vis = _bgr(gray.copy())

    rooms = semantic.detect_rooms(raster.binarize(gray))
    app = semantic.extract_from_image(png, truth["px_per_mm"],
                                      title_block=truth["title_block"])

    # draw detected room boxes + what was read inside
    for room, space in zip(rooms, app["spaces"]):
        x, y, w, h = room["x"], room["y"], room["w"], room["h"]
        cv2.rectangle(vis, (x, y), (x + w, y + h), GREEN, 5)
        label = space.get("name", "?")
        ch = space.get("ceiling_height_m")
        chtxt = ""
        if isinstance(ch, dict):
            chtxt = "  CH=%.2fm (%.0f%%)" % (ch["value"], ch["confidence"] * 100)
        cv2.putText(vis, "%s%s" % (label, chtxt), (x + 20, y + 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, GREEN, 3, cv2.LINE_AA)
        if "egress_window" in space:
            ew = space["egress_window"]
            cv2.putText(vis, "EGRESS %.2fm2/%dmm" % (ew["open_area_m2"]["value"],
                        ew["min_dimension_mm"]["value"]), (x + 20, y + 110),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, BLUE, 2, cv2.LINE_AA)

    # run compliance and draw a verdict legend panel
    full = {"application": {"id": scan_id, "documents": [{"name": scan_id}]},
            "building": app.get("building", truth["title_block"]),
            "spaces": app["spaces"], "stairs": app["stairs"],
            "fire_safety": truth.get("fire_safety", {})}
    run = RulesEngine.from_file(os.path.join(BASE, "rules", "nbc_rules.json")).run(full)

    panel_x = 40
    y = 60
    cv2.putText(vis, "COMPLIANCE VERDICTS (%s)" % run["overall"], (panel_x, y),
                cv2.FONT_HERSHEY_SIMPLEX, 1.3, VCOLOR.get(run["overall"], GREY), 4, cv2.LINE_AA)
    y += 60
    for r in run["results"]:
        if not r["applicable"]:
            continue
        color = VCOLOR.get(r["verdict"], GREY)
        cv2.putText(vis, "%-14s %-24s %s" % (r["rule_id"], r["title"]["en"][:24], r["verdict"]),
                    (panel_x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2, cv2.LINE_AA)
        y += 42

    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, scan_id + "_annotated.png")
    cv2.imwrite(out, vis)
    return out, run["overall"]


def annotate_real_schedule(pdf_path):
    """Overlay the extracted room records onto the real plan's schedule."""
    from permitcheck.extract import planscan
    img = planscan.load_image(pdf_path)
    vis = _bgr(img.copy())
    words, _ = planscan._words(img)
    # mark every 'SF' anchor found + count
    sf = [w for w in words if w["text"].upper() in ("SF", "SE", "SFE")]
    for w in sf:
        cv2.rectangle(vis, (int(w["x"]), int(w["y"])),
                      (int(w["x"] + w["w"]), int(w["y"] + w["h"])), GREEN, 3)
    sched = planscan.extract_room_schedule(img)
    ol = planscan.compute_occupant_load(sched["rooms"])
    banner = "REAL PLAN: %d rooms extracted, %d SF, occupant load %d" % (
        len(sched["rooms"]), sched["total_area_sf"], ol["total_occupant_load"])
    cv2.rectangle(vis, (0, 0), (vis.shape[1], 90), (255, 255, 255), -1)
    cv2.putText(vis, banner, (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.6, RED, 4, cv2.LINE_AA)
    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, "real_school_plan_annotated.png")
    cv2.imwrite(out, vis)
    return out, len(sched["rooms"])


if __name__ == "__main__":
    import sys
    sys.path.insert(0, BASE)
    for sid in ("SCAN-2026-0301", "SCAN-2026-0302"):
        path, overall = annotate_scan(sid)
        print("wrote %s  (overall %s)" % (path, overall))
    real = os.path.join(os.path.expanduser("~"), "Downloads", "1344536052.pdf")
    if os.path.exists(real):
        path, n = annotate_real_schedule(real)
        print("wrote %s  (%d real rooms)" % (path, n))
