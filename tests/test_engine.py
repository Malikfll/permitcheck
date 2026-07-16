"""Unit tests for the deterministic rules engine.

Run:  python -m unittest discover tests -v
"""

import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from permitcheck.engine import (  # noqa: E402
    RulesEngine, MEETS, DOES_NOT_MEET, INFO_NOT_AVAILABLE, UNCERTAIN,
)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name):
    with open(os.path.join(BASE, "data", "applications", name), "r", encoding="utf-8") as fh:
        return json.load(fh)


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = RulesEngine.from_file(os.path.join(BASE, "rules", "nbc_rules.json"))
        cls.bungalow = load("APP-2026-0101_bungalow_compliant.json")
        cls.duplex = load("APP-2026-0142_duplex_issues.json")
        cls.office = load("APP-2026-0177_office_part3.json")

    def result(self, run, rule_id):
        return next(r for r in run["results"] if r["rule_id"] == rule_id)

    # ---- typology classification (Parts 3 vs 9) -------------------- #
    def test_classification_part9(self):
        self.assertEqual(self.engine.classify(self.bungalow)["part"], 9)

    def test_classification_part3(self):
        self.assertEqual(self.engine.classify(self.office)["part"], 3)

    def test_part_filtering(self):
        run = self.engine.run(self.bungalow)
        self.assertFalse(self.result(run, "R-3.4.2.1-EXIT-COUNT")["applicable"])
        run3 = self.engine.run(self.office)
        self.assertFalse(self.result(run3, "R-9.5.3.1-CEILING")["applicable"])
        self.assertTrue(self.result(run3, "R-ADM-COMPLETENESS")["applicable"])

    # ---- four verdict categories ------------------------------------ #
    def test_compliant_application_meets(self):
        run = self.engine.run(self.bungalow)
        self.assertEqual(run["overall"], MEETS)
        self.assertEqual(run["summary"][DOES_NOT_MEET], 0)

    def test_violation_detected(self):
        run = self.engine.run(self.duplex)
        ceiling = self.result(run, "R-9.5.3.1-CEILING")
        self.assertEqual(ceiling["verdict"], DOES_NOT_MEET)
        bad = next(i for i in ceiling["instances"] if i["element"] == "S-102")
        self.assertEqual(bad["verdict"], DOES_NOT_MEET)

    def test_missing_data_is_info_not_available(self):
        run = self.engine.run(self.duplex)
        headroom = self.result(run, "R-9.8.2.2-HEADROOM")
        self.assertEqual(headroom["verdict"], INFO_NOT_AVAILABLE)
        alarms = self.result(run, "R-9.10.19.3-SMOKE-ALARMS")
        self.assertEqual(alarms["verdict"], INFO_NOT_AVAILABLE)

    def test_low_confidence_is_uncertain(self):
        run = self.engine.run(self.duplex)
        runrule = self.result(run, "R-9.8.4.2-STAIR-RUN")
        self.assertEqual(runrule["verdict"], UNCERTAIN)

    def test_margin_near_limit_is_uncertain(self):
        app = copy.deepcopy(self.bungalow)
        app["spaces"] = [{"id": "S-X", "habitable": True, "is_bedroom": False,
                          "ceiling_height_m": 2.31}]  # within ±0.02 of the 2.3 m limit
        run = self.engine.run(app)
        self.assertEqual(self.result(run, "R-9.5.3.1-CEILING")["verdict"], UNCERTAIN)

    # ---- RASE exceptions and conditional requirements ---------------- #
    def test_sprinkler_exception_waives_egress_window(self):
        app = copy.deepcopy(self.duplex)
        app["building"]["sprinklered"] = True
        run = self.engine.run(app)
        egress = self.result(run, "R-9.9.10.1-EGRESS-WINDOW")
        self.assertEqual(egress["verdict"], MEETS)
        self.assertTrue(all(i.get("exception_applied") for i in egress["instances"]))

    def test_guard_height_threshold_depends_on_condition(self):
        run = self.engine.run(self.duplex)
        guard = self.result(run, "R-9.8.8.2-GUARD-HEIGHT")
        # balcony at 2 500 mm above grade -> 1 070 mm limit applies, 900 mm fails
        checks = guard["instances"][0]["checks"]
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0]["limit"], 1070)
        self.assertEqual(guard["verdict"], DOES_NOT_MEET)

    def test_computed_limit_exit_width(self):
        run = self.engine.run(self.office)
        width = self.result(run, "R-3.4.3.2-EXIT-WIDTH")
        fa3 = next(i for i in width["instances"] if i["element"] == "FA-3")
        self.assertEqual(fa3["checks"][0]["limit"], 976.0)  # 160 occupants x 6.1 mm
        self.assertEqual(fa3["verdict"], DOES_NOT_MEET)

    # ---- determinism and traceability -------------------------------- #
    def test_determinism_identical_inputs_identical_outputs(self):
        a = self.engine.run(self.duplex)
        b = self.engine.run(self.duplex)
        for key in ("run_id", "input_hash", "summary", "overall"):
            self.assertEqual(a[key], b[key])
        self.assertEqual(
            json.dumps(a["results"], sort_keys=True),
            json.dumps(b["results"], sort_keys=True),
        )

    def test_traceability_fields_present(self):
        run = self.engine.run(self.duplex)
        for key in ("run_id", "timestamp", "engine_version", "ruleset_version", "input_hash"):
            self.assertIn(key, run)
        ceiling = self.result(run, "R-9.5.3.1-CEILING")
        check = ceiling["instances"][0]["checks"][0]
        self.assertIn("source", check)


if __name__ == "__main__":
    unittest.main()
