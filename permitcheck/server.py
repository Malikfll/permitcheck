"""Open REST API + web UI host (essential outcome: intuitive UI and open APIs).

Pure-stdlib HTTP server so the prototype runs with zero dependencies.

Endpoints
    GET  /                         web UI
    GET  /api/rules                machine-readable ruleset
    GET  /api/applications         bundled sample applications
    POST /api/check                {"application_file": "<name>"} or {"application": {...}}
    POST /api/review               human-in-the-loop decision on a rule result
    GET  /api/runs/<run_id>        full run result (incl. reviews)
    GET  /api/report/<run_id>?format=json|csv|html&lang=en|fr
    GET  /api/audit                audit trail + hash-chain verification
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .audit import AuditLog
from .engine import RulesEngine
from .registry import SubmissionRegistry
from .extract import pipeline
from . import report as report_mod
from . import bcf, orx, codesync, benchmark, manual_entry

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_PATH = os.path.join(BASE, "rules", "nbc_rules.json")
APPS_DIR = os.path.join(BASE, "data", "applications")
SUBMISSIONS_DIR = os.path.join(BASE, "data", "submissions")
STATIC_DIR = os.path.join(BASE, "static")
AUDIT_PATH = os.path.join(BASE, "data", "audit.log.jsonl")

ENGINE = RulesEngine.from_file(RULES_PATH)
AUDIT = AuditLog(AUDIT_PATH)
REGISTRY = SubmissionRegistry(os.path.join(BASE, "data", "registry.json"),
                              os.path.join(BASE, "data", ".seal_key"))
RUNS = {}  # run_id -> run result (in-memory store; a product would persist in Canada-resident storage)
APPS = {}  # run_id -> the application dict, so a run can be re-evaluated after manual entry

VALID_DECISIONS = {"APPROVED", "OVERRIDDEN_MEETS", "OVERRIDDEN_DOES_NOT_MEET", "NEEDS_RESUBMISSION"}


class Handler(BaseHTTPRequestHandler):
    server_version = "PermitCheck/0.1"

    # -------------------------------------------------------------- #
    def _send(self, code, body, ctype="application/json; charset=utf-8", download=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if download:
            self.send_header("Content-Disposition", "attachment; filename=%s" % download)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False, indent=2))

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def log_message(self, fmt, *args):  # quiet console
        pass

    # -------------------------------------------------------------- #
    def do_GET(self):
        url = urlparse(self.path)
        path = url.path

        if path in ("/", "/index.html"):
            with open(os.path.join(STATIC_DIR, "index.html"), "rb") as fh:
                return self._send(200, fh.read(), "text/html; charset=utf-8")

        if path == "/api/rules":
            return self._json(200, ENGINE.doc)

        if path == "/api/applications":
            out = []
            for name in sorted(os.listdir(APPS_DIR)):
                if name.endswith(".json"):
                    with open(os.path.join(APPS_DIR, name), "r", encoding="utf-8") as fh:
                        app = json.load(fh)
                    out.append({
                        "file": name,
                        "id": app["application"]["id"],
                        "municipality": app["application"]["municipality"],
                        "building": app["building"]["name"],
                    })
            return self._json(200, out)

        if path == "/api/submissions":
            out = []
            if os.path.isdir(SUBMISSIONS_DIR):
                for name in sorted(os.listdir(SUBMISSIONS_DIR)):
                    manifest = os.path.join(SUBMISSIONS_DIR, name, "manifest.json")
                    if os.path.exists(manifest):
                        with open(manifest, "r", encoding="utf-8") as fh:
                            m = json.load(fh)
                        out.append({"folder": name,
                                    "id": m["application"]["id"],
                                    "municipality": m["application"].get("municipality"),
                                    "files": [f["path"] for f in m.get("files", [])]})
            return self._json(200, out)

        if path == "/api/benchmark":
            return self._json(200, benchmark.run_benchmark())

        if path == "/api/annotatable":
            # every PDF/image under data/submissions, grouped by folder
            out = []
            if os.path.isdir(SUBMISSIONS_DIR):
                for folder in sorted(os.listdir(SUBMISSIONS_DIR)):
                    fpath = os.path.join(SUBMISSIONS_DIR, folder)
                    if not os.path.isdir(fpath):
                        continue
                    files = [n for n in sorted(os.listdir(fpath))
                             if os.path.splitext(n)[1].lower() in
                             (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff")]
                    if files:
                        out.append({"folder": folder, "files": files})
            return self._json(200, out)

        if path == "/api/annotate":
            q = parse_qs(url.query)
            folder = os.path.basename(q.get("folder", [""])[0])
            fname = os.path.basename(q.get("file", [""])[0])
            target = os.path.join(SUBMISSIONS_DIR, folder, fname)
            if not os.path.exists(target):
                return self._json(404, {"error": "document not found"})
            try:
                from . import annotate_viz
                png, kind = annotate_viz.annotate_to_png(target)
            except Exception as exc:
                return self._json(500, {"error": "annotation failed: %s" % exc})
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("X-Annotation-Kind", kind)
            self.send_header("Content-Length", str(len(png)))
            self.end_headers()
            self.wfile.write(png)
            return

        if path.startswith("/api/registry/"):
            app_id = path.rsplit("/", 1)[1]
            return self._json(200, {"application_id": app_id,
                                    "versions": REGISTRY.versions(app_id),
                                    "verification": REGISTRY.verify(app_id)})

        if path == "/api/audit":
            return self._json(200, {"verification": AUDIT.verify(), "entries": AUDIT.entries()})

        if path.startswith("/api/runs/"):
            run = RUNS.get(path.rsplit("/", 1)[1])
            return self._json(200, run) if run else self._json(404, {"error": "run not found"})

        if path.startswith("/api/report/"):
            run = RUNS.get(path.rsplit("/", 1)[1])
            if not run:
                return self._json(404, {"error": "run not found"})
            q = parse_qs(url.query)
            fmt = q.get("format", ["json"])[0]
            lang = q.get("lang", ["en"])[0]
            if lang not in ("en", "fr"):
                lang = "en"
            if fmt == "csv":
                return self._send(200, "﻿" + report_mod.to_csv(run, lang),
                                  "text/csv; charset=utf-8",
                                  download="%s_%s.csv" % (run["run_id"], lang))
            if fmt == "html":
                return self._send(200, report_mod.to_html(run, lang), "text/html; charset=utf-8")
            if fmt == "bcf":
                return self._send(200, bcf.to_bcf(run, lang), "application/octet-stream",
                                  download=bcf.suggested_filename(run))
            return self._json(200, run)

        return self._json(404, {"error": "not found"})

    # -------------------------------------------------------------- #
    def do_POST(self):
        global ENGINE
        path = urlparse(self.path).path
        try:
            body = self._read_body()
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"error": "invalid JSON body"})

        if path == "/api/check":
            registered = None
            if "application_file" in body:
                fpath = os.path.join(APPS_DIR, os.path.basename(body["application_file"]))
                if not os.path.exists(fpath):
                    return self._json(404, {"error": "unknown application file"})
                with open(fpath, "r", encoding="utf-8") as fh:
                    app = json.load(fh)
            elif "submission_folder" in body:
                folder = os.path.join(SUBMISSIONS_DIR, os.path.basename(body["submission_folder"]))
                if not os.path.exists(os.path.join(folder, "manifest.json")):
                    return self._json(404, {"error": "unknown submission folder"})
                app = pipeline.process_submission(folder)
                registered = REGISTRY.register(
                    app["application"]["id"], app["application"]["documents"],
                    app["application"].get("applicant", "unknown"))
            elif "submission_set" in body:
                # a folder of uploaded documents (Fiches) - auto-classify, merge
                from . import ingest_set
                folder = os.path.join(SUBMISSIONS_DIR, os.path.basename(body["submission_set"]))
                if not os.path.isdir(folder):
                    return self._json(404, {"error": "unknown submission set"})
                app = ingest_set.ingest_folder(folder)
            elif "application" in body:
                app = body["application"]
            else:
                return self._json(400, {"error": "provide application_file, submission_folder or application"})

            run = ENGINE.run(app)
            units = app.get("building", {}).get("dwelling_units", 1)
            run["_dwelling_units"] = units.get("value", 1) if isinstance(units, dict) else units
            run["missing_fields"] = manual_entry.missing_fields(run)
            APPS[run["run_id"]] = app
            if registered:
                run["submission_version"] = {"version": registered["version"],
                                             "seal": registered["seal"],
                                             "changes": registered.get("changes")}
            RUNS[run["run_id"]] = run
            AUDIT.append("compliance_check", {
                "run_id": run["run_id"],
                "application_id": run["application"]["id"],
                "input_hash": run["input_hash"],
                "ruleset_version": run["ruleset_version"],
                "overall": run["overall"],
                "summary": run["summary"],
            })
            return self._json(200, run)

        if path == "/api/review":
            required = {"run_id", "rule_id", "reviewer", "decision"}
            if not required.issubset(body):
                return self._json(400, {"error": "required fields: %s" % ", ".join(sorted(required))})
            run = RUNS.get(body["run_id"])
            if not run:
                return self._json(404, {"error": "run not found"})
            if body["decision"] not in VALID_DECISIONS:
                return self._json(400, {"error": "decision must be one of %s" % ", ".join(sorted(VALID_DECISIONS))})
            if not any(r["rule_id"] == body["rule_id"] for r in run["results"]):
                return self._json(404, {"error": "rule not in this run"})
            review = {
                "rule_id": body["rule_id"],
                "reviewer": body["reviewer"],
                "decision": body["decision"],
                "comment": body.get("comment", ""),
            }
            entry = AUDIT.append("human_review", dict(review, run_id=body["run_id"]),
                                 actor=body["reviewer"])
            review["timestamp"] = entry["timestamp"]
            review["audit_hash"] = entry["hash"]
            run["reviews"].append(review)
            return self._json(200, review)

        if path == "/api/upload":
            import base64
            folder = os.path.basename(body.get("folder", "").strip() or "uploaded")
            fname = os.path.basename(body.get("filename", "").strip())
            content_b64 = body.get("content_b64", "")
            ext = os.path.splitext(fname)[1].lower()
            if not fname or ext not in (".pdf", ".png", ".jpg", ".jpeg", ".tif",
                                        ".tiff", ".ifc", ".dxf"):
                return self._json(400, {"error": "unsupported or missing filename"})
            try:
                data = base64.b64decode(content_b64.split(",")[-1])
            except Exception:
                return self._json(400, {"error": "invalid base64 content"})
            if len(data) > 50 * 1024 * 1024:
                return self._json(413, {"error": "file too large (50 MB limit)"})
            dest_dir = os.path.join(SUBMISSIONS_DIR, folder)
            os.makedirs(dest_dir, exist_ok=True)
            with open(os.path.join(dest_dir, fname), "wb") as fh:
                fh.write(data)
            AUDIT.append("document_upload", {"folder": folder, "filename": fname,
                                             "bytes": len(data),
                                             "sha256": __import__("hashlib").sha256(data).hexdigest()})
            return self._json(200, {"folder": folder, "filename": fname,
                                    "bytes": len(data), "ok": True})

        if path == "/api/fill":
            run_id = body.get("run_id")
            entries = body.get("entries") or []
            reviewer = body.get("reviewer", "manual")
            if run_id not in APPS:
                return self._json(404, {"error": "run not found (re-run the check)"})
            if not entries:
                return self._json(400, {"error": "provide entries: [{element, field, value}]"})
            app = manual_entry.apply_entries(APPS[run_id], entries, reviewer)
            run = ENGINE.run(app)
            units = app.get("building", {}).get("dwelling_units", 1)
            run["_dwelling_units"] = units.get("value", 1) if isinstance(units, dict) else units
            run["missing_fields"] = manual_entry.missing_fields(run)
            run["reviews"] = RUNS.get(run_id, {}).get("reviews", [])
            RUNS[run["run_id"]] = run
            APPS[run["run_id"]] = app
            AUDIT.append("manual_entry", {
                "from_run": run_id, "new_run": run["run_id"], "reviewer": reviewer,
                "fields_provided": [{"element": e.get("element"), "field": e["field"],
                                     "value": e["value"]} for e in entries],
                "overall": run["overall"],
            }, actor=reviewer)
            return self._json(200, run)

        if path == "/api/triage":
            reviewers = body.get("reviewers") or []
            run_ids = body.get("run_ids") or list(RUNS.keys())
            runs = [RUNS[rid] for rid in run_ids if rid in RUNS]
            if not runs:
                return self._json(400, {"error": "no known runs; run checks first"})
            if not reviewers:
                return self._json(400, {"error": "provide reviewers: [{name, disciplines}]"})
            plan = orx.triage(runs, reviewers)
            AUDIT.append("triage_plan", {
                "run_ids": [r["run_id"] for r in runs],
                "reviewers": [r["name"] for r in reviewers],
                "findings": plan["findings_count"],
                "queue_improvement_pct": plan["queue"]["improvement_pct"],
            })
            return self._json(200, plan)

        if path == "/api/codes/ingest":
            amendment = body.get("amendment")
            if not amendment and "amendment_file" in body:
                apath = os.path.join(BASE, "rules", "amendments",
                                     os.path.basename(body["amendment_file"]))
                if not os.path.exists(apath):
                    return self._json(404, {"error": "unknown amendment file"})
                with open(apath, "r", encoding="utf-8") as fh:
                    amendment = json.load(fh)
            if not amendment:
                return self._json(400, {"error": "provide amendment or amendment_file"})
            try:
                summary = codesync.apply_amendment(RULES_PATH, amendment)
            except codesync.AmendmentError as exc:
                return self._json(422, {"error": str(exc)})
            ENGINE = RulesEngine.from_file(RULES_PATH)  # hot reload
            AUDIT.append("code_amendment", summary)
            return self._json(200, summary)

        return self._json(404, {"error": "not found"})


def main(port=8742):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("PermitCheck prototype listening on http://127.0.0.1:%d" % port)
    server.serve_forever()


if __name__ == "__main__":
    import sys
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8742)
