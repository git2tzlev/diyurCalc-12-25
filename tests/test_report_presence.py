# -*- coding: utf-8 -*-
"""בדיקות לשאילתות המשותפות לזיהוי מדריכים עם דוח חודשי."""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.report_presence import get_report_overlap_counts, get_report_presence_counts


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
            conn, date(2026, 3, 1), date(2026, 4, 1),
        )

        self.assertEqual(counts, {10: 3})
        self.assertEqual(payment_only, {20})
        self.assertNotIn("JOIN apartments", conn.calls[0][0])
        self.assertEqual(conn.calls[0][1], (date(2026, 3, 1), date(2026, 4, 1)))

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

    def test_asd_april_2026_excludes_completion_apartments_from_counts(self):
        conn = _FakeConnection([
            [{"person_id": 10, "cnt": 3}],
            [{"person_id": 20}],
        ])

        counts, payment_only = get_report_presence_counts(
            conn, date(2026, 4, 1), date(2026, 5, 1), housing_array_id=2,
        )

        self.assertEqual(counts, {10: 3})
        self.assertEqual(payment_only, {20})
        self.assertIn("tr.apartment_id <> ALL(%s)", conn.calls[0][0])
        self.assertEqual(conn.calls[0][1][:3], (date(2026, 4, 1), date(2026, 5, 1), 2))
        self.assertCountEqual(conn.calls[0][1][3], [29, 37])

    def test_all_housing_april_2026_excludes_only_asd_completion_counts(self):
        conn = _FakeConnection([
            [{"person_id": 10, "cnt": 3}],
            [{"person_id": 20}],
        ])

        get_report_presence_counts(
            conn, date(2026, 4, 1), date(2026, 5, 1),
        )

        self.assertIn("ap.housing_array_id = %s", conn.calls[0][0])
        self.assertIn("tr.apartment_id = ANY(%s)", conn.calls[0][0])
        self.assertEqual(conn.calls[0][1][:3], (date(2026, 4, 1), date(2026, 5, 1), 2))
        self.assertCountEqual(conn.calls[0][1][3], [29, 37])

    def test_overlap_counts_detects_overlapping_reports_per_person(self):
        conn = _FakeConnection([
            [
                {"id": 1, "person_id": 10, "date": date(2026, 3, 9), "start_time": "13:00", "end_time": "17:00", "shift_type_id": 138},
                {"id": 2, "person_id": 10, "date": date(2026, 3, 9), "start_time": "16:30", "end_time": "20:00", "shift_type_id": 103},
                {"id": 3, "person_id": 20, "date": date(2026, 3, 9), "start_time": "08:00", "end_time": "12:00", "shift_type_id": 138},
                {"id": 4, "person_id": 20, "date": date(2026, 3, 9), "start_time": "12:00", "end_time": "16:00", "shift_type_id": 138},
            ],
            [
                {"shift_type_id": 103, "start_time": "16:30", "end_time": "22:00", "segment_type": "work"},
                {"shift_type_id": 103, "start_time": "22:00", "end_time": "06:30", "segment_type": "standby"},
                {"shift_type_id": 103, "start_time": "06:30", "end_time": "08:30", "segment_type": "work"},
            ],
        ])

        counts = get_report_overlap_counts(
            conn, date(2026, 3, 1), date(2026, 4, 1),
        )

        self.assertEqual(counts, {10: 1})

    def test_overlap_counts_ignores_work_overlapping_standby(self):
        conn = _FakeConnection([
            [
                {"id": 1, "person_id": 10, "date": date(2026, 3, 9), "start_time": "16:30", "end_time": "08:30", "shift_type_id": 103},
                {"id": 2, "person_id": 10, "date": date(2026, 3, 9), "start_time": "23:00", "end_time": "23:30", "shift_type_id": 138},
            ],
            [
                {"shift_type_id": 103, "start_time": "16:30", "end_time": "22:00", "segment_type": "work"},
                {"shift_type_id": 103, "start_time": "22:00", "end_time": "06:30", "segment_type": "standby"},
                {"shift_type_id": 103, "start_time": "06:30", "end_time": "08:30", "segment_type": "work"},
            ],
        ])

        counts = get_report_overlap_counts(
            conn, date(2026, 3, 1), date(2026, 4, 1),
        )

        self.assertEqual(counts, {})

    def test_overlap_counts_scopes_by_housing_filter(self):
        conn = _FakeConnection([[]])

        get_report_overlap_counts(
            conn, date(2026, 3, 1), date(2026, 4, 1), housing_array_id=2,
        )

        self.assertIn("JOIN apartments", conn.calls[0][0])
        self.assertIn("ap.housing_array_id = %s", conn.calls[0][0])
        self.assertEqual(conn.calls[0][1], (date(2026, 3, 1), date(2026, 4, 1), 2))


if __name__ == "__main__":
    unittest.main()
