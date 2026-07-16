import os
import sys

import fitz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pdf = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "data", "submissions", "calgary_fiches", "fiche_17_section.pdf")
doc = fitz.open(pdf)
pg = doc[0]
print("page size (pts):", round(pg.rect.width), "x", round(pg.rect.height))
d = pg.get_text("dict")
for b in d["blocks"]:
    for l in b.get("lines", []):
        vert = abs(l.get("dir", (1, 0))[1]) > abs(l.get("dir", (1, 0))[0])
        for s in l["spans"]:
            t = s["text"].strip()
            if "RUN" in t.upper() or "RISE" in t.upper():
                bb = s["bbox"]
                print("  %-16r vert=%s bbox=(%.0f,%.0f,%.0f,%.0f)"
                      % (t, vert, bb[0], bb[1], bb[2], bb[3]))
