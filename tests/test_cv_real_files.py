"""Tests for the computer-vision / geometric extraction stack against REAL
third-party files downloaded from the internet (data/real/):

  - IfcOpenHouse_IFC4.ifc   real IFC4 model (IfcOpenShell project)
  - flange.dxf, example00.dxf, entities.dxf   real QCAD example drawings
  - ontario_permit_form.pdf   official Ontario permit-to-construct form

Run:  python -m unittest discover tests -v
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from permitcheck.extract.ifc import parse_step  # noqa: E402
from permitcheck.extract import pdfx  # noqa: E402
from permitcheck.extract.raster import HAVE_CV2  # noqa: E402
from permitcheck.extract.dxf_geom import HAVE_EZDXF  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL = os.path.join(BASE, "data", "real")


def real(name):
    return os.path.join(REAL, name)


def have(name):
    return os.path.exists(real(name))


class RealIfcTests(unittest.TestCase):
    @unittest.skipUnless(have("IfcOpenHouse_IFC4.ifc"), "real IFC not downloaded")
    def test_step_parser_reads_real_model(self):
        with open(real("IfcOpenHouse_IFC4.ifc"), encoding="utf-8", errors="replace") as fh:
            entities = parse_step(fh.read())
        types = {}
        for e in entities.values():
            types[e.type] = types.get(e.type, 0) + 1
        self.assertGreater(len(entities), 2000)
        self.assertEqual(types.get("IFCBUILDING"), 1)
        self.assertEqual(types.get("IFCWINDOW"), 5)


class RealDxfGeometryTests(unittest.TestCase):
    @unittest.skipUnless(HAVE_EZDXF and have("flange.dxf"), "ezdxf or file missing")
    def test_dimension_entities_read_exactly(self):
        from permitcheck.extract import dxf_geom
        geo = dxf_geom.measure(real("flange.dxf"))
        self.assertEqual(geo["counts"]["lines"], 153)
        self.assertEqual(geo["counts"]["dimensions"], 6)
        values = sorted(d["measurement"] for d in geo["dimensions"])
        self.assertEqual(values, [2.0, 5.0, 6.0, 28.0, 42.0, 60.0])


class RealPdfTests(unittest.TestCase):
    @unittest.skipUnless(have("ontario_permit_form.pdf"), "real PDF not downloaded")
    def test_compressed_government_form_text_extracted(self):
        text, parser = pdfx.extract_text(real("ontario_permit_form.pdf"))
        self.assertIn("Permit to Construct or Demolish", text)
        self.assertIn("Building Code Act", text)


@unittest.skipUnless(HAVE_CV2, "opencv not installed")
class RasterPipelineTests(unittest.TestCase):
    def test_known_segments_measured_within_1pct(self):
        from permitcheck.extract import raster
        segments = [((0, 0), (500, 0)), ((0, 0), (0, 300)),
                    ((100, 50), (400, 250)), ((250, 20), (250, 280))]
        img, ppu, _ = raster.rasterize(segments, width_px=1600,
                                       rotate_deg=0.8, noise_sigma=8.0, blur=3)
        res = raster.analyze(img, ppu)
        gt = sorted(math.hypot(s[1][0] - s[0][0], s[1][1] - s[0][1]) for s in segments)
        got = sorted(m["value"] for m in res["measurements"])[-4:]
        for expected, measured in zip(gt, got):
            self.assertLess(abs(measured - expected) / expected, 0.01,
                            "expected %.1f got %.1f" % (expected, measured))

    def test_parallel_spacing_detects_stair_treads(self):
        from permitcheck.extract import raster
        # 6 'treads' 255 mm apart, like a stair plan detail
        segments = [((i * 255, 0), (i * 255, 900)) for i in range(6)]
        segments.append(((0, 0), (5 * 255, 0)))  # stringer line
        img, ppu, _ = raster.rasterize(segments, width_px=1600,
                                       rotate_deg=0.5, noise_sigma=6.0, blur=3)
        res = raster.analyze(img, ppu)
        self.assertTrue(res["parallel_clusters"])
        spacing = res["parallel_clusters"][0]["value"]
        self.assertLess(abs(spacing - 255) / 255, 0.02,
                        "expected ~255 mm, got %.1f" % spacing)

    @unittest.skipUnless(HAVE_EZDXF and have("entities.dxf"), "ezdxf or file missing")
    def test_real_drawing_accuracy_target(self):
        from permitcheck import benchmark_geom
        report = benchmark_geom._evaluate_drawing(real("entities.dxf"))
        self.assertGreaterEqual(report["mean_accuracy_pct"], 99.0)
        self.assertEqual(report["match_rate_pct"], 100.0)


class OcrTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from permitcheck.extract.ocr import HAVE_TESSERACT
        cls.have_ocr = HAVE_TESSERACT and HAVE_CV2

    def test_scanned_form_fields_with_confidence(self):
        if not self.have_ocr:
            self.skipTest("Tesseract/OpenCV not installed")
        import cv2
        import numpy as np
        from permitcheck.extract import ocr
        import tempfile
        lines = ["DWELLING UNITS: 2", "SECONDARY SUITE: YES",
                 "SMOKE ALARMS INTERCONNECTED BETWEEN SUITES: NO"]
        img = np.full((260, 1300), 255, np.uint8)
        for i, ln in enumerate(lines):
            cv2.putText(img, ln, (40, 70 + i * 70),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.1, 0, 2, cv2.LINE_AA)
        m = cv2.getRotationMatrix2D((650, 130), 0.6, 1.0)
        img = cv2.warpAffine(img, m, (1300, 260), borderValue=255)
        path = os.path.join(tempfile.gettempdir(), "pc_ocr_test.png")
        cv2.imwrite(path, img)
        out = ocr.extract(path)
        self.assertEqual(out["building"]["dwelling_units"]["value"], 2)
        self.assertTrue(out["building"]["has_secondary_suite"]["value"])
        alarm = out["fire_safety"]["smoke_alarms_interconnected"]
        self.assertFalse(alarm["value"])
        self.assertTrue(0.0 < alarm["confidence"] <= 1.0)
        self.assertIn("Tesseract", alarm["source"])


if __name__ == "__main__":
    unittest.main()
