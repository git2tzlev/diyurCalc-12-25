# -*- coding: utf-8 -*-
"""
בדיקות לחישוב תשלום חג (סמל 254).

הרצה:
    python -m pytest tests/test_holiday_payment.py -v
"""

import unittest
import sys
import os
from datetime import date
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.holiday_payment import (
    get_holiday_dates_in_month,
    calculate_holiday_payments,
)
from core.constants import PERMANENT_EMPLOYEE_TYPE


def _make_shabbat_cache_with_holiday(holiday_dates, enter_dates=None):
    """יצירת shabbat_cache מינימלי עם ימי חג."""
    cache = {}
    for d in holiday_dates:
        day_str = d.strftime("%Y-%m-%d")
        cache[day_str] = {"holiday": "חג", "enter": None, "exit": None}
    if enter_dates:
        for d in enter_dates:
            day_str = d.strftime("%Y-%m-%d")
            if day_str in cache:
                cache[day_str]["enter"] = "18:00"
            else:
                cache[day_str] = {"holiday": "חג", "enter": "18:00", "exit": None}
    return cache


def _make_report(person_id, apartment_id, report_date):
    """יצירת דיווח מינימלי."""
    return {
        "person_id": person_id,
        "apartment_id": apartment_id,
        "date": report_date,
    }


def _get_amount(result, person_id):
    """חילוץ סכום תשלום חג מהתוצאה."""
    data = result.get(person_id)
    if data is None:
        return 0
    return data["amount"]


class TestGetHolidayDatesInMonth(unittest.TestCase):
    """בדיקות לזיהוי ימי חג בחודש."""

    def test_no_holidays_in_month(self):
        """חודש ללא חגים → רשימה ריקה."""
        cache = {}
        result = get_holiday_dates_in_month(2025, 3, cache)
        self.assertEqual(result, [])

    def test_single_holiday(self):
        """חג יחיד בחודש."""
        cache = _make_shabbat_cache_with_holiday([date(2025, 10, 2)])
        result = get_holiday_dates_in_month(2025, 10, cache)
        self.assertIn(date(2025, 10, 2), result)

    def test_two_day_holiday(self):
        """חג דו-יומי (ר"ה) — שני ימים עם רשומות נפרדות ב-DB."""
        cache = {
            "2025-09-23": {"holiday": "ראש השנה א", "enter": "18:00", "exit": None},
            "2025-09-24": {"holiday": "ראש השנה ב", "enter": None, "exit": "19:30"},
        }
        result = get_holiday_dates_in_month(2025, 9, cache)
        self.assertIn(date(2025, 9, 23), result)
        self.assertIn(date(2025, 9, 24), result)
        self.assertEqual(len(result), 2)

    def test_shabbat_without_holiday_excluded(self):
        """שבת רגילה בלי holiday לא נכללת."""
        cache = {
            "2025-03-08": {"parsha": "פרשה", "enter": "17:30", "exit": "18:30"},
        }
        result = get_holiday_dates_in_month(2025, 3, cache)
        self.assertEqual(result, [])


class TestCalculateHolidayPayments(unittest.TestCase):
    """בדיקות לחישוב תשלום חג."""

    def setUp(self):
        """הגדרת mock לחיבור DB."""
        self.conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"start_time": "08:00", "end_time": "16:00"}  # 480 minutes = 8 hours
        ]
        self.conn.cursor.return_value = mock_cursor

        # Reset cache
        import core.holiday_payment as hp_mod
        hp_mod._weekday_shift_work_minutes_cache = None

        self.minimum_wage = 32.3
        self.full_shift_pay = round(480 / 60, 2) * round(self.minimum_wage, 2)  # 8 * 32.3
        self.half_shift_pay = round(480 / 2 / 60, 2) * round(self.minimum_wage, 2)  # 4 * 32.3

    def test_no_holidays_returns_empty(self):
        """חודש בלי חגים → dict ריק."""
        cache = {}
        result = calculate_holiday_payments(
            self.conn, 2025, 3, cache, self.minimum_wage,
            all_reports=[], person_types={},
        )
        self.assertEqual(result, {})

    def test_single_permanent_not_worked_gets_full_shift(self):
        """מדריך קבוע אחד בדירה, לא עבד בחג → משמרת חול שלמה."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])

        reports = [
            _make_report(1, 100, date(2025, 10, 5)),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE}

        result = calculate_holiday_payments(
            self.conn, 2025, 10, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
        )
        self.assertAlmostEqual(_get_amount(result, 1), self.full_shift_pay)
        self.assertEqual(result[1]["count"], 1)
        self.assertAlmostEqual(result[1]["rate"], self.full_shift_pay)

    def test_single_permanent_worked_holiday_gets_nothing(self):
        """מדריך קבוע אחד, עבד בחג → לא מקבל תשלום."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])

        reports = [
            _make_report(1, 100, holiday),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE}

        result = calculate_holiday_payments(
            self.conn, 2025, 10, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
        )
        self.assertEqual(_get_amount(result, 1), 0)

    def test_two_permanent_neither_worked_get_half(self):
        """2 קבועים, אף אחד לא עבד בחג → כל אחד חצי משמרת."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])

        reports = [
            _make_report(1, 100, date(2025, 10, 5)),
            _make_report(2, 100, date(2025, 10, 6)),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE, 2: PERMANENT_EMPLOYEE_TYPE}

        result = calculate_holiday_payments(
            self.conn, 2025, 10, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
        )
        self.assertAlmostEqual(_get_amount(result, 1), self.half_shift_pay)
        self.assertAlmostEqual(_get_amount(result, 2), self.half_shift_pay)

    def test_two_permanent_one_worked_other_gets_half(self):
        """2 קבועים, אחד עבד בחג → רק השני מקבל חצי."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])

        reports = [
            _make_report(1, 100, holiday),
            _make_report(1, 100, date(2025, 10, 5)),
            _make_report(2, 100, date(2025, 10, 6)),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE, 2: PERMANENT_EMPLOYEE_TYPE}

        result = calculate_holiday_payments(
            self.conn, 2025, 10, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
        )
        self.assertEqual(_get_amount(result, 1), 0)
        self.assertAlmostEqual(_get_amount(result, 2), self.half_shift_pay)

    def test_multi_day_holiday_pays_per_day(self):
        """חג דו-יומי — תשלום נפרד לכל יום."""
        holidays = [date(2025, 10, 2), date(2025, 10, 3)]
        cache = _make_shabbat_cache_with_holiday(holidays)

        reports = [
            _make_report(1, 100, date(2025, 10, 5)),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE}

        result = calculate_holiday_payments(
            self.conn, 2025, 10, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
        )
        # מדריך קבוע יחיד × 2 ימי חג = 2 × full_shift_pay
        self.assertAlmostEqual(_get_amount(result, 1), self.full_shift_pay * 2)
        self.assertEqual(result[1]["count"], 2)

    def test_substitute_excluded(self):
        """מדריך מחליף לא מקבל תשלום חג."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])

        reports = [
            _make_report(1, 100, date(2025, 10, 5)),
        ]
        person_types = {1: "substitute"}

        result = calculate_holiday_payments(
            self.conn, 2025, 10, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
        )
        self.assertEqual(result, {})

    def test_guide_two_apartments_separate_calc(self):
        """מדריך בשתי דירות — חישוב נפרד לכל דירה."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])

        reports = [
            _make_report(1, 100, date(2025, 10, 5)),
            _make_report(1, 200, date(2025, 10, 6)),
            _make_report(2, 100, date(2025, 10, 6)),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE, 2: PERMANENT_EMPLOYEE_TYPE}

        result = calculate_holiday_payments(
            self.conn, 2025, 10, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
        )
        # person 1: יחיד בדירה 200 (full) + 1 מתוך 2 בדירה 100 (half) = full + half
        self.assertAlmostEqual(_get_amount(result, 1), self.full_shift_pay + self.half_shift_pay)
        # person 2: 1 מתוך 2 בדירה 100 (half)
        self.assertAlmostEqual(_get_amount(result, 2), self.half_shift_pay)


if __name__ == "__main__":
    unittest.main()
