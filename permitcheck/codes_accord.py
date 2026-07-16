"""Ingest the CODE-ACCORD annotated regulation corpus into PermitCheck rules.

CODE-ACCORD (Hettiarachchi et al., Nature Scientific Data 2024;
github.com/Accord-Project/CODE-ACCORD) is a real, published corpus of building
regulation sentences annotated with entities (object / property / quality /
value) and logical relations (greater-equal, less-equal, equal, necessity,
selection, part-of ...). It is the same class of machine-readable, RASE-aligned
digitalized-code data that NRC is producing for the Canadian codes.

This module converts the corpus's comparative relations into machine-executable
rule stubs in PermitCheck's own JSON schema, demonstrating that authoring the
"thousands of provisions" is a data-ingestion pipeline, not thousands of
hand-written rules. The stubs are review-ready drafts: a human maps the free-
text field name to a canonical IFC/data-dictionary field and confirms - exactly
the human-in-the-loop authoring workflow a Phase-2 plan would scale.

Real code data in; deterministic rule stubs out. No probabilistic step.
"""

import ast
import csv
import os
import re

# relation_type -> engine comparison operator
RELATION_OPS = {
    "greater-equal": "ge", "less-equal": "le",
    "greater": "gt", "less": "lt", "equal": "eq",
}
# unit surface forms -> canonical
UNITS = {
    "millimetre": "mm", "millimetres": "mm", "mm": "mm",
    "metre": "m", "metres": "m", "m": "m",
    "square metre": "m2", "square metres": "m2", "m2": "m2",
    "degree": "deg", "degrees": "deg", "percent": "pct", "%": "pct",
}
_E1 = re.compile(r"<e1>(.*?)</e1>")
_E2 = re.compile(r"<e2>(.*?)</e2>")
_NUM_UNIT = re.compile(
    r"([\d]{1,3}(?:[,\s]?\d{3})*(?:\.\d+)?)\s*"
    r"(millimetres?|metres?|mm|m2|square metres?|m|degrees?|percent|%)", re.I)


def _slug(text):
    text = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return text[:48] or "field"


def _parse_value_unit(e2):
    m = _NUM_UNIT.search(e2)
    if not m:
        return None, None
    value = float(m.group(1).replace(",", "").replace(" ", ""))
    unit = UNITS.get(m.group(2).lower())
    if value.is_integer():
        value = int(value)
    return value, unit


def convert_relations(relations_csv):
    """Yield rule stubs from comparative relations that carry a numeric value."""
    with open(relations_csv, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            op = RELATION_OPS.get(row["relation_type"])
            if not op:
                continue
            e1 = _E1.search(row["tagged_sentence"])
            e2 = _E2.search(row["tagged_sentence"])
            if not (e1 and e2):
                continue
            value, unit = _parse_value_unit(e2.group(1))
            if value is None:
                continue
            try:
                meta = ast.literal_eval(row["metadata"])
                ref = meta.get("ID", "CODE-ACCORD")
            except (ValueError, SyntaxError):
                ref = "CODE-ACCORD"
            yield {
                "id": "ACCORD-%s-%s" % (ref, _slug(e1.group(1))[:20]),
                "reference": "CODE-ACCORD corpus, clause %s" % ref,
                "source_text": row["content"],
                "draft_requirement": {
                    "field": _slug(e1.group(1)),
                    "op": op, "value": value, "unit": unit,
                },
                "status": "draft_needs_field_mapping",
            }


def ingest(relations_csv, out_path=None):
    """Convert the whole corpus and report coverage statistics."""
    stubs = list(convert_relations(relations_csv))
    total_rows = sum(1 for _ in open(relations_csv, encoding="utf-8")) - 1
    with_unit = sum(1 for s in stubs if s["draft_requirement"]["unit"])
    by_op = {}
    for s in stubs:
        by_op[s["draft_requirement"]["op"]] = by_op.get(s["draft_requirement"]["op"], 0) + 1
    report = {
        "corpus": os.path.basename(relations_csv),
        "total_relations": total_rows,
        "rule_stubs_generated": len(stubs),
        "with_recognised_unit": with_unit,
        "by_operator": by_op,
        "sample": stubs[:8],
    }
    if out_path:
        import json
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump({"report": report, "stubs": stubs}, fh, indent=2, ensure_ascii=False)
    return report


def main():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rel = os.path.join(base, "data", "code_accord", "relations_all.csv")
    if not os.path.exists(rel):
        print("CODE-ACCORD data not found at %s" % rel)
        print("Download: https://github.com/Accord-Project/CODE-ACCORD")
        return 1
    out = os.path.join(base, "data", "code_accord", "generated_rule_stubs.json")
    r = ingest(rel, out)
    print("CODE-ACCORD ingestion (%s)" % r["corpus"])
    print("  total relations in corpus : %d" % r["total_relations"])
    print("  machine-executable stubs  : %d  (%d with a recognised unit)"
          % (r["rule_stubs_generated"], r["with_recognised_unit"]))
    print("  by operator               : %s" % r["by_operator"])
    print("  written to                : %s" % out)
    print("\n  sample auto-generated rule stubs:")
    for s in r["sample"][:5]:
        d = s["draft_requirement"]
        print("   [%s]  %s %s %s %s" % (s["reference"], d["field"], d["op"],
                                        d["value"], d["unit"] or ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
