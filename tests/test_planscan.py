"""Tests for dynamic real-plan analysis (permitcheck.extract.planscan).

The heavy end-to-end assertion runs only when the real school-plan PDF is
present in data/real/ and Tesseract is installed; otherwise it skips. The
resolution-independent and RFI logic is tested without external files.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from permitcheck.extract.raster import HAVE_CV2  # noqa: E402
from permitcheck.extract.ocr import HAVE_TESSERACT  # noqa: E402
from permitcheck.extract import planscan  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_PDF = os.path.join(BASE, "data", "real", "drawing_1344536052.png")


class CompletenessTests(unittest.TestCase):
    def test_rfi_lists_missing_when_only_areas_present(self):
        comp = planscan.analyze_completeness({"rooms": [{"area_sf": 800, "name": "CR", "number": "125"}]})
        self.assertIn("room_areas", comp["present"])
        self.assertIn("ceiling_heights", comp["missing"])
        self.assertIn("stair_geometry", comp["missing"])
        items = {r["item"] for r in comp["request_for_information"]}
        self.assertEqual(items, set(comp["missing"]))

    def test_occupant_load_from_areas(self):
        rooms = [{"area_sf": 800, "name": "CR", "number": "125"},
                 {"area_sf": 4323, "name": "GYMNASIUM", "number": "187"}]
        ol = planscan.compute_occupant_load(rooms)
        self.assertIn("classroom", ol["by_type"])
        self.assertIn("assembly", ol["by_type"])
        self.assertGreater(ol["total_occupant_load"], 0)

    def test_classification(self):
        rooms = [{"name": "CR"}, {"name": "GYMNASIUM"}, {"name": "TOILET"}, {"name": "OFFICE"}]
        types = planscan.classify_rooms(rooms)
        self.assertEqual(types["classroom"], 1)
        self.assertEqual(types["assembly"], 1)
        self.assertEqual(types["service"], 1)
        self.assertEqual(types["office"], 1)


@unittest.skipUnless(HAVE_CV2 and HAVE_TESSERACT and os.path.exists(REAL_PDF),
                     "real plan or Tesseract not available")
class RealPlanTests(unittest.TestCase):
    def test_extracts_many_real_rooms(self):
        res = planscan.analyze_plan(REAL_PDF)
        rooms = res["schedule"]["rooms"]
        self.assertGreater(len(rooms), 80)              # ~130 rooms on the sheet
        self.assertGreater(res["schedule"]["total_area_sf"], 30000)
        # resolution detected dynamically, not hardcoded
        self.assertGreater(max(res["schedule"]["resolution"]), 3000)
        # real occupant load computed from real areas
        self.assertGreater(res["occupant_load"]["total_occupant_load"], 100)
        # RFI raised for the dimensional data this plan sheet lacks
        self.assertIn("ceiling_heights", res["completeness"]["missing"])


if __name__ == "__main__":
    unittest.main()
