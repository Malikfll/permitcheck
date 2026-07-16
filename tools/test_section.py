import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from permitcheck.extract import section

r = section.extract(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "data", "submissions", "calgary_fiches", "fiche_17_section.pdf"))
print("STAIR (extracted from the real section, explicitly labelled):")
print("  rise:", r["stair"]["rise_mm"])
print("  run :", r["stair"]["run_mm"])
print("CEILING-HEIGHT CANDIDATES (surfaced for human confirmation):")
for c in r["ceiling_height_candidates"][:8]:
    print("  %.3f m  from '%s'  note=%s  conf=%.2f"
          % (c["value_m"], c["text"], c["note"], c["confidence"]))
print("parser checks:")
for t in ['9\'-0"', '7 1/4"', '10"', '8\'-1"', '9\'-0 3/4"', '9\'-0¾"']:
    print("  %-12r -> %s mm" % (t, section.imperial_to_mm(t)))
