"""Tamper-evident audit trail (essential outcome: traceability and auditability).

Every engine run and every human review decision is appended to a JSON-Lines
log where each entry embeds the SHA-256 hash of the previous entry, forming a
verifiable hash chain. `verify()` re-computes the chain and reports the first
broken link, if any.
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Optional


class AuditLog:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def _last_hash(self) -> str:
        if not os.path.exists(self.path):
            return "GENESIS"
        last = None
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    last = line
        if last is None:
            return "GENESIS"
        return json.loads(last)["hash"]

    @staticmethod
    def _entry_hash(entry: dict) -> str:
        body = {k: v for k, v in entry.items() if k != "hash"}
        return hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()

    def append(self, event: str, payload: dict, actor: Optional[str] = None) -> dict:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "actor": actor or "system",
            "payload": payload,
            "prev_hash": self._last_hash(),
        }
        entry["hash"] = self._entry_hash(entry)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def entries(self):
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    out.append(json.loads(line))
        return out

    def verify(self) -> dict:
        prev = "GENESIS"
        for i, entry in enumerate(self.entries()):
            if entry["prev_hash"] != prev or self._entry_hash(entry) != entry["hash"]:
                return {"valid": False, "broken_at": i}
            prev = entry["hash"]
        return {"valid": True, "entries": len(self.entries())}
