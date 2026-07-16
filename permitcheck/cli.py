"""Command-line interface.

    python -m permitcheck.cli check data/applications/APP-2026-0142_duplex_issues.json --lang fr
    python -m permitcheck.cli check <file> --csv out.csv --html out.html --json out.json
    python -m permitcheck.cli rules
    python -m permitcheck.cli serve --port 8742
"""

import argparse
import json
import os
import sys

from .engine import RulesEngine
from .audit import AuditLog
from . import report as report_mod

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_PATH = os.path.join(BASE, "rules", "nbc_rules.json")

BADGE = {"MEETS": "[PASS]", "DOES_NOT_MEET": "[FAIL]",
         "UNCERTAIN": "[????]", "INFO_NOT_AVAILABLE": "[N/AV]"}


def cmd_check(args):
    engine = RulesEngine.from_file(RULES_PATH)
    if os.path.isdir(args.application):
        # a submission folder with raw IFC/DXF/PDF files + manifest.json
        from .extract import pipeline
        app = pipeline.process_submission(args.application)
    else:
        with open(args.application, "r", encoding="utf-8") as fh:
            app = json.load(fh)
    run = engine.run(app)

    AuditLog(os.path.join(BASE, "data", "audit.log.jsonl")).append("compliance_check", {
        "run_id": run["run_id"], "application_id": run["application"]["id"],
        "input_hash": run["input_hash"], "overall": run["overall"],
        "summary": run["summary"], "interface": "cli",
    })

    lang = args.lang
    print("=" * 78)
    print("PermitCheck %s | ruleset %s | %s" %
          (run["engine_version"], run["ruleset_version"], run["run_id"]))
    print("Application %s (%s) - %s" % (run["application"]["id"],
          run["application"]["municipality"], run["application"]["building_name"]))
    c = run["classification"]
    print("Classified under NBC Part %d - %s | input %s" %
          (c["part"], c.get("subtype_label", {}).get(lang, ""),
           run["input_hash"][:23] + "..."))
    print("=" * 78)
    for r in run["results"]:
        if not r["applicable"]:
            continue
        print("%s %-28s %s" % (BADGE[r["verdict"]], r["rule_id"], r["title"][lang]))
        for inst in r["instances"]:
            if inst["verdict"] == "MEETS" and run["overall"] != "MEETS" and not args.verbose:
                continue
            for c in inst["checks"]:
                if c["verdict"] == "MEETS" and not args.verbose:
                    continue
                unit = c.get("unit") or ""
                print("        - %s %s: observed=%s%s limit=%s%s %s" % (
                    BADGE[c["verdict"]], inst["element"],
                    c.get("observed"), unit, c.get("limit"), unit,
                    ("(" + c.get("reason", "") + ")") if c.get("reason") else ""))
    print("-" * 78)
    print("Summary: %s" % "  ".join("%s=%d" % (k, v) for k, v in run["summary"].items()))
    print("Overall: %s %s" % (BADGE[run["overall"]], run["overall"]))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(run, fh, ensure_ascii=False, indent=2)
        print("JSON report written to %s" % args.json)
    if args.csv:
        with open(args.csv, "w", encoding="utf-8-sig", newline="") as fh:
            fh.write(report_mod.to_csv(run, lang))
        print("CSV report written to %s" % args.csv)
    if args.html:
        with open(args.html, "w", encoding="utf-8") as fh:
            fh.write(report_mod.to_html(run, lang))
        print("HTML report written to %s" % args.html)

    return 0 if run["overall"] == "MEETS" else 1


def cmd_rules(args):
    engine = RulesEngine.from_file(RULES_PATH)
    for rule in engine.rules:
        print("%-26s Part %-3s %-14s %s" % (rule["id"], rule["part"],
                                            rule["severity"], rule["title"][args.lang]))
    return 0


def cmd_measure(args):
    ext = os.path.splitext(args.file)[1].lower()
    if ext == ".dxf":
        from .extract import dxf_geom
        geo = dxf_geom.measure(args.file)
        print("%s - unit: %s - %s" % (geo["file"], geo["unit"], geo["counts"]))
        for d in geo["dimensions"]:
            print("  DIMENSION: %s %s%s" % (d["measurement"], geo["unit"],
                  (" (text: %s)" % d["text_override"]) if d["text_override"] else ""))
        for l in sorted(geo["lines"], key=lambda l: -l["length"])[:args.top]:
            print("  LINE %8.2f %s  layer=%s" % (l["length"], geo["unit"], l["layer"]))
    elif ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"):
        if not args.px_per_mm:
            print("--px-per-mm is required for scanned images (plot scale x scan DPI)")
            return 2
        from .extract import raster
        res = raster.analyze(args.file, args.px_per_mm)
        print("skew: %s deg | detected lines: %d" % (res["skew_deg"], res["n_raw_segments"]))
        for m in res["measurements"][:args.top]:
            print("  LINE %8.2f mm  angle=%6.1f  confidence=%.2f" %
                  (m["value"], m["angle_deg"], m["confidence"]))
        for c in res["parallel_clusters"]:
            print("  PARALLEL x%d spacing=%.1f mm (regularity %.2f, confidence %.2f)" %
                  (c["members"], c["value"], c["regularity"], c["confidence"]))
    else:
        print("unsupported file type: %s" % ext)
        return 2
    return 0


def cmd_check_scan(args):
    """Full scanned-drawing -> compliance-verdict path."""
    import json as _json
    from .extract import semantic
    title_block = {}
    if args.title_block:
        with open(args.title_block, "r", encoding="utf-8") as fh:
            meta = _json.load(fh)
        title_block = meta.get("title_block", meta)
        if args.px_per_mm is None and "px_per_mm" in meta:
            args.px_per_mm = meta["px_per_mm"]
    if args.px_per_mm is None:
        print("--px-per-mm is required (or provide it in --title-block JSON)")
        return 2
    extracted = semantic.extract_from_image(args.image, args.px_per_mm,
                                            title_block=title_block)
    app = {"application": {"id": os.path.splitext(os.path.basename(args.image))[0],
                           "municipality": title_block.get("municipality", ""),
                           "legal_description": "on file",
                           "documents": [{"name": os.path.basename(args.image), "type": "scan"}]},
           "building": extracted.get("building", title_block),
           "spaces": extracted["spaces"], "stairs": extracted["stairs"],
           "fire_safety": {}}
    meta = extracted["_extraction"]
    print("Extracted from scan: %d rooms, %d stairs, %d OCR lines, skew %.2f deg" %
          (meta["rooms_detected"], meta["stairs_detected"], meta["ocr_lines"], meta["skew_deg"]))
    engine = RulesEngine.from_file(RULES_PATH)
    run = engine.run(app)
    for r in run["results"]:
        if r["applicable"] and (r["verdict"] != "MEETS" or args.verbose):
            print("  %s %-26s %s" % (BADGE[r["verdict"]], r["rule_id"], r["title"][args.lang]))
    print("Overall: %s %s" % (BADGE[run["overall"]], run["overall"]))
    return 0


def cmd_check_set(args):
    """Ingest a folder of separate Fiches (PDFs/images), merge into one
    application, run the check, and report what is still missing."""
    from . import ingest_set, manual_entry
    paths = args.files if args.files else None
    if args.folder:
        app = ingest_set.ingest_folder(args.folder)
        cov_paths = [os.path.join(args.folder, d["name"]) for d in app["application"]["documents"]]
    else:
        app = ingest_set.ingest_documents(paths)
        cov_paths = paths
    print("Merged %d Fiches into one submission:" % len(app["application"]["documents"]))
    for d in app["application"]["documents"]:
        print("  - %-26s [%s] type=%-22s contributed=%s"
              % (d["name"], d["format"], ",".join(d["sheet_types"]), d["contributed"] or "-"))
    engine = RulesEngine.from_file(RULES_PATH)
    run = engine.run(app)
    print("\nClassified NBC Part %d | overall: %s %s"
          % (run["classification"]["part"], BADGE[run["overall"]], run["overall"]))
    print("Summary:", "  ".join("%s=%d" % (k, v) for k, v in run["summary"].items()))
    missing = manual_entry.missing_fields(run)
    if missing:
        print("\nStill missing (enter manually or add the Fiche that carries it):")
        for m in missing[:20]:
            print("  - %s %s (%s)" % (m["element"], m["field"], m["reference"]))
    return 0


def cmd_analyze(args):
    """Dynamic multi-sheet analysis with a request for whatever is missing."""
    from . import analyze as az
    result = az.analyze_set(args.sheets)
    for s in result["sheets"]:
        ex = s["extraction"]
        detail = ("%d rooms" % ex["rooms"]) if "rooms" in ex else str(ex)
        print("- %-26s [%s]  type: %-28s  -> %s" %
              (s["source"], s["format"], ",".join(s["sheet_types"]), detail))
    print("\nCombined compliance inputs present: %s" % ", ".join(result["combined_provides"]))
    if result["ready_for_full_verdict"]:
        print("READY: all inputs present for a full verdict.")
    else:
        print("\nRequest for Information (still needed):")
        for item in result["request_for_information"]:
            print("  - %-38s provide on: %s" % (item["need"], item["provide_sheet"]))
    return 0


def cmd_analyze_plan(args):
    """Dynamic analysis of a real plan sheet: room schedule + occupant load +
    a Request for Information for whatever the sheet does not carry."""
    import json as _json
    from .extract import planscan
    res = planscan.analyze_plan(args.plan)
    s = res["schedule"]
    print("Plan: %s  (resolution %sx%s, %s column groups)" %
          (res["source"], s["resolution"][0], s["resolution"][1], s.get("column_groups")))
    print("Rooms extracted: %d   total area: %s SF" % (len(s["rooms"]), s["total_area_sf"]))
    print("Room types:", s["room_types"])
    ol = res["occupant_load"]
    for typ, v in ol["by_type"].items():
        print("  %-10s %d rooms  %.0f m2  -> %d occupants" %
              (typ, v["rooms"], v["area_m2"], v["occupants"]))
    print("Estimated total occupant load (NBC 3.1.17): %d persons" % ol["total_occupant_load"])
    print("\nRequest for Information (data not on this sheet):")
    for item in res["completeness"]["request_for_information"]:
        print("  - %-42s (usually on: %s)" % (item["label"][args.lang], item["usual_location"]))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            _json.dump(res, fh, indent=2)
        print("\nFull result written to %s" % args.json)
    return 0


def cmd_serve(args):
    from .server import main as serve_main
    serve_main(args.port)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="permitcheck",
                                description="Deterministic compliance checking for building permit applications")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="run a compliance check on an application JSON file")
    c.add_argument("application")
    c.add_argument("--lang", choices=["en", "fr"], default="en")
    c.add_argument("--verbose", action="store_true", help="also print passing checks")
    c.add_argument("--json"); c.add_argument("--csv"); c.add_argument("--html")
    c.set_defaults(fn=cmd_check)

    r = sub.add_parser("rules", help="list the machine-readable ruleset")
    r.add_argument("--lang", choices=["en", "fr"], default="en")
    r.set_defaults(fn=cmd_rules)

    s = sub.add_parser("serve", help="start the API + web UI")
    s.add_argument("--port", type=int, default=8742)
    s.set_defaults(fn=cmd_serve)

    b = sub.add_parser("benchmark",
                       help="measure verdict accuracy against the gold-labelled corpus")
    b.set_defaults(fn=lambda a: __import__("permitcheck.benchmark", fromlist=["main"]).main())

    bg = sub.add_parser("benchmark-geometry",
                        help="measure raster/CV dimensional accuracy on real CAD drawings")
    bg.set_defaults(fn=lambda a: __import__("permitcheck.benchmark_geom", fromlist=["main"]).main())

    bs = sub.add_parser("benchmark-semantic",
                        help="scanned floor plan -> compliance-verdict accuracy (end to end)")
    bs.set_defaults(fn=lambda a: __import__("permitcheck.benchmark_semantic", fromlist=["main"]).main())

    ks = sub.add_parser("check-set",
                        help="ingest a folder or list of separate Fiche PDFs, merge them "
                             "into one submission, and run the compliance check")
    ks.add_argument("--folder", help="folder containing the Fiche files")
    ks.add_argument("files", nargs="*", help="or an explicit list of files")
    ks.set_defaults(fn=cmd_check_set)

    an = sub.add_parser("analyze",
                        help="dynamic: auto-detect any sheet(s) you feed, extract, and "
                             "request whatever is still missing for a full verdict")
    an.add_argument("sheets", nargs="+", help="one or more PDF/image drawing sheets")
    an.set_defaults(fn=cmd_analyze)

    ap = sub.add_parser("analyze-plan",
                        help="dynamically analyze a real plan sheet (PDF/image): room "
                             "schedule, occupant load, and a request for missing info")
    ap.add_argument("plan")
    ap.add_argument("--lang", choices=["en", "fr"], default="en")
    ap.add_argument("--json", help="write full JSON result to this path")
    ap.set_defaults(fn=cmd_analyze_plan)

    cs = sub.add_parser("check-scan",
                        help="run a compliance check directly on a scanned floor-plan image")
    cs.add_argument("image")
    cs.add_argument("--px-per-mm", type=float, default=None)
    cs.add_argument("--title-block", help="JSON with building fields + px_per_mm")
    cs.add_argument("--lang", choices=["en", "fr"], default="en")
    cs.add_argument("--verbose", action="store_true")
    cs.set_defaults(fn=cmd_check_scan)

    m = sub.add_parser("measure",
                       help="geometric measurement of a CAD file (DXF) or scanned drawing (PNG/JPG)")
    m.add_argument("file")
    m.add_argument("--px-per-mm", type=float, default=None,
                   help="scale for scanned images: pixels per drawing millimetre")
    m.add_argument("--top", type=int, default=15, help="show N longest measurements")
    m.set_defaults(fn=cmd_measure)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
