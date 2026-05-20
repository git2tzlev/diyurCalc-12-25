# -*- coding: utf-8 -*-
"""
בדיקות לחישוב תשלום חג (סמל 254).

הרצה:
    python -m pytest tests/test_holiday_payment.py -v
"""

import unittest
import sys
import os
from datetime import date, time
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.holiday_payment import (
    get_holiday_dates_in_month,
    get_holiday_payment_dates_in_month,
    calculate_holiday_payments,
)
from core.constants import (
    ASD_HOUSING_ARRAY_ID,
    COMPLETION_APARTMENT_IDS,
    HIGH_FUNCTIONING_APT_TYPE,
    HOLIDAY_PAY_MIN_SENIORITY_MONTHS,
    LOW_FUNCTIONING_APT_TYPE,
    PERMANENT_EMPLOYEE_TYPE,
)
from core.holiday_payment import _has_sufficient_seniority


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


def _make_report(person_id, apartment_id, report_date, housing_array_id=None,
                  apartment_type_id=None):
    """יצירת דיווח מינימלי."""
    return {
        "person_id": person_id,
        "apartment_id": apartment_id,
        "date": report_date,
        "housing_array_id": housing_array_id,
        "apartment_type_id": apartment_type_id,
    }


# תאריך התחלה עם ותק מספיק (ברירת מחדל לבדיקות)
VETERAN_START = date(2020, 1, 1)


def _start_dates(*person_ids):
    """יצירת מיפוי person_start_dates עם ותק מספיק."""
    return {pid: VETERAN_START for pid in person_ids}


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


class TestSpecialHolidayPaymentDates(unittest.TestCase):
    """בדיקות לימים מיוחדים שנספרים כתשלום חג."""

    def _conn_with_special_days(self, rows):
        conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchall.return_value = rows
        conn.cursor.return_value = cursor
        return conn

    def test_independence_counts_as_single_holiday_payment_day(self):
        """חלון עצמאות 20:00-20:00 נספר כיום תשלום אחד בתאריך הסיום."""
        conn = self._conn_with_special_days([
            {
                "start_date": date(2026, 4, 21),
                "end_date": date(2026, 4, 22),
            }
        ])

        holiday_dates, work_dates = get_holiday_payment_dates_in_month(conn, 2026, 4, {})

        self.assertEqual(holiday_dates, [date(2026, 4, 22)])
        self.assertEqual(work_dates[date(2026, 4, 22)], {date(2026, 4, 21), date(2026, 4, 22)})

    def test_special_holiday_merges_with_regular_holidays(self):
        """חגים רגילים וימים מיוחדים מסומנים חוזרים יחד, בלי כפילויות."""
        regular_holiday = date(2026, 4, 2)
        conn = self._conn_with_special_days([
            {
                "start_date": date(2026, 4, 21),
                "end_date": date(2026, 4, 22),
            }
        ])

        holiday_dates, _work_dates = get_holiday_payment_dates_in_month(
            conn, 2026, 4, _make_shabbat_cache_with_holiday([regular_holiday])
        )

        self.assertEqual(holiday_dates, [regular_holiday, date(2026, 4, 22)])

    def test_special_holiday_same_date_as_regular_holiday_not_duplicated(self):
        """אם יום מיוחד מסומן באותו תאריך של חג רגיל, הוא לא יוצר יום תשלום כפול."""
        same_day = date(2026, 4, 22)
        conn = self._conn_with_special_days([
            {
                "start_date": date(2026, 4, 21),
                "end_date": same_day,
            }
        ])

        holiday_dates, work_dates = get_holiday_payment_dates_in_month(
            conn, 2026, 4, _make_shabbat_cache_with_holiday([same_day])
        )

        self.assertEqual(holiday_dates, [same_day])
        self.assertEqual(work_dates[same_day], {date(2026, 4, 21), same_day})

    def test_special_holiday_crosses_from_previous_month(self):
        """חלון שמתחיל בחודש קודם ויום הזכאות בחודש הנוכחי נספר בחודש הנכון."""
        conn = self._conn_with_special_days([
            {
                "start_date": date(2026, 3, 31),
                "end_date": date(2026, 4, 1),
            }
        ])

        holiday_dates, work_dates = get_holiday_payment_dates_in_month(conn, 2026, 4, {})

        self.assertEqual(holiday_dates, [date(2026, 4, 1)])
        self.assertEqual(work_dates[date(2026, 4, 1)], {date(2026, 3, 31), date(2026, 4, 1)})

    def test_special_holiday_ending_next_month_not_counted_in_current_month(self):
        """חלון שמסתיים בחודש הבא לא נספר כתשלום חג בחודש הנוכחי."""
        conn = self._conn_with_special_days([
            {
                "start_date": date(2026, 4, 30),
                "end_date": date(2026, 5, 1),
            }
        ])

        holiday_dates, _work_dates = get_holiday_payment_dates_in_month(conn, 2026, 4, {})

        self.assertEqual(holiday_dates, [])

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
            all_reports=[], person_types={}, person_start_dates={},
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
            person_start_dates=_start_dates(1),
        )
        self.assertAlmostEqual(_get_amount(result, 1), self.full_shift_pay)
        self.assertEqual(result[1]["count"], 1)
        self.assertAlmostEqual(result[1]["rate"], self.full_shift_pay)

    def test_special_holiday_not_worked_gets_full_shift(self):
        """יום פרימיום שמסומן לתשלום חג משלם 254 למדריך קבוע שלא עבד בו."""
        pay_date = date(2026, 4, 22)
        reports = [
            _make_report(1, 100, date(2026, 4, 5)),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE}

        with patch(
            "core.holiday_payment._get_special_holiday_payment_windows",
            return_value={pay_date: {date(2026, 4, 21), pay_date}},
        ), patch("core.holiday_payment._get_apartment_work_minutes", return_value={100: 480}):
            result = calculate_holiday_payments(
                self.conn, 2026, 4, {}, self.minimum_wage,
                all_reports=reports, person_types=person_types,
                person_start_dates=_start_dates(1),
            )

        self.assertAlmostEqual(_get_amount(result, 1), self.full_shift_pay)
        self.assertEqual(result[1]["count"], 1)

    def test_special_holiday_worked_on_eve_blocks_payment(self):
        """עבודה בערב יום העצמאות בתוך החלון מונעת תשלום חג לאותו יום."""
        pay_date = date(2026, 4, 22)
        reports = [
            _make_report(1, 100, date(2026, 4, 21)),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE}

        with patch(
            "core.holiday_payment._get_special_holiday_payment_windows",
            return_value={pay_date: {date(2026, 4, 21), pay_date}},
        ), patch("core.holiday_payment._get_apartment_work_minutes", return_value={100: 480}):
            result = calculate_holiday_payments(
                self.conn, 2026, 4, {}, self.minimum_wage,
                all_reports=reports, person_types=person_types,
                person_start_dates=_start_dates(1),
            )

        self.assertEqual(_get_amount(result, 1), 0)

    def test_special_holiday_worked_on_pay_date_blocks_payment(self):
        """עבודה ביום העצמאות עצמו מונעת תשלום חג לאותו יום."""
        pay_date = date(2026, 4, 22)
        reports = [
            _make_report(1, 100, pay_date),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE}

        with patch(
            "core.holiday_payment._get_special_holiday_payment_windows",
            return_value={pay_date: {date(2026, 4, 21), pay_date}},
        ), patch("core.holiday_payment._get_apartment_work_minutes", return_value={100: 480}):
            result = calculate_holiday_payments(
                self.conn, 2026, 4, {}, self.minimum_wage,
                all_reports=reports, person_types=person_types,
                person_start_dates=_start_dates(1),
            )

        self.assertEqual(_get_amount(result, 1), 0)

    def test_special_holiday_previous_day_normal_carryover_does_not_block_payment(self):
        """משמרת מהיום הקודם שנמשכת רגיל לתוך חג לא מבטלת תשלום חג."""
        pay_date = date(2026, 4, 22)
        report = _make_report(1, 100, date(2026, 4, 21))
        report.update({"start_time": "15:00", "end_time": "08:00", "shift_type_id": 103})
        reports = [report]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE}
        window = {
            pay_date: [{
                "start_date": date(2026, 4, 21),
                "start_time": time(20, 0),
                "end_date": pay_date,
                "end_time": time(20, 0),
            }]
        }
        shift_segments = [
            {"start_time": "15:00", "end_time": "22:00"},
            {"start_time": "22:00", "end_time": "06:30"},
            {"start_time": "06:30", "end_time": "08:00"},
        ]

        with patch("core.holiday_payment._get_special_holiday_payment_window_details", return_value=window), \
             patch("core.holiday_payment._get_shift_segments", return_value=shift_segments), \
             patch("core.holiday_payment._get_apartment_work_minutes", return_value={100: 480}):
            result = calculate_holiday_payments(
                self.conn, 2026, 4, {}, self.minimum_wage,
                all_reports=reports, person_types=person_types,
                person_start_dates=_start_dates(1),
            )

        self.assertAlmostEqual(_get_amount(result, 1), self.full_shift_pay)

    def test_special_holiday_previous_day_extra_carryover_blocks_payment(self):
        """אם המשמרת מהיום הקודם נמשכה מעבר לסוף המקטעים, היא כן מבטלת חג."""
        pay_date = date(2026, 4, 22)
        report = _make_report(1, 100, date(2026, 4, 21))
        report.update({"start_time": "15:00", "end_time": "10:00", "shift_type_id": 103})
        reports = [report]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE}
        window = {
            pay_date: [{
                "start_date": date(2026, 4, 21),
                "start_time": time(20, 0),
                "end_date": pay_date,
                "end_time": time(20, 0),
            }]
        }
        shift_segments = [
            {"start_time": "15:00", "end_time": "22:00"},
            {"start_time": "22:00", "end_time": "06:30"},
            {"start_time": "06:30", "end_time": "08:00"},
        ]

        with patch("core.holiday_payment._get_special_holiday_payment_window_details", return_value=window), \
             patch("core.holiday_payment._get_shift_segments", return_value=shift_segments), \
             patch("core.holiday_payment._get_apartment_work_minutes", return_value={100: 480}):
            result = calculate_holiday_payments(
                self.conn, 2026, 4, {}, self.minimum_wage,
                all_reports=reports, person_types=person_types,
                person_start_dates=_start_dates(1),
            )

        self.assertEqual(_get_amount(result, 1), 0)

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
            person_start_dates=_start_dates(1),
        )
        self.assertEqual(_get_amount(result, 1), 0)

    def test_completion_apartment_never_gets_holiday_payment(self):
        """דירת השלמות לא מקבלת תשלום חג גם אם יש בה דיווח בחודש."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])
        completion_apt_id = next(iter(COMPLETION_APARTMENT_IDS))

        reports = [
            _make_report(1, completion_apt_id, date(2025, 10, 5), housing_array_id=1),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE}

        result = calculate_holiday_payments(
            self.conn, 2025, 10, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
            person_start_dates=_start_dates(1),
        )

        self.assertEqual(result, {})

    def test_no_payment_second_slot_makes_single_guide_half_shift(self):
        """מדריך אחד + משבצת ללא תשלום חג → המדריך מקבל חצי חג."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])
        reports = [
            _make_report(1, 100, date(2025, 10, 5), housing_array_id=1),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE}

        with patch("core.holiday_payment._load_saved_assignments", return_value={
            100: {
                "apartment_id": 100,
                "guide_1_id": 1,
                "guide_2_id": None,
                "guide_2_no_holiday_payment": True,
            }
        }), patch("core.holiday_payment._get_relevant_apartments", return_value=[
            {"id": 100, "name": "דירה", "housing_array_id": None}
        ]):
            result = calculate_holiday_payments(
                self.conn, 2025, 10, cache, self.minimum_wage,
                all_reports=reports, person_types=person_types,
                person_start_dates=_start_dates(1),
            )

        self.assertAlmostEqual(_get_amount(result, 1), self.half_shift_pay)

    def test_no_payment_second_slot_keeps_seniority_filter(self):
        """גם עם משבצת ללא תשלום חג, מדריך בלי ותק לא מקבל."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])
        reports = [
            _make_report(1, 100, date(2025, 10, 5), housing_array_id=1),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE}
        person_start_dates = {1: date(2025, 8, 15)}

        with patch("core.holiday_payment._load_saved_assignments", return_value={
            100: {
                "apartment_id": 100,
                "guide_1_id": 1,
                "guide_2_id": None,
                "guide_2_no_holiday_payment": True,
            }
        }), patch("core.holiday_payment._get_relevant_apartments", return_value=[
            {"id": 100, "name": "דירה", "housing_array_id": None}
        ]):
            result = calculate_holiday_payments(
                self.conn, 2025, 10, cache, self.minimum_wage,
                all_reports=reports, person_types=person_types,
                person_start_dates=person_start_dates,
            )

        self.assertEqual(_get_amount(result, 1), 0)

    def test_partial_saved_assignments_override_only_saved_apartments(self):
        """שורה שמורה חלה רק על הדירה שלה; דירות בלי שורה נשארות אוטומטיות."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])
        reports = [
            _make_report(1, 100, date(2025, 10, 5), housing_array_id=1),
            _make_report(1, 200, date(2025, 10, 6), housing_array_id=1),
            _make_report(2, 300, date(2025, 10, 7), housing_array_id=1),
        ]
        person_types = {
            1: PERMANENT_EMPLOYEE_TYPE,
            2: PERMANENT_EMPLOYEE_TYPE,
        }

        with patch("core.holiday_payment._load_saved_assignments", return_value={
            100: {
                "apartment_id": 100,
                "guide_1_id": 1,
                "guide_2_id": None,
                "guide_2_no_holiday_payment": False,
            },
            200: {
                "apartment_id": 200,
                "guide_1_id": None,
                "guide_2_id": None,
                "guide_2_no_holiday_payment": False,
            },
        }):
            result = calculate_holiday_payments(
                self.conn, 2025, 10, cache, self.minimum_wage,
                all_reports=reports, person_types=person_types,
                person_start_dates=_start_dates(1, 2),
            )

        self.assertAlmostEqual(_get_amount(result, 1), self.full_shift_pay)
        self.assertAlmostEqual(_get_amount(result, 2), self.full_shift_pay)

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
            person_start_dates=_start_dates(1, 2),
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
            person_start_dates=_start_dates(1, 2),
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
            person_start_dates=_start_dates(1),
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
            person_start_dates=_start_dates(1),
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
            person_start_dates=_start_dates(1, 2),
        )
        # person 1: יחיד בדירה 200 (full) + 1 מתוך 2 בדירה 100 (half) = full + half
        self.assertAlmostEqual(_get_amount(result, 1), self.full_shift_pay + self.half_shift_pay)
        # person 2: 1 מתוך 2 בדירה 100 (half)
        self.assertAlmostEqual(_get_amount(result, 2), self.half_shift_pay)


class TestHolidayPayPerApartmentOverride(unittest.TestCase):
    """בדיקות לתשלום חג לפי override של דירה (מ-02/2026)."""

    def setUp(self):
        self.conn = MagicMock()
        mock_cursor = MagicMock()
        # Global fallback: 480 minutes
        mock_cursor.fetchall.return_value = [
            {"start_time": "08:00", "end_time": "16:00"}
        ]
        self.conn.cursor.return_value = mock_cursor

        import core.holiday_payment as hp_mod
        hp_mod._weekday_shift_work_minutes_cache = None

        self.minimum_wage = 33.49

    @patch("app_utils._fetch_weekday_overrides")
    @patch("app_utils._build_sick_vacation_segments")
    def test_apartment_with_override_gets_more_hours(
        self, mock_build_segs, mock_fetch_overrides
    ):
        """דירה עם override 15:00-08:00 מקבלת 8.5 שעות חג (במקום 8)."""
        # Override: apartment 10 has 15:00-08:00
        mock_fetch_overrides.return_value = (
            {10: ("15:00", "08:00")},  # apt_overrides
            {},  # ha_defaults
        )
        # _build_sick_vacation_segments returns work segments totaling 510 min
        mock_build_segs.return_value = [
            {"start_time": "15:00", "end_time": "22:00", "segment_type": "work"},
            {"start_time": "06:30", "end_time": "08:00", "segment_type": "work"},
        ]

        holiday = date(2026, 4, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])

        reports = [
            _make_report(1, 10, date(2026, 4, 5), housing_array_id=1),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE}

        result = calculate_holiday_payments(
            self.conn, 2026, 4, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
            person_start_dates=_start_dates(1),
        )

        # 510 min = 8.5 hours → 8.5 × 33.49
        expected = round(510 / 60, 2) * round(self.minimum_wage, 2)
        self.assertAlmostEqual(_get_amount(result, 1), expected)

    def test_before_feb_2026_uses_global(self):
        """לפני 02/2026 — תמיד ערך גלובלי (480 דק')."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])

        reports = [
            _make_report(1, 10, date(2025, 10, 5), housing_array_id=1),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE}

        result = calculate_holiday_payments(
            self.conn, 2025, 10, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
            person_start_dates=_start_dates(1),
        )

        expected = round(480 / 60, 2) * round(self.minimum_wage, 2)
        self.assertAlmostEqual(_get_amount(result, 1), expected)

    @patch("app_utils._fetch_weekday_overrides")
    @patch("app_utils._build_sick_vacation_segments")
    def test_two_apartments_different_overrides(
        self, mock_build_segs, mock_fetch_overrides
    ):
        """שתי דירות עם overrides שונים — כל אחת מקבלת תשלום לפי שעותיה."""
        mock_fetch_overrides.return_value = (
            {10: ("15:00", "08:00"), 20: ("17:00", "08:00")},
            {},
        )

        def side_effect(start, end):
            if start == "15:00":
                return [
                    {"start_time": "15:00", "end_time": "22:00", "segment_type": "work"},
                    {"start_time": "06:30", "end_time": "08:00", "segment_type": "work"},
                ]  # 510 min
            else:
                return [
                    {"start_time": "17:00", "end_time": "22:00", "segment_type": "work"},
                    {"start_time": "06:30", "end_time": "08:00", "segment_type": "work"},
                ]  # 390 min

        mock_build_segs.side_effect = side_effect

        holiday = date(2026, 4, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])

        reports = [
            _make_report(1, 10, date(2026, 4, 5), housing_array_id=1),
            _make_report(1, 20, date(2026, 4, 6), housing_array_id=1),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE}

        result = calculate_holiday_payments(
            self.conn, 2026, 4, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
            person_start_dates=_start_dates(1),
        )

        # apt 10: 510 min (single guide = full)
        pay_apt10 = round(510 / 60, 2) * round(self.minimum_wage, 2)
        # apt 20: 390 min (single guide = full)
        pay_apt20 = round(390 / 60, 2) * round(self.minimum_wage, 2)

        self.assertAlmostEqual(_get_amount(result, 1), pay_apt10 + pay_apt20)


class TestAsdHolidayPayment(unittest.TestCase):
    """בדיקות תשלום חג לדירות ASD — תמיד משמרת שלמה."""

    def setUp(self):
        self.conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"start_time": "08:00", "end_time": "16:00"}
        ]
        self.conn.cursor.return_value = mock_cursor

        import core.holiday_payment as hp_mod
        hp_mod._weekday_shift_work_minutes_cache = None

        self.minimum_wage = 32.3
        self.full_shift_pay = round(480 / 60, 2) * round(self.minimum_wage, 2)
        self.half_shift_pay = round(480 / 2 / 60, 2) * round(self.minimum_wage, 2)

    def test_asd_two_permanent_both_get_full_shift(self):
        """2 קבועים בדירת ASD, אף אחד לא עבד בחג → כל אחד משמרת שלמה."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])

        reports = [
            _make_report(1, 100, date(2025, 10, 5),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
            _make_report(2, 100, date(2025, 10, 6),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE, 2: PERMANENT_EMPLOYEE_TYPE}

        result = calculate_holiday_payments(
            self.conn, 2025, 10, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
            person_start_dates=_start_dates(1, 2),
        )
        self.assertAlmostEqual(_get_amount(result, 1), self.full_shift_pay)
        self.assertAlmostEqual(_get_amount(result, 2), self.full_shift_pay)

    def test_asd_low_functioning_also_full_shift(self):
        """דירת תפקוד נמוך (ASD) — גם משמרת שלמה עם 2+ קבועים."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])

        reports = [
            _make_report(1, 100, date(2025, 10, 5),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=LOW_FUNCTIONING_APT_TYPE),
            _make_report(2, 100, date(2025, 10, 6),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=LOW_FUNCTIONING_APT_TYPE),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE, 2: PERMANENT_EMPLOYEE_TYPE}

        result = calculate_holiday_payments(
            self.conn, 2025, 10, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
            person_start_dates=_start_dates(1, 2),
        )
        self.assertAlmostEqual(_get_amount(result, 1), self.full_shift_pay)
        self.assertAlmostEqual(_get_amount(result, 2), self.full_shift_pay)

    def test_asd_one_worked_other_gets_full(self):
        """2 קבועים בדירת ASD, אחד עבד בחג → השני מקבל משמרת שלמה."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])

        reports = [
            _make_report(1, 100, holiday,
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
            _make_report(1, 100, date(2025, 10, 5),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
            _make_report(2, 100, date(2025, 10, 6),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE, 2: PERMANENT_EMPLOYEE_TYPE}

        result = calculate_holiday_payments(
            self.conn, 2025, 10, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
            person_start_dates=_start_dates(1, 2),
        )
        self.assertEqual(_get_amount(result, 1), 0)
        self.assertAlmostEqual(_get_amount(result, 2), self.full_shift_pay)

    def test_non_asd_still_gets_half(self):
        """דירה רגילה (לא ASD) עם 2+ קבועים → עדיין חצי משמרת."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])

        reports = [
            _make_report(1, 100, date(2025, 10, 5), apartment_type_id=1),
            _make_report(2, 100, date(2025, 10, 6), apartment_type_id=1),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE, 2: PERMANENT_EMPLOYEE_TYPE}

        result = calculate_holiday_payments(
            self.conn, 2025, 10, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
            person_start_dates=_start_dates(1, 2),
        )
        self.assertAlmostEqual(_get_amount(result, 1), self.half_shift_pay)
        self.assertAlmostEqual(_get_amount(result, 2), self.half_shift_pay)

    def test_guide_in_asd_and_regular_apartments(self):
        """מדריך בדירת ASD ודירה רגילה — ASD שלמה, רגילה חצי."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])

        reports = [
            # דירה 100: ASD, 2 קבועים
            _make_report(1, 100, date(2025, 10, 5),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
            _make_report(2, 100, date(2025, 10, 6),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
            # דירה 200: רגילה, 2 קבועים
            _make_report(1, 200, date(2025, 10, 7), apartment_type_id=1),
            _make_report(3, 200, date(2025, 10, 8), apartment_type_id=1),
        ]
        person_types = {
            1: PERMANENT_EMPLOYEE_TYPE,
            2: PERMANENT_EMPLOYEE_TYPE,
            3: PERMANENT_EMPLOYEE_TYPE,
        }

        result = calculate_holiday_payments(
            self.conn, 2025, 10, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
            person_start_dates=_start_dates(1, 2, 3),
        )
        # person 1: full (ASD apt 100) + half (regular apt 200)
        self.assertAlmostEqual(
            _get_amount(result, 1),
            self.full_shift_pay + self.half_shift_pay,
        )
        # person 2: full (ASD apt 100)
        self.assertAlmostEqual(_get_amount(result, 2), self.full_shift_pay)
        # person 3: half (regular apt 200)
        self.assertAlmostEqual(_get_amount(result, 3), self.half_shift_pay)

    def test_asd_two_day_holiday_three_guides(self):
        """חג דו-יומי, 3 קבועים ב-ASD, כל אחד עבד ביום אחר."""
        holidays = [date(2025, 10, 2), date(2025, 10, 3)]
        cache = _make_shabbat_cache_with_holiday(holidays)

        reports = [
            # person 1 עבד ביום ראשון של החג
            _make_report(1, 100, holidays[0],
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
            _make_report(1, 100, date(2025, 10, 5),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
            # person 2 עבד ביום שני של החג
            _make_report(2, 100, holidays[1],
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
            _make_report(2, 100, date(2025, 10, 6),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
            # person 3 לא עבד בחג כלל
            _make_report(3, 100, date(2025, 10, 7),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
        ]
        person_types = {
            1: PERMANENT_EMPLOYEE_TYPE,
            2: PERMANENT_EMPLOYEE_TYPE,
            3: PERMANENT_EMPLOYEE_TYPE,
        }

        result = calculate_holiday_payments(
            self.conn, 2025, 10, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
            person_start_dates=_start_dates(1, 2, 3),
        )
        # person 1: עבד ביום 1, לא עבד ביום 2 → 1 × full
        self.assertAlmostEqual(_get_amount(result, 1), self.full_shift_pay)
        self.assertEqual(result[1]["count"], 1)
        # person 2: לא עבד ביום 1, עבד ביום 2 → 1 × full
        self.assertAlmostEqual(_get_amount(result, 2), self.full_shift_pay)
        self.assertEqual(result[2]["count"], 1)
        # person 3: לא עבד בשני הימים → 2 × full
        self.assertAlmostEqual(_get_amount(result, 3), self.full_shift_pay * 2)
        self.assertEqual(result[3]["count"], 2)

    def test_asd_saved_assignment_supports_third_guide_full_shift(self):
        """ניהול תשלום חג ב-ASD יכול לשייך 3 מדריכים, וכל אחד מקבל חג שלם."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])
        reports = [
            _make_report(1, 100, date(2025, 10, 5),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
        ]
        person_types = {
            1: PERMANENT_EMPLOYEE_TYPE,
            2: PERMANENT_EMPLOYEE_TYPE,
            3: PERMANENT_EMPLOYEE_TYPE,
        }

        with patch("core.holiday_payment._load_saved_assignments", return_value={
            100: {
                "apartment_id": 100,
                "guide_1_id": 1,
                "guide_2_id": 2,
                "guide_3_id": 3,
                "guide_2_no_holiday_payment": False,
            }
        }):
            result = calculate_holiday_payments(
                self.conn, 2025, 10, cache, self.minimum_wage,
                all_reports=reports, person_types=person_types,
                person_start_dates=_start_dates(1, 2, 3),
            )

        self.assertAlmostEqual(_get_amount(result, 1), self.full_shift_pay)
        self.assertAlmostEqual(_get_amount(result, 2), self.full_shift_pay)
        self.assertAlmostEqual(_get_amount(result, 3), self.full_shift_pay)

    @patch("app_utils._fetch_weekday_overrides")
    @patch("app_utils._build_sick_vacation_segments")
    def test_asd_with_override_full_shift_custom_hours(
        self, mock_build_segs, mock_fetch_overrides
    ):
        """דירת ASD עם override שעות (מ-02/2026) — משמרת שלמה לפי שעות מותאמות."""
        mock_fetch_overrides.return_value = (
            {100: ("15:00", "08:00")},
            {},
        )
        mock_build_segs.return_value = [
            {"start_time": "15:00", "end_time": "22:00", "segment_type": "work"},
            {"start_time": "06:30", "end_time": "08:00", "segment_type": "work"},
        ]  # 510 min

        holiday = date(2026, 4, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])

        reports = [
            _make_report(1, 100, date(2026, 4, 5), housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
            _make_report(2, 100, date(2026, 4, 6), housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE, 2: PERMANENT_EMPLOYEE_TYPE}

        result = calculate_holiday_payments(
            self.conn, 2026, 4, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
            person_start_dates=_start_dates(1, 2),
        )
        # 510 min = 8.5 hours, full shift for both (ASD)
        expected_full = round(510 / 60, 2) * round(self.minimum_wage, 2)
        self.assertAlmostEqual(_get_amount(result, 1), expected_full)
        self.assertAlmostEqual(_get_amount(result, 2), expected_full)

    def test_asd_no_type_id_falls_back_to_half(self):
        """דיווח ללא apartment_type_id → לא מזוהה כ-ASD → חצי משמרת."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])

        reports = [
            _make_report(1, 100, date(2025, 10, 5)),  # no apartment_type_id
            _make_report(2, 100, date(2025, 10, 6)),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE, 2: PERMANENT_EMPLOYEE_TYPE}

        result = calculate_holiday_payments(
            self.conn, 2025, 10, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
            person_start_dates=_start_dates(1, 2),
        )
        self.assertAlmostEqual(_get_amount(result, 1), self.half_shift_pay)
        self.assertAlmostEqual(_get_amount(result, 2), self.half_shift_pay)

    def test_asd_permanent_plus_substitute(self):
        """דירת ASD עם קבוע + מחליף → קבוע יחיד, משמרת שלמה."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])

        reports = [
            _make_report(1, 100, date(2025, 10, 5),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
            _make_report(2, 100, date(2025, 10, 6),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE, 2: "substitute"}

        result = calculate_holiday_payments(
            self.conn, 2025, 10, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
            person_start_dates=_start_dates(1, 2),
        )
        # רק קבוע אחד בדירה (person 2 מחליף) → full shift
        self.assertAlmostEqual(_get_amount(result, 1), self.full_shift_pay)
        self.assertEqual(_get_amount(result, 2), 0)


class TestSeniorityFilter(unittest.TestCase):
    """בדיקות סינון ותק — מדריך עם פחות מ-3 חודשים לא מקבל חג."""

    def setUp(self):
        self.conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"start_time": "08:00", "end_time": "16:00"}
        ]
        self.conn.cursor.return_value = mock_cursor

        import core.holiday_payment as hp_mod
        hp_mod._weekday_shift_work_minutes_cache = None

        self.minimum_wage = 32.3
        self.full_shift_pay = round(480 / 60, 2) * round(self.minimum_wage, 2)
        self.half_shift_pay = round(480 / 2 / 60, 2) * round(self.minimum_wage, 2)

    def test_has_sufficient_seniority_true(self):
        """ותק 3+ חודשים → True."""
        self.assertTrue(_has_sufficient_seniority(date(2025, 6, 1), 2025, 10))

    def test_has_sufficient_seniority_exactly_3_months(self):
        """ותק בדיוק 3 חודשים → True."""
        self.assertTrue(_has_sufficient_seniority(date(2025, 7, 1), 2025, 10))

    def test_has_sufficient_seniority_false(self):
        """ותק פחות מ-3 חודשים → False."""
        self.assertFalse(_has_sufficient_seniority(date(2025, 8, 1), 2025, 10))

    def test_has_sufficient_seniority_none(self):
        """ללא תאריך התחלה → False."""
        self.assertFalse(_has_sufficient_seniority(None, 2025, 10))

    def test_has_sufficient_seniority_cross_year(self):
        """ותק חוצה שנה (חג בינואר, התחלה באוקטובר) → True."""
        self.assertTrue(_has_sufficient_seniority(date(2024, 10, 1), 2025, 1))

    def test_has_sufficient_seniority_cross_year_too_new(self):
        """ותק חוצה שנה, פחות מ-3 חודשים → False."""
        self.assertFalse(_has_sufficient_seniority(date(2024, 11, 2), 2025, 1))

    def test_new_guide_no_holiday_payment(self):
        """מדריך חדש (פחות מ-3 חודשים) בדירת ASD לא מקבל תשלום חג."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])

        reports = [
            _make_report(1, 100, date(2025, 10, 5),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE}
        # התחיל באוגוסט — פחות מ-3 חודשים לפני אוקטובר
        person_start_dates = {1: date(2025, 8, 15)}

        result = calculate_holiday_payments(
            self.conn, 2025, 10, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
            person_start_dates=person_start_dates,
        )
        self.assertEqual(_get_amount(result, 1), 0)

    def test_veteran_guide_gets_payment(self):
        """מדריך ותיק (3+ חודשים) מקבל תשלום חג כרגיל."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])

        reports = [
            _make_report(1, 100, date(2025, 10, 5)),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE}
        person_start_dates = {1: date(2025, 5, 1)}

        result = calculate_holiday_payments(
            self.conn, 2025, 10, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
            person_start_dates=person_start_dates,
        )
        self.assertAlmostEqual(_get_amount(result, 1), self.full_shift_pay)

    def test_new_regular_housing_guide_no_holiday_payment(self):
        """מדריך חדש במערך רגיל לא מקבל תשלום חג."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])

        reports = [
            _make_report(1, 100, date(2025, 10, 5), housing_array_id=1),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE}
        person_start_dates = {1: date(2025, 8, 15)}

        result = calculate_holiday_payments(
            self.conn, 2025, 10, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
            person_start_dates=person_start_dates,
        )
        self.assertEqual(_get_amount(result, 1), 0)

    def test_mixed_seniority_two_guides(self):
        """2 קבועים בדירת ASD: ותיק + חדש — ותיק מקבל שלמה (ASD), חדש לא מקבל."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])

        reports = [
            _make_report(1, 100, date(2025, 10, 5),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
            _make_report(2, 100, date(2025, 10, 6),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE, 2: PERMANENT_EMPLOYEE_TYPE}
        person_start_dates = {
            1: date(2020, 1, 1),  # ותיק
            2: date(2025, 9, 1),  # חדש — פחות מ-3 חודשים
        }

        result = calculate_holiday_payments(
            self.conn, 2025, 10, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
            person_start_dates=person_start_dates,
        )
        # ASD → משמרת שלמה לוותיק
        self.assertAlmostEqual(_get_amount(result, 1), self.full_shift_pay)
        # חדש: לא מקבל (אין ותק ב-ASD)
        self.assertEqual(_get_amount(result, 2), 0)

    def test_new_guide_still_counted_for_num_permanent(self):
        """ASD: מדריך חדש נספר כקבוע — ותיק מקבל שלמה (ASD), חדש לא (אין ותק)."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])

        reports = [
            _make_report(1, 100, date(2025, 10, 5),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
            _make_report(2, 100, date(2025, 10, 6),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE, 2: PERMANENT_EMPLOYEE_TYPE}
        person_start_dates = {
            1: date(2020, 1, 1),  # ותיק
            2: date(2025, 8, 15), # חדש
        }

        result = calculate_holiday_payments(
            self.conn, 2025, 10, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
            person_start_dates=person_start_dates,
        )
        # ASD → ותיק מקבל שלמה
        self.assertAlmostEqual(_get_amount(result, 1), self.full_shift_pay)


    def test_both_new_guides_no_payment(self):
        """2 קבועים חדשים בדירת ASD — אף אחד לא מקבל תשלום חג."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])

        reports = [
            _make_report(1, 100, date(2025, 10, 5),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
            _make_report(2, 100, date(2025, 10, 6),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE, 2: PERMANENT_EMPLOYEE_TYPE}
        person_start_dates = {
            1: date(2025, 8, 1),
            2: date(2025, 9, 1),
        }

        result = calculate_holiday_payments(
            self.conn, 2025, 10, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
            person_start_dates=person_start_dates,
        )
        self.assertEqual(_get_amount(result, 1), 0)
        self.assertEqual(_get_amount(result, 2), 0)

    def test_asd_new_guide_no_payment(self):
        """דירת ASD + מדריך חדש — פטור ASD לא עוקף סינון ותק."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])

        reports = [
            _make_report(1, 100, date(2025, 10, 5),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
            _make_report(2, 100, date(2025, 10, 6),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE, 2: PERMANENT_EMPLOYEE_TYPE}
        person_start_dates = {
            1: date(2020, 1, 1),  # ותיק
            2: date(2025, 9, 1),  # חדש
        }

        result = calculate_holiday_payments(
            self.conn, 2025, 10, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
            person_start_dates=person_start_dates,
        )
        # ASD → משמרת שלמה, אבל רק הוותיק מקבל
        self.assertAlmostEqual(_get_amount(result, 1), self.full_shift_pay)
        self.assertEqual(_get_amount(result, 2), 0)

    def test_asd_low_functioning_new_guide_no_payment(self):
        """דירת תפקוד נמוך (ASD) + מדריך חדש — גם כאן ותק עוקף."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])

        reports = [
            _make_report(1, 100, date(2025, 10, 5),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=LOW_FUNCTIONING_APT_TYPE),
            _make_report(2, 100, date(2025, 10, 6),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=LOW_FUNCTIONING_APT_TYPE),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE, 2: PERMANENT_EMPLOYEE_TYPE}
        person_start_dates = {
            1: date(2020, 1, 1),  # ותיק
            2: date(2025, 9, 1),  # חדש
        }

        result = calculate_holiday_payments(
            self.conn, 2025, 10, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
            person_start_dates=person_start_dates,
        )
        # ASD תפקוד נמוך → משמרת שלמה, אבל רק הוותיק מקבל
        self.assertAlmostEqual(_get_amount(result, 1), self.full_shift_pay)
        self.assertEqual(_get_amount(result, 2), 0)

    def test_new_guide_worked_holiday_veteran_gets_full_asd(self):
        """ASD: חדש עבד בחג, ותיק לא — ותיק מקבל שלמה (ASD תמיד שלמה)."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])

        reports = [
            _make_report(1, 100, date(2025, 10, 5),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
            _make_report(2, 100, holiday,
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
            _make_report(2, 100, date(2025, 10, 6),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE, 2: PERMANENT_EMPLOYEE_TYPE}
        person_start_dates = {
            1: date(2020, 1, 1),  # ותיק, לא עבד בחג
            2: date(2025, 9, 1),  # חדש, עבד בחג
        }

        result = calculate_holiday_payments(
            self.conn, 2025, 10, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
            person_start_dates=person_start_dates,
        )
        # ASD → ותיק eligible (לא עבד בחג), משמרת שלמה
        self.assertAlmostEqual(_get_amount(result, 1), self.full_shift_pay)
        # חדש עבד בחג → לא eligible בכלל (וגם אין ותק)
        self.assertEqual(_get_amount(result, 2), 0)

    def test_start_date_as_datetime(self):
        """start_date מגיע כ-datetime — ההמרה ל-date עובדת."""
        from datetime import datetime
        self.assertTrue(
            _has_sufficient_seniority(datetime(2025, 6, 15, 10, 30), 2025, 10)
        )
        self.assertFalse(
            _has_sufficient_seniority(datetime(2025, 8, 1, 0, 0), 2025, 10)
        )

    def test_empty_start_dates_dict_blocks_all_asd(self):
        """ASD: person_start_dates ריק → אף מדריך לא מקבל חג."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])

        reports = [
            _make_report(1, 100, date(2025, 10, 5),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
        ]
        person_types = {1: PERMANENT_EMPLOYEE_TYPE}

        result = calculate_holiday_payments(
            self.conn, 2025, 10, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
            person_start_dates={},
        )
        self.assertEqual(_get_amount(result, 1), 0)

    def test_january_holiday_october_start(self):
        """חג בינואר, התחיל ב-31 באוקטובר — פחות מ-3 חודשים מלאים."""
        # cutoff = date(2024, 10, 1). Oct 31 > Oct 1 → False
        self.assertFalse(_has_sufficient_seniority(date(2024, 10, 31), 2025, 1))
        # אבל מי שהתחיל ב-1 באוקטובר כן מקבל
        self.assertTrue(_has_sufficient_seniority(date(2024, 10, 1), 2025, 1))

    def test_three_guides_two_veteran_one_new(self):
        """ASD: 3 קבועים: 2 ותיקים + 1 חדש — ותיקים מקבלים שלמה, חדש לא."""
        holiday = date(2025, 10, 2)
        cache = _make_shabbat_cache_with_holiday([holiday])

        reports = [
            _make_report(1, 100, date(2025, 10, 5),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
            _make_report(2, 100, date(2025, 10, 6),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
            _make_report(3, 100, date(2025, 10, 7),
                         housing_array_id=ASD_HOUSING_ARRAY_ID,
                         apartment_type_id=HIGH_FUNCTIONING_APT_TYPE),
        ]
        person_types = {
            1: PERMANENT_EMPLOYEE_TYPE,
            2: PERMANENT_EMPLOYEE_TYPE,
            3: PERMANENT_EMPLOYEE_TYPE,
        }
        person_start_dates = {
            1: date(2020, 1, 1),
            2: date(2020, 1, 1),
            3: date(2025, 9, 1),  # חדש
        }

        result = calculate_holiday_payments(
            self.conn, 2025, 10, cache, self.minimum_wage,
            all_reports=reports, person_types=person_types,
            person_start_dates=person_start_dates,
        )
        # ASD: 3 קבועים → שלמה לוותיקים, חדש לא מקבל
        self.assertAlmostEqual(_get_amount(result, 1), self.full_shift_pay)
        self.assertAlmostEqual(_get_amount(result, 2), self.full_shift_pay)
        self.assertEqual(_get_amount(result, 3), 0)


if __name__ == "__main__":
    unittest.main()
