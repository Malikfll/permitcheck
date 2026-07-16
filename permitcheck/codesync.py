"""Codes-database ingestion pipeline (additional outcome: configurable data
pipeline with an NRC/AHJ digitalized-codes database).

Consumes amendment documents - the exchange format an NRC or AHJ codes
database would publish (via API pull, file drop, or event stream) - validates
them, applies add/modify/remove operations to the ruleset, bumps the ruleset
version, and archives the previous version for reproducibility: any past run
can be re-verified against the exact ruleset version recorded in its result.

Amendment document format:
    {
      "amendment_id": "...", "effective": "YYYY-MM-DD",
      "target_ruleset": "1.0.0", "new_version": "1.1.0",
      "add": [ <rule objects> ], "modify": [ <rule objects> ], "remove": [ "rule ids" ]
    }
"""

import json
import os
import shutil

REQUIRED_RULE_FIELDS = ("id", "reference", "part", "severity", "discipline",
                        "title", "requirement_text", "requirements")


class AmendmentError(ValueError):
    pass


def _validate_rule(rule):
    missing = [f for f in REQUIRED_RULE_FIELDS if f not in rule]
    if missing:
        raise AmendmentError("rule %s missing fields: %s"
                             % (rule.get("id", "?"), ", ".join(missing)))
    for text_field in ("title", "requirement_text"):
        if not ("en" in rule[text_field] and "fr" in rule[text_field]):
            raise AmendmentError("rule %s: %s must be bilingual (en+fr)"
                                 % (rule["id"], text_field))


def apply_amendment(rules_path, amendment):
    """Validate and apply an amendment; archive the old ruleset. Returns a
    summary dict. Raises AmendmentError on any validation failure (the
    ruleset is left untouched)."""
    with open(rules_path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    current = doc["@context"]["ruleset_version"]

    if amendment.get("target_ruleset") != current:
        raise AmendmentError("amendment targets ruleset %s but current is %s"
                             % (amendment.get("target_ruleset"), current))
    if not amendment.get("new_version"):
        raise AmendmentError("new_version is required")

    ids = {r["id"] for r in doc["rules"]}
    for rule in amendment.get("add", []):
        _validate_rule(rule)
        if rule["id"] in ids:
            raise AmendmentError("cannot add %s: id already exists" % rule["id"])
    for rule in amendment.get("modify", []):
        _validate_rule(rule)
        if rule["id"] not in ids:
            raise AmendmentError("cannot modify %s: unknown id" % rule["id"])
    for rid in amendment.get("remove", []):
        if rid not in ids:
            raise AmendmentError("cannot remove %s: unknown id" % rid)

    # archive current version, then apply
    archive_dir = os.path.join(os.path.dirname(rules_path), "versions")
    os.makedirs(archive_dir, exist_ok=True)
    shutil.copyfile(rules_path,
                    os.path.join(archive_dir, "nbc_rules_%s.json" % current))

    by_id = {r["id"]: r for r in doc["rules"]}
    for rule in amendment.get("modify", []):
        by_id[rule["id"]].update(rule)
    for rid in amendment.get("remove", []):
        del by_id[rid]
    doc["rules"] = [r for r in doc["rules"] if r["id"] in by_id]
    doc["rules"].extend(amendment.get("add", []))
    doc["@context"]["ruleset_version"] = amendment["new_version"]

    with open(rules_path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)

    return {
        "amendment_id": amendment.get("amendment_id"),
        "from_version": current,
        "to_version": amendment["new_version"],
        "added": [r["id"] for r in amendment.get("add", [])],
        "modified": [r["id"] for r in amendment.get("modify", [])],
        "removed": list(amendment.get("remove", [])),
        "archived_as": "versions/nbc_rules_%s.json" % current,
    }
