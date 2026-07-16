"""Tests for extraction adapters, fine-grained typology, OR models, and the
additional-outcome modules (BCF, registry, codesync, benchmark).

Run:  python -m unittest discover tests -v
"""

import io
import itertools
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from permitcheck.engine import RulesEngine, MEETS, DOES_NOT_MEET, UNCERTAIN  # noqa: E402
from permitcheck.extract import pipeline  # noqa: E402
from permitcheck import orx, bcf, codesync, benchmark  # noqa: E402
from permitcheck.registry import SubmissionRegistry  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUBMISSION = os.path.join(BASE, "data", "submissions", "APP-2026-0201")


def load(name):
    with open(os.path.join(BASE, "data", "applications", name), "r", encoding="utf-8") as fh:
        return json.load(fh)


class ExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = pipeline.process_submission(SUBMISSION)
        cls.engine = RulesEngine.from_file(os.path.join(BASE, "rules", "nbc_rules.json"))
        cls.checkrun = cls.engine.run(cls.app)

    def result(self, rule_id):
        return next(r for r in self.checkrun["results"] if r["rule_id"] == rule_id)

    def test_ifc_building_extraction(self):
        b = self.app["building"]
        self.assertEqual(b["storeys"]["value"], 2)
        self.assertEqual(b["major_occupancy"]["value"], "C")
        self.assertTrue(b["has_secondary_suite"]["value"])
        self.assertEqual(b["storeys"]["confidence"], 0.98)
        self.assertIn("model.ifc", b["storeys"]["source"])

    def test_ifc_space_height_unit_conversion(self):
        s3 = next(s for s in self.app["spaces"] if s["id"] == "S-03")
        self.assertAlmostEqual(s3["ceiling_height_m"]["value"], 2.32)
        self.assertTrue(s3["is_bedroom"])

    def test_egress_window_attached_to_space(self):
        s3 = next(s for s in self.app["spaces"] if s["id"] == "S-03")
        self.assertAlmostEqual(s3["egress_window"]["open_area_m2"]["value"], 0.38)

    def test_dxf_merges_into_ifc_stair(self):
        stair = self.app["stairs"][0]
        self.assertEqual(stair["rise_mm"]["value"], 195)          # from IFC
        self.assertEqual(stair["headroom_mm"]["value"], 1980)     # from DXF
        self.assertEqual(stair["headroom_mm"]["confidence"], 0.90)
        self.assertIn("plans.dxf", stair["headroom_mm"]["source"])

    def test_pdf_form_extraction(self):
        fs = self.app["fire_safety"]
        self.assertFalse(fs["smoke_alarms_interconnected"]["value"])
        self.assertEqual(fs["smoke_alarms_interconnected"]["confidence"], 0.85)
        self.assertEqual(self.app["application"]["legal_description"],
                         "Lot 22, Block 3, Plan 8899 CLSR YT")

    def test_documents_fingerprinted(self):
        docs = self.app["application"]["documents"]
        self.assertEqual(len(docs), 3)
        self.assertTrue(all(len(d["sha256"]) == 64 for d in docs))

    def test_end_to_end_verdicts(self):
        self.assertEqual(self.result("R-9.5.3.1-CEILING")["verdict"], UNCERTAIN)
        self.assertEqual(self.result("R-9.8.4.2-STAIR-RUN")["verdict"], DOES_NOT_MEET)
        self.assertEqual(self.result("R-9.10.19.5-SS-INTERCONNECT")["verdict"], DOES_NOT_MEET)
        self.assertEqual(self.result("R-9.8.8.2-GUARD-HEIGHT")["verdict"], MEETS)


class TypologyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = RulesEngine.from_file(os.path.join(BASE, "rules", "nbc_rules.json"))

    def test_house_subtype(self):
        c = self.engine.classify(load("APP-2026-0101_bungalow_compliant.json"))
        self.assertEqual((c["part"], c["subtype"]), (9, "house"))

    def test_multi_dwelling_subtype(self):
        c = self.engine.classify(load("APP-2026-0142_duplex_issues.json"))
        self.assertEqual((c["part"], c["subtype"]), (9, "small_multi_dwelling"))

    def test_secondary_suite_subtype(self):
        c = self.engine.classify(pipeline.process_submission(SUBMISSION))
        self.assertEqual(c["subtype"], "house_with_secondary_suite")

    def test_nbc_3_2_2_article(self):
        c = self.engine.classify(load("APP-2026-0177_office_part3.json"))
        self.assertEqual((c["part"], c["nbc_3_2_2"]), (3, "3.2.2.58"))

    def test_typology_scoped_rule_skipped_for_house(self):
        run = self.engine.run(load("APP-2026-0101_bungalow_compliant.json"))
        ss = next(r for r in run["results"] if r["rule_id"] == "R-9.10.19.5-SS-INTERCONNECT")
        self.assertFalse(ss["applicable"])


class HungarianTests(unittest.TestCase):
    def brute_force(self, cost):
        n, m = len(cost), len(cost[0])
        best = None
        for perm in itertools.permutations(range(m), n):
            total = sum(cost[i][perm[i]] for i in range(n))
            if best is None or total < best:
                best = total
        return best

    def test_matches_brute_force(self):
        matrices = [
            [[4, 1, 3], [2, 0, 5], [3, 2, 2]],
            [[10, 19, 8, 15], [10, 18, 7, 17], [13, 16, 9, 14], [12, 19, 8, 18]],
            [[7, 3], [2, 9], [5, 5]][:2],                   # 2x2
            [[1, 2, 3, 4], [4, 3, 2, 1]],                    # rectangular 2x4
        ]
        for cost in matrices:
            result = orx.hungarian(cost)
            total = sum(cost[i][result[i]] for i in range(len(cost)))
            self.assertEqual(len(set(result)), len(result))  # no column reused
            self.assertAlmostEqual(total, self.brute_force(cost))


class OrWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        engine = RulesEngine.from_file(os.path.join(BASE, "rules", "nbc_rules.json"))
        cls.run_duplex = engine.run(load("APP-2026-0142_duplex_issues.json"))
        cls.run_duplex["_dwelling_units"] = 2
        cls.run_office = engine.run(load("APP-2026-0177_office_part3.json"))
        cls.run_office["_dwelling_units"] = 1

    def test_findings_only_non_meets(self):
        findings = orx.findings_from_run(self.run_duplex)
        self.assertTrue(findings)
        self.assertTrue(all(f["verdict"] != "MEETS" for f in findings))

    def test_assignment_prefers_matching_discipline(self):
        reviewers = [{"name": "fire", "disciplines": ["fire_safety"]},
                     {"name": "arch", "disciplines": ["architectural", "administrative"]}]
        plan = orx.triage([self.run_duplex, self.run_office], reviewers)
        for name, items in plan["assignment"]["assignments"].items():
            for f in items:
                if f["discipline_match"]:
                    r = next(r for r in reviewers if r["name"] == name)
                    self.assertIn(f["discipline"], r["disciplines"])

    def test_wspt_not_worse_than_fifo(self):
        jobs = [{"application_id": "A", "processing_minutes": 100, "weight": 1},
                {"application_id": "B", "processing_minutes": 10, "weight": 5},
                {"application_id": "C", "processing_minutes": 50, "weight": 2}]
        out = orx.schedule_queue(jobs, n_reviewers=1)
        self.assertLessEqual(out["weighted_completion_minutes"],
                             out["fifo_weighted_completion_minutes"])
        # WSPT optimality on single machine: verify against all orderings
        best = min(
            sum(j["weight"] * c for j, c in
                zip(perm, itertools.accumulate(j["processing_minutes"] for j in perm)))
            for perm in itertools.permutations(jobs))
        self.assertAlmostEqual(out["weighted_completion_minutes"], best)


class BcfTests(unittest.TestCase):
    def test_bcf_zip_topics(self):
        engine = RulesEngine.from_file(os.path.join(BASE, "rules", "nbc_rules.json"))
        run = engine.run(load("APP-2026-0142_duplex_issues.json"))
        blob = bcf.to_bcf(run)
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            names = zf.namelist()
            self.assertIn("bcf.version", names)
            markups = [n for n in names if n.endswith("markup.bcf")]
            expected = sum(1 for r in run["results"]
                           if r["applicable"] and r["verdict"] != "MEETS")
            self.assertEqual(len(markups), expected)
            content = zf.read(markups[0]).decode("utf-8")
            self.assertIn("<Markup", content)
            self.assertIn(run["run_id"], content)

    def test_bcf_guids_deterministic(self):
        engine = RulesEngine.from_file(os.path.join(BASE, "rules", "nbc_rules.json"))
        run = engine.run(load("APP-2026-0142_duplex_issues.json"))
        with zipfile.ZipFile(io.BytesIO(bcf.to_bcf(run))) as z1, \
                zipfile.ZipFile(io.BytesIO(bcf.to_bcf(run))) as z2:
            self.assertEqual(sorted(z1.namelist()), sorted(z2.namelist()))


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reg = SubmissionRegistry(os.path.join(self.tmp, "registry.json"),
                                      os.path.join(self.tmp, ".key"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_versioning_and_diff(self):
        v1 = self.reg.register("APP-X", [{"name": "a.pdf", "sha256": "1" * 64}], "builder")
        v2 = self.reg.register("APP-X", [{"name": "a.pdf", "sha256": "2" * 64},
                                         {"name": "b.ifc", "sha256": "3" * 64}], "builder")
        self.assertEqual((v1["version"], v2["version"]), (1, 2))
        self.assertEqual(v2["changes"]["modified"], ["a.pdf"])
        self.assertEqual(v2["changes"]["added"], ["b.ifc"])

    def test_seal_verification_detects_tamper(self):
        self.reg.register("APP-X", [{"name": "a.pdf", "sha256": "1" * 64}], "builder")
        self.assertTrue(all(v["valid"] for v in self.reg.verify("APP-X")))
        store = os.path.join(self.tmp, "registry.json")
        with open(store, "r+", encoding="utf-8") as fh:
            data = json.load(fh)
            data["APP-X"][0]["submitter"] = "attacker"
            fh.seek(0)
            json.dump(data, fh)
            fh.truncate()
        self.assertFalse(all(v["valid"] for v in self.reg.verify("APP-X")))


class CodesyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.rules_copy = os.path.join(self.tmp, "nbc_rules.json")
        shutil.copyfile(os.path.join(BASE, "rules", "nbc_rules.json"), self.rules_copy)
        with open(os.path.join(BASE, "rules", "amendments", "amd-2026-001.json"),
                  "r", encoding="utf-8") as fh:
            self.amendment = json.load(fh)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_amendment_applies_and_archives(self):
        summary = codesync.apply_amendment(self.rules_copy, self.amendment)
        self.assertEqual(summary["to_version"], "1.1.0")
        engine = RulesEngine.from_file(self.rules_copy)
        self.assertIn("R-9.13.4.2-RADON-ROUGH-IN", {r["id"] for r in engine.rules})
        self.assertEqual(engine.ruleset_version, "1.1.0")
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "versions",
                                                    "nbc_rules_1.0.0.json")))

    def test_wrong_target_version_rejected(self):
        bad = dict(self.amendment, target_ruleset="0.9.9")
        with self.assertRaises(codesync.AmendmentError):
            codesync.apply_amendment(self.rules_copy, bad)

    def test_non_bilingual_rule_rejected(self):
        bad = json.loads(json.dumps(self.amendment))
        del bad["add"][0]["title"]["fr"]
        with self.assertRaises(codesync.AmendmentError):
            codesync.apply_amendment(self.rules_copy, bad)


class BenchmarkTests(unittest.TestCase):
    def test_targets_met(self):
        report = benchmark.run_benchmark()
        self.assertFalse(report["mismatches"])
        for stats in report["classes"].values():
            self.assertTrue(stats["meets_target"])

    def test_complexity_derivation(self):
        engine = RulesEngine.from_file(os.path.join(BASE, "rules", "nbc_rules.json"))
        by_id = {r["id"]: benchmark.rule_complexity(r) for r in engine.rules}
        self.assertEqual(by_id["R-3.4.3.2-EXIT-WIDTH"], "complex")   # equation
        self.assertEqual(by_id["R-9.9.10.1-EGRESS-WINDOW"], "complex")  # exceptions
        self.assertEqual(by_id["R-9.8.4.2-STAIR-RUN"], "simple")


if __name__ == "__main__":
    unittest.main()
