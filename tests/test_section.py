"""Tests for section-sheet extraction (permitcheck.extract.section):
the imperial parser and stair/ceiling extraction on the real Calgary section.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from permitcheck.extract import section  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_SECTION = os.path.join(BASE, "data", "submissions", "calgary_fiches",
                            "fiche_17_section.pdf")


class ImperialParserTests(unittest.TestCase):
    def test_feet_inches(self):
        self.assertAlmostEqual(section.imperial_to_mm("9'-0\""), 2743.2, places=1)
        self.assertAlmostEqual(section.imperial_to_mm("8'-1\""), 2463.8, places=1)

    def test_inches_only_with_fraction(self):
        self.assertAlmostEqual(section.imperial_to_mm("7 1/4\""), 184.1, places=1)
        self.assertAlmostEqual(section.imperial_to_mm("10\""), 254.0, places=1)

    def test_feet_inches_fraction(self):
        self.assertAlmostEqual(section.imperial_to_mm("9'-0 3/4\""), 2762.3, places=1)

    def test_unicode_fraction(self):
        self.assertAlmostEqual(section.imperial_to_mm("9'-0¾\""), 2762.3, places=1)

    def test_non_dimension_returns_none(self):
        self.assertIsNone(section.imperial_to_mm("BASEMENT"))


@unittest.skipUnless(os.path.exists(REAL_SECTION), "real section Fiche not present")
class RealSectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = section.extract(REAL_SECTION)

    def test_stair_rise_run_extracted_from_labels(self):
        rise = self.data["stair"]["rise_mm"]
        run = self.data["stair"]["run_mm"]
        self.assertIsNotNone(rise)
        self.assertIsNotNone(run)
        self.assertEqual(rise["value"], 184)      # 7 1/4"
        self.assertEqual(run["value"], 254)       # 10"
        self.assertGreaterEqual(rise["confidence"], 0.9)
        self.assertIn("RISE", rise["source"].upper())

    def test_ceiling_candidates_surfaced_and_ranked(self):
        cands = self.data["ceiling_height_candidates"]
        self.assertTrue(cands)
        # every candidate is in the plausible ceiling range
        for c in cands:
            self.assertGreaterEqual(c["value_mm"], 2000)
            self.assertLessEqual(c["value_mm"], 3500)
        # unqualified storey heights rank above "UNDER BEAM"/"BASEMENT" clearances
        noted = [c for c in cands if c["note"]]
        unnoted = [c for c in cands if not c["note"]]
        if noted and unnoted:
            self.assertLess(cands.index(unnoted[0]), cands.index(noted[0]))


if __name__ == "__main__":
    unittest.main()
