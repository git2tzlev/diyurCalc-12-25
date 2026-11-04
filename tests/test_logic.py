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
    _should_calculate_monthly_accruals,
)
from core.constants import (
    ASD_SENIORITY_SUPPLEMENT,
    TAGBUR_FRIDAY_SHIFT_ID,
    TAGBUR_SHABBAT_SHIFT_ID,
    WEEKDAY_SHIFT_TYPE_ID,
    WORK_HOUR_SHIFT_ID,
)
from core.shift_hours import build_report_based_tagbur_windows, calculate_tagbur_segments
from routes.guide import (
    _allocation_windows_for_report,
    _apply_calculated_hours_to_shift_rows,
    _apply_payment_period_markers_to_chains,
    _marked_payment_windows_for_report,
)
from core.sick_days import get_sick_payment_rate
from core.history import _resolve_person_status_for_date
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


class TestMonthlyAccrualEligibility(unittest.TestCase):
    """בדיקות זכאות לצבירת חופשה/מחלה לפי חודש עבודה."""

    def test_permanent_started_before_month_end_gets_accruals(self):
        self.assertTrue(
            _should_calculate_monthly_accruals(
                date(2026, 5, 31),
                2026,
                5,
            )
        )

    def test_future_start_date_does_not_get_previous_month_accruals(self):
        self.assertFalse(
            _should_calculate_monthly_accruals(
                date(2026, 6, 1),
                2026,
                5,
            )
        )

    def test_accruals_do_not_depend_on_current_employee_type(self):
        self.assertTrue(
            _should_calculate_monthly_accruals(
                date(2026, 5, 1),
                2026,
                5,
            )
        )


class TestPersonStatusEffectiveDate(unittest.TestCase):
    """בדיקות תוקף יומי של סטטוס עובד."""

    def test_effective_date_uses_old_status_before_change_and_current_from_change_day(self):
        current = {
            "is_married": True,
            "employer_id": 2,
            "employee_type": "permanent",
        }
        history_rows = [{
            "is_married": False,
            "employer_id": 2,
            "employee_type": "substitute",
            "year": 2026,
            "month": 6,
            "effective_date": date(2026, 6, 8),
        }]

        before = _resolve_person_status_for_date(current, history_rows, date(2026, 6, 7))
        on_change = _resolve_person_status_for_date(current, history_rows, date(2026, 6, 8))

        self.assertFalse(before["is_married"])
        self.assertEqual(before["employee_type"], "substitute")
        self.assertTrue(on_change["is_married"])
        self.assertEqual(on_change["employee_type"], "permanent")


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


class TestTagburReportBased(unittest.TestCase):
    """בדיקות תגבור לפי דיווח (מ-10/2026): שעות הדיווח פחות ליל שבת/חג 22:00-10:00."""

    def setUp(self):
        self.friday = date(2026, 10, 9)
        self.saturday = date(2026, 10, 10)
        self.shabbat_cache = {"2026-10-10": {"enter": "17:30", "exit": "18:30"}}
        self.db_segments = [
            {"start_time": "10:00", "end_time": "16:00", "segment_type": "work"},
            {"start_time": "17:00", "end_time": "19:00", "segment_type": "work"},
        ]

    def _apply(self, shift_type_id: int, report_date: date, start_min: int, end_min: int, month: int = 10) -> list[dict]:
        return _apply_tagbur_dynamic_boundaries(
            shift_type_id, self.db_segments, report_date, start_min, end_min, 2026, month, self.shabbat_cache
        )

    def test_friday_tagbur_pays_from_report_start_until_holy_night(self):
        result = self._apply(TAGBUR_FRIDAY_SHIFT_ID, self.friday, 17 * 60 + 10, 32 * 60)

        self.assertEqual(
            [(seg["start_time"], seg["end_time"], seg["day_offset"]) for seg in result],
            [("17:10", "22:00", 0)],
        )

    def test_shabbat_tagbur_pays_continuously_from_ten_until_report_end(self):
        result = self._apply(TAGBUR_SHABBAT_SHIFT_ID, self.saturday, 0, 22 * 60 + 35)

        self.assertEqual(
            [(seg["start_time"], seg["end_time"]) for seg in result],
            [("10:00", "22:35")],
        )

    def test_short_report_inside_holy_night_pays_nothing(self):
        self.assertEqual(self._apply(TAGBUR_SHABBAT_SHIFT_ID, self.saturday, 0, 2 * 60), [])
        self.assertEqual(self._apply(TAGBUR_FRIDAY_SHIFT_ID, self.friday, 23 * 60, 32 * 60), [])

    def test_holiday_followed_by_shabbat_excludes_night_between_them(self):
        cache = {
            "2026-10-09": {"enter": "17:30", "exit": "18:30", "holiday": "חג"},
            "2026-10-10": {"enter": "17:30", "exit": "18:30"},
        }

        windows = build_report_based_tagbur_windows(self.friday, 10 * 60, 36 * 60, cache)

        self.assertEqual(windows, [(10 * 60, 22 * 60), (34 * 60, 36 * 60)])

    def test_next_day_segment_gets_day_offset(self):
        cache = {
            "2026-10-09": {"enter": "17:30", "exit": "18:30", "holiday": "חג"},
            "2026-10-10": {"enter": "17:30", "exit": "18:30"},
        }

        result = _apply_tagbur_dynamic_boundaries(
            TAGBUR_SHABBAT_SHIFT_ID, self.db_segments, self.friday, 10 * 60, 36 * 60, 2026, 10, cache
        )

        self.assertEqual(result[-1]["start_time"], "10:00")
        self.assertEqual(result[-1]["day_offset"], 1)

    def test_september_keeps_table_segments(self):
        result = self._apply(TAGBUR_SHABBAT_SHIFT_ID, self.saturday, 0, 22 * 60 + 35, month=9)

        self.assertEqual(result[0]["start_time"], "10:00")
        self.assertEqual(result[0]["end_time"], "16:00")
        self.assertNotIn("day_offset", result[0])

    def test_shift_report_shows_report_based_hours(self):
        result = calculate_tagbur_segments(
            "00:00",
            "22:30",
            TAGBUR_SHABBAT_SHIFT_ID,
            {TAGBUR_SHABBAT_SHIFT_ID: self.db_segments},
            report_date=self.saturday,
            year=2026,
            month=10,
            shabbat_cache=self.shabbat_cache,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual((result[0]["display_start"], result[0]["display_end"]), ("10:00", "22:30"))
        self.assertEqual(result[0]["work_hours"], 12.5)


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

    def test_explicit_work_hour_row_gets_priority_inside_long_shift(self):
        rows = [
            {
                "date": "20/04/26",
                "day": "שני",
                "apartment": "דירה",
                "shift_type": "חול",
                "start_time": "16:00",
                "end_time": "08:00",
                "work_hours": 0.0,
                "standby_hours": 0.0,
                "_allocation_windows": _allocation_windows_for_report(date(2026, 4, 20), "16:00", "08:00"),
                "_allocation_shift_type_id": WEEKDAY_SHIFT_TYPE_ID,
            },
            {
                "date": "20/04/26",
                "day": "שני",
                "apartment": "דירה",
                "shift_type": "שעת עבודה",
                "start_time": "22:00",
                "end_time": "23:00",
                "work_hours": 0.0,
                "standby_hours": 0.0,
                "_allocation_windows": _allocation_windows_for_report(date(2026, 4, 20), "22:00", "23:00"),
                "_allocation_shift_type_id": WORK_HOUR_SHIFT_ID,
            },
        ]
        daily_segments = [{
            "date_obj": date(2026, 4, 20),
            "total_minutes_no_standby": 510,
            "chains": [
                {
                    "type": "work",
                    "start_time": "16:00",
                    "end_time": "22:00",
                    "total_minutes": 360,
                    "shift_id": WEEKDAY_SHIFT_TYPE_ID,
                },
                {
                    "type": "work",
                    "start_time": "22:00",
                    "end_time": "23:00",
                    "total_minutes": 60,
                    "shift_id": WORK_HOUR_SHIFT_ID,
                },
                {"type": "standby", "start_time": "23:00", "end_time": "06:30", "total_minutes": 450},
                {
                    "type": "work",
                    "start_time": "06:30",
                    "end_time": "08:00",
                    "total_minutes": 90,
                    "shift_id": WEEKDAY_SHIFT_TYPE_ID,
                },
            ],
        }]

        total_work_hours, standby_count = _apply_calculated_hours_to_shift_rows(rows, daily_segments)

        self.assertEqual(rows[0]["work_hours"], 7.5)
        self.assertEqual(rows[0]["standby_hours"], 7.5)
        self.assertEqual(rows[1]["work_hours"], 1.0)
        self.assertEqual(rows[1]["standby_hours"], 0.0)
        self.assertEqual(total_work_hours, 8.5)
        self.assertEqual(standby_count, 1)
        self.assertNotIn("_allocation_shift_type_id", rows[0])

    def test_payment_period_marker_marks_overlapping_chain_only(self):
        markers = _marked_payment_windows_for_report(
            {
                "date": date(2026, 4, 20),
                "start_time": "22:00",
                "end_time": "23:00",
                "payment_year": 2026,
                "payment_month": 5,
                "payment_note": "השלמה",
            },
            2026,
            4,
        )
        daily_segments = [{
            "date_obj": date(2026, 4, 20),
            "chains": [
                {"type": "work", "start_time": "16:00", "end_time": "22:00"},
                {"type": "work", "start_time": "22:00", "end_time": "23:00"},
            ],
        }]

        _apply_payment_period_markers_to_chains(daily_segments, markers)

        self.assertNotIn("payment_period_label", daily_segments[0]["chains"][0])
        self.assertEqual(daily_segments[0]["chains"][1]["payment_period_label"], "לתשלום ב-05/2026")
        self.assertEqual(daily_segments[0]["chains"][1]["payment_period_note"], "השלמה")

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

    def _aggregate_payment_comps(
        self,
        year: int,
        month: int,
        payment_comps: list,
        include_deferred: bool = False,
    ) -> dict:
        return aggregate_daily_segments_to_monthly(
            conn=None,
            daily_segments=[],
            person_id=1,
            year=year,
            month=month,
            minimum_wage=34.40,
            preloaded_payment_comps=payment_comps,
            person_start_date=date(2026, 1, 1),
            include_deferred_payment_items=include_deferred,
        )

    def test_pension_follows_payment_row_flag_from_october_2026(self):
        totals = self._aggregate_payment_comps(2026, 10, [
            {"total_amount": 10000, "component_type_id": 99, "for_pension": True, "type_for_pension": False},
            {"total_amount": 2500, "component_type_id": 99, "for_pension": False, "type_for_pension": True},
        ])

        self.assertEqual(totals["extras_for_pension"], 100.0)
        self.assertEqual(totals["extras"], 25.0)
        self.assertEqual(totals["gesher_total"], 125.0)

    def test_pension_follows_component_type_flag_before_october_2026(self):
        totals = self._aggregate_payment_comps(2026, 9, [
            {"total_amount": 10000, "component_type_id": 99, "for_pension": True, "type_for_pension": False},
            {"total_amount": 2500, "component_type_id": 99, "for_pension": False, "type_for_pension": True},
        ])

        self.assertEqual(totals["extras_for_pension"], 25.0)
        self.assertEqual(totals["extras"], 100.0)

    def test_pension_follows_payment_row_flag_for_completion_paid_from_october_2026(self):
        paid_in_october = {"payment_year": 2026, "payment_month": 10}
        totals = self._aggregate_payment_comps(2026, 3, [
            {"total_amount": 6190, "component_type_id": 6, "for_pension": True, "type_for_pension": False, **paid_in_october},
            {"total_amount": 2500, "component_type_id": 99, "for_pension": False, "type_for_pension": True, **paid_in_october},
        ], include_deferred=True)

        self.assertEqual(totals["extras_for_pension"], 61.9)
        self.assertEqual(totals["extras"], 25.0)

    def test_pension_follows_component_type_flag_for_completion_paid_before_october_2026(self):
        paid_in_september = {"payment_year": 2026, "payment_month": 9}
        totals = self._aggregate_payment_comps(2026, 3, [
            {"total_amount": 6190, "component_type_id": 6, "for_pension": True, "type_for_pension": False, **paid_in_september},
            {"total_amount": 2500, "component_type_id": 99, "for_pension": False, "type_for_pension": True, **paid_in_september},
        ], include_deferred=True)

        self.assertEqual(totals["extras_for_pension"], 25.0)
        self.assertEqual(totals["extras"], 61.9)

    def test_pension_flag_takes_precedence_over_travel_and_professional_support(self):
        totals = self._aggregate_payment_comps(2026, 10, [
            {"total_amount": 1000, "component_type_id": 2, "for_pension": True},
            {"total_amount": 2000, "component_type_id": 7, "for_pension": True},
            {"total_amount": 3000, "component_type_id": 13, "for_pension": True},
        ])

        self.assertEqual(totals["extras_for_pension"], 60.0)
        self.assertEqual(totals["travel"], 0.0)
        self.assertEqual(totals["professional_support"], 0.0)


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
