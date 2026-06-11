"""בדיקות לחישוב ותק בחודשים קלנדריים ולתצוגתו."""
import os
import sys
import unittest
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.time_utils import calculate_seniority_months
from utils.utils import format_seniority_months


class TestCalculateSeniorityMonths(unittest.TestCase):
    """חישוב ותק - אותה פונקציה שמשמשת זכאות תשלום חג ותצוגה בטבלה."""

    def test_none_start_date(self):
        self.assertIsNone(calculate_seniority_months(None, 2026, 6))

    def test_started_first_of_month_counts_full_month(self):
        # התחיל ב-1 במרץ, ייחוס יוני = 3 חודשים מלאים
        self.assertEqual(calculate_seniority_months(date(2026, 3, 1), 2026, 6), 3)

    def test_started_mid_month_not_counted(self):
        # התחיל ב-2 במרץ - מרץ לא נספר כחודש מלא
        self.assertEqual(calculate_seniority_months(date(2026, 3, 2), 2026, 6), 2)

    def test_cross_year(self):
        self.assertEqual(calculate_seniority_months(date(2024, 10, 1), 2025, 1), 3)
        self.assertEqual(calculate_seniority_months(date(2024, 11, 2), 2025, 1), 1)

    def test_future_start_clamped_to_zero(self):
        self.assertEqual(calculate_seniority_months(date(2026, 9, 1), 2026, 6), 0)

    def test_datetime_input(self):
        self.assertEqual(
            calculate_seniority_months(datetime(2025, 6, 15, 10, 30), 2025, 10), 3
        )

    def test_long_tenure(self):
        # 15 שנים בדיוק
        self.assertEqual(calculate_seniority_months(date(2011, 6, 1), 2026, 6), 180)


class TestFormatSeniorityMonths(unittest.TestCase):
    """תצוגה קריאה של ותק בחודשים."""

    def test_none(self):
        self.assertEqual(format_seniority_months(None), "-")

    def test_zero(self):
        self.assertEqual(format_seniority_months(0), "פחות מחודש")

    def test_months_only(self):
        self.assertEqual(format_seniority_months(1), "חודש")
        self.assertEqual(format_seniority_months(2), "חודשיים")
        self.assertEqual(format_seniority_months(8), "8 חודשים")

    def test_whole_years(self):
        self.assertEqual(format_seniority_months(12), "שנה")
        self.assertEqual(format_seniority_months(24), "שנתיים")
        self.assertEqual(format_seniority_months(36), "3 שנים")

    def test_years_and_months(self):
        self.assertEqual(format_seniority_months(13), "שנה וחודש")
        self.assertEqual(format_seniority_months(14), "שנה וחודשיים")
        self.assertEqual(format_seniority_months(20), "שנה ו-8 חודשים")
        self.assertEqual(format_seniority_months(28), "שנתיים ו-4 חודשים")
        self.assertEqual(format_seniority_months(182), "15 שנים וחודשיים")


if __name__ == "__main__":
    unittest.main()
