# -*- coding: utf-8 -*-
"""בדיקות לשאילתות המשותפות לזיהוי מדריכים עם דוח חודשי."""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.report_presence import get_report_presence_counts


class _FakeConnection:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self.responses.pop(0)


class TestReportPresence(unittest.TestCase):
    def test_without_housing_filter_uses_plain_month_queries(self):
        conn = _FakeConnection([
            [{"person_id": 10, "cnt": 3}],
            [{"person_id": 20}],
        ])

        counts, payment_only = get_report_presence_counts(
            conn, date(2026, 4, 1), date(2026, 5, 1),
        )

        self.assertEqual(counts, {10: 3})
        self.assertEqual(payment_only, {20})
        self.assertNotIn("JOIN apartments", conn.calls[0][0])
        self.assertEqual(conn.calls[0][1], (date(2026, 4, 1), date(2026, 5, 1)))

    def test_with_housing_filter_scopes_reports_and_payment_components(self):
        conn = _FakeConnection([
            [{"person_id": 10, "cnt": 3}],
            [{"person_id": 20}],
        ])

        counts, payment_only = get_report_presence_counts(
            conn, date(2026, 4, 1), date(2026, 5, 1), housing_array_id=7,
        )

        self.assertEqual(counts, {10: 3})
        self.assertEqual(payment_only, {20})
        self.assertIn("ap.housing_array_id = %s", conn.calls[0][0])
        self.assertIn("ap.housing_array_id = %s", conn.calls[1][0])
        self.assertEqual(conn.calls[0][1], (date(2026, 4, 1), date(2026, 5, 1), 7))


if __name__ == "__main__":
    unittest.main()
