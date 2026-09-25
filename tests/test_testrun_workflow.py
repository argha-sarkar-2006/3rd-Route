"""Tests for the ingestion-to-router handoff entrypoint."""

import tempfile
import unittest
from pathlib import Path

import knowledge
import testrun


class TestRunWorkflowTests(unittest.TestCase):
    def test_text_source_is_chunked_into_searchable_knowledge_db(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "company.txt"
            database = Path(directory) / "knowledge.db"
            source.write_text("The company revenue target is 42 million.")

            ids = testrun.ingest_source(str(source), db_path=str(database))

            self.assertEqual(len(ids), 1)
            self.assertEqual(knowledge.document_count(str(database)), 1)
            self.assertTrue(knowledge.search("revenue", db_path=str(database)))


if __name__ == "__main__":
    unittest.main()
