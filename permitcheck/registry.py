"""Submission version registry with digital seals (additional outcome:
track versions of drawings/models/data with identity and trust).

Every registered submission version records the SHA-256 of each document and
is sealed with HMAC-SHA256 under a server-held key, binding *who submitted
what, and when*. Consecutive versions are diffed so reviewers see exactly
which documents changed after a resubmission. Production Phase 2 replaces the
HMAC seal with PKI-backed digital signatures (e.g. CSA/Notarius seals).
"""

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone


class SubmissionRegistry:
    def __init__(self, store_path, key_path):
        self.store_path = store_path
        os.makedirs(os.path.dirname(store_path) or ".", exist_ok=True)
        if not os.path.exists(key_path):
            with open(key_path, "wb") as fh:
                fh.write(os.urandom(32))
        with open(key_path, "rb") as fh:
            self._key = fh.read()

    # ------------------------------------------------------------ #
    def _load(self):
        if not os.path.exists(self.store_path):
            return {}
        with open(self.store_path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def _save(self, data):
        with open(self.store_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)

    def _seal(self, record):
        body = json.dumps({k: v for k, v in record.items() if k != "seal"},
                          sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hmac.new(self._key, body, hashlib.sha256).hexdigest()

    # ------------------------------------------------------------ #
    def register(self, application_id, documents, submitter):
        """documents: [{"name":..., "sha256":...}]. Returns the version record."""
        data = self._load()
        versions = data.setdefault(application_id, [])
        record = {
            "version": len(versions) + 1,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "submitter": submitter,
            "documents": [{"name": d["name"], "sha256": d["sha256"]} for d in documents],
        }
        if versions:
            prev = {d["name"]: d["sha256"] for d in versions[-1]["documents"]}
            record["changes"] = {
                "added": [d["name"] for d in record["documents"] if d["name"] not in prev],
                "modified": [d["name"] for d in record["documents"]
                             if d["name"] in prev and prev[d["name"]] != d["sha256"]],
                "removed": [n for n in prev
                            if n not in {d["name"] for d in record["documents"]}],
            }
        record["seal"] = self._seal(record)
        versions.append(record)
        self._save(data)
        return record

    def versions(self, application_id):
        return self._load().get(application_id, [])

    def verify(self, application_id):
        """Re-compute every seal; report tampering."""
        out = []
        for record in self.versions(application_id):
            out.append({"version": record["version"],
                        "valid": hmac.compare_digest(record["seal"], self._seal(record))})
        return out
