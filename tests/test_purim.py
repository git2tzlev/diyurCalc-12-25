"""
בדיקות יחידה ללוגיקת פורים - חישוב תאריכים, גבולות שעות ותעריפים.
"""

import unittest
from datetime import date, timedelta

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.time_utils import _get_purim_date, _get_purim_boundaries, _is_purim_time
from core.constants import PURIM_ENTER_MINUTES, PURIM_EXIT_MINUTES

# תאריכי פורים 2026 (תשפ"ו) - ירושלים כשאר הארץ
PURIM_2026 = date(2026, 3, 3)


class TestGetPurimDate(unittest.TestCase):
    """בדיקות חישוב תאריך פורים מהלוח העברי."""

    def test_purim_regular_city_2026(self):
        """פורים תשפ"ו = 3 במרץ 2026."""
        result = _get_purim_date(PURIM_2026, is_jerusalem=False)
        self.assertEqual(result, PURIM_2026)

    def test_jerusalem_same_as_regular_2026(self):
        """תשפ"ו: ירושלים מקבלת כמו שאר הארץ = 3 במרץ 2026."""
        result = _get_purim_date(PURIM_2026, is_jerusalem=True)
        self.assertEqual(result, PURIM_2026)

    def test_jerusalem_equals_regular_2026(self):
        """תשפ"ו: אין הבדל בין ירושלים לשאר הארץ."""
        regular = _get_purim_date(PURIM_2026, is_jerusalem=False)
        jerusalem = _get_purim_date(PURIM_2026, is_jerusalem=True)
        self.assertEqual(regular, jerusalem)


class TestGetPurimBoundaries(unittest.TestCase):
    """בדיקות גבולות שעות פורים."""

    def test_purim_day_returns_boundaries(self):
        """ביום פורים מוחזרים הגבולות 08:00-22:00."""
        enter, exit_ = _get_purim_boundaries(PURIM_2026, is_jerusalem=False)
        self.assertEqual(enter, PURIM_ENTER_MINUTES)  # 480 = 08:00
        self.assertEqual(exit_, PURIM_EXIT_MINUTES)    # 1320 = 22:00

    def test_jerusalem_same_boundaries_2026(self):
        """תשפ"ו: ירושלים מקבלת גבולות באותו יום כשאר הארץ."""
        enter, exit_ = _get_purim_boundaries(PURIM_2026, is_jerusalem=True)
        self.assertEqual(enter, PURIM_ENTER_MINUTES)
        self.assertEqual(exit_, PURIM_EXIT_MINUTES)

    def test_non_purim_day_returns_negative(self):
        """ביום שאינו פורים מוחזר (-1, -1)."""
        enter, exit_ = _get_purim_boundaries(date(2026, 3, 10), is_jerusalem=False)
        self.assertEqual((enter, exit_), (-1, -1))

    def test_day_before_purim_returns_negative(self):
        """יום לפני פורים - לא פורים."""
        day_before = PURIM_2026 - timedelta(days=1)
        enter, exit_ = _get_purim_boundaries(day_before, is_jerusalem=False)
        self.assertEqual((enter, exit_), (-1, -1))

    def test_day_after_purim_returns_negative(self):
        """יום אחרי פורים - לא פורים."""
        day_after = PURIM_2026 + timedelta(days=1)
        enter, exit_ = _get_purim_boundaries(day_after, is_jerusalem=False)
        self.assertEqual((enter, exit_), (-1, -1))

    def test_day_after_not_purim_for_jerusalem_2026(self):
        """תשפ"ו: גם ירושלים לא מקבלת פורים ב-4.3 (ט"ו אדר)."""
        day_after = PURIM_2026 + timedelta(days=1)
        enter, exit_ = _get_purim_boundaries(day_after, is_jerusalem=True)
        self.assertEqual((enter, exit_), (-1, -1))


class TestIsPurimTime(unittest.TestCase):
    """בדיקות האם זמן נתון חל בשעות פורים."""

    def test_within_purim_hours(self):
        """10:00 בפורים = בתוך שעות פורים."""
        self.assertTrue(_is_purim_time(PURIM_2026, 600, is_jerusalem=False))

    def test_at_start_boundary_inclusive(self):
        """08:00 בדיוק = תחילת פורים (כולל)."""
        self.assertTrue(_is_purim_time(PURIM_2026, 480, is_jerusalem=False))

    def test_one_minute_before_start(self):
        """07:59 = לפני פורים."""
        self.assertFalse(_is_purim_time(PURIM_2026, 479, is_jerusalem=False))

    def test_at_end_boundary_exclusive(self):
        """22:00 בדיוק = סוף פורים (לא כולל)."""
        self.assertFalse(_is_purim_time(PURIM_2026, 1320, is_jerusalem=False))

    def test_after_end(self):
        """23:00 = אחרי שעות פורים."""
        self.assertFalse(_is_purim_time(PURIM_2026, 1380, is_jerusalem=False))

    def test_midnight_not_purim(self):
        """00:00 בפורים = לא בשעות פורים (לפני 08:00)."""
        self.assertFalse(_is_purim_time(PURIM_2026, 0, is_jerusalem=False))

    def test_not_purim_day(self):
        """10:00 ביום רגיל = לא פורים."""
        self.assertFalse(_is_purim_time(date(2026, 3, 10), 600, is_jerusalem=False))

    def test_overnight_shift_modulo(self):
        """משמרת לילה (>1440 דקות) - modulo מחזיר 480=08:00."""
        self.assertTrue(_is_purim_time(PURIM_2026, 1920, is_jerusalem=False))

    def test_jerusalem_same_as_regular_2026(self):
        """תשפ"ו: ירושלים מקבלת פורים באותו יום כשאר הארץ."""
        self.assertTrue(_is_purim_time(PURIM_2026, 600, is_jerusalem=True))

    def test_jerusalem_day_after_not_purim_2026(self):
        """תשפ"ו: ט"ו אדר (4.3) לא פורים גם לירושלים."""
        day_after = PURIM_2026 + timedelta(days=1)
        self.assertFalse(_is_purim_time(day_after, 600, is_jerusalem=True))

    def test_mid_range(self):
        """15:00 בפורים = בתוך הטווח."""
        self.assertTrue(_is_purim_time(PURIM_2026, 900, is_jerusalem=False))

    def test_just_before_end(self):
        """21:59 בפורים = עדיין בטווח."""
        self.assertTrue(_is_purim_time(PURIM_2026, 1319, is_jerusalem=False))


if __name__ == "__main__":
    unittest.main()
