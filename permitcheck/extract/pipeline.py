"""Extraction pipeline: merge multi-source extractions into one application.

A submission folder contains the raw permit files plus a small manifest:

    data/submissions/APP-XXXX/
        manifest.json      administrative metadata + file list
        model.ifc          BIM model          -> ifc adapter
        plans.dxf          2D CAD drawing     -> dxf adapter
        permit_form.pdf    application form   -> pdfx adapter

Merge policy is deterministic: envelope fields are kept from the source with
the highest extraction confidence; collection items (spaces, stairs, guards…)
are merged by element id. Every document is fingerprinted (SHA-256) so the
compliance run is bound to exact file versions.
"""

import hashlib
import json
import os

from . import ifc, dxf, pdfx

ADAPTERS = {".ifc": ifc.extract, ".dxf": dxf.extract, ".pdf": pdfx.extract}
try:  # scanned form images, only when Tesseract is available
    from . import ocr
    if ocr.HAVE_TESSERACT:
        for _ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff"):
            ADAPTERS[_ext] = ocr.extract
except ImportError:
    pass
COLLECTIONS = ("spaces", "stairs", "guards", "raised_surfaces", "floor_areas")


def _confidence(v):
    if isinstance(v, dict) and "confidence" in v:
        return v["confidence"]
    return 1.0  # manifest-declared values are authoritative


def _merge_dict(base: dict, add: dict):
    for key, val in add.items():
        if key in COLLECTIONS:
            _merge_collection(base.setdefault(key, []), val)
        elif isinstance(val, dict) and "value" not in val:
            _merge_dict(base.setdefault(key, {}), val)
        else:
            if key not in base or _confidence(val) > _confidence(base[key]):
                base[key] = val


def _merge_collection(base_list: list, add_list: list):
    by_id = {item.get("id"): item for item in base_list}
    for item in add_list:
        target = by_id.get(item.get("id"))
        if target is None:
            base_list.append(item)
            by_id[item.get("id")] = item
        else:
            _merge_dict(target, item)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def process_submission(folder: str) -> dict:
    """Run all adapters over a submission folder and return an engine-ready
    application dict with per-value provenance and document fingerprints."""
    with open(os.path.join(folder, "manifest.json"), "r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    app = {k: v for k, v in manifest.items() if k != "files"}
    app.setdefault("application", {})
    documents = []

    for entry in manifest.get("files", []):
        path = os.path.join(folder, entry["path"])
        ext = os.path.splitext(path)[1].lower()
        adapter = ADAPTERS.get(ext)
        record = {"name": entry["path"], "type": ext.lstrip("."),
                  "role": entry.get("role"), "sha256": _sha256(path)}
        if adapter:
            extracted = adapter(path)
            _merge_dict(app, extracted)
            record["extracted"] = True
        documents.append(record)

    app["application"]["documents"] = documents
    return app
