"""Accuracy benchmark harness (additional outcome: >=90% accuracy on simple
rules, >=80% on complex rules).

Runs the engine over a gold-labelled corpus and reports accuracy separately
for *simple* and *complex* rules, following the challenge footnotes:

  simple  - prescriptive provision, <=1 cross-reference, no lookup tables or
            equations;
  complex - multiple cross-references, lookup tables or equations.

Complexity is derived deterministically from rule structure: a rule is
complex when it uses a computed limit (equation), conditional requirements
(selection/lookup), exceptions (cross-references), or compound applicability.

The prototype corpus is small; Phase 2 grows it to a statistically meaningful
benchmark co-curated with NRC (real anonymized permit sets).
"""

import json
import os

from .engine import RulesEngine
from .extract import pipeline

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THRESHOLDS = {"simple": 0.90, "complex": 0.80}


def rule_complexity(rule):
    if rule.get("exceptions"):
        return "complex"
    if any("value_expr" in req or req.get("condition") for req in rule["requirements"]):
        return "complex"
    if any("any" in cond for cond in rule.get("applicability", [])):
        return "complex"
    return "simple"


def _load_case(case):
    if "application_file" in case:
        path = os.path.join(BASE, "data", "applications", case["application_file"])
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return pipeline.process_submission(os.path.join(BASE, case["submission_folder"]))


def run_benchmark(gold_path=None, rules_path=None):
    gold_path = gold_path or os.path.join(BASE, "benchmarks", "gold_labels.json")
    rules_path = rules_path or os.path.join(BASE, "rules", "nbc_rules.json")
    engine = RulesEngine.from_file(rules_path)
    complexity = {r["id"]: rule_complexity(r) for r in engine.rules}

    with open(gold_path, "r", encoding="utf-8") as fh:
        gold = json.load(fh)

    tally = {"simple": [0, 0], "complex": [0, 0]}  # [correct, total]
    mismatches = []
    for case in gold["cases"]:
        run = engine.run(_load_case(case))
        got = {r["rule_id"]: r["verdict"] for r in run["results"] if r["applicable"]}
        for rule_id, expected in case["expected"].items():
            cls = complexity.get(rule_id, "simple")
            tally[cls][1] += 1
            if got.get(rule_id) == expected:
                tally[cls][0] += 1
            else:
                mismatches.append({"case": case["name"], "rule": rule_id,
                                   "expected": expected, "got": got.get(rule_id)})

    report = {"ruleset_version": engine.ruleset_version, "classes": {}, "mismatches": mismatches}
    for cls, (correct, total) in tally.items():
        accuracy = correct / total if total else None
        report["classes"][cls] = {
            "correct": correct, "total": total,
            "accuracy": round(accuracy, 4) if accuracy is not None else None,
            "threshold": THRESHOLDS[cls],
            "meets_target": accuracy is not None and accuracy >= THRESHOLDS[cls],
        }
    return report


def main():
    report = run_benchmark()
    print("Ruleset %s" % report["ruleset_version"])
    for cls, stats in report["classes"].items():
        print("  %-8s %d/%d correct  accuracy=%.1f%%  target=%.0f%%  %s" % (
            cls, stats["correct"], stats["total"],
            100 * (stats["accuracy"] or 0), 100 * stats["threshold"],
            "OK" if stats["meets_target"] else "BELOW TARGET"))
    for m in report["mismatches"]:
        print("  MISMATCH %(case)s %(rule)s expected=%(expected)s got=%(got)s" % m)
    return 0 if all(s["meets_target"] for s in report["classes"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
