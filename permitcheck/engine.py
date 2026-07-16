"""Deterministic rules engine.

Evaluates machine-executable compliance rules (RASE-inspired JSON) against a
structured permit application. There is no probabilistic or generative
component in the decision path: identical inputs always produce identical
outputs, and every verdict carries full traceability (rule id, code reference,
observed value, source document, engine version, input hash).

Verdicts (per the ISC/NRC challenge essential outcomes):
    MEETS                  requirement satisfied
    DOES_NOT_MEET          requirement violated
    INFO_NOT_AVAILABLE     required data absent from the application
    UNCERTAIN              data present but not trustworthy enough to decide
                           (low extraction confidence, or value within the
                           declared measurement tolerance of the code limit)
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import ENGINE_VERSION

MEETS = "MEETS"
DOES_NOT_MEET = "DOES_NOT_MEET"
INFO_NOT_AVAILABLE = "INFO_NOT_AVAILABLE"
UNCERTAIN = "UNCERTAIN"

# Worst-first ranking used to aggregate instance verdicts into a rule verdict.
VERDICT_SEVERITY = {DOES_NOT_MEET: 3, UNCERTAIN: 2, INFO_NOT_AVAILABLE: 1, MEETS: 0}

CONFIDENCE_THRESHOLD = 0.80

_MISSING = object()


class Value:
    """A resolved data point, possibly carrying extraction provenance."""

    def __init__(self, value, confidence=None, source=None):
        self.value = value
        self.confidence = confidence
        self.source = source


def _resolve(path: str, ctx: Any, root: dict):
    """Resolve a dotted field path against ctx (or the application root if the
    path starts with '$.'). Returns _MISSING when any segment is absent or null.
    Values may be plain scalars or {"value":..,"confidence":..,"source":..}
    envelopes produced by an upstream document/BIM extraction pipeline."""
    node = root if path.startswith("$.") else ctx
    parts = path[2:].split(".") if path.startswith("$.") else path.split(".")
    for part in parts:
        if isinstance(node, dict) and "value" in node and part not in node:
            node = node["value"]
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    if node is None:
        return _MISSING
    if isinstance(node, dict) and "value" in node:
        return Value(node["value"], node.get("confidence"), node.get("source"))
    return Value(node)


def _compare(op: str, observed, limit) -> Optional[bool]:
    try:
        if op == "eq":
            return observed == limit
        if op == "ne":
            return observed != limit
        if op == "ge":
            return observed >= limit
        if op == "le":
            return observed <= limit
        if op == "gt":
            return observed > limit
        if op == "lt":
            return observed < limit
        if op == "in":
            return observed in limit
        if op == "is_true":
            return observed is True
        if op == "is_false":
            return observed is False
        if op == "not_empty":
            return bool(observed)
        if op == "exists":
            # reaching here means the field resolved successfully
            return True
    except TypeError:
        return None
    return None


def _canonical_hash(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


class RulesEngine:
    def __init__(self, rules_doc: dict):
        self.doc = rules_doc
        self.rules = rules_doc["rules"]
        self.ruleset_version = rules_doc.get("@context", {}).get("ruleset_version", "unknown")

    @classmethod
    def from_file(cls, path: str) -> "RulesEngine":
        with open(path, "r", encoding="utf-8") as fh:
            return cls(json.load(fh))

    # ------------------------------------------------------------------ #
    # Typology classification (essential outcome: distinguish applicable
    # provisions across building typologies - NBC Parts 3 and 9)
    # ------------------------------------------------------------------ #
    def classify(self, app: dict) -> dict:
        cls = self.doc["classification"]
        details = []
        part9 = True
        for cond in cls["part9_conditions"]:
            ok, detail = self._eval_condition(cond, app, app)
            details.append(detail)
            if ok is not True:
                part9 = False
        result = {
            "part": 9 if part9 else 3,
            "conditions": details,
            "basis": cls["description"],
        }
        if part9:
            result.update(self._classify_part9_subtype(app, cls))
        else:
            result.update(self._classify_3_2_2(app, cls))
        return result

    def _classify_part9_subtype(self, app: dict, cls: dict) -> dict:
        """First-match resolution of Part 9 housing/small-building subtypes
        (house, house with secondary suite, multiple dwellings, non-residential)."""
        for entry in cls.get("part9_subtypes", []):
            if all(self._eval_condition(c, app, app)[0] is True for c in entry["when"]):
                return {"subtype": entry["subtype"], "subtype_label": entry["label"]}
        return {"subtype": "unclassified",
                "subtype_label": {"en": "Part 9 subtype could not be determined from the "
                                        "application data - human classification required",
                                  "fr": "Sous-type de la partie 9 indéterminable à partir des "
                                        "données - classification humaine requise"}}

    def _classify_3_2_2(self, app: dict, cls: dict) -> dict:
        """First-match lookup in the (simplified) NBC 3.2.2 decision table."""
        group = _resolve("$.building.major_occupancy", app, app)
        storeys = _resolve("$.building.storeys", app, app)
        sprink = _resolve("$.building.sprinklered", app, app)
        if group is _MISSING or storeys is _MISSING:
            return {"nbc_3_2_2": None,
                    "subtype_label": {"en": "3.2.2 classification impossible: occupancy or "
                                            "storeys missing", "fr": "Classification 3.2.2 "
                                            "impossible : usage ou nombre d'étages manquant"}}
        for entry in cls.get("nbc_3_2_2_table", {}).get("entries", []):
            if entry["group"] != group.value:
                continue
            if storeys.value > entry["max_storeys"]:
                continue
            if entry["sprinklered"] is not None and \
                    (sprink is _MISSING or sprink.value != entry["sprinklered"]):
                continue
            return {"nbc_3_2_2": entry["article"], "subtype": "part3_" + entry["article"],
                    "subtype_label": entry["label"]}
        return {"nbc_3_2_2": None, "subtype": "part3_unmatched",
                "subtype_label": {"en": "No match in the simplified 3.2.2 table - "
                                        "human classification required",
                                  "fr": "Aucune correspondance dans la table 3.2.2 simplifiée - "
                                        "classification humaine requise"}}

    # ------------------------------------------------------------------ #
    # Condition evaluation
    # ------------------------------------------------------------------ #
    def _eval_condition(self, cond: dict, ctx: Any, root: dict) -> Tuple[Optional[bool], dict]:
        if "any" in cond:
            subs = [self._eval_condition(c, ctx, root) for c in cond["any"]]
            ok = any(s[0] is True for s in subs)
            return ok, {"any": [s[1] for s in subs], "result": ok}
        resolved = _resolve(cond["field"], ctx, root)
        if resolved is _MISSING:
            return None, {"field": cond["field"], "result": None, "reason": "missing"}
        ok = _compare(cond["op"], resolved.value, cond.get("value"))
        return ok, {
            "field": cond["field"],
            "op": cond["op"],
            "expected": cond.get("value"),
            "observed": resolved.value,
            "result": ok,
        }

    def _resolve_limit(self, req: dict, ctx: Any, root: dict):
        """A requirement's limit is either a literal `value` or a `value_expr`
        computed from another field (e.g. exit width >= occupant_load * 6.1 mm)."""
        if "value" in req:
            return req["value"], None
        expr = req["value_expr"]
        base = _resolve(expr["field"], ctx, root)
        if base is _MISSING:
            return _MISSING, expr["field"]
        return round(base.value * expr.get("factor", 1.0), 4), expr["field"]

    # ------------------------------------------------------------------ #
    # Requirement evaluation → one check record
    # ------------------------------------------------------------------ #
    def _eval_requirement(self, req: dict, ctx: Any, root: dict) -> Optional[dict]:
        # Conditional requirement (e.g. guard height threshold depends on
        # walking-surface height). If the condition is unmet, the requirement
        # does not apply to this item.
        for cond in req.get("condition", []):
            ok, _ = self._eval_condition(cond, ctx, root)
            if ok is not True:
                return None

        record = {
            "field": req["field"],
            "op": req["op"],
            "unit": req.get("unit"),
            "message": req.get("message"),
        }

        limit, limit_source = self._resolve_limit(req, ctx, root) if (
            "value" in req or "value_expr" in req
        ) else (None, None)
        if limit is _MISSING:
            record.update(verdict=INFO_NOT_AVAILABLE,
                          reason="limit operand missing: %s" % limit_source)
            return record
        record["limit"] = limit

        resolved = _resolve(req["field"], ctx, root)
        if resolved is _MISSING:
            record.update(verdict=INFO_NOT_AVAILABLE, observed=None,
                          reason="required data not found in application")
            return record

        record["observed"] = resolved.value
        record["source"] = resolved.source
        record["confidence"] = resolved.confidence

        ok = _compare(req["op"], resolved.value, limit)
        if ok is None:
            record.update(verdict=INFO_NOT_AVAILABLE,
                          reason="value not comparable (wrong type)")
            return record

        # Uncertainty handling - deterministic rules, applied in fixed order:
        # 1. extraction confidence below threshold
        # 2. numeric value within declared measurement tolerance of the limit
        margin = req.get("uncertainty_margin")
        if resolved.confidence is not None and resolved.confidence < CONFIDENCE_THRESHOLD:
            record.update(verdict=UNCERTAIN, tentative=MEETS if ok else DOES_NOT_MEET,
                          reason="extraction confidence %.2f below threshold %.2f"
                                 % (resolved.confidence, CONFIDENCE_THRESHOLD))
            return record
        if margin is not None and isinstance(resolved.value, (int, float)) \
                and isinstance(limit, (int, float)) \
                and abs(resolved.value - limit) <= margin + 1e-9:
            record.update(verdict=UNCERTAIN, tentative=MEETS if ok else DOES_NOT_MEET,
                          reason="observed value within measurement tolerance (±%s %s) of the code limit"
                                 % (margin, req.get("unit") or ""))
            return record

        record["verdict"] = MEETS if ok else DOES_NOT_MEET
        return record

    # ------------------------------------------------------------------ #
    # Rule evaluation
    # ------------------------------------------------------------------ #
    def _eval_rule(self, rule: dict, app: dict, part: int, subtype: str = None) -> dict:
        result = {
            "rule_id": rule["id"],
            "reference": rule["reference"],
            "title": rule["title"],
            "requirement_text": rule["requirement_text"],
            "severity": rule["severity"],
            "discipline": rule["discipline"],
            "instances": [],
        }

        if rule["part"] != "any" and rule["part"] != part:
            result.update(applicable=False, verdict=None,
                          skip_reason="rule targets Part %s; building classified under Part %s"
                                      % (rule["part"], part))
            return result

        # Typology selection (the "S" in RASE): rules may target specific
        # Part 9 subtypes or 3.2.2 classifications.
        typologies = rule.get("typologies")
        if typologies and subtype not in typologies:
            result.update(applicable=False, verdict=None,
                          skip_reason="rule targets typologies %s; building classified as %s"
                                      % (", ".join(typologies), subtype))
            return result

        # Rule-level applicability (the "A" in RASE)
        for cond in rule.get("applicability", []):
            ok, detail = self._eval_condition(cond, app, app)
            if ok is not True:
                result.update(applicable=False, verdict=None,
                              skip_reason="applicability condition not met",
                              applicability_detail=detail)
                return result

        result["applicable"] = True

        scope = rule.get("scope") or {}
        collection = scope.get("collection")
        if collection:
            items = app.get(collection, [])
            targets = []
            for item in items:
                if all(self._eval_condition(c, item, app)[0] is True
                       for c in scope.get("filter", [])):
                    targets.append(item)
            if not targets:
                result.update(verdict=MEETS, note="no elements in scope")
                return result
        else:
            targets = [app]

        for item in targets:
            item_id = item.get("id") or item.get("name") or ("application" if item is app else "?")
            if isinstance(item_id, dict):
                item_id = item_id.get("value", "?")

            # Exceptions (the "E" in RASE): any satisfied exception exempts the item.
            exempted = None
            for exc in rule.get("exceptions", []):
                ok, detail = self._eval_condition(exc, item, app)
                if ok is True:
                    exempted = detail
                    break
            if exempted:
                result["instances"].append({
                    "element": item_id, "verdict": MEETS,
                    "checks": [], "exception_applied": exempted,
                })
                continue

            checks = []
            for req in rule["requirements"]:
                rec = self._eval_requirement(req, item, app)
                if rec is not None:
                    checks.append(rec)
            worst = max((c["verdict"] for c in checks),
                        key=lambda v: VERDICT_SEVERITY[v], default=MEETS)
            result["instances"].append({"element": item_id, "verdict": worst, "checks": checks})

        result["verdict"] = max((i["verdict"] for i in result["instances"]),
                                key=lambda v: VERDICT_SEVERITY[v], default=MEETS)
        return result

    # ------------------------------------------------------------------ #
    # Full run
    # ------------------------------------------------------------------ #
    def run(self, app: dict) -> dict:
        classification = self.classify(app)
        part = classification["part"]
        subtype = classification.get("subtype")
        rule_results = [self._eval_rule(rule, app, part, subtype) for rule in self.rules]

        applicable = [r for r in rule_results if r["applicable"]]
        summary = {v: 0 for v in (MEETS, DOES_NOT_MEET, UNCERTAIN, INFO_NOT_AVAILABLE)}
        for r in applicable:
            summary[r["verdict"]] += 1

        input_hash = _canonical_hash(app)
        run_id = "RUN-" + _canonical_hash(
            {"input": input_hash, "ruleset": self.ruleset_version}
        )[:12].upper()

        return {
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "engine_version": ENGINE_VERSION,
            "ruleset_version": self.ruleset_version,
            "code_edition": self.doc["@context"]["code_edition"],
            "input_hash": "sha256:" + input_hash,
            "application": {
                "id": app.get("application", {}).get("id"),
                "municipality": app.get("application", {}).get("municipality"),
                "building_name": app.get("building", {}).get("name"),
            },
            "classification": classification,
            "summary": summary,
            "overall": (
                DOES_NOT_MEET if summary[DOES_NOT_MEET] else
                UNCERTAIN if summary[UNCERTAIN] else
                INFO_NOT_AVAILABLE if summary[INFO_NOT_AVAILABLE] else MEETS
            ),
            "results": rule_results,
            "reviews": [],
        }
