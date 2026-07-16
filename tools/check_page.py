import os
import sys

import fitz

pdf = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "data", "submissions", "calgary_fiches", "fiche_17_section.pdf")
pg = fitz.open(pdf)[0]
print("rotation:", pg.rotation)
print("mediabox:", pg.mediabox)
print("cropbox:", pg.cropbox)
print("rect:", pg.rect)
print("cropbox_position:", pg.cropbox_position)
# find a RUN span and print its bbox + the char origins (rawdict)
d = pg.get_text("rawdict")
for b in d["blocks"]:
    for l in b.get("lines", []):
        for s in l["spans"]:
            txt = "".join(c["c"] for c in s["chars"]).strip()
            if txt == '10" RUN' or "10\" RUN" in txt:
                print("span bbox:", [round(v) for v in s["bbox"]], "dir:", l.get("dir"))
                print("  first char origin:", [round(v) for v in s["chars"][0]["origin"]],
                      "bbox:", [round(v) for v in s["chars"][0]["bbox"]])
                print("  last char origin:", [round(v) for v in s["chars"][-1]["origin"]])
