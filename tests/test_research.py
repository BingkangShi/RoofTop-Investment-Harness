import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app import db
from app import research


class ResearchSchemaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "research.db"
        db.initialize(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_strategy_has_one_owned_risk_policy(self):
        original_connect = research.connect
        research.connect = lambda: db.connect(self.path)
        try:
            research.seed_research_catalog()
            with closing(db.connect(self.path)) as conn:
                strategies = conn.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]
                policies = conn.execute("SELECT COUNT(*) FROM strategy_risk_policies").fetchone()[0]
                orphaned = conn.execute(
                    """SELECT COUNT(*) FROM strategies s LEFT JOIN strategy_risk_policies r
                       ON r.strategy_id=s.id WHERE r.id IS NULL"""
                ).fetchone()[0]
                self.assertEqual((strategies, policies, orphaned), (3, 3, 0))
        finally:
            research.connect = original_connect

    def test_factor_and_backtest_audit_tables_exist(self):
        with closing(db.connect(self.path)) as conn:
            names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"factors", "factor_runs", "backtest_runs", "strategy_factors"} <= names)

    def test_curve_compaction_keeps_endpoints(self):
        import pandas as pd
        series = pd.Series(range(1000), index=pd.date_range("2023-01-01", periods=1000))
        points = research._curve_points(series, max_points=100)
        self.assertLessEqual(len(points), 101)
        self.assertEqual((points[0]["value"], points[-1]["value"]), (0.0, 999.0))


class SourceStatusTests(unittest.TestCase):
    def test_no_credentials_are_returned(self):
        from app.data_sources.intelligence import source_access_status
        with patch.dict("os.environ", {"ROOFTOP_X_BEARER_TOKEN": "secret", "ROOFTOP_SEC_IDENTITY": "a@b.com"}):
            encoded = json.dumps(source_access_status())
        self.assertNotIn("secret", encoded)
        self.assertNotIn("a@b.com", encoded)


if __name__ == "__main__":
    unittest.main()
