"""CAD (DXF) extraction adapter.

Reads TEXT/MTEXT entities from the ENTITIES section of an ASCII DXF file and
interprets compliance annotations placed on the ``PC-COMPLIANCE`` layer using
a small deterministic grammar:

    STAIR ST-1 RISE=205 RUN=250 WIDTH=900 HEADROOM=1980
    GUARD G-1 HEIGHT=1100 ABOVE=2600
    SURFACE RS-1 ABOVE=2600 GUARD=YES

All linear values are millimetres. This mirrors how practices already annotate
2D drawings; a Phase 2 adapter adds geometric measurement of polylines and
dimensions (and DWG via ODA/Teigha).
"""

from . import CONFIDENCE

LAYER = "PC-COMPLIANCE"

GRAMMAR = {
    "STAIR": ("stairs", {"RISE": "rise_mm", "RUN": "run_mm",
                          "WIDTH": "width_mm", "HEADROOM": "headroom_mm",
                          "PRIVATE": "private"}),
    "GUARD": ("guards", {"HEIGHT": "height_mm",
                          "ABOVE": "walking_surface_height_above_grade_mm"}),
    "SURFACE": ("raised_surfaces", {"ABOVE": "height_above_adjacent_mm",
                                     "GUARD": "guard_provided"}),
}


def _pairs(path):
    """Yield (group_code, value) pairs from an ASCII DXF file."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = [ln.rstrip("\r\n") for ln in fh]
    for i in range(0, len(lines) - 1, 2):
        try:
            yield int(lines[i].strip()), lines[i + 1]
        except ValueError:
            continue


def _texts(path):
    """(layer, text) for every TEXT/MTEXT entity."""
    texts, current, layer, text = [], None, None, None
    for code, value in _pairs(path):
        if code == 0:
            if current in ("TEXT", "MTEXT") and text is not None:
                texts.append((layer, text))
            current, layer, text = value.strip().upper(), None, None
        elif code == 8:
            layer = value.strip()
        elif code == 1:
            text = value.strip()
    if current in ("TEXT", "MTEXT") and text is not None:
        texts.append((layer, text))
    return texts


def _coerce(raw):
    if raw.upper() in ("YES", "TRUE", "OUI"):
        return True
    if raw.upper() in ("NO", "FALSE", "NON"):
        return False
    try:
        return float(raw) if "." in raw else int(raw)
    except ValueError:
        return raw


def extract(path):
    """Extract stairs / guards / raised surfaces from DXF annotations."""
    fname = path.replace("\\", "/").rsplit("/", 1)[-1]
    result = {}
    for layer, text in _texts(path):
        if (layer or "").upper() != LAYER:
            continue
        tokens = text.split()
        if len(tokens) < 3 or tokens[0].upper() not in GRAMMAR:
            continue
        collection, field_map = GRAMMAR[tokens[0].upper()]
        item = {"id": tokens[1]}
        for tok in tokens[2:]:
            if "=" not in tok:
                continue
            key, raw = tok.split("=", 1)
            field = field_map.get(key.upper())
            if not field:
                continue
            item[field] = {
                "value": _coerce(raw),
                "confidence": CONFIDENCE["dxf"],
                "source": "%s (layer %s: \"%s\")" % (fname, LAYER, text),
            }
        result.setdefault(collection, []).append(item)
    return result
