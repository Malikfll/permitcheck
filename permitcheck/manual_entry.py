"""Dynamic manual data entry for whatever the drawings didn't provide.

The form is not hardcoded: it is generated from the compliance run itself.
Every check that came back INFO_NOT_AVAILABLE (a datum the extractor could not
find) becomes exactly one form field - element, field, unit, the code limit,
and the article it is needed for. Fill them in, they are merged into the
application with full provenance (source = "manual entry by <reviewer>",
confidence 1.0), and the engine re-runs to a complete verdict.

Missing fewer things -> a shorter form. Missing nothing -> no form at all.
"""


def missing_fields(run):
    """Build the dynamic entry form from a run's INFO_NOT_AVAILABLE checks."""
    fields = []
    seen = set()
    for r in run["results"]:
        if not r.get("applicable"):
            continue
        for inst in r.get("instances", []):
            element = inst.get("element")
            for c in inst.get("checks", []):
                if c.get("verdict") != "INFO_NOT_AVAILABLE":
                    continue
                key = (element, c["field"])
                if key in seen:
                    continue
                seen.add(key)
                unit = c.get("unit") or ""
                fields.append({
                    "element": element,
                    "field": c["field"],
                    "unit": c.get("unit"),
                    "rule_id": r["rule_id"],
                    "reference": r["reference"],
                    "code_limit": c.get("limit"),
                    "prompt": {
                        "en": "%s - %s%s (%s)" % (
                            element, _human(c["field"]),
                            (" in %s" % unit) if unit else "", r["reference"]),
                        "fr": "%s - %s%s (%s)" % (
                            element, _human(c["field"]),
                            (" en %s" % unit) if unit else "", r["reference"]),
                    },
                })
    return fields


def _human(field):
    return field.rsplit(".", 1)[-1].replace("_", " ").replace("mm", "").replace("m2", "").strip()


def _set_path(container, dotted, value):
    parts = dotted.split(".")
    node = container
    for p in parts[:-1]:
        nxt = node.get(p)
        if not isinstance(nxt, dict) or "value" in nxt:
            nxt = {}
            node[p] = nxt
        node = nxt
    node[parts[-1]] = value


def apply_entries(app, entries, reviewer="manual"):
    """Merge manual entries into a COPY of the application.

    entries: [{"element": <id or None>, "field": <dotted>, "value": <scalar>}]
    An element of None (or "application"/building-level) writes to the root.
    Returns the updated application dict."""
    import copy
    out = copy.deepcopy(app)
    for e in entries:
        env = {"value": e["value"], "confidence": 1.0,
               "source": "manual entry by %s" % reviewer}
        element = e.get("element")
        target = None
        if element in (None, "", "application", "building"):
            target = out
        else:
            for coll in ("spaces", "stairs", "guards", "raised_surfaces", "floor_areas"):
                for item in out.get(coll, []):
                    iid = item.get("id") or item.get("name")
                    if iid == element:
                        target = item
                        break
                if target is not None:
                    break
        if target is None:
            target = out  # fall back to root (building-level datum)
        _set_path(target, e["field"], env)
    return out
