import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app.db import connect, initialize


class DatabaseTests(unittest.TestCase):
    def test_seed_is_idempotent_and_labels_hypotheses_unverified(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.db"
            initialize(path)
            initialize(path)
            with closing(connect(path)) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 10)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0], 2)
                statuses = {row[0] for row in conn.execute("SELECT status FROM hypotheses")}
                self.assertEqual(statuses, {"UNVERIFIED"})

    def test_order_tables_do_not_exist(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.db"
            initialize(path)
            with closing(connect(path)) as conn:
                names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertNotIn("orders", names)
                self.assertNotIn("broker_accounts", names)
                self.assertIn("event_graphs", names)
                self.assertIn("graph_nodes", names)
                self.assertIn("data_sources", names)
                self.assertIn("data_quality_checks", names)
                self.assertIn("quote_snapshots", names)
                self.assertIn("minute_bars", names)
                self.assertIn("market_daily_bars", names)
                self.assertIn("chat_sessions", names)
                self.assertIn("chat_messages", names)
                self.assertIn("semantic_documents", names)
                self.assertIn("source_documents", names)
                self.assertIn("source_document_versions", names)
                self.assertIn("graph_snapshots", names)

    def test_graph_node_can_reference_relational_record(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.db"
            initialize(path)
            with closing(connect(path)) as conn:
                node = conn.execute("SELECT relational_ref FROM graph_nodes LIMIT 1").fetchone()
                self.assertTrue(node[0].startswith("trad://"))


if __name__ == "__main__":
    unittest.main()
