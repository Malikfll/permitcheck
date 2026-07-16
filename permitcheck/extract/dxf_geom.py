"""Geometric measurement of CAD entities (DXF) via ezdxf.

Unlike the annotation adapter (dxf.py), this module measures the *drawing
geometry itself*: line segment lengths, polyline perimeters, circle/arc radii,
and - most valuable for compliance - the real measured values of DIMENSION
entities placed by the CAD author. Vector coordinates are exact, so these
measurements carry the highest extraction confidence.

Requires ezdxf (pip install ezdxf). All results are expressed in drawing
units; $INSUNITS from the DXF header identifies the unit (4 = millimetres).
"""

import math

try:
    import ezdxf
    HAVE_EZDXF = True
except ImportError:  # keep the core prototype importable without extras
    HAVE_EZDXF = False

INSUNITS = {0: "unitless", 1: "in", 2: "ft", 4: "mm", 5: "cm", 6: "m"}

CONFIDENCE_VECTOR = 0.99  # exact coordinates; residual risk is semantic, not metric


def _length(p1, p2):
    return math.hypot(p2[0] - p1[0], p2[1] - p1[1])


def measure(path):
    """Measure all supported entities in the modelspace of a real DXF file."""
    if not HAVE_EZDXF:
        raise RuntimeError("ezdxf is required for geometric measurement: pip install ezdxf")
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    unit = INSUNITS.get(doc.header.get("$INSUNITS", 0), "unitless")

    lines, polylines, circles, dimensions = [], [], [], []
    for e in msp:
        kind = e.dxftype()
        if kind == "LINE":
            p1 = (e.dxf.start.x, e.dxf.start.y)
            p2 = (e.dxf.end.x, e.dxf.end.y)
            lines.append({"start": p1, "end": p2, "length": _length(p1, p2),
                          "layer": e.dxf.layer})
        elif kind == "LWPOLYLINE":
            pts = [(p[0], p[1]) for p in e.get_points()]
            per = sum(_length(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
            if e.closed and len(pts) > 1:
                per += _length(pts[-1], pts[0])
            polylines.append({"points": len(pts), "perimeter": per,
                              "closed": bool(e.closed), "layer": e.dxf.layer})
        elif kind in ("CIRCLE", "ARC"):
            circles.append({"type": kind, "radius": e.dxf.radius,
                            "layer": e.dxf.layer})
        elif kind == "DIMENSION":
            try:
                value = e.get_measurement()
                if not isinstance(value, (int, float)):
                    continue  # angular/ordinate dims return vectors
            except Exception:
                continue
            dimensions.append({
                "measurement": round(float(value), 6),
                "dimtype": e.dimtype,
                "text_override": e.dxf.text if e.dxf.text not in ("", "<>") else None,
                "layer": e.dxf.layer,
            })

    return {
        "file": path.replace("\\", "/").rsplit("/", 1)[-1],
        "unit": unit,
        "confidence": CONFIDENCE_VECTOR,
        "counts": {"lines": len(lines), "polylines": len(polylines),
                   "circles_arcs": len(circles), "dimensions": len(dimensions)},
        "lines": lines,
        "polylines": polylines,
        "circles": circles,
        "dimensions": dimensions,
    }
