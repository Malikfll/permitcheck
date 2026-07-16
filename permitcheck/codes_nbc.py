"""Ingest the digitalized National Building Code of Canada into PermitCheck.

Source: the BC government's open, machine-readable BC Building Code JSON
(github.com/bcgov/BC-Building-Code) - the NBC 2020 content (IDs are `nbc.2020.*`)
with BC amendments, released as open data. 2 volumes, 2,020 articles,
5,130 sentences. This is a real, openly available digitalized Canadian code.

(The National and Quebec codes themselves are distributed by NRC / the Régie du
bâtiment as PDF and, for machine-readable form, directly to funded parties; the
BC edition is the open NBC-derived proxy. NRC's own digitalized code would be
ingested by the same walker with only the field selectors changed.)

Two stages, both deterministic:
  1. extract_requirements(): walk every sentence, pull measurable provisions
     ("not less than 2.3 m", "not more than 200 mm", ...) into rule stubs with
     the article citation, operator, value and unit.
  2. field-mapping: map a stub's free-text subject to a canonical engine field
     (a small, reviewable dictionary) so the rule runs live against an
     application. This is the human-in-the-loop authoring step, scaled by data.
"""

import json
import os
import re

# requirement phrasings -> engine operator
PATTERNS = [
    (re.compile(r"not\s+less\s+than\s+([\d.,]+)\s*(mm|millimetres?|m|metres?|m2|m²|°|degrees?|%)", re.I), "ge"),
    (re.compile(r"at\s+least\s+([\d.,]+)\s*(mm|millimetres?|m|metres?|m2|m²|°|degrees?|%)", re.I), "ge"),
    (re.compile(r"not\s+more\s+than\s+([\d.,]+)\s*(mm|millimetres?|m|metres?|m2|m²|°|degrees?|%)", re.I), "le"),
    (re.compile(r"not\s+exceed(?:ing)?\s+([\d.,]+)\s*(mm|millimetres?|m|metres?|m2|m²|°|degrees?|%)", re.I), "le"),
    (re.compile(r"maximum\s+(?:of\s+)?([\d.,]+)\s*(mm|millimetres?|m|metres?|m2|m²|°|degrees?|%)", re.I), "le"),
    (re.compile(r"minimum\s+(?:of\s+)?([\d.,]+)\s*(mm|millimetres?|m|metres?|m2|m²|°|degrees?|%)", re.I), "ge"),
]
UNIT_CANON = {"mm": "mm", "millimetre": "mm", "millimetres": "mm",
              "m": "m", "metre": "m", "metres": "m", "m2": "m2", "m²": "m2",
              "°": "deg", "degree": "deg", "degrees": "deg", "%": "pct"}
_REF = re.compile(r"\[REF:[^\]]*?([^:\]]+)\]")
_TAG = re.compile(r"\[REF:[^\]]*\]")


def _clean(text):
    # turn [REF:term:...:building] style tags into their trailing label
    text = _REF.sub(lambda m: m.group(1), text)
    text = _TAG.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _iter_sentences(node, article=None):
    if isinstance(node, dict):
        if node.get("type") == "article":
            article = {"id": node.get("id"), "number": node.get("number"),
                       "title": node.get("title")}
        if node.get("type") == "sentence" and node.get("text"):
            yield article, node
        for v in node.values():
            yield from _iter_sentences(v, article)
    elif isinstance(node, list):
        for it in node:
            yield from _iter_sentences(it, article)


def _article_number(article, sent):
    """Best-effort dotted article number from the sentence id path."""
    sid = sent.get("id", "")
    m = re.search(r"part(\d+)\.sect(\d+)\.subsect(\d+)\.art(\d+)", sid)
    if m:
        return "%s.%s.%s.%s" % m.groups()
    return (article or {}).get("id", "nbc")


def extract_requirements(code_json_path):
    """Return measurable requirement stubs extracted from every sentence."""
    with open(code_json_path, encoding="utf-8") as fh:
        doc = json.load(fh)
    stubs = []
    total_sentences = 0
    for article, sent in _iter_sentences(doc):
        total_sentences += 1
        text = _clean(sent["text"])
        for rx, op in PATTERNS:
            m = rx.search(text)
            if not m:
                continue
            value = float(m.group(1).replace(",", ""))
            if value.is_integer():
                value = int(value)
            unit = UNIT_CANON.get(m.group(2).lower(), m.group(2).lower())
            art_no = _article_number(article, sent)
            stubs.append({
                "id": "NBC-%s" % art_no,
                "reference": "NBC 2020 Div. B, Article %s (BC digitalized edition)" % art_no,
                "article_title": (article or {}).get("title"),
                "source_text": text[:300],
                "draft_requirement": {"op": op, "value": value, "unit": unit},
                "status": "draft_needs_field_mapping",
            })
            break  # one requirement per sentence for the stub
    return {"total_sentences": total_sentences, "stubs": stubs}


# --------------------------------------------------------------------- #
# Field mapping: free-text provision -> canonical engine field.
# A small, reviewable dictionary keyed on words in the article title/text.
# This is the step that makes a stub executable against an application.
# --------------------------------------------------------------------- #
# Each entry: title keywords, collection, field, scope, expected unit, and a
# plausibility range [min,max] in that unit. Anchoring on the ARTICLE TITLE
# (not stray text) plus a value range removes false matches like a 1 mm gap
# in a clause that merely mentions the word "ceiling".
FIELD_MAP_RULES = [
    (("ceiling height",), "spaces", "ceiling_height_m", {"habitable": True}, "m", (1.9, 3.5)),
    (("stair width", "width of stair"), "stairs", "width_mm", {"private": True}, "mm", (600, 1500)),
    (("height of guard", "guard height", "guards"), "guards", "height_mm", {}, "mm", (700, 1200)),
    (("riser", "rise of step"), "stairs", "rise_mm", {"private": True}, "mm", (100, 250)),
    (("tread", "run of"), "stairs", "run_mm", {"private": True}, "mm", (200, 400)),
    (("headroom",), "stairs", "headroom_mm", {"private": True}, "mm", (1800, 2200)),
]


def map_fields(stubs):
    """Attach a canonical (collection, field, scope) to stubs whose ARTICLE
    TITLE matches a known provision AND whose value is physically plausible for
    that field. Returns clean, live engine requirements."""
    live = []
    for s in stubs:
        title = (s.get("article_title") or "").lower()
        req = s["draft_requirement"]
        for keys, collection, field, scope, unit, (lo, hi) in FIELD_MAP_RULES:
            if not any(k in title for k in keys):
                continue
            if req["unit"] != unit or not (lo <= req["value"] <= hi):
                continue
            live.append({
                "id": s["id"], "reference": s["reference"],
                "collection": collection, "field": field, "scope": scope,
                "op": req["op"], "value": req["value"], "unit": unit,
                "source_text": s["source_text"],
            })
            break
    return live


def to_engine_ruleset(live_rules, ruleset_version="nbc-auto-0.1"):
    """Wrap field-mapped live rules into a ruleset document the deterministic
    engine can load and run directly - closing the loop from code text to
    executable verdict."""
    rules = []
    for r in live_rules:
        rules.append({
            "id": r["id"], "reference": r["reference"], "part": 9 if ".9." in r["id"] or r["id"].split("-")[1].startswith("9") else 3,
            "severity": "major", "discipline": "architectural",
            "title": {"en": r["field"], "fr": r["field"]},
            "requirement_text": {"en": r["source_text"], "fr": r["source_text"]},
            "scope": {"collection": r["collection"],
                      "filter": [{"field": k, "op": "is_true"} for k in r["scope"]]},
            "applicability": [], "exceptions": [],
            "requirements": [{"field": r["field"], "op": r["op"], "value": r["value"],
                              "unit": r["unit"],
                              "message": {"en": r["source_text"][:80], "fr": r["source_text"][:80]}}],
        })
    return {
        "@context": {"code_edition": {"id": "NBC-2020-BC",
                     "title": {"en": "NBC 2020 (BC digitalized edition)",
                               "fr": "CNB 2020 (édition numérisée BC)"}},
                     "ruleset_version": ruleset_version},
        "classification": {"description": {"en": "auto", "fr": "auto"},
                           "part9_conditions": [{"field": "$.building.storeys", "op": "le", "value": 3}]},
        "rules": rules,
    }


def main():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, "data", "bc_code", "BuildingCode.json")
    if not os.path.exists(path):
        print("BC/NBC code JSON not found at %s" % path)
        print("Download: https://github.com/bcgov/BC-Building-Code (BuildingCode.json)")
        return 1
    result = extract_requirements(path)
    stubs = result["stubs"]
    live = map_fields(stubs)
    by_op = {}
    for s in stubs:
        by_op[s["draft_requirement"]["op"]] = by_op.get(s["draft_requirement"]["op"], 0) + 1

    out = os.path.join(base, "data", "bc_code", "nbc_rule_stubs.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"stubs": stubs, "live_mapped": live}, fh, indent=2, ensure_ascii=False)

    print("NBC 2020 ingestion (BC open digitalized edition)")
    print("  sentences scanned          : %d" % result["total_sentences"])
    print("  measurable requirement stubs: %d" % len(stubs))
    print("  by operator                : %s" % by_op)
    print("  auto field-mapped to live rules: %d" % len(live))
    print("  written to                 : %s" % out)
    print("\n  sample live-mapped rules (run directly against an application):")
    seen = set()
    for r in live:
        key = (r["collection"], r["field"], r["op"], r["value"])
        if key in seen:
            continue
        seen.add(key)
        print("   [%s] %s.%s %s %s %s" % (r["id"], r["collection"], r["field"],
                                          r["op"], r["value"], r["unit"]))
        if len(seen) >= 8:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
