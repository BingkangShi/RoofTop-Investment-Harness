import unittest

import pandas as pd

from app.data_sources.reports import _rows_to_documents


class ReportCollectorTests(unittest.TestCase):
    def test_recent_reports_are_sorted_and_limited(self):
        frame = pd.DataFrame([{"标题": f"报告{i}", "日期": f"2026-01-{(i % 28)+1:02d}"} for i in range(50)])
        docs = _rows_to_documents(frame, "report", "标题", None, "source", "日期", ["日期"])
        self.assertEqual(len(docs), 30)
        self.assertGreaterEqual(docs[0]["published_at"], docs[-1]["published_at"])


if __name__ == "__main__":
    unittest.main()
