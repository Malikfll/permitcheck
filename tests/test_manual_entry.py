"""Tests for dynamic manual data entry (permitcheck.manual_entry)."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from permitcheck import manual_entry  # noqa: E402
from permitcheck.engine import RulesEngine, INFO_NOT_AVAILABLE  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name):
    with open(os.path.join(BASE, "data", "applications", name), encoding="utf-8") as fh:
        return json.load(fh)


class ManualEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = RulesEngine.from_file(os.path.join(BASE, "rules", "nbc_rules.json"))
        cls.app = load("APP-2026-0142_duplex_issues.json")  # has missing headroom + alarms
        cls.baseline = cls.engine.run(cls.app)

    def test_form_generated_from_info_not_available(self):
        form = manual_entry.missing_fields(self.baseline)
        self.assertTrue(form)
        # every form field corresponds to a real INFO_NOT_AVAILABLE datum
        fields = {(f["element"], f["field"]) for f in form}
        self.assertIn(("ST-1", "headroom_mm"), fields)
        for f in form:
            self.assertIn("prompt", f)
            self.assertIn("reference", f)

    def test_form_shrinks_as_data_is_provided(self):
        form = manual_entry.missing_fields(self.baseline)
        n_before = len(form)
        # supply the stair headroom manually
        app2 = manual_entry.apply_entries(self.app,
                                          [{"element": "ST-1", "field": "headroom_mm", "value": 2000}],
                                          reviewer="tester")
        run2 = self.engine.run(app2)
        form2 = manual_entry.missing_fields(run2)
        self.assertLess(len(form2), n_before)
        self.assertNotIn(("ST-1", "headroom_mm"),
                         {(f["element"], f["field"]) for f in form2})

    def test_manual_value_changes_verdict_and_records_provenance(self):
        app2 = manual_entry.apply_entries(self.app,
                                          [{"element": "ST-1", "field": "headroom_mm", "value": 2000}],
                                          reviewer="A. Reviewer")
        stair = next(s for s in app2["stairs"] if s["id"] == "ST-1")
        self.assertEqual(stair["headroom_mm"]["value"], 2000)
        self.assertIn("manual entry by A. Reviewer", stair["headroom_mm"]["source"])
        self.assertEqual(stair["headroom_mm"]["confidence"], 1.0)
        run2 = self.engine.run(app2)
        headroom = next(r for r in run2["results"] if r["rule_id"] == "R-9.8.2.2-HEADROOM")
        self.assertNotEqual(headroom["verdict"], INFO_NOT_AVAILABLE)

    def test_nested_field_entry(self):
        # a bedroom missing its egress window sub-fields
        app = {"spaces": [{"id": "S-1", "is_bedroom": True, "habitable": True,
                           "ceiling_height_m": 2.5}], "building": {"storeys": 1}}
        app2 = manual_entry.apply_entries(app, [
            {"element": "S-1", "field": "egress_window.open_area_m2", "value": 0.5}])
        self.assertEqual(app2["spaces"][0]["egress_window"]["open_area_m2"]["value"], 0.5)

    def test_original_app_not_mutated(self):
        before = json.dumps(self.app, sort_keys=True)
        manual_entry.apply_entries(self.app,
                                   [{"element": "ST-1", "field": "headroom_mm", "value": 2000}])
        self.assertEqual(before, json.dumps(self.app, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
