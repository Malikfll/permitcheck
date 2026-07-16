"""Tests for CODE-ACCORD real-corpus ingestion (permitcheck.codes_accord).

Uses the real downloaded corpus when present; otherwise runs the parser on a
small inline sample so the conversion logic is always covered.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from permitcheck import codes_accord  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL = os.path.join(BASE, "data", "code_accord", "relations_all.csv")

SAMPLE = (
    "example_id,content,metadata,tagged_sentence,relation_type\n"
    "1,\"free space at least 800 mm\",{'ID': '44_Finnish_Accessibility'},"
    "\"a <e1>free space</e1> of at least <e2>800 millimetres</e2>\",greater-equal\n"
    "2,\"riser max 220 mm\",{'ID': '24_Finnish_SafetyOfUse'},"
    "\"the <e1>riser</e1> shall be at most <e2>220 millimetres</e2>\",less-equal\n"
    "3,\"door shall be marked\",{'ID': '9_UK'},"
    "\"the <e1>door</e1> shall be <e2>marked</e2>\",necessity\n"
)


class ConversionTests(unittest.TestCase):
    def _tmp(self, text):
        path = os.path.join(tempfile.gettempdir(), "accord_sample.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_comparative_relations_become_rule_stubs(self):
        stubs = list(codes_accord.convert_relations(self._tmp(SAMPLE)))
        self.assertEqual(len(stubs), 2)  # necessity ignored; 2 comparatives
        by_field = {s["draft_requirement"]["field"]: s["draft_requirement"] for s in stubs}
        self.assertEqual(by_field["free_space"]["op"], "ge")
        self.assertEqual(by_field["free_space"]["value"], 800)
        self.assertEqual(by_field["free_space"]["unit"], "mm")
        self.assertEqual(by_field["riser"]["op"], "le")
        self.assertEqual(by_field["riser"]["value"], 220)

    def test_value_unit_parser(self):
        self.assertEqual(codes_accord._parse_value_unit("1,500 millimetres"), (1500, "mm"))
        self.assertEqual(codes_accord._parse_value_unit("2.3 metres"), (2.3, "m"))
        self.assertEqual(codes_accord._parse_value_unit("no number here"), (None, None))

    @unittest.skipUnless(os.path.exists(REAL), "real CODE-ACCORD corpus not downloaded")
    def test_real_corpus_yields_many_stubs(self):
        report = codes_accord.ingest(REAL)
        self.assertGreater(report["rule_stubs_generated"], 50)
        self.assertEqual(report["with_recognised_unit"], report["rule_stubs_generated"])
        self.assertIn("ge", report["by_operator"])


if __name__ == "__main__":
    unittest.main()
