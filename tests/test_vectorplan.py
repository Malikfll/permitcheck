"""Tests for vector-PDF plan extraction, incl. riser-notation stair detection."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from permitcheck.extract import vectorplan  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLOOR_PLAN = os.path.join(BASE, "data", "submissions", "calgary_fiches",
                          "fiche_15_main_floor_plan_3.pdf")


class RiserRegexTests(unittest.TestCase):
    def test_riser_pattern(self):
        self.assertTrue(vectorplan._RISER_RE.match("15R"))
        self.assertTrue(vectorplan._RISER_RE.match("7R"))
        self.assertIsNone(vectorplan._RISER_RE.match("STAIR"))
        self.assertIsNone(vectorplan._RISER_RE.match("R15"))

    def test_updn_pattern(self):
        self.assertTrue(vectorplan._UPDN_RE.match("UP"))
        self.assertTrue(vectorplan._UPDN_RE.match("DN"))
        self.assertIsNone(vectorplan._UPDN_RE.match("DOWNSTAIRS"))


@unittest.skipUnless(os.path.exists(FLOOR_PLAN), "floor-plan Fiche not present")
class RealPlanStairTests(unittest.TestCase):
    def test_stairs_detected_via_riser_notation(self):
        data = vectorplan.extract(FLOOR_PLAN)
        # this plan labels its stair "UP 15R" / "DN 15R" (no literal "STAIR" word)
        self.assertGreaterEqual(data["summary"]["stairs"], 1)
        labels = [s["label"] for s in data["stairs"]]
        self.assertTrue(any("15R" in l for l in labels),
                        "expected a riser-count stair label, got %s" % labels)
        for s in data["stairs"]:
            if "risers" in s:
                self.assertEqual(s["risers"], 15)


if __name__ == "__main__":
    unittest.main()
