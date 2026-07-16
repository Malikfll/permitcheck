"""Tests for NBC ingestion from the real BC-digitalized code JSON.

The real-corpus tests run only when data/bc_code/BuildingCode.json is present
(downloaded from github.com/bcgov/BC-Building-Code); otherwise they skip. The
extraction/mapping logic is also covered on small inline samples.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from permitcheck import codes_nbc  # noqa: E402
from permitcheck.engine import RulesEngine  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE = os.path.join(BASE, "data", "bc_code", "BuildingCode.json")


class PatternTests(unittest.TestCase):
    def test_requirement_phrasings(self):
        stub_ge = None
        for rx, op in codes_nbc.PATTERNS:
            m = rx.search("shall be not less than 2.3 m")
            if m:
                stub_ge = op
                break
        self.assertEqual(stub_ge, "ge")

    def test_clean_strips_ref_tags(self):
        txt = codes_nbc._clean("a new [REF:term:bldng:building] shall be [REF:internal:x:marked]")
        self.assertNotIn("[REF", txt)
        self.assertIn("building", txt)

    def test_field_map_plausibility_filter(self):
        # a stub mentioning ceiling but with an implausible 1 mm value is rejected
        stubs = [{"id": "NBC-x", "reference": "r", "article_title": "Ceiling Height",
                  "source_text": "gap of 1 mm near the ceiling",
                  "draft_requirement": {"op": "le", "value": 1, "unit": "mm"}},
                 {"id": "NBC-9.5.3.1", "reference": "r", "article_title": "Ceiling Heights of Rooms",
                  "source_text": "ceiling height not less than 2.1 m",
                  "draft_requirement": {"op": "ge", "value": 2.1, "unit": "m"}}]
        live = codes_nbc.map_fields(stubs)
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0]["field"], "ceiling_height_m")
        self.assertEqual(live[0]["value"], 2.1)


@unittest.skipUnless(os.path.exists(CODE), "BC/NBC code JSON not downloaded")
class RealCodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = codes_nbc.extract_requirements(CODE)
        cls.live = codes_nbc.map_fields(cls.res["stubs"])

    def test_many_real_requirements_extracted(self):
        self.assertGreater(self.res["total_sentences"], 4000)
        self.assertGreater(len(self.res["stubs"]), 300)

    def test_extraction_matches_known_provisions(self):
        # the ingester must independently find the real stair-width and guard
        # values that were hand-authored earlier
        vals = {(r["field"], r["value"]) for r in self.live}
        self.assertIn(("width_mm", 860), vals)     # NBC 9.8.2.1 private stair
        self.assertIn(("height_mm", 900), vals)    # NBC 9.8.8.3 guard

    def test_auto_rules_run_live(self):
        picked, seen = [], set()
        for r in self.live:
            k = (r["field"], r["op"], r["value"])
            if k not in seen:
                seen.add(k)
                picked.append(r)
        doc = codes_nbc.to_engine_ruleset(picked)
        engine = RulesEngine(doc)
        import json
        with open(os.path.join(BASE, "data", "applications",
                               "APP-2026-0101_bungalow_compliant.json"), encoding="utf-8") as fh:
            app = json.load(fh)
        run = engine.run(app)
        applicable = [r for r in run["results"] if r["applicable"]]
        self.assertTrue(applicable)
        self.assertTrue(all(r["verdict"] in ("MEETS", "DOES_NOT_MEET", "UNCERTAIN",
                                             "INFO_NOT_AVAILABLE") for r in applicable))


if __name__ == "__main__":
    unittest.main()
