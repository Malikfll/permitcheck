"""End-to-end scanned-drawing -> compliance-verdict accuracy benchmark.

This is the metric that answers "does a scanned floor plan produce the right
compliance verdicts", not merely "are lengths measured accurately".

Methodology:
  1. Ground truth = a structured floor-plan spec with known rooms, heights,
     stairs and egress windows (tools/make_floorplan.py). Running the
     deterministic engine on that answer key gives the ground-truth verdicts.
  2. The same plan is rendered as a degraded raster 'scan' (skew, noise, blur).
  3. The BLIND pipeline reads the scan only: OpenCV geometry + Tesseract OCR
     + semantic association -> engine-ready application -> verdicts.
  4. Verdicts are compared rule-by-rule. Accuracy = matching verdicts / total.

Run:  python -m permitcheck.benchmark_semantic
"""

import json
import os

from .engine import RulesEngine
from .extract import semantic

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCANS = os.path.join(BASE, "data", "scans")
TARGET = 0.90


def _verdicts(run):
    return {r["rule_id"]: r["verdict"] for r in run["results"] if r["applicable"]}


def _evaluate(engine, truth_path):
    with open(truth_path, "r", encoding="utf-8") as fh:
        truth = json.load(fh)
    scan_id = os.path.basename(truth_path).replace(".truth.json", "")
    scan_png = os.path.join(SCANS, scan_id + ".png")

    # ground-truth verdicts from the answer-key application
    gt_run = engine.run(truth["application"])
    gt = _verdicts(gt_run)

    # blind extraction from the scan image only
    extracted = semantic.extract_from_image(scan_png, truth["px_per_mm"],
                                            title_block=truth["title_block"])
    app = {"application": {"id": scan_id, "municipality": truth.get("municipality", ""),
                           "legal_description": "on file",
                           "documents": [{"name": scan_id + ".png", "type": "scan"}]},
           "building": extracted.get("building", truth["title_block"]),
           "spaces": extracted["spaces"], "stairs": extracted["stairs"],
           "fire_safety": truth.get("fire_safety", {})}
    got = _verdicts(engine.run(app))

    rules = sorted(set(gt) | set(got))
    matches, mism = 0, []
    for rid in rules:
        if gt.get(rid) == got.get(rid):
            matches += 1
        else:
            mism.append({"rule": rid, "truth": gt.get(rid), "scan": got.get(rid)})
    return {
        "scan": scan_id,
        "rooms_detected": extracted["_extraction"]["rooms_detected"],
        "stairs_detected": extracted["_extraction"]["stairs_detected"],
        "rules": len(rules), "matched": matches,
        "accuracy": round(matches / len(rules), 4) if rules else None,
        "mismatches": mism,
    }


def run_benchmark():
    engine = RulesEngine.from_file(os.path.join(BASE, "rules", "nbc_rules.json"))
    reports = []
    if os.path.isdir(SCANS):
        for name in sorted(os.listdir(SCANS)):
            if name.endswith(".truth.json"):
                reports.append(_evaluate(engine, os.path.join(SCANS, name)))
    if not reports:
        return {"error": "no scans found; run: python tools/make_floorplan.py"}
    total = sum(r["matched"] for r in reports)
    denom = sum(r["rules"] for r in reports)
    overall = total / denom if denom else 0
    return {"scans": reports, "overall_accuracy": round(overall, 4),
            "target": TARGET, "meets_target": overall >= TARGET}


def main():
    report = run_benchmark()
    if "error" in report:
        print(report["error"])
        return 1
    print("Scanned-drawing -> compliance-verdict accuracy")
    for r in report["scans"]:
        print("  %-16s rooms=%d stairs=%d  verdicts %d/%d = %.1f%%"
              % (r["scan"], r["rooms_detected"], r["stairs_detected"],
                 r["matched"], r["rules"], 100 * r["accuracy"]))
        for m in r["mismatches"]:
            print("      MISMATCH %-26s truth=%-18s scan=%s"
                  % (m["rule"], m["truth"], m["scan"]))
    print("Overall verdict accuracy: %.1f%% (target %.0f%%) -> %s"
          % (100 * report["overall_accuracy"], 100 * report["target"],
             "OK" if report["meets_target"] else "BELOW TARGET"))
    return 0 if report["meets_target"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
