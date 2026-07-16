"""Tests for the scanned-drawing -> compliance-verdict semantic pipeline.

Requires OpenCV (+ Tesseract for the OCR-dependent assertions). Tests that
need Tesseract skip cleanly when it is not installed.

Run:  python -m unittest discover tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from permitcheck.extract.raster import HAVE_CV2  # noqa: E402
from permitcheck.extract.ocr import HAVE_TESSERACT  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCANS = os.path.join(BASE, "data", "scans")


@unittest.skipUnless(HAVE_CV2, "opencv not installed")
class SemanticExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # ensure the scan fixtures exist
        if not os.path.exists(os.path.join(SCANS, "SCAN-2026-0301.png")):
            sys.path.insert(0, os.path.join(BASE, "tools"))
            import make_floorplan
            make_floorplan.build()

    def test_room_detection(self):
        import cv2
        from permitcheck.extract import semantic, raster
        img = cv2.imread(os.path.join(SCANS, "SCAN-2026-0301.png"), cv2.IMREAD_GRAYSCALE)
        rooms = semantic.detect_rooms(raster.binarize(img))
        self.assertEqual(len(rooms), 5)  # 5 rooms in the bungalow plan

    def test_ceiling_height_parser(self):
        from permitcheck.extract.semantic import _ceiling_height_m
        self.assertEqual(_ceiling_height_m("CH 2440"), 2.44)
        self.assertEqual(_ceiling_height_m("2440 CH"), 2.44)   # reversed OCR
        self.assertEqual(_ceiling_height_m("CH 2.35"), 2.35)
        self.assertEqual(_ceiling_height_m("CLG 2440"), 2.44)  # CLG tag
        self.assertEqual(_ceiling_height_m("CEILING 2440"), 2.44)
        self.assertEqual(_ceiling_height_m("C/H 2440"), 2.44)
        self.assertEqual(_ceiling_height_m("9'-0\" CLG"), 2.743)  # imperial + tag
        self.assertIsNone(_ceiling_height_m("CH 23550"))       # garbage rejected
        self.assertIsNone(_ceiling_height_m("no height here"))

    @unittest.skipUnless(HAVE_TESSERACT, "Tesseract not installed")
    def test_end_to_end_extraction_bungalow(self):
        import json
        from permitcheck.extract import semantic
        with open(os.path.join(SCANS, "SCAN-2026-0301.truth.json"), encoding="utf-8") as fh:
            truth = json.load(fh)
        app = semantic.extract_from_image(
            os.path.join(SCANS, "SCAN-2026-0301.png"),
            truth["px_per_mm"], title_block=truth["title_block"])
        self.assertEqual(len(app["spaces"]), 5)
        self.assertEqual(len(app["stairs"]), 1)
        bedrooms = [s for s in app["spaces"] if s["is_bedroom"]]
        self.assertEqual(len(bedrooms), 2)
        self.assertTrue(all("egress_window" in s for s in bedrooms))
        habitable = [s for s in app["spaces"] if s["habitable"]]
        self.assertTrue(all("ceiling_height_m" in s for s in habitable))
        stair = app["stairs"][0]
        self.assertIn("run_mm", stair)
        self.assertIn("rise_mm", stair)

    @unittest.skipUnless(HAVE_TESSERACT, "Tesseract not installed")
    def test_verdict_accuracy_meets_target(self):
        from permitcheck import benchmark_semantic
        report = benchmark_semantic.run_benchmark()
        self.assertNotIn("error", report)
        self.assertGreaterEqual(report["overall_accuracy"], 0.90)

    @unittest.skipUnless(HAVE_TESSERACT, "Tesseract not installed")
    def test_extracted_confidence_present(self):
        import json
        from permitcheck.extract import semantic
        with open(os.path.join(SCANS, "SCAN-2026-0302.truth.json"), encoding="utf-8") as fh:
            truth = json.load(fh)
        app = semantic.extract_from_image(
            os.path.join(SCANS, "SCAN-2026-0302.png"),
            truth["px_per_mm"], title_block=truth["title_block"])
        # every extracted scalar carries a confidence + source for traceability
        for s in app["spaces"]:
            ch = s.get("ceiling_height_m")
            if isinstance(ch, dict):
                self.assertIn("confidence", ch)
                self.assertIn("source", ch)


if __name__ == "__main__":
    unittest.main()
