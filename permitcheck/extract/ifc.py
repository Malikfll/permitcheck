"""IFC (ISO 16739 STEP / SPF) extraction adapter.

A minimal, dependency-free STEP physical file parser plus a *data dictionary*
that maps IFC entities and property sets to PermitCheck engine fields
(essential outcome: "mappings to Industry Foundation Classes objects in a
data dictionary"). Production Phase 2 would swap the parser for ifcopenshell;
the data dictionary and downstream pipeline stay unchanged.

Assumes IFC4 argument layouts and project length unit = millimetre (the
common convention), which is why space heights carry a 0.001 factor.
"""

import re
from . import CONFIDENCE


# --------------------------------------------------------------------- #
# STEP physical file parsing
# --------------------------------------------------------------------- #
class Entity:
    __slots__ = ("id", "type", "args")

    def __init__(self, eid, etype, args):
        self.id, self.type, self.args = eid, etype, args


class Ref:
    __slots__ = ("id",)

    def __init__(self, eid):
        self.id = eid


class Typed:
    """A typed value such as IFCLENGTHMEASURE(2420.)"""
    __slots__ = ("type", "value")

    def __init__(self, t, v):
        self.type, self.value = t, v


def _parse_args(text, pos):
    """Recursive-descent parse of a STEP argument list starting at '('."""
    assert text[pos] == "("
    pos += 1
    out = []
    while True:
        while pos < len(text) and text[pos] in " ,\r\n\t":
            pos += 1
        ch = text[pos]
        if ch == ")":
            return out, pos + 1
        if ch == "(":
            sub, pos = _parse_args(text, pos)
            out.append(sub)
        elif ch == "'":
            j = pos + 1
            buf = []
            while True:
                if text[j] == "'":
                    if j + 1 < len(text) and text[j + 1] == "'":
                        buf.append("'")
                        j += 2
                        continue
                    break
                buf.append(text[j])
                j += 1
            out.append("".join(buf))
            pos = j + 1
        elif ch == "#":
            m = re.match(r"#(\d+)", text[pos:])
            out.append(Ref(int(m.group(1))))
            pos += m.end()
        elif ch == ".":
            m = re.match(r"\.([A-Z0-9_]+)\.", text[pos:])
            token = m.group(1)
            out.append(True if token == "T" else False if token == "F" else token)
            pos += m.end()
        elif ch in "$*":
            out.append(None)
            pos += 1
        elif ch.isalpha():
            m = re.match(r"([A-Z0-9_]+)\s*\(", text[pos:])
            sub, pos2 = _parse_args(text, pos + m.end() - 1)
            out.append(Typed(m.group(1), sub[0] if len(sub) == 1 else sub))
            pos = pos2
        else:
            m = re.match(r"[-+0-9.Ee]+", text[pos:])
            raw = m.group(0)
            out.append(float(raw) if any(c in raw for c in ".Ee") else int(raw))
            pos += m.end()


def parse_step(text):
    """Return {id: Entity} for every instance in the DATA section."""
    entities = {}
    for m in re.finditer(r"#(\d+)\s*=\s*([A-Z0-9_]+)\s*(\()", text):
        args, _ = _parse_args(text, m.start(3))
        eid = int(m.group(1))
        entities[eid] = Entity(eid, m.group(2), args)
    return entities


# --------------------------------------------------------------------- #
# Data dictionary: (entity type, pset name, property name) -> engine field
# Third tuple element: multiplicative unit factor (None = pass through/bool).
# --------------------------------------------------------------------- #
DATA_DICTIONARY = {
    ("IFCSPACE", "Qto_SpaceBaseQuantities", "Height"): ("ceiling_height_m", 0.001),
    ("IFCSPACE", "Pset_SpaceCommon", "IsHabitable"): ("habitable", None),
    ("IFCSTAIR", "Pset_StairCommon", "RiserHeight"): ("rise_mm", 1.0),
    ("IFCSTAIR", "Pset_StairCommon", "TreadLength"): ("run_mm", 1.0),
    ("IFCSTAIR", "Pset_StairCommon", "ClearWidth"): ("width_mm", 1.0),
    ("IFCSTAIR", "Pset_StairCommon", "Headroom"): ("headroom_mm", 1.0),
    ("IFCSTAIR", "Pset_PermitCheck", "IsPrivate"): ("private", None),
    ("IFCWINDOW", "Pset_PermitCheck_EgressWindow", "ClearOpeningArea"): ("open_area_m2", None),
    ("IFCWINDOW", "Pset_PermitCheck_EgressWindow", "MinClearDimension"): ("min_dimension_mm", 1.0),
    ("IFCBUILDING", "Pset_BuildingCommon", "NumberOfStoreys"): ("storeys", None),
    ("IFCBUILDING", "Pset_BuildingCommon", "GrossPlannedArea"): ("building_area_m2", None),
    ("IFCBUILDING", "Pset_BuildingCommon", "OccupancyType"): ("major_occupancy", None),
    ("IFCBUILDING", "Pset_BuildingCommon", "SprinklerProtection"): ("sprinklered", None),
    ("IFCBUILDING", "Pset_PermitCheck_Building", "DwellingUnits"): ("dwelling_units", None),
    ("IFCBUILDING", "Pset_PermitCheck_Building", "SecondarySuite"): ("has_secondary_suite", None),
    ("IFCBUILDING", "Pset_PermitCheck_Building", "FuelBurningAppliance"): ("fuel_burning_appliance", None),
    ("IFCBUILDING", "Pset_PermitCheck_Building", "AttachedGarage"): ("attached_garage", None),
}

BEDROOM_KEYWORDS = ("bedroom", "chambre")


def _psets_by_object(entities):
    """object id -> list of (pset_name, {prop: value})."""
    out = {}
    for e in entities.values():
        if e.type != "IFCRELDEFINESBYPROPERTIES":
            continue
        related, pset_ref = e.args[4], e.args[5]
        pset = entities[pset_ref.id]
        if pset.type != "IFCPROPERTYSET":
            continue
        props = {}
        for pref in pset.args[4]:
            p = entities[pref.id]
            if p.type == "IFCPROPERTYSINGLEVALUE":
                val = p.args[2]
                props[p.args[0]] = val.value if isinstance(val, Typed) else val
        for ref in related:
            out.setdefault(ref.id, []).append((pset.args[2], props))
    return out


def _mapped_fields(entity, psets, source):
    fields = {}
    for pset_name, props in psets:
        for prop, raw in props.items():
            key = (entity.type, pset_name, prop)
            if key not in DATA_DICTIONARY:
                continue
            field, factor = DATA_DICTIONARY[key]
            value = raw * factor if (factor is not None and isinstance(raw, (int, float))) else raw
            if isinstance(value, float):
                value = round(value, 4)
            fields[field] = {
                "value": value,
                "confidence": CONFIDENCE["ifc"],
                "source": "%s (%s #%d, %s.%s)" % (source, entity.type, entity.id, pset_name, prop),
            }
    return fields


def extract(path):
    """Extract building / spaces / stairs / egress windows from an IFC file."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        entities = parse_step(fh.read())
    psets = _psets_by_object(entities)
    fname = path.replace("\\", "/").rsplit("/", 1)[-1]

    result = {"building": {}, "spaces": [], "stairs": []}
    windows = []

    for e in entities.values():
        own = psets.get(e.id, [])
        if e.type == "IFCBUILDING":
            result["building"].update(_mapped_fields(e, own, fname))
            if e.args[2]:
                result["building"]["name"] = e.args[2]
        elif e.type == "IFCSPACE":
            name, long_name = e.args[2], e.args[7]
            space = {"id": name, "name": long_name or name,
                     "is_bedroom": any(k in (long_name or "").lower() for k in BEDROOM_KEYWORDS)}
            space.update(_mapped_fields(e, own, fname))
            result["spaces"].append(space)
        elif e.type == "IFCSTAIR":
            stair = {"id": e.args[2], "name": e.args[7] or e.args[2]}
            stair.update(_mapped_fields(e, own, fname))
            result["stairs"].append(stair)
        elif e.type == "IFCWINDOW":
            win = {"id": e.args[2]}
            win.update(_mapped_fields(e, own, fname))
            for pset_name, props in own:
                if pset_name == "Pset_PermitCheck_EgressWindow" and "SpaceName" in props:
                    win["space"] = props["SpaceName"]
            windows.append(win)

    # Attach egress windows to their bedroom spaces.
    by_space = {w.get("space"): w for w in windows}
    for space in result["spaces"]:
        w = by_space.get(space["id"])
        if w:
            space["egress_window"] = {k: v for k, v in w.items()
                                      if k in ("open_area_m2", "min_dimension_mm")}
    return result
