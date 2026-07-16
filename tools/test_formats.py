import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from permitcheck.extract import semantic, section

print("METRIC callouts via semantic._ceiling_height_m (floor-plan tags):")
for t in ["CLG 2440", "CH 2440", "CH 2.44", "2440 CH", "CLG 2.44", "C/H 2440",
          "CEILING 2440", "9'-0\" CLG", "CLG 8'-0\"", "2440 CLG"]:
    print("  %-14r -> %s" % (t, semantic._ceiling_height_m(t)))

print("\nIMPERIAL dimensions via section.imperial_to_mm (section sheets):")
for t in ['9\'-0"', '9\'-0¾"', '9\'-0 3/4"', "8'-1\"", '7 1/4"', '10"']:
    print("  %-12r -> %s mm" % (t, section.imperial_to_mm(t)))
