"""Generate the sample submission files for APP-2026-0201 (house with a
secondary suite): a real IFC4 STEP file, an ASCII DXF drawing with compliance
annotations, and a vector-text PDF permit form. Kept in the repo so the
fixtures are reproducible:  python tools/make_samples.py
"""

import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "data", "submissions", "APP-2026-0201")


# --------------------------------------------------------------------- #
def make_ifc():
    lines = [
        "ISO-10303-21;",
        "HEADER;",
        "FILE_DESCRIPTION(('PermitCheck sample'),'2;1');",
        "FILE_NAME('model.ifc','2026-07-07',('PermitCheck'),('NRC ISC prototype'),'','','');",
        "FILE_SCHEMA(('IFC4'));",
        "ENDSEC;",
        "DATA;",
        "#1=IFCPROJECT('0PrjGuid0000000000000A',$,'APP-2026-0201',$,$,$,$,$,$);",
        # Building with standard + prototype psets
        "#20=IFCBUILDING('0BldGuid0000000000000A',$,'Two-storey house with secondary suite',$,$,$,$,$,.ELEMENT.,$,$,$);",
        "#21=IFCPROPERTYSINGLEVALUE('NumberOfStoreys',$,IFCINTEGER(2),$);",
        "#22=IFCPROPERTYSINGLEVALUE('GrossPlannedArea',$,IFCAREAMEASURE(210.),$);",
        "#23=IFCPROPERTYSINGLEVALUE('OccupancyType',$,IFCLABEL('C'),$);",
        "#24=IFCPROPERTYSINGLEVALUE('SprinklerProtection',$,IFCBOOLEAN(.F.),$);",
        "#25=IFCPROPERTYSET('0Pse0000000000000000AA',$,'Pset_BuildingCommon',$,(#21,#22,#23,#24));",
        "#26=IFCRELDEFINESBYPROPERTIES('0Rel0000000000000000AA',$,$,$,(#20),#25);",
        "#27=IFCPROPERTYSINGLEVALUE('DwellingUnits',$,IFCINTEGER(2),$);",
        "#28=IFCPROPERTYSINGLEVALUE('SecondarySuite',$,IFCBOOLEAN(.T.),$);",
        "#29=IFCPROPERTYSINGLEVALUE('FuelBurningAppliance',$,IFCBOOLEAN(.T.),$);",
        "#30=IFCPROPERTYSINGLEVALUE('AttachedGarage',$,IFCBOOLEAN(.F.),$);",
        "#31=IFCPROPERTYSET('0Pse0000000000000000AB',$,'Pset_PermitCheck_Building',$,(#27,#28,#29,#30));",
        "#32=IFCRELDEFINESBYPROPERTIES('0Rel0000000000000000AB',$,$,$,(#20),#31);",
    ]
    eid = 100

    def space(sid, long_name, height_mm, habitable=True):
        nonlocal eid
        s, p1, p2, ps1, ps2, r1, r2 = eid, eid + 1, eid + 2, eid + 3, eid + 4, eid + 5, eid + 6
        eid += 10
        lines.extend([
            "#%d=IFCSPACE('0Spc%018d',$,'%s',$,$,$,$,'%s',.ELEMENT.,.INTERNAL.,$);"
            % (s, s, sid, long_name),
            "#%d=IFCPROPERTYSINGLEVALUE('Height',$,IFCLENGTHMEASURE(%s.),$);" % (p1, height_mm),
            "#%d=IFCPROPERTYSET('0Pse%018d',$,'Qto_SpaceBaseQuantities',$,(#%d));" % (ps1, ps1, p1),
            "#%d=IFCRELDEFINESBYPROPERTIES('0Rel%018d',$,$,$,(#%d),#%d);" % (r1, r1, s, ps1),
            "#%d=IFCPROPERTYSINGLEVALUE('IsHabitable',$,IFCBOOLEAN(.%s.),$);" % (p2, "T" if habitable else "F"),
            "#%d=IFCPROPERTYSET('0Pse%018d',$,'Pset_SpaceCommon',$,(#%d));" % (ps2, ps2, p2),
            "#%d=IFCRELDEFINESBYPROPERTIES('0Rel%018d',$,$,$,(#%d),#%d);" % (r2, r2, s, ps2),
        ])

    def window(wid, space_id, area_m2, min_dim_mm):
        nonlocal eid
        w, p1, p2, p3, ps, r = eid, eid + 1, eid + 2, eid + 3, eid + 4, eid + 5
        eid += 10
        lines.extend([
            "#%d=IFCWINDOW('0Win%018d',$,'%s',$,$,$,$,$,$,$,$,$,$);" % (w, w, wid),
            "#%d=IFCPROPERTYSINGLEVALUE('ClearOpeningArea',$,IFCAREAMEASURE(%s),$);" % (p1, area_m2),
            "#%d=IFCPROPERTYSINGLEVALUE('MinClearDimension',$,IFCLENGTHMEASURE(%s.),$);" % (p2, min_dim_mm),
            "#%d=IFCPROPERTYSINGLEVALUE('SpaceName',$,IFCLABEL('%s'),$);" % (p3, space_id),
            "#%d=IFCPROPERTYSET('0Pse%018d',$,'Pset_PermitCheck_EgressWindow',$,(#%d,#%d,#%d));"
            % (ps, ps, p1, p2, p3),
            "#%d=IFCRELDEFINESBYPROPERTIES('0Rel%018d',$,$,$,(#%d),#%d);" % (r, r, w, ps),
        ])

    space("S-01", "Main living room", 2440)
    space("S-02", "Main bedroom", 2440)
    space("S-03", "Suite bedroom (basement)", 2320)
    space("S-04", "Suite kitchen-living", 2350)
    window("W-01", "S-02", "0.42", 500)
    window("W-02", "S-03", "0.38", 400)

    # Stair: rise/run/width from BIM; headroom comes from the DXF drawing.
    lines.extend([
        "#500=IFCSTAIR('0Str000000000000000500',$,'ST-1',$,$,$,$,'Main interior stair',.STRAIGHT_RUN_STAIR.);",
        "#501=IFCPROPERTYSINGLEVALUE('RiserHeight',$,IFCLENGTHMEASURE(195.),$);",
        "#502=IFCPROPERTYSINGLEVALUE('TreadLength',$,IFCLENGTHMEASURE(250.),$);",
        "#503=IFCPROPERTYSINGLEVALUE('ClearWidth',$,IFCLENGTHMEASURE(900.),$);",
        "#504=IFCPROPERTYSET('0Pse000000000000000504',$,'Pset_StairCommon',$,(#501,#502,#503));",
        "#505=IFCRELDEFINESBYPROPERTIES('0Rel000000000000000505',$,$,$,(#500),#504);",
        "#506=IFCPROPERTYSINGLEVALUE('IsPrivate',$,IFCBOOLEAN(.T.),$);",
        "#507=IFCPROPERTYSET('0Pse000000000000000507',$,'Pset_PermitCheck',$,(#506));",
        "#508=IFCRELDEFINESBYPROPERTIES('0Rel000000000000000508',$,$,$,(#500),#507);",
        "ENDSEC;",
        "END-ISO-10303-21;",
    ])
    with open(os.path.join(OUT, "model.ifc"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# --------------------------------------------------------------------- #
def make_dxf():
    annotations = [
        "STAIR ST-1 HEADROOM=1980 PRIVATE=YES",
        "GUARD G-1 HEIGHT=1100 ABOVE=2600",
        "SURFACE RS-1 ABOVE=2600 GUARD=YES",
        "SURFACE RS-2 ABOVE=450 GUARD=NO",
    ]
    pairs = [("0", "SECTION"), ("2", "ENTITIES")]
    y = 400.0
    for text in annotations:
        pairs += [("0", "TEXT"), ("8", "PC-COMPLIANCE"),
                  ("10", "60.0"), ("20", str(y)), ("40", "3.5"), ("1", text)]
        y -= 20.0
    pairs += [("0", "ENDSEC"), ("0", "EOF")]
    with open(os.path.join(OUT, "plans.dxf"), "w", encoding="utf-8") as fh:
        for code, value in pairs:
            fh.write(code + "\n" + value + "\n")


# --------------------------------------------------------------------- #
def make_pdf():
    form_lines = [
        "BUILDING PERMIT APPLICATION FORM",
        "APPLICATION NO: APP-2026-0201",
        "MUNICIPALITY: Whitehorse, YT",
        "APPLICANT: Northern Craft Builders Ltd.",
        "LEGAL DESCRIPTION: Lot 22, Block 3, Plan 8899 CLSR YT",
        "DWELLING UNITS: 2",
        "SECONDARY SUITE: YES",
        "SMOKE ALARM EACH STOREY: YES",
        "SMOKE ALARM EACH BEDROOM: YES",
        "SMOKE ALARMS INTERCONNECTED BETWEEN SUITES: NO",
        "CO ALARM PROVIDED: YES",
        "FUEL BURNING APPLIANCE: YES",
        "ATTACHED GARAGE: NO",
    ]
    def esc(s):
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    content = "BT /F1 11 Tf 50 760 Td 16 TL\n"
    content += "".join("(%s) Tj T*\n" % esc(ln) for ln in form_lines)
    content += "ET\n"
    content_b = content.encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content_b), content_b),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, xref_pos))
    with open(os.path.join(OUT, "permit_form.pdf"), "wb") as fh:
        fh.write(bytes(out))


# --------------------------------------------------------------------- #
def make_manifest():
    manifest = {
        "application": {
            "id": "APP-2026-0201",
            "municipality": "Whitehorse, YT",
            "applicant": "Northern Craft Builders Ltd.",
            "submitted": "2026-07-06",
            "code": "NBC-2020",
        },
        "files": [
            {"path": "model.ifc", "role": "bim"},
            {"path": "plans.dxf", "role": "cad"},
            {"path": "permit_form.pdf", "role": "form"},
        ],
    }
    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    make_ifc()
    make_dxf()
    make_pdf()
    make_manifest()
    print("Sample submission written to", OUT)
