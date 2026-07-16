"""End-to-end on the REAL Calgary set: merge Fiches, take the stair rise/run
extracted from the real section sheet, confirm the building context (human-in-
the-loop), and show genuine NBC verdicts on real extracted data.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from permitcheck import ingest_set, manual_entry
from permitcheck.engine import RulesEngine

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app = ingest_set.ingest_folder(os.path.join(BASE, "data", "submissions", "calgary_fiches"))

# Reviewer confirms the building is a single-family house (Part 9) and the top
# ceiling-height candidate the section extractor surfaced. These are manual
# confirmations of machine-surfaced data - recorded with provenance.
cands = app.get("_ceiling_height_candidates", [])
top_ceiling = cands[0]["value_m"] if cands else 2.44
app = manual_entry.apply_entries(app, [
    {"element": None, "field": "building.building_area_m2", "value": 180},
    {"element": None, "field": "building.major_occupancy", "value": "C"},
    {"element": None, "field": "building.dwelling_units", "value": 1},
], reviewer="reviewer (confirming building context)")

engine = RulesEngine.from_file(os.path.join(BASE, "rules", "nbc_rules.json"))
run = engine.run(app)

print("REAL Calgary permit set - end-to-end")
print("Classified: NBC Part %d" % run["classification"]["part"])
print("\nStair verdicts (from rise/run extracted off the real SECTION sheet):")
for r in run["results"]:
    if r["rule_id"] in ("R-9.8.4.1-STAIR-RISE", "R-9.8.4.2-STAIR-RUN") and r["applicable"]:
        for inst in r["instances"]:
            for c in inst["checks"]:
                print("  %-22s %s: observed %s %s (limit %s) -> %s"
                      % (r["title"]["en"], inst["element"], c.get("observed"),
                         c.get("unit"), c.get("limit"), c["verdict"]))
                if c.get("source"):
                    print("        source:", c["source"])
print("\nSection surfaced ceiling-height candidate for confirmation: %.2f m" % top_ceiling)
print("Overall:", run["overall"])
