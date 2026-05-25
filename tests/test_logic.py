"""
Unit tests for logic module - testing critical calculation functions.
"""

import unittest
from datetime import datetime, date
from unittest.mock import Mock, patch, MagicMock
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app_utils import (
    aggregate_daily_segments_to_monthly,
    calculate_wage_rate,
    get_effective_hourly_rate,
    _get_asd_seniority_supplement,
    _apply_tagbur_dynamic_boundaries,
    _filter_asd_completion_reports_for_one_time_exclusion,
    _filter_previous_month_carryover_reports,
)
from core.constants import (
    ASD_SENIORITY_SUPPLEMENT,
    TAGBUR_FRIDAY_SHIFT_ID,
    TAGBUR_SHABBAT_SHIFT_ID,
)
from core.shift_hours import calculate_tagbur_segments
from routes.guide import _allocation_windows_for_report, _apply_calculated_hours_to_shift_rows
from core.sick_days import get_sick_payment_rate
from core.time_utils import (
    minutes_to_time_str,
    span_minutes,
    REGULAR_HOURS_LIMIT,
    OVERTIME_125_LIMIT,
    parse_hhmm,
)
from utils.utils import calculate_annual_vacation_quota, overlap_minutes

# from logic_enhanced import (
#     calculate_wage_rate_enhanced,
#     validate_time_string,
#     validate_date_range,
#     format_hours_minutes,
#     parse_time_to_minutes,
#     calculate_overlap_percentage,
#     ValidationError
# )


class TestWageCalculations(unittest.TestCase):
    """Test wage rate calculations."""

    def test_regular_hours_rate(self):
        """Test wage rate for regular hours (first 8 hours)."""
        # Regular hours, not Shabbat
        self.assertEqual(calculate_wage_rate(0, False), "100%")
        self.assertEqual(calculate_wage_rate(240, False), "100%")  # 4 hours
        self.assertEqual(calculate_wage_rate(480, False), "100%")  # 8 hours

        # Regular hours during Shabbat
        self.assertEqual(calculate_wage_rate(0, True), "150%")
        self.assertEqual(calculate_wage_rate(240, True), "150%")
        self.assertEqual(calculate_wage_rate(480, True), "150%")

    def test_overtime_125_rate(self):
        """Test wage rate for hours 9-10 (125% overtime)."""
        # Hours 9-10, not Shabbat
        self.assertEqual(calculate_wage_rate(481, False), "125%")  # Just over 8 hours
        self.assertEqual(calculate_wage_rate(540, False), "125%")  # 9 hours
        self.assertEqual(calculate_wage_rate(600, False), "125%")  # 10 hours

        # Hours 9-10 during Shabbat
        self.assertEqual(calculate_wage_rate(481, True), "175%")
        self.assertEqual(calculate_wage_rate(540, True), "175%")
        self.assertEqual(calculate_wage_rate(600, True), "175%")

    def test_overtime_150_rate(self):
        """Test wage rate for hours 11+ (150% overtime)."""
        # Hours 11+, not Shabbat
        self.assertEqual(calculate_wage_rate(601, False), "150%")  # Just over 10 hours
        self.assertEqual(calculate_wage_rate(720, False), "150%")  # 12 hours
        self.assertEqual(calculate_wage_rate(900, False), "150%")  # 15 hours

        # Hours 11+ during Shabbat
        self.assertEqual(calculate_wage_rate(601, True), "200%")
        self.assertEqual(calculate_wage_rate(720, True), "200%")
        self.assertEqual(calculate_wage_rate(900, True), "200%")

    # def test_enhanced_wage_rate(self):
    #     """Test enhanced wage rate function with type safety."""
    #     rate_type, percentage = calculate_wage_rate_enhanced(240, False)
    #     self.assertEqual(percentage, 1.0)
    #
    #     rate_type, percentage = calculate_wage_rate_enhanced(540, False)
    #     self.assertEqual(percentage, 1.25)
    #
    #     rate_type, percentage = calculate_wage_rate_enhanced(700, True)
    #     self.assertEqual(percentage, 2.0)
    #
    #     # Test negative minutes validation
    #     with self.assertRaises(ValidationError):
    #         calculate_wage_rate_enhanced(-10, False)


class TestVacationCalculations(unittest.TestCase):
    """Test vacation quota calculations."""

    def test_vacation_quota_5_day_week(self):
        """Test vacation quota for 5-day work week."""
        # First 5 years
        for year in range(1, 6):
            self.assertEqual(calculate_annual_vacation_quota(year, False), 12)

        # Year 6
        self.assertEqual(calculate_annual_vacation_quota(6, False), 14)

        # Year 10
        self.assertEqual(calculate_annual_vacation_quota(10, False), 18)

        # Year 12+
        self.assertEqual(calculate_annual_vacation_quota(12, False), 20)
        self.assertEqual(calculate_annual_vacation_quota(15, False), 20)

    def test_vacation_quota_6_day_week(self):
        """Test vacation quota for 6-day work week."""
        # First 4 years
        for year in range(1, 5):
            self.assertEqual(calculate_annual_vacation_quota(year, True), 14)

        # Year 5
        self.assertEqual(calculate_annual_vacation_quota(5, True), 16)

        # Year 7
        self.assertEqual(calculate_annual_vacation_quota(7, True), 21)

        # Year 10+
        self.assertEqual(calculate_annual_vacation_quota(10, True), 24)
        self.assertEqual(calculate_annual_vacation_quota(15, True), 24)


class TestTimeUtilities(unittest.TestCase):
    """Test time utility functions."""

    def test_minutes_to_time_str(self):
        """Test conversion from minutes to HH:MM string."""
        self.assertEqual(minutes_to_time_str(0), "00:00")
        self.assertEqual(minutes_to_time_str(60), "01:00")
        self.assertEqual(minutes_to_time_str(90), "01:30")
        self.assertEqual(minutes_to_time_str(480), "08:00")
        self.assertEqual(minutes_to_time_str(1439), "23:59")

    def test_parse_hhmm(self):
        """Test parsing HH:MM to (hours, minutes) tuple."""
        # parse_hhmm returns (hours, minutes) tuple, not total minutes
        self.assertEqual(parse_hhmm("00:00"), (0, 0))
        self.assertEqual(parse_hhmm("01:00"), (1, 0))
        self.assertEqual(parse_hhmm("08:30"), (8, 30))
        self.assertEqual(parse_hhmm("23:59"), (23, 59))

    def test_span_minutes(self):
        """Test calculating span between times - returns (start_min, end_min) tuple."""
        # span_minutes returns (start_minutes, end_minutes) tuple, not duration
        # Same day: 08:00 = 480, 16:00 = 960
        start, end = span_minutes("08:00", "16:00")
        self.assertEqual(start, 480)
        self.assertEqual(end, 960)
        self.assertEqual(end - start, 480)  # Duration is 8 hours

        # 09:30 = 570, 17:45 = 1065
        start, end = span_minutes("09:30", "17:45")
        self.assertEqual(start, 570)
        self.assertEqual(end, 1065)
        self.assertEqual(end - start, 495)  # Duration is 8:15

        # Cross midnight: 22:00 = 1320, 06:00 = 360 -> becomes 1800 (360 + 1440)
        start, end = span_minutes("22:00", "06:00")
        self.assertEqual(start, 1320)
        self.assertEqual(end, 1800)  # 360 + 1440 for overnight
        self.assertEqual(end - start, 480)  # Duration is 8 hours

        # 23:00 = 1380, 01:00 = 60 -> becomes 1500 (60 + 1440)
        start, end = span_minutes("23:00", "01:00")
        self.assertEqual(start, 1380)
        self.assertEqual(end, 1500)  # 60 + 1440 for overnight
        self.assertEqual(end - start, 120)  # Duration is 2 hours


class TestTagburDynamicBoundaries(unittest.TestCase):
    """בדיקות גבולות דינמיים למשמרות תגבור."""

    def setUp(self):
        self.friday = date(2026, 4, 10)
        self.saturday = date(2026, 4, 11)
        self.shabbat_cache = {
            "2026-04-11": {
                "enter": "17:00",
                "exit": "19:00",
            }
        }

    def test_friday_tagbur_before_2026_04_keeps_dynamic_start(self):
        segs = [{"start_time": "15:00", "end_time": "22:00", "segment_type": "work"}]

        result = _apply_tagbur_dynamic_boundaries(
            TAGBUR_FRIDAY_SHIFT_ID,
            segs,
            self.friday,
            report_start_min=16 * 60 + 15,
            report_end_min=22 * 60,
            year=2026,
            month=3,
            shabbat_cache=self.shabbat_cache,
        )

        self.assertEqual(result[0]["start_time"], "16:00")

    def test_friday_tagbur_from_2026_04_clips_late_report_start(self):
        segs = [{"start_time": "15:00", "end_time": "22:00", "segment_type": "work"}]

        result = _apply_tagbur_dynamic_boundaries(
            TAGBUR_FRIDAY_SHIFT_ID,
            segs,
            self.friday,
            report_start_min=16 * 60 + 15,
            report_end_min=22 * 60,
            year=2026,
            month=4,
            shabbat_cache=self.shabbat_cache,
        )

        self.assertEqual(result[0]["start_time"], "16:15")

    def test_friday_tagbur_from_2026_04_keeps_early_report_start(self):
        segs = [{"start_time": "15:00", "end_time": "22:00", "segment_type": "work"}]

        result = _apply_tagbur_dynamic_boundaries(
            TAGBUR_FRIDAY_SHIFT_ID,
            segs,
            self.friday,
            report_start_min=15 * 60 + 30,
            report_end_min=22 * 60,
            year=2026,
            month=4,
            shabbat_cache=self.shabbat_cache,
        )

        self.assertEqual(result[0]["start_time"], "16:00")

    def test_shabbat_tagbur_before_2026_04_keeps_dynamic_end(self):
        segs = [
            {"start_time": "10:00", "end_time": "16:00", "segment_type": "work"},
            {"start_time": "17:00", "end_time": "19:00", "segment_type": "work"},
        ]

        result = _apply_tagbur_dynamic_boundaries(
            TAGBUR_SHABBAT_SHIFT_ID,
            segs,
            self.saturday,
            report_start_min=0,
            report_end_min=20 * 60,
            year=2026,
            month=3,
            shabbat_cache=self.shabbat_cache,
        )

        self.assertEqual(result[-1]["end_time"], "21:00")

    def test_shabbat_tagbur_from_2026_04_clips_early_report_end(self):
        segs = [
            {"start_time": "10:00", "end_time": "16:00", "segment_type": "work"},
            {"start_time": "17:00", "end_time": "19:00", "segment_type": "work"},
        ]

        result = _apply_tagbur_dynamic_boundaries(
            TAGBUR_SHABBAT_SHIFT_ID,
            segs,
            self.saturday,
            report_start_min=0,
            report_end_min=20 * 60,
            year=2026,
            month=4,
            shabbat_cache=self.shabbat_cache,
        )

        self.assertEqual(result[-1]["end_time"], "20:00")

    def test_shift_report_friday_tagbur_uses_calculation_start_before_2026_04(self):
        segments_by_shift = {
            TAGBUR_FRIDAY_SHIFT_ID: [
                {"start_time": "15:00", "end_time": "22:00", "segment_type": "work"}
            ]
        }

        result = calculate_tagbur_segments(
            "16:15",
            "22:00",
            TAGBUR_FRIDAY_SHIFT_ID,
            segments_by_shift,
            report_date=self.friday,
            year=2026,
            month=3,
            shabbat_cache=self.shabbat_cache,
        )

        self.assertEqual(result[0]["display_start"], "16:00")
        self.assertEqual(result[0]["display_end"], "22:00")
        self.assertEqual(result[0]["work_hours"], 6.0)

    def test_shift_report_friday_tagbur_clips_start_from_2026_04(self):
        segments_by_shift = {
            TAGBUR_FRIDAY_SHIFT_ID: [
                {"start_time": "15:00", "end_time": "22:00", "segment_type": "work"}
            ]
        }

        result = calculate_tagbur_segments(
            "16:15",
            "22:00",
            TAGBUR_FRIDAY_SHIFT_ID,
            segments_by_shift,
            report_date=self.friday,
            year=2026,
            month=4,
            shabbat_cache=self.shabbat_cache,
        )

        self.assertEqual(result[0]["display_start"], "16:15")
        self.assertEqual(result[0]["display_end"], "22:00")
        self.assertEqual(result[0]["work_hours"], 5.75)

    def test_shift_report_shabbat_tagbur_uses_calculation_end_before_2026_04(self):
        segments_by_shift = {
            TAGBUR_SHABBAT_SHIFT_ID: [
                {"start_time": "10:00", "end_time": "16:00", "segment_type": "work"},
                {"start_time": "17:00", "end_time": "19:00", "segment_type": "work"},
            ]
        }

        result = calculate_tagbur_segments(
            "00:00",
            "20:00",
            TAGBUR_SHABBAT_SHIFT_ID,
            segments_by_shift,
            report_date=self.saturday,
            year=2026,
            month=3,
            shabbat_cache=self.shabbat_cache,
        )

        self.assertEqual(result[-1]["display_end"], "21:00")
        self.assertEqual(sum(row["work_hours"] for row in result), 10.0)

    def test_shift_report_shabbat_tagbur_clips_end_from_2026_04(self):
        segments_by_shift = {
            TAGBUR_SHABBAT_SHIFT_ID: [
                {"start_time": "10:00", "end_time": "16:00", "segment_type": "work"},
                {"start_time": "17:00", "end_time": "19:00", "segment_type": "work"},
            ]
        }

        result = calculate_tagbur_segments(
            "00:00",
            "20:00",
            TAGBUR_SHABBAT_SHIFT_ID,
            segments_by_shift,
            report_date=self.saturday,
            year=2026,
            month=4,
            shabbat_cache=self.shabbat_cache,
        )

        self.assertEqual(result[-1]["display_end"], "20:00")
        self.assertEqual(sum(row["work_hours"] for row in result), 9.0)


class TestShiftReportDisplayAllocation(unittest.TestCase):
    """בדיקות הקצאת שעות חישוב לשורות דוח משמרות בלי שינוי מבנה הדוח."""

    def test_report_row_can_receive_hours_from_next_workday(self):
        rows = [{
            "date": "06/04/26",
            "day": "שני",
            "apartment": "דירה",
            "shift_type": "חול",
            "start_time": "16:00",
            "end_time": "08:30",
            "work_hours": 0.0,
            "standby_hours": 0.0,
            "_allocation_windows": _allocation_windows_for_report(date(2026, 4, 6), "16:00", "08:30"),
        }]
        daily_segments = [
            {
                "date_obj": date(2026, 4, 6),
                "total_minutes_no_standby": 450,
                "chains": [
                    {"type": "work", "start_time": "16:00", "end_time": "22:00", "total_minutes": 360},
                    {"type": "standby", "start_time": "22:00", "end_time": "06:30", "total_minutes": 510},
                    {"type": "work", "start_time": "06:30", "end_time": "08:00", "total_minutes": 90},
                ],
            },
            {
                "date_obj": date(2026, 4, 7),
                "total_minutes_no_standby": 30,
                "chains": [
                    {"type": "work", "start_time": "08:00", "end_time": "08:30", "total_minutes": 30},
                ],
            },
        ]

        total_work_hours, standby_count = _apply_calculated_hours_to_shift_rows(rows, daily_segments)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["work_hours"], 8.0)
        self.assertEqual(rows[0]["standby_hours"], 8.5)
        self.assertEqual(total_work_hours, 8.0)
        self.assertEqual(standby_count, 1)
        self.assertNotIn("_allocation_windows", rows[0])

    # def test_format_hours_minutes(self):
    #     """Test formatting minutes to HH:MM."""
    #     self.assertEqual(format_hours_minutes(0), "00:00")
    #     self.assertEqual(format_hours_minutes(90), "01:30")
    #     self.assertEqual(format_hours_minutes(615), "10:15")

    # def test_parse_time_to_minutes(self):
    #     """Test parsing time string to minutes with validation."""
    #     self.assertEqual(parse_time_to_minutes("08:30"), 510)
    #     self.assertEqual(parse_time_to_minutes("00:00"), 0)
    #     self.assertEqual(parse_time_to_minutes("23:59"), 1439)

        # Test invalid format
        # with self.assertRaises(ValidationError):
        #     parse_time_to_minutes("25:00")

        # with self.assertRaises(ValidationError):
        #     parse_time_to_minutes("12:70")


class TestOverlapCalculations(unittest.TestCase):
    """Test time overlap calculations."""

    def test_overlap_minutes(self):
        """Test calculating overlap between time ranges."""
        # Full overlap
        self.assertEqual(overlap_minutes(480, 600, 480, 600), 120)

        # Partial overlap
        self.assertEqual(overlap_minutes(480, 600, 540, 660), 60)

        # No overlap
        self.assertEqual(overlap_minutes(480, 540, 600, 660), 0)

        # One range contains the other
        self.assertEqual(overlap_minutes(480, 720, 540, 600), 60)


class TestSickPaymentRate(unittest.TestCase):
    """Test sick day payment rate calculations."""

    def test_first_day_zero_percent(self):
        """Test that first sick day pays 0%."""
        self.assertEqual(get_sick_payment_rate(1), 0.0)

    def test_days_2_3_fifty_percent(self):
        """Test that days 2-3 pay 50%."""
        self.assertEqual(get_sick_payment_rate(2), 0.5)
        self.assertEqual(get_sick_payment_rate(3), 0.5)

    def test_day_4_plus_full_payment(self):
        """Test that day 4 and beyond pay 100%."""
        self.assertEqual(get_sick_payment_rate(4), 1.0)
        self.assertEqual(get_sick_payment_rate(5), 1.0)
        self.assertEqual(get_sick_payment_rate(10), 1.0)
        self.assertEqual(get_sick_payment_rate(30), 1.0)


class TestEffectiveHourlyRate(unittest.TestCase):
    """Test effective hourly rate calculation with housing rates."""

    def test_custom_rate_used(self):
        """Test that custom rate from housing_rates_cache is used."""
        report = {'shift_type_id': 101, 'housing_array_id': 1, 'is_married': False}
        minimum_wage = 32.30
        # תעריף קבוע 50 ש"ח (5000 אגורות)
        housing_rates_cache = {
            (101, 1): {'weekday_single_rate': 5000, 'weekday_single_wage_percentage': None,
                       'weekday_married_rate': None, 'weekday_married_wage_percentage': None,
                       'shabbat_rate': None, 'shabbat_wage_percentage': None}
        }
        self.assertEqual(get_effective_hourly_rate(report, minimum_wage, False, housing_rates_cache), 50.0)

    def test_minimum_wage_when_no_rate(self):
        """Test that minimum wage is used when no housing rate defined."""
        report = {}
        minimum_wage = 32.30
        self.assertEqual(get_effective_hourly_rate(report, minimum_wage), 32.30)

    def test_minimum_wage_when_rate_none(self):
        """Test that minimum wage is used when housing_rates_cache is None."""
        report = {'shift_type_id': 101, 'housing_array_id': 1}
        minimum_wage = 32.30
        self.assertEqual(get_effective_hourly_rate(report, minimum_wage, False, None), 32.30)

    def test_minimum_wage_when_rate_zero(self):
        """Test that minimum wage is used when all rate fields are empty."""
        report = {'shift_type_id': 101, 'housing_array_id': 1, 'is_married': False}
        minimum_wage = 32.30
        housing_rates_cache = {
            (101, 1): {'weekday_single_rate': None, 'weekday_single_wage_percentage': None,
                       'weekday_married_rate': None, 'weekday_married_wage_percentage': None,
                       'shabbat_rate': None, 'shabbat_wage_percentage': None}
        }
        self.assertEqual(get_effective_hourly_rate(report, minimum_wage, False, housing_rates_cache), 32.30)

    def test_rate_conversion_from_agorot(self):
        """Test that rate is correctly converted from agorot to shekels."""
        # 3230 agorot = 32.30 shekels
        report = {'shift_type_id': 101, 'housing_array_id': 1, 'is_married': True}
        minimum_wage = 30.0
        housing_rates_cache = {
            (101, 1): {'weekday_single_rate': None, 'weekday_single_wage_percentage': None,
                       'weekday_married_rate': 3230, 'weekday_married_wage_percentage': None,
                       'shabbat_rate': None, 'shabbat_wage_percentage': None}
        }
        self.assertEqual(get_effective_hourly_rate(report, minimum_wage, False, housing_rates_cache), 32.30)


class TestPaymentComponentsClassification(unittest.TestCase):
    """סיווג רכיבי תשלום ידניים בסיכום חודשי."""

    def test_vacation_payment_details_group_hours_by_rate(self):
        totals = aggregate_daily_segments_to_monthly(
            conn=None,
            daily_segments=[
                {
                    "date_obj": date(2026, 4, 1),
                    "total_minutes_no_standby": 180,
                    "chains": [
                        {
                            "type": "vacation",
                            "total_minutes": 120,
                            "payment": 68.80,
                            "effective_rate": 34.40,
                        },
                        {
                            "type": "vacation",
                            "total_minutes": 60,
                            "payment": 40.00,
                            "effective_rate": 40.00,
                        },
                    ],
                }
            ],
            person_id=1,
            year=2026,
            month=4,
            minimum_wage=34.40,
            preloaded_payment_comps=[],
            person_start_date=date(2026, 1, 1),
        )

        self.assertEqual(
            totals["vacation_payment_details"],
            [
                {"hours": 2.0, "rate": 34.4, "payment": 68.8},
                {"hours": 1.0, "rate": 40.0, "payment": 40.0},
            ],
        )

    def test_sick_payment_details_group_paid_hours_by_rate(self):
        totals = aggregate_daily_segments_to_monthly(
            conn=None,
            daily_segments=[
                {
                    "date_obj": date(2026, 4, 1),
                    "total_minutes_no_standby": 180,
                    "chains": [
                        {
                            "type": "sick",
                            "total_minutes": 120,
                            "payment": 34.40,
                            "effective_rate": 34.40,
                            "sick_rate_percent": 50,
                        },
                        {
                            "type": "sick",
                            "total_minutes": 60,
                            "payment": 40.00,
                            "effective_rate": 40.00,
                            "sick_rate_percent": 100,
                        },
                    ],
                }
            ],
            person_id=1,
            year=2026,
            month=4,
            minimum_wage=34.40,
            preloaded_payment_comps=[],
            person_start_date=date(2026, 1, 1),
        )

        self.assertEqual(
            totals["sick_payment_details"],
            [
                {"hours": 1.0, "rate": 34.4, "payment": 34.4, "raw_hours": 2.0},
                {"hours": 1.0, "rate": 40.0, "payment": 40.0, "raw_hours": 1.0},
            ],
        )

    def test_preloaded_for_pension_components_are_split_from_regular_extras(self):
        totals = aggregate_daily_segments_to_monthly(
            conn=None,
            daily_segments=[],
            person_id=1,
            year=2026,
            month=4,
            minimum_wage=34.40,
            preloaded_payment_comps=[
                {"total_amount": 10000, "component_type_id": 99, "for_pension": True},
                {"total_amount": 2500, "component_type_id": 99, "for_pension": False},
            ],
            person_start_date=date(2026, 1, 1),
        )

        self.assertEqual(totals["extras_for_pension"], 100.0)
        self.assertEqual(totals["extras"], 25.0)
        self.assertEqual(totals["gesher_total"], 125.0)


class TestAsdSenioritySupplement(unittest.TestCase):
    """בדיקות תוספת ותק ASD לפי תאריך תחילת עבודה."""

    def test_exact_calendar_year_is_eligible(self):
        supplement = _get_asd_seniority_supplement(
            "permanent",
            date(2025, 4, 1),
            2026,
            4,
        )

        self.assertEqual(supplement, ASD_SENIORITY_SUPPLEMENT)

    def test_less_than_calendar_year_is_not_eligible(self):
        supplement = _get_asd_seniority_supplement(
            "permanent",
            date(2025, 4, 2),
            2026,
            4,
        )

        self.assertEqual(supplement, 0)


class TestPreviousMonthCarryoverFiltering(unittest.TestCase):
    """בדיקות סינון דיווחים לחישוב carryover מחודש קודם."""

    def test_sick_day_without_times_breaks_previous_chain(self):
        """יום מחלה בלי שעות חותך את ה-carryover הישן."""
        reports = [
            {
                "date": date(2026, 2, 21),
                "start_time": "22:00",
                "end_time": "08:00",
                "shift_type_id": 101,
                "shift_name": "לילה",
            },
            {
                "date": date(2026, 2, 22),
                "start_time": None,
                "end_time": None,
                "shift_type_id": 143,
                "shift_name": "יום מחלה",
            },
        ]

        filtered = _filter_previous_month_carryover_reports(reports, person_id=200)

        self.assertEqual(filtered, [])

    def test_reports_after_sick_break_are_kept(self):
        """אחרי יום מחלה, הרצף החדש עדיין יכול להיספר."""
        reports = [
            {
                "date": date(2026, 2, 25),
                "start_time": "22:00",
                "end_time": "08:00",
                "shift_type_id": 101,
                "shift_name": "לילה",
            },
            {
                "date": date(2026, 2, 26),
                "start_time": None,
                "end_time": None,
                "shift_type_id": 143,
                "shift_name": "יום מחלה",
            },
            {
                "date": date(2026, 2, 28),
                "start_time": "22:00",
                "end_time": "08:00",
                "shift_type_id": 101,
                "shift_name": "לילה",
            },
        ]

        filtered = _filter_previous_month_carryover_reports(reports, person_id=200)

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["date"], date(2026, 2, 28))


class TestOneTimeAsdCompletionExclusion(unittest.TestCase):
    """בדיקות להחרגת השלמות ASD החד-פעמית באפריל 2026."""

    def test_filters_asd_completion_reports_only_for_april_2026(self):
        reports = [
            {"apartment_id": 29, "housing_array_id": 2, "label": "excluded"},
            {"apartment_id": 100, "housing_array_id": 2, "label": "asd_regular"},
            {"apartment_id": 29, "housing_array_id": 1, "label": "other_array_completion"},
        ]

        filtered = _filter_asd_completion_reports_for_one_time_exclusion(
            reports, 2026, 4
        )

        self.assertEqual(
            [report["label"] for report in filtered],
            ["asd_regular", "other_array_completion"],
        )

    def test_does_not_filter_other_months(self):
        reports = [{"apartment_id": 29, "housing_array_id": 2, "label": "kept"}]

        filtered = _filter_asd_completion_reports_for_one_time_exclusion(
            reports, 2026, 5
        )

        self.assertEqual(filtered, reports)


    # def test_overlap_percentage(self):
    #     """Test calculating overlap percentage."""
    #     # Full overlap
    #     self.assertAlmostEqual(calculate_overlap_percentage(480, 600, 480, 600), 1.0)
    #
    #     # 50% overlap
    #     self.assertAlmostEqual(calculate_overlap_percentage(480, 600, 540, 660), 0.5)
    #
    #     # No overlap
    #     self.assertAlmostEqual(calculate_overlap_percentage(480, 540, 600, 660), 0.0)
    #
    #     # Invalid ranges
    #     self.assertAlmostEqual(calculate_overlap_percentage(600, 480, 540, 660), 0.0)


# class TestValidation(unittest.TestCase):
#     """Test validation functions."""

#     def test_validate_time_string(self):
#         """Test time string validation."""
#         # Valid times
#         self.assertTrue(validate_time_string("00:00"))
#         self.assertTrue(validate_time_string("12:30"))
#         self.assertTrue(validate_time_string("23:59"))

#         # Invalid times
#         self.assertFalse(validate_time_string("24:00"))
#         self.assertFalse(validate_time_string("12:60"))
#         self.assertFalse(validate_time_string("invalid"))
#         self.assertFalse(validate_time_string("12"))

#     def test_validate_date_range(self):
#         """Test date range validation."""
#         today = datetime.now()
#         yesterday = today - timedelta(days=1)
#         tomorrow = today + timedelta(days=1)
#         next_year = today + timedelta(days=365)
#         far_future = today + timedelta(days=500)
#         far_past = today - timedelta(days=4000)

#         # Valid ranges
#         self.assertTrue(validate_date_range(yesterday, today))
#         self.assertTrue(validate_date_range(today, tomorrow))
#         self.assertTrue(validate_date_range(today, next_year))

#         # Invalid ranges
#         self.assertFalse(validate_date_range(tomorrow, yesterday))  # End before start
#         self.assertFalse(validate_date_range(today, far_future))     # Too far future
#         self.assertFalse(validate_date_range(far_past, today))       # Too far past


def run_tests():
    """Run all tests and return results."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(TestWageCalculations))
    suite.addTests(loader.loadTestsFromTestCase(TestVacationCalculations))
    suite.addTests(loader.loadTestsFromTestCase(TestTimeUtilities))
    suite.addTests(loader.loadTestsFromTestCase(TestOverlapCalculations))
    suite.addTests(loader.loadTestsFromTestCase(TestSickPaymentRate))
    suite.addTests(loader.loadTestsFromTestCase(TestEffectiveHourlyRate))
    suite.addTests(loader.loadTestsFromTestCase(TestPreviousMonthCarryoverFiltering))
    # suite.addTests(loader.loadTestsFromTestCase(TestValidation))

    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return result


if __name__ == "__main__":
    result = run_tests()
    if result.wasSuccessful():
        print("\n✅ All tests passed successfully!")
    else:
        print(f"\n❌ {len(result.failures)} tests failed, {len(result.errors)} errors")
        sys.exit(1)
