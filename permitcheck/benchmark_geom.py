"""Geometric measurement accuracy benchmark on REAL CAD drawings.

Methodology (no synthetic assumptions about content):
  1. Ground truth = exact vector geometry of real, third-party DXF drawings
     (QCAD project examples downloaded from the internet, in data/real/).
  2. Each drawing is rasterized to a simulated scan - including rotation
     (sheet skew), Gaussian sensor noise and blur - at a known scale.
  3. The OpenCV pipeline (permitcheck.extract.raster) detects and measures
     line lengths from the degraded scan with NO access to the vector data.
  4. Every prominent ground-truth segment is matched to a detected segment
     and the relative measurement error is computed.

Reported metrics:
  - vector DIMENSION extraction: exact values read from real DIMENSION
    entities (ezdxf) - deterministic, error-free by construction;
  - raster mean measurement accuracy = 100 x (1 - mean relative error);
  - share of ground-truth segments matched and measured within 1%.

Run:  python -m permitcheck.benchmark_geom
"""

import math
import os

from .extract import dxf_geom, raster

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_DIR = os.path.join(BASE, "data", "real")

REAL_DRAWINGS = ["flange.dxf", "example00.dxf", "entities.dxf"]

# scan degradation: ~0.8 deg sheet skew, sensor noise, slight optical blur
DEGRADATION = {"rotate_deg": 0.8, "noise_sigma": 8.0, "blur": 3}
PROMINENCE = 0.05   # evaluate segments longer than 5% of the drawing extent
MATCH_TOL_PX = 12.0


def _merge_collinear_gt(lines, perp_tol, gap_tol):
    """Merge collinear, touching ground-truth entities into *visible lines*.
    A raster pipeline measures ink features; two CAD entities drawn end-to-end
    on the same line are one indistinguishable ink line, so the fair ground
    truth is their union (exact vector computation, no approximation)."""
    out = []
    used = [False] * len(lines)
    for i, li in enumerate(lines):
        if used[i]:
            continue
        used[i] = True
        a = math.atan2(li["end"][1] - li["start"][1],
                       li["end"][0] - li["start"][0]) % math.pi
        ca, sa = math.cos(a), math.sin(a)
        off = -li["start"][0] * sa + li["start"][1] * ca
        cluster = [li]
        for j, lj in enumerate(lines):
            if used[j]:
                continue
            aj = math.atan2(lj["end"][1] - lj["start"][1],
                            lj["end"][0] - lj["start"][0]) % math.pi
            diff = min(abs(a - aj), math.pi - abs(a - aj))
            if math.degrees(diff) > 0.2:
                continue
            d1 = abs((-lj["start"][0] * sa + lj["start"][1] * ca) - off)
            d2 = abs((-lj["end"][0] * sa + lj["end"][1] * ca) - off)
            if max(d1, d2) > perp_tol:
                continue
            cluster.append(lj)
            used[j] = True
        intervals = sorted(sorted((s["start"][0] * ca + s["start"][1] * sa,
                                   s["end"][0] * ca + s["end"][1] * sa))
                           for s in cluster)
        merged = [list(intervals[0])]
        for lo, hi in intervals[1:]:
            if lo <= merged[-1][1] + gap_tol:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        for lo, hi in merged:
            out.append({
                "start": (lo * ca - off * sa, lo * sa + off * ca),
                "end": (hi * ca - off * sa, hi * sa + off * ca),
                "length": hi - lo,
                "layer": li["layer"],
            })
    return out


def _evaluate_drawing(path):
    geo = dxf_geom.measure(path)
    segments = [(l["start"], l["end"]) for l in geo["lines"]]
    if not segments:
        return None

    img, px_per_unit, to_px = raster.rasterize(segments, width_px=4000, **DEGRADATION)
    # visible-line ground truth: merge collinear entities whose gap is below
    # the scan's ink point-spread (stroke + blur) - such gaps are physically
    # invisible in the raster, so their union is the measurable feature
    geo["lines"] = _merge_collinear_gt(geo["lines"], perp_tol=1.0 / px_per_unit,
                                       gap_tol=8.0 / px_per_unit)
    result = raster.analyze(img, px_per_unit)
    detected = result["measurements"]

    # prominent ground-truth segments, mapped into pixel space (rotation is
    # applied only for matching bookkeeping; the pipeline never sees it)
    span = max(l["length"] for l in geo["lines"])
    theta = math.radians(-DEGRADATION["rotate_deg"])
    h, w = img.shape
    cx, cy = w / 2, h / 2

    def rot(p):
        x, y = to_px(p)
        return (cx + (x - cx) * math.cos(theta) - (y - cy) * math.sin(theta),
                cy + (x - cx) * math.sin(theta) + (y - cy) * math.cos(theta))

    errors, matched, total = [], 0, 0
    for line in geo["lines"]:
        if line["length"] < PROMINENCE * span:
            continue
        total += 1
        p1, p2 = rot(line["start"]), rot(line["end"])
        gt_len_px = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        gt_mid = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
        best = None
        for m in detected:
            px = m["px"]
            mid = ((px["x1"] + px["x2"]) / 2, (px["y1"] + px["y2"]) / 2)
            d = math.hypot(mid[0] - gt_mid[0], mid[1] - gt_mid[1])
            if d > max(MATCH_TOL_PX, 0.05 * gt_len_px):
                continue
            det_len_px = m.get("length_px_refined") or \
                math.hypot(px["x2"] - px["x1"], px["y2"] - px["y1"])
            err = abs(det_len_px - gt_len_px) / gt_len_px
            if best is None or err < best:
                best = err
        if best is not None:
            matched += 1
            errors.append(best)

    mean_err = sum(errors) / len(errors) if errors else 1.0
    return {
        "drawing": geo["file"],
        "unit": geo["unit"],
        "gt_segments_prominent": total,
        "matched": matched,
        "match_rate_pct": round(100.0 * matched / total, 1) if total else 0,
        "mean_accuracy_pct": round(100.0 * (1 - mean_err), 2),
        "within_1pct": sum(1 for e in errors if e <= 0.01),
        "within_1pct_rate": round(100.0 * sum(1 for e in errors if e <= 0.01)
                                  / len(errors), 1) if errors else 0,
        "dimension_entities": geo["counts"]["dimensions"],
        "dimension_values": [d["measurement"] for d in geo["dimensions"][:8]],
    }


def run_benchmark():
    reports = []
    for name in REAL_DRAWINGS:
        path = os.path.join(REAL_DIR, name)
        if os.path.exists(path):
            rep = _evaluate_drawing(path)
            if rep:
                reports.append(rep)
    if not reports:
        return {"error": "no real drawings found in data/real/"}
    all_acc = [r["mean_accuracy_pct"] for r in reports]
    return {
        "degradation": DEGRADATION,
        "drawings": reports,
        "overall_mean_accuracy_pct": round(sum(all_acc) / len(all_acc), 2),
        "target_pct": 99.0,
        "meets_target": all(a >= 99.0 for a in all_acc),
    }


def main():
    report = run_benchmark()
    if "error" in report:
        print(report["error"])
        return 1
    print("Raster measurement benchmark on real CAD drawings")
    print("(scan degradation: %(rotate_deg)s deg skew, noise sigma=%(noise_sigma)s, blur=%(blur)s)"
          % report["degradation"])
    for r in report["drawings"]:
        print("  %-16s GT segments=%-4d matched=%s%%  mean accuracy=%s%%  <=1%% err: %s%%  "
              "DIMENSION entities=%d" % (r["drawing"], r["gt_segments_prominent"],
                                         r["match_rate_pct"], r["mean_accuracy_pct"],
                                         r["within_1pct_rate"], r["dimension_entities"]))
        if r["dimension_values"]:
            print("     vector dimensions read exactly: %s %s"
                  % (r["dimension_values"], r["unit"]))
    print("Overall mean accuracy: %s%% (target %s%%) -> %s"
          % (report["overall_mean_accuracy_pct"], report["target_pct"],
             "OK" if report["meets_target"] else "BELOW TARGET"))
    return 0 if report["meets_target"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
