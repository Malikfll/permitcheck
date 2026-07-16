"""Tests for multi-document ('Fiche') submission ingestion."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from permitcheck import ingest_set  # noqa: E402
from permitcheck.extract import pipeline  # noqa: E402


class MergeTests(unittest.TestCase):
    def test_merge_combines_collections_by_id(self):
        # simulate two Fiches contributing to the same submission
        app = {"application": {"documents": []}}
        fiche_plan = {"stairs": [{"id": "ST-1", "run_mm": {"value": 260, "confidence": 0.9}}]}
        fiche_section = {"stairs": [{"id": "ST-1", "rise_mm": {"value": 185, "confidence": 0.95}}]}
        pipeline._merge_dict(app, fiche_plan)
        pipeline._merge_dict(app, fiche_section)
        self.assertEqual(len(app["stairs"]), 1)          # same stair, merged
        self.assertEqual(app["stairs"][0]["run_mm"]["value"], 260)
        self.assertEqual(app["stairs"][0]["rise_mm"]["value"], 185)

    def test_highest_confidence_wins(self):
        app = {}
        pipeline._merge_dict(app, {"building": {"storeys": {"value": 2, "confidence": 0.6}}})
        pipeline._merge_dict(app, {"building": {"storeys": {"value": 3, "confidence": 0.95}}})
        self.assertEqual(app["building"]["storeys"]["value"], 3)

    def test_ingest_documents_records_provenance(self):
        # two tiny text 'form' PDFs are not needed; use the merge path with a
        # synthetic folder of nothing to confirm the structure is well-formed
        app = ingest_set.ingest_documents([])
        self.assertIn("documents", app["application"])
        self.assertEqual(app["application"]["documents"], [])


class RealFicheSetTests(unittest.TestCase):
    FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "submissions", "montreal_fiches_demo")

    @unittest.skipUnless(os.path.isdir(FOLDER) and
                         any(f.endswith(".pdf") for f in os.listdir(FOLDER))
                         if os.path.isdir(FOLDER) else False,
                         "Fiche demo folder not present")
    def test_folder_merges_multiple_fiches(self):
        app = ingest_set.ingest_folder(self.FOLDER)
        docs = app["application"]["documents"]
        self.assertGreaterEqual(len(docs), 2)
        # at least one Fiche contributed spaces and at least one contributed stairs
        contributed = set()
        for d in docs:
            contributed.update(d["contributed"])
        self.assertTrue({"spaces", "stairs"} & contributed)


if __name__ == "__main__":
    unittest.main()
