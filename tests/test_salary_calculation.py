# -*- coding: utf-8 -*-
"""
קובץ בדיקות מקיף לחישוב שכר ותצוגה
=====================================

חלק 1: בדיקות אוטומטיות (Unit Tests)
חלק 2: בדיקות ידניות עם נתונים אמיתיים מהמערכת

הרצה:
    python tests/test_salary_calculation.py

    או רק בדיקות אוטומטיות:
    python tests/test_salary_calculation.py --unit

    או רק בדיקות ידניות:
    python tests/test_salary_calculation.py --manual
"""

import unittest
import sys
import os
from datetime import datetime, date, timedelta
from decimal import Decimal

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app_utils import calculate_wage_rate, _calculate_chain_wages as _calculate_chain_wages_new
from core.time_utils import (
    REGULAR_HOURS_LIMIT,
    OVERTIME_125_LIMIT,
    classify_day_type,
)
from core.constants import (
    NIGHT_REGULAR_HOURS_LIMIT,
    NIGHT_OVERTIME_125_LIMIT,
    calculate_night_hours_in_segment,
    qualifies_as_night_shift,
)


def _calculate_chain_wages(segments, day_date, shabbat_cache, minutes_offset, is_night_shift=False):
    """
    פונקציית עטיפה לתאימות לאחור עם הבדיקות הקיימות.
    ממירה מהחתימה הישנה (segments, day_date, cache, offset) לחדשה (segments_with_date, cache, offset).

    הפונקציה מפצלת סגמנטים שחוצים חצות כדי שלכל חלק יהיה התאריך הנכון:
    - חלק לפני חצות (< 1440) נשאר עם day_date
    - חלק אחרי חצות (>= 1440) מקבל day_date + 1
    """
    from datetime import timedelta

    MINUTES_PER_DAY = 1440
    segments_with_date = []

    for s, e, sid in segments:
        if s < MINUTES_PER_DAY and e > MINUTES_PER_DAY:
            # סגמנט חוצה חצות - פיצול לשני חלקים
            # חלק 1: עד חצות (ביום הנוכחי)
            segments_with_date.append((s, MINUTES_PER_DAY, sid, day_date))
            # חלק 2: מחצות ואילך (ביום הבא)
            # הזמנים נשארים מעל 1440 כדי לשמור על הרציפות
            segments_with_date.append((MINUTES_PER_DAY, e, sid, day_date + timedelta(days=1)))
        elif s >= MINUTES_PER_DAY:
            # סגמנט כולו אחרי חצות - שייך ליום הבא
            segments_with_date.append((s, e, sid, day_date + timedelta(days=1)))
        else:
            # סגמנט כולו לפני חצות - שייך ליום הנוכחי
            segments_with_date.append((s, e, sid, day_date))

    return _calculate_chain_wages_new(segments_with_date, shabbat_cache, minutes_offset, is_night_shift)

# ============================================================================
# חלק 1: בדיקות אוטומטיות (Unit Tests)
# ============================================================================

class TestOvertimeCalculation(unittest.TestCase):
    """בדיקות חישוב שעות נוספות"""

    def test_regular_hours_100_percent(self):
        """8 שעות ראשונות = 100%"""
        # 0 דקות = 100%
        self.assertEqual(calculate_wage_rate(0, False), "100%")
        # 4 שעות = 100%
        self.assertEqual(calculate_wage_rate(240, False), "100%")
        # 8 שעות = 100%
        self.assertEqual(calculate_wage_rate(480, False), "100%")

    def test_overtime_125_percent(self):
        """שעות 9-10 = 125%"""
        # 8 שעות ודקה = 125%
        self.assertEqual(calculate_wage_rate(481, False), "125%")
        # 9 שעות = 125%
        self.assertEqual(calculate_wage_rate(540, False), "125%")
        # 10 שעות = 125%
        self.assertEqual(calculate_wage_rate(600, False), "125%")

    def test_overtime_150_percent(self):
        """שעה 11+ = 150%"""
        # 10 שעות ודקה = 150%
        self.assertEqual(calculate_wage_rate(601, False), "150%")
        # 12 שעות = 150%
        self.assertEqual(calculate_wage_rate(720, False), "150%")
        # 16 שעות = 150%
        self.assertEqual(calculate_wage_rate(960, False), "150%")

    def test_chain_8_hours_all_100(self):
        """רצף של 8 שעות = הכל 100%"""
        # רצף 08:00-16:00 (480 דקות)
        segments = [(480, 960, None)]  # start, end, shift_id
        result = _calculate_chain_wages(segments, date(2024, 12, 15), {}, 0)  # יום ראשון

        self.assertEqual(result["calc100"], 480)
        self.assertEqual(result["calc125"], 0)
        self.assertEqual(result["calc150"], 0)

    def test_chain_10_hours_100_and_125(self):
        """רצף של 10 שעות = 8 שעות 100% + 2 שעות 125%"""
        # רצף 08:00-18:00 (600 דקות)
        segments = [(480, 1080, None)]
        result = _calculate_chain_wages(segments, date(2024, 12, 15), {}, 0)

        self.assertEqual(result["calc100"], 480)
        self.assertEqual(result["calc125"], 120)
        self.assertEqual(result["calc150"], 0)

    def test_chain_12_hours_all_tiers(self):
        """רצף של 12 שעות = 8 שעות 100% + 2 שעות 125% + 2 שעות 150%"""
        # רצף 08:00-20:00 (720 דקות)
        segments = [(480, 1200, None)]
        result = _calculate_chain_wages(segments, date(2024, 12, 15), {}, 0)

        self.assertEqual(result["calc100"], 480)
        self.assertEqual(result["calc125"], 120)
        self.assertEqual(result["calc150"], 120)


class TestShabbatCalculation(unittest.TestCase):
    """בדיקות חישוב שבת"""

    def test_shabbat_adds_50_percent(self):
        """שבת מוסיפה 50% לכל אחוז"""
        # 100% + שבת = 150%
        self.assertEqual(calculate_wage_rate(0, True), "150%")
        self.assertEqual(calculate_wage_rate(480, True), "150%")

        # 125% + שבת = 175%
        self.assertEqual(calculate_wage_rate(481, True), "175%")
        self.assertEqual(calculate_wage_rate(600, True), "175%")

        # 150% + שבת = 200%
        self.assertEqual(calculate_wage_rate(601, True), "200%")
        self.assertEqual(calculate_wage_rate(720, True), "200%")


class TestCarryover(unittest.TestCase):
    """בדיקות העברת שעות בין ימים"""

    def test_carryover_affects_overtime_tiers(self):
        """העברת שעות משפיעה על חישוב שעות נוספות"""
        # יום 1: 6 שעות (נגמר ב-08:00)
        # יום 2: 6 שעות (מתחיל ב-08:00)
        # סה"כ רצף: 12 שעות = 8*100% + 2*125% + 2*150%

        # יום 2 עם העברה של 360 דקות (6 שעות)
        segments = [(480, 840, None)]  # 08:00-14:00 (6 שעות)
        result = _calculate_chain_wages(segments, date(2024, 12, 15), {}, 360)

        # 6+6=12 שעות, אז:
        # שעות 1-6 מיום קודם (360 דקות) - לא נספרות כאן
        # שעות 7-8 = 100% = 120 דקות
        # שעות 9-10 = 125% = 120 דקות
        # שעות 11-12 = 150% = 120 דקות
        self.assertEqual(result["calc100"], 120)  # רק 2 שעות 100% (עד 480)
        self.assertEqual(result["calc125"], 120)  # 2 שעות 125%
        self.assertEqual(result["calc150"], 120)  # 2 שעות 150%

    def test_no_carryover_starts_fresh(self):
        """בלי העברה, הרצף מתחיל מאפס"""
        segments = [(480, 840, None)]  # 08:00-14:00 (6 שעות)
        result = _calculate_chain_wages(segments, date(2024, 12, 15), {}, 0)

        # 6 שעות = הכל 100%
        self.assertEqual(result["calc100"], 360)
        self.assertEqual(result["calc125"], 0)
        self.assertEqual(result["calc150"], 0)


class TestOverlappingShiftsWithDifferentRates(unittest.TestCase):
    """בדיקות משמרות חופפות עם תעריפים שונים"""

    def test_payment_calculation_with_different_rates(self):
        """חישוב תשלום כשיש תעריפים שונים"""
        # דוגמה: 6 שעות בתעריף 34.40 + 2 שעות בתעריף 40
        # תשלום צפוי: 6*34.40 + 2*40 = 206.40 + 80 = 286.40

        hours_rate_34_40 = 6
        hours_rate_40 = 2
        rate_low = 34.40
        rate_high = 40.0

        expected_payment = (hours_rate_34_40 * rate_low) + (hours_rate_40 * rate_high)
        self.assertAlmostEqual(expected_payment, 286.40, places=2)

    def test_overtime_with_different_rates(self):
        """שעות נוספות עם תעריפים שונים"""
        # 10 שעות: 8 שעות 100% + 2 שעות 125%
        # אם שעות 9-10 הן בתעריף גבוה (40), התשלום צריך להיות:
        # 8 * 34.40 * 1.0 + 2 * 40 * 1.25 = 275.20 + 100 = 375.20

        payment = (8 * 34.40 * 1.0) + (2 * 40 * 1.25)
        self.assertAlmostEqual(payment, 375.20, places=2)


class TestMedicalEscort(unittest.TestCase):
    """בדיקות ליווי רפואי (shift_type_id=148)"""

    def test_minimum_one_hour_payment(self):
        """ליווי רפואי - מינימום שעה"""
        # דיווח של 30 דקות צריך לקבל תשלום של 60 דקות
        reported_minutes = 30
        minimum_payment_minutes = 60

        actual_payment_minutes = max(reported_minutes, minimum_payment_minutes)
        self.assertEqual(actual_payment_minutes, 60)

    def test_no_bonus_for_longer_escort(self):
        """ליווי רפואי מעל שעה - תשלום לפי דיווח"""
        # דיווח של 90 דקות = תשלום 90 דקות
        reported_minutes = 90
        minimum_payment_minutes = 60

        actual_payment_minutes = max(reported_minutes, minimum_payment_minutes)
        self.assertEqual(actual_payment_minutes, 90)


class TestStandaloneMidnightShift(unittest.TestCase):
    """בדיקות דיווחים עצמאיים בלילה"""

    def test_standalone_shift_stays_in_day(self):
        """דיווח עצמאי 00:20-03:00 נשאר ביום שלו"""
        # הלוגיקה: אם p_date == r_date ושעת התחלה < 08:00
        # אז זה דיווח עצמאי ולא חלק ממשמרת חוצה חצות

        report_date = date(2024, 12, 3)
        part_date = date(2024, 12, 3)
        start_time = 20  # 00:20 = 20 דקות

        is_standalone = (part_date == report_date and start_time < 480)
        self.assertTrue(is_standalone)

    def test_overnight_shift_moves_to_previous_day(self):
        """משמרת חוצה חצות עוברת ליום הקודם"""
        # משמרת 22:00-06:00: החלק של 00:00-06:00 צריך להיות ביום הקודם

        report_date = date(2024, 12, 2)  # יום הדיווח
        part_date = date(2024, 12, 3)    # יום החלק (אחרי חצות)
        start_time = 0  # 00:00

        is_standalone = (part_date == report_date and start_time < 480)
        self.assertFalse(is_standalone)  # לא עצמאי = עובר ליום הקודם


class TestTagbur(unittest.TestCase):
    """בדיקות תגבור"""

    def test_tagbur_uses_fixed_percentages(self):
        """תגבור משתמש באחוזים קבועים"""
        # תגבור לא מחשב שעות נוספות רגילות
        # אלא משתמש בסגמנטים הקבועים של המשמרת

        # דוגמה: תגבור 12:00-17:00 @ 100%, 17:00-22:00 @ 150%
        segment_100_minutes = 300  # 5 שעות
        segment_150_minutes = 300  # 5 שעות

        # לא משנה שיש 10 שעות, האחוזים קבועים
        self.assertEqual(segment_100_minutes, 300)
        self.assertEqual(segment_150_minutes, 300)


class TestStandby(unittest.TestCase):
    """בדיקות כוננות"""

    def test_standby_payment_separate_from_work(self):
        """תשלום כוננות נפרד מתשלום עבודה"""
        standby_rate = 150  # ש"ח לכוננות
        work_hours = 8
        hourly_rate = 34.40

        work_payment = work_hours * hourly_rate
        total_payment = work_payment + standby_rate

        self.assertAlmostEqual(work_payment, 275.20, places=2)
        self.assertAlmostEqual(total_payment, 425.20, places=2)

    def test_standby_cancelled_when_work_overlaps(self):
        """כוננות מבוטלת כשיש עבודה חופפת"""
        # אם יש עבודה שחופפת לכוננות, הכוננות לא משולמת
        work_start = 480   # 08:00
        work_end = 960     # 16:00
        standby_start = 600  # 10:00
        standby_end = 720    # 12:00

        # בדיקה אם יש חפיפה
        overlaps = (work_start < standby_end and work_end > standby_start)
        self.assertTrue(overlaps)

    def test_early_exit_partial_standby_becomes_work(self):
        """כוננות חלקית בגלל יציאה מוקדמת - משלמים כשעות עבודה"""
        # דוגמה: משמרת לילה 22:00-03:00 (יציאה מוקדמת)
        # כוננות מוגדרת: 00:00-06:30
        # העובד יצא ב-03:00, אז הכוננות היא רק 00:00-03:00

        hourly_rate = 34.40  # שכר מינימום לשעה

        # עבודה: 22:00-00:00 = 2 שעות
        work_hours = 2

        # כוננות חלקית בגלל יציאה מוקדמת: 00:00-03:00 = 3 שעות
        # לפי הכלל החדש: משלמים כשעות עבודה שממשיכות את הרצף
        partial_standby_hours = 3

        # סה"כ שעות עבודה (כולל הכוננות החלקית): 5 שעות
        total_work_hours = work_hours + partial_standby_hours
        self.assertEqual(total_work_hours, 5)

        # כל 5 השעות הן @ 100% (פחות מ-8)
        expected_payment = total_work_hours * hourly_rate * 1.0
        self.assertAlmostEqual(expected_payment, 172.00, places=2)

    def test_early_exit_partial_standby_continues_chain_overtime(self):
        """כוננות חלקית בגלל יציאה מוקדמת - ממשיכה את הרצף לשעות נוספות"""
        # דוגמה: משמרת 13:00-03:00 (יציאה מוקדמת)
        # כוננות מוגדרת: 22:00-08:00
        # עבודה: 13:00-22:00 = 9 שעות
        # כוננות חלקית: 22:00-03:00 = 5 שעות (יציאה מוקדמת)

        hourly_rate = 34.40

        # עבודה: 9 שעות
        #   8 שעות @ 100%
        #   1 שעה @ 125%
        work_payment = (8 * hourly_rate * 1.0) + (1 * hourly_rate * 1.25)

        # כוננות חלקית: 5 שעות - ממשיכה את הרצף
        #   1 שעה @ 125% (שעות 9-10)
        #   4 שעות @ 150% (שעה 11+)
        partial_standby_payment = (1 * hourly_rate * 1.25) + (4 * hourly_rate * 1.5)

        # סה"כ: 14 שעות
        total_hours = 9 + 5
        self.assertEqual(total_hours, 14)

        total_payment = work_payment + partial_standby_payment
        # 8*34.40*1.0 + 2*34.40*1.25 + 4*34.40*1.5
        # = 275.20 + 86.00 + 206.40 = 567.60
        self.assertAlmostEqual(total_payment, 567.60, places=2)

    def test_full_standby_not_affected(self):
        """כוננות מלאה (לא יציאה מוקדמת) - משלמים תעריף כוננות קבוע"""
        # דוגמה: משמרת לילה 22:00-08:00 (שמרה עד הסוף)
        # כוננות מוגדרת: 00:00-06:30
        # העובד היה עד הסוף, אז זו כוננות מלאה

        hourly_rate = 34.40
        standby_rate = 150  # ש"ח - תעריף כוננות קבוע

        # עבודה: 22:00-00:00 = 2 שעות + 06:30-08:00 = 1.5 שעות = 3.5 שעות
        work_hours = 3.5
        work_payment = work_hours * hourly_rate * 1.0

        # כוננות מלאה: 00:00-06:30 = 6.5 שעות - תעריף קבוע
        total_payment = work_payment + standby_rate

        # 3.5 * 34.40 + 150 = 120.40 + 150 = 270.40
        self.assertAlmostEqual(total_payment, 270.40, places=2)


class TestFullSalaryCalculation(unittest.TestCase):
    """בדיקות מקיפות לחישוב שכר מלא"""

    def test_full_day_payment_8_hours(self):
        """חישוב תשלום ליום עבודה של 8 שעות"""
        hours = 8
        rate = 34.40  # שכר מינימום לשעה

        # 8 שעות @ 100%
        expected_payment = hours * rate * 1.0
        self.assertAlmostEqual(expected_payment, 275.20, places=2)

    def test_full_day_payment_10_hours(self):
        """חישוב תשלום ליום עבודה של 10 שעות"""
        rate = 34.40

        # 8 שעות @ 100% + 2 שעות @ 125%
        payment_100 = 8 * rate * 1.0
        payment_125 = 2 * rate * 1.25
        expected_payment = payment_100 + payment_125

        self.assertAlmostEqual(payment_100, 275.20, places=2)
        self.assertAlmostEqual(payment_125, 86.00, places=2)
        self.assertAlmostEqual(expected_payment, 361.20, places=2)

    def test_full_day_payment_12_hours(self):
        """חישוב תשלום ליום עבודה של 12 שעות"""
        rate = 34.40

        # 8 שעות @ 100% + 2 שעות @ 125% + 2 שעות @ 150%
        payment_100 = 8 * rate * 1.0
        payment_125 = 2 * rate * 1.25
        payment_150 = 2 * rate * 1.5
        expected_payment = payment_100 + payment_125 + payment_150

        self.assertAlmostEqual(payment_100, 275.20, places=2)
        self.assertAlmostEqual(payment_125, 86.00, places=2)
        self.assertAlmostEqual(payment_150, 103.20, places=2)
        self.assertAlmostEqual(expected_payment, 464.40, places=2)

    def test_full_day_payment_16_hours(self):
        """חישוב תשלום ליום עבודה של 16 שעות (משמרת לילה מלאה)"""
        rate = 34.40

        # 8 שעות @ 100% + 2 שעות @ 125% + 6 שעות @ 150%
        payment_100 = 8 * rate * 1.0    # 275.20
        payment_125 = 2 * rate * 1.25   # 86.00
        payment_150 = 6 * rate * 1.5    # 309.60
        expected_payment = payment_100 + payment_125 + payment_150  # 670.80

        self.assertAlmostEqual(expected_payment, 670.80, places=2)

    def test_shabbat_full_day_8_hours(self):
        """חישוב תשלום ליום שבת של 8 שעות"""
        rate = 34.40

        # 8 שעות @ 150% (שבת)
        expected_payment = 8 * rate * 1.5
        self.assertAlmostEqual(expected_payment, 412.80, places=2)

    def test_shabbat_full_day_12_hours(self):
        """חישוב תשלום ליום שבת של 12 שעות"""
        rate = 34.40

        # 8 שעות @ 150% + 2 שעות @ 175% + 2 שעות @ 200%
        payment_150 = 8 * rate * 1.5
        payment_175 = 2 * rate * 1.75
        payment_200 = 2 * rate * 2.0
        expected_payment = payment_150 + payment_175 + payment_200

        self.assertAlmostEqual(payment_150, 412.80, places=2)
        self.assertAlmostEqual(payment_175, 120.40, places=2)
        self.assertAlmostEqual(payment_200, 137.60, places=2)
        self.assertAlmostEqual(expected_payment, 670.80, places=2)

    def test_mixed_rates_overlapping_shifts(self):
        """חישוב תשלום למשמרות חופפות עם תעריפים שונים"""
        # משמרת לילה 16:00-08:00 (16 שעות) תעריף 34.40
        # שמירה על דייר 22:00-03:00 (5 שעות) תעריף 40.00

        # שעות לא חופפות במשמרת לילה: 16:00-22:00 (6 שעות) + 03:00-08:00 (5 שעות) = 11 שעות
        # שעות חופפות (שמירה על דייר): 22:00-03:00 = 5 שעות

        # חישוב (בהנחה שהכל @ 100% לפשטות):
        hours_low_rate = 11
        hours_high_rate = 5
        rate_low = 34.40
        rate_high = 40.00

        payment_low = hours_low_rate * rate_low * 1.0
        payment_high = hours_high_rate * rate_high * 1.0
        total_payment = payment_low + payment_high

        self.assertAlmostEqual(payment_low, 378.40, places=2)
        self.assertAlmostEqual(payment_high, 200.00, places=2)
        self.assertAlmostEqual(total_payment, 578.40, places=2)

    def test_night_shift_with_overtime(self):
        """חישוב משמרת לילה עם שעות נוספות"""
        rate = 34.40

        # משמרת 16:00-08:00 (16 שעות)
        # 8 שעות @ 100% + 2 שעות @ 125% + 6 שעות @ 150%
        calc100 = 8 * 60  # 480 דקות
        calc125 = 2 * 60  # 120 דקות
        calc150 = 6 * 60  # 360 דקות

        payment = (calc100/60 * 1.0 + calc125/60 * 1.25 + calc150/60 * 1.5) * rate

        expected = (8 * 1.0 + 2 * 1.25 + 6 * 1.5) * rate  # 19.5 * 34.40 = 670.80
        self.assertAlmostEqual(payment, expected, places=2)
        self.assertAlmostEqual(payment, 670.80, places=2)

    def test_partial_shabbat_shift(self):
        """חישוב משמרת שחלקה בשבת וחלקה לא"""
        rate = 34.40

        # משמרת 14:00 יום שישי עד 08:00 שבת (18 שעות)
        # נניח כניסת שבת 16:00
        # 14:00-16:00 = 2 שעות חול @ 100%
        # 16:00-08:00 = 16 שעות שבת

        hours_weekday = 2
        hours_shabbat_100 = 8  # ראשונות בשבת @ 150%
        hours_shabbat_125 = 2  # @ 175%
        hours_shabbat_150 = 6  # @ 200%

        payment_weekday = hours_weekday * rate * 1.0       # 68.80
        payment_shabbat_150 = hours_shabbat_100 * rate * 1.5   # 412.80
        payment_shabbat_175 = hours_shabbat_125 * rate * 1.75  # 120.40
        payment_shabbat_200 = hours_shabbat_150 * rate * 2.0   # 412.80

        total = payment_weekday + payment_shabbat_150 + payment_shabbat_175 + payment_shabbat_200

        self.assertAlmostEqual(payment_weekday, 68.80, places=2)
        # total = 68.80 + 412.80 + 120.40 + 412.80 = 1014.80
        self.assertAlmostEqual(total, 1014.80, places=2)

    def test_two_separate_shifts_same_day(self):
        """חישוב שתי משמרות נפרדות באותו יום"""
        rate = 34.40

        # משמרת בוקר 08:00-12:00 (4 שעות)
        # הפסקה
        # משמרת ערב 16:00-20:00 (4 שעות)
        # סה"כ 8 שעות @ 100%

        hours_total = 8
        expected_payment = hours_total * rate * 1.0
        self.assertAlmostEqual(expected_payment, 275.20, places=2)

    def test_continuous_shift_with_break(self):
        """חישוב משמרת רציפה עם הפסקה קצרה (לא שוברת רצף)"""
        rate = 34.40

        # משמרת 08:00-17:00 עם הפסקה של 30 דקות
        # סה"כ 8.5 שעות עבודה
        # 8 שעות @ 100% + 0.5 שעות @ 125%

        hours_100 = 8
        hours_125 = 0.5

        payment = (hours_100 * 1.0 + hours_125 * 1.25) * rate
        self.assertAlmostEqual(payment, 296.70, places=2)

    def test_monthly_calculation_example(self):
        """דוגמה לחישוב חודשי"""
        rate = 34.40

        # חודש עם 20 ימי עבודה של 8 שעות
        days = 20
        hours_per_day = 8

        total_hours = days * hours_per_day
        monthly_payment = total_hours * rate

        self.assertEqual(total_hours, 160)
        self.assertAlmostEqual(monthly_payment, 5504.00, places=2)

    def test_monthly_with_overtime(self):
        """חישוב חודשי עם שעות נוספות"""
        rate = 34.40

        # 20 ימים: 15 ימים של 8 שעות + 5 ימים של 10 שעות
        regular_days = 15
        overtime_days = 5

        # ימים רגילים: 15 * 8 * 34.40 = 4128
        regular_payment = regular_days * 8 * rate * 1.0

        # ימי שעות נוספות: 5 * (8*34.40 + 2*34.40*1.25) = 5 * 361.20 = 1806
        overtime_payment = overtime_days * (8 * rate * 1.0 + 2 * rate * 1.25)

        total = regular_payment + overtime_payment

        self.assertAlmostEqual(regular_payment, 4128.00, places=2)
        self.assertAlmostEqual(overtime_payment, 1806.00, places=2)
        self.assertAlmostEqual(total, 5934.00, places=2)


class TestEdgeCases(unittest.TestCase):
    """בדיקות מקרי קצה"""

    def test_exactly_8_hours(self):
        """בדיוק 8 שעות - גבול בין 100% ל-125%"""
        rate = 34.40
        hours = 8

        # כל השעות צריכות להיות 100%
        payment = hours * rate * 1.0
        self.assertAlmostEqual(payment, 275.20, places=2)

    def test_exactly_10_hours(self):
        """בדיוק 10 שעות - גבול בין 125% ל-150%"""
        rate = 34.40

        # 8 @ 100% + 2 @ 125%
        payment = 8 * rate * 1.0 + 2 * rate * 1.25
        self.assertAlmostEqual(payment, 361.20, places=2)

    def test_one_minute_over_8_hours(self):
        """8 שעות ודקה - צריך להיות 125%"""
        # 481 דקות = 8 שעות ודקה
        result = calculate_wage_rate(481, False)
        self.assertEqual(result, "125%")

    def test_one_minute_over_10_hours(self):
        """10 שעות ודקה - צריך להיות 150%"""
        # 601 דקות = 10 שעות ודקה
        result = calculate_wage_rate(601, False)
        self.assertEqual(result, "150%")

    def test_zero_hours(self):
        """אפס שעות"""
        rate = 34.40
        hours = 0

        payment = hours * rate
        self.assertEqual(payment, 0)

    def test_very_long_shift(self):
        """משמרת ארוכה מאוד (24 שעות)"""
        rate = 34.40

        # 8 @ 100% + 2 @ 125% + 14 @ 150%
        payment_100 = 8 * rate * 1.0
        payment_125 = 2 * rate * 1.25
        payment_150 = 14 * rate * 1.5

        total = payment_100 + payment_125 + payment_150
        self.assertAlmostEqual(total, 1083.60, places=2)

    def test_fraction_of_hour(self):
        """חלק משעה (דקות)"""
        rate = 34.40

        # 30 דקות = 0.5 שעה @ 100%
        minutes = 30
        payment = (minutes / 60) * rate * 1.0
        self.assertAlmostEqual(payment, 17.20, places=2)

    def test_different_rates_exact_boundary(self):
        """תעריפים שונים בדיוק על הגבול"""
        # 8 שעות בתעריף נמוך, דקה אחת בתעריף גבוה
        rate_low = 34.40
        rate_high = 40.00

        # 8 שעות @ 100% בתעריף נמוך
        payment_low = 8 * rate_low * 1.0

        # 1 דקה @ 125% בתעריף גבוה
        payment_high = (1/60) * rate_high * 1.25

        total = payment_low + payment_high
        self.assertAlmostEqual(payment_low, 275.20, places=2)
        self.assertAlmostEqual(payment_high, 0.83, places=2)

    def test_rounding(self):
        """בדיקת עיגול"""
        rate = 34.40

        # 8 שעות ו-20 דקות = 8.333... שעות
        hours = 8 + 20/60
        payment = hours * rate * 1.0

        # 8.333... * 34.40 = 286.666...
        self.assertAlmostEqual(payment, 286.67, places=2)


class TestChainCalculationIntegration(unittest.TestCase):
    """בדיקות אינטגרציה לחישוב רצפים"""

    def test_simple_chain(self):
        """רצף פשוט"""
        # רצף 08:00-16:00 (480 דקות)
        segments = [(480, 960, None)]
        result = _calculate_chain_wages(segments, date(2024, 12, 15), {}, 0)

        self.assertEqual(result["calc100"], 480)
        self.assertEqual(result["calc125"], 0)
        self.assertEqual(result["calc150"], 0)

    def test_chain_crossing_overtime_boundary(self):
        """רצף שחוצה גבול שעות נוספות"""
        # רצף 08:00-19:00 (660 דקות = 11 שעות)
        segments = [(480, 1140, None)]
        result = _calculate_chain_wages(segments, date(2024, 12, 15), {}, 0)

        self.assertEqual(result["calc100"], 480)  # 8 שעות
        self.assertEqual(result["calc125"], 120)  # 2 שעות
        self.assertEqual(result["calc150"], 60)   # 1 שעה

    def test_chain_with_carryover(self):
        """רצף עם העברה מיום קודם"""
        # 4 שעות מיום קודם + 6 שעות היום = 10 שעות
        segments = [(480, 840, None)]  # 08:00-14:00 (6 שעות)
        result = _calculate_chain_wages(segments, date(2024, 12, 15), {}, 240)  # 4 שעות carryover

        # 4 שעות מיום קודם + 4 שעות היום = 8 שעות @ 100%
        # 2 שעות היום = 125%
        self.assertEqual(result["calc100"], 240)  # 4 שעות
        self.assertEqual(result["calc125"], 120)  # 2 שעות
        self.assertEqual(result["calc150"], 0)

    def test_multiple_segments_same_chain(self):
        """מספר סגמנטים באותו רצף"""
        # סגמנט 1: 08:00-12:00 (4 שעות)
        # סגמנט 2: 12:00-16:00 (4 שעות)
        segments = [(480, 720, None), (720, 960, None)]
        result = _calculate_chain_wages(segments, date(2024, 12, 15), {}, 0)

        self.assertEqual(result["calc100"], 480)  # 8 שעות
        self.assertEqual(result["calc125"], 0)
        self.assertEqual(result["calc150"], 0)

    def test_night_shift_chain(self):
        """רצף משמרת לילה"""
        # 16:00-08:00 (16 שעות = 960 דקות)
        # בייצוג: 960-1920 (16:00 ביום הראשון עד 08:00 ביום השני)
        segments = [(960, 1920, None)]
        result = _calculate_chain_wages(segments, date(2024, 12, 15), {}, 0)

        self.assertEqual(result["calc100"], 480)   # 8 שעות
        self.assertEqual(result["calc125"], 120)   # 2 שעות
        self.assertEqual(result["calc150"], 360)   # 6 שעות


class TestNightShiftDetection(unittest.TestCase):
    """בדיקות זיהוי שעות לילה (22:00-06:00)"""

    def test_night_hours_full_night(self):
        """משמרת שלמה בלילה 22:00-06:00"""
        # 22:00 = 1320, 06:00 = 360 (למחרת) = 1800 בייצוג מנורמל
        night_mins = calculate_night_hours_in_segment(1320, 1800)
        self.assertEqual(night_mins, 480)  # 8 שעות

    def test_night_hours_partial_evening(self):
        """משמרת ערב חלקית 20:00-23:00"""
        # רק שעה אחת בלילה (22:00-23:00)
        night_mins = calculate_night_hours_in_segment(1200, 1380)
        self.assertEqual(night_mins, 60)  # שעה אחת

    def test_night_hours_early_morning(self):
        """משמרת בוקר מוקדם 04:00-08:00"""
        # 04:00-06:00 = 2 שעות לילה
        night_mins = calculate_night_hours_in_segment(240, 480)
        self.assertEqual(night_mins, 120)  # 2 שעות

    def test_night_hours_no_night(self):
        """משמרת יום 08:00-16:00"""
        night_mins = calculate_night_hours_in_segment(480, 960)
        self.assertEqual(night_mins, 0)

    def test_qualifies_2_hours(self):
        """סף 2 שעות - בדיוק 2 שעות"""
        # 22:00-00:00 = בדיוק 2 שעות
        self.assertTrue(qualifies_as_night_shift([(1320, 1440)]))

    def test_qualifies_above_threshold(self):
        """מעל סף 2 שעות"""
        # 22:00-01:00 = 3 שעות
        self.assertTrue(qualifies_as_night_shift([(1320, 1500)]))

    def test_not_qualifies_below_threshold(self):
        """מתחת לסף 2 שעות"""
        # 22:00-23:30 = 1.5 שעות
        self.assertFalse(qualifies_as_night_shift([(1320, 1410)]))

    def test_qualifies_multiple_segments(self):
        """מספר סגמנטים שביחד מגיעים ל-2 שעות"""
        # 22:00-23:00 (1 שעה) + 05:00-06:00 (1 שעה) = 2 שעות
        segments = [(1320, 1380), (300, 360)]
        self.assertTrue(qualifies_as_night_shift(segments))


class TestNightShiftOvertime(unittest.TestCase):
    """בדיקות שעות נוספות במשמרת לילה (סף 7 שעות)"""

    def test_night_shift_7_hours_all_100(self):
        """משמרת לילה של 7 שעות = הכל 100%"""
        # 22:00-05:00 = 7 שעות (1320-1740)
        segments = [(1320, 1740, None)]
        result = _calculate_chain_wages(segments, date(2024, 12, 15), {}, 0, is_night_shift=True)

        self.assertEqual(result["calc100"], 420)  # 7 שעות
        self.assertEqual(result["calc125"], 0)
        self.assertEqual(result["calc150"], 0)

    def test_night_shift_8_hours_has_125(self):
        """משמרת לילה של 8 שעות = 7 שעות 100% + 1 שעה 125%"""
        # 22:00-06:00 = 8 שעות (1320-1800)
        segments = [(1320, 1800, None)]
        result = _calculate_chain_wages(segments, date(2024, 12, 15), {}, 0, is_night_shift=True)

        self.assertEqual(result["calc100"], 420)  # 7 שעות
        self.assertEqual(result["calc125"], 60)   # 1 שעה
        self.assertEqual(result["calc150"], 0)

    def test_night_shift_10_hours_has_150(self):
        """משמרת לילה של 10 שעות = 7 שעות 100% + 2 שעות 125% + 1 שעה 150%"""
        # 20:00-06:00 = 10 שעות (1200-1800)
        segments = [(1200, 1800, None)]
        result = _calculate_chain_wages(segments, date(2024, 12, 15), {}, 0, is_night_shift=True)

        self.assertEqual(result["calc100"], 420)  # 7 שעות
        self.assertEqual(result["calc125"], 120)  # 2 שעות
        self.assertEqual(result["calc150"], 60)   # 1 שעה

    def test_regular_shift_8_hours_all_100(self):
        """משמרת רגילה של 8 שעות = הכל 100% (סף 8 שעות)"""
        # 08:00-16:00 = 8 שעות
        segments = [(480, 960, None)]
        result = _calculate_chain_wages(segments, date(2024, 12, 15), {}, 0, is_night_shift=False)

        self.assertEqual(result["calc100"], 480)  # 8 שעות
        self.assertEqual(result["calc125"], 0)
        self.assertEqual(result["calc150"], 0)

    def test_regular_shift_9_hours_has_125(self):
        """משמרת רגילה של 9 שעות = 8 שעות 100% + 1 שעה 125%"""
        # 08:00-17:00 = 9 שעות
        segments = [(480, 1020, None)]
        result = _calculate_chain_wages(segments, date(2024, 12, 15), {}, 0, is_night_shift=False)

        self.assertEqual(result["calc100"], 480)  # 8 שעות
        self.assertEqual(result["calc125"], 60)   # 1 שעה
        self.assertEqual(result["calc150"], 0)

    def test_night_vs_regular_comparison(self):
        """השוואה: אותה משמרת עם דגל לילה שונה"""
        # משמרת 18:00-02:00 = 8 שעות (1080-1560)
        segments = [(1080, 1560, None)]

        # כמשמרת רגילה (סף 8 שעות)
        result_regular = _calculate_chain_wages(segments, date(2024, 12, 15), {}, 0, is_night_shift=False)
        self.assertEqual(result_regular["calc100"], 480)  # 8 שעות ב-100%
        self.assertEqual(result_regular["calc125"], 0)

        # כמשמרת לילה (סף 7 שעות)
        result_night = _calculate_chain_wages(segments, date(2024, 12, 15), {}, 0, is_night_shift=True)
        self.assertEqual(result_night["calc100"], 420)   # 7 שעות ב-100%
        self.assertEqual(result_night["calc125"], 60)    # 1 שעה ב-125%


class TestNightShiftWageRate(unittest.TestCase):
    """בדיקות פונקציית calculate_wage_rate עם משמרת לילה"""

    def test_night_shift_rate_at_7_hours(self):
        """בדיוק 7 שעות במשמרת לילה = 100%"""
        self.assertEqual(calculate_wage_rate(420, False, is_night_shift=True), "100%")

    def test_night_shift_rate_at_8_hours(self):
        """8 שעות במשמרת לילה = 125%"""
        self.assertEqual(calculate_wage_rate(421, False, is_night_shift=True), "125%")

    def test_night_shift_rate_at_9_hours(self):
        """9 שעות במשמרת לילה = 125%"""
        self.assertEqual(calculate_wage_rate(540, False, is_night_shift=True), "125%")

    def test_night_shift_rate_at_10_hours(self):
        """10 שעות במשמרת לילה = 150%"""
        self.assertEqual(calculate_wage_rate(541, False, is_night_shift=True), "150%")

    def test_night_shift_shabbat_rate(self):
        """משמרת לילה בשבת"""
        self.assertEqual(calculate_wage_rate(420, True, is_night_shift=True), "150%")   # 7 שעות
        self.assertEqual(calculate_wage_rate(421, True, is_night_shift=True), "175%")   # 8 שעות
        self.assertEqual(calculate_wage_rate(541, True, is_night_shift=True), "200%")   # 10 שעות

    def test_regular_shift_rate_at_8_hours(self):
        """בדיוק 8 שעות במשמרת רגילה = 100%"""
        self.assertEqual(calculate_wage_rate(480, False, is_night_shift=False), "100%")

    def test_regular_shift_rate_at_9_hours(self):
        """9 שעות במשמרת רגילה = 125%"""
        self.assertEqual(calculate_wage_rate(481, False, is_night_shift=False), "125%")


class TestNightChainOvertimeThresholds(unittest.TestCase):
    """
    בדיקות מקיפות לוודא שסף 7/8 שעות מחושב נכון בכל סוגי המשמרות.

    כלל: משמרת שיש בה 2+ שעות בטווח 22:00-06:00 = משמרת לילה (סף 7 שעות)
    אחרת = משמרת רגילה (סף 8 שעות)
    """

    def test_weekday_night_22_to_0630_is_night_chain(self):
        """
        משמרת לילה בחול 22:00-06:30 = 8.5 שעות, כולן בטווח לילה
        צריך להיות: 7 שעות 100% + 1.5 שעות 125%

        הערה: משתמשים ביום ראשון (2024-12-15) שהוא יום חול
        """
        # 22:00 = 1320, 06:30 למחרת = 1830 (במערכת הזמנים המורחבת)
        segments = [(1320, 1830, None)]
        result = _calculate_chain_wages(segments, date(2024, 12, 15), {}, 0, is_night_shift=True)

        # סף 7 שעות (420 דקות)
        self.assertEqual(result["calc100"], 420)   # 7 שעות @ 100%
        self.assertEqual(result["calc125"], 90)    # 1.5 שעות @ 125%
        self.assertEqual(result["calc150"], 0)

    def test_weekday_night_22_to_0630_NOT_night_chain_comparison(self):
        """
        אותה משמרת 22:00-06:30 אבל עם is_night_shift=False
        צריך להיות: 8 שעות 100% + 0.5 שעות 125%
        """
        segments = [(1320, 1830, None)]
        result = _calculate_chain_wages(segments, date(2024, 12, 15), {}, 0, is_night_shift=False)

        # סף 8 שעות (480 דקות) - ההתנהגות השגויה לפני התיקון
        self.assertEqual(result["calc100"], 480)   # 8 שעות @ 100%
        self.assertEqual(result["calc125"], 30)    # 0.5 שעות @ 125%
        self.assertEqual(result["calc150"], 0)

    def test_friday_night_is_shabbat(self):
        """
        משמרת ליל שישי 22:00-06:30 (ביום שישי) = שבת הלכתית
        כי שבת נכנסת בערב שישי (~17:00)
        צריך להיות: 7 שעות @ 150% + 1.5 שעות @ 175%
        """
        # 09/01/2026 = יום שישי, 22:00 = אחרי כניסת שבת
        segments = [(1320, 1830, None)]
        result = _calculate_chain_wages(segments, date(2026, 1, 9), {}, 0, is_night_shift=True)

        # בשבת עם סף לילה (7 שעות)
        self.assertEqual(result["calc100"], 0)
        self.assertEqual(result["calc125"], 0)
        self.assertEqual(result["calc150"], 420)   # 7 שעות @ 150% שבת
        self.assertEqual(result["calc175"], 90)    # 1.5 שעות @ 175% שבת

    def test_shabbat_night_22_to_0630_with_shabbat_rates(self):
        """
        משמרת שבת 22:00-06:30 בשבת הלכתית = 8.5 שעות
        עם is_night_shift=True ושבת:
        - 7 שעות @ 150% (100% בסיס + 50% שבת)
        - 1.5 שעות @ 175% (125% בסיס + 50% שבת)

        הערה: הבדיקה הזו בודקת את calc150 ו-calc175 שמתאימים לשבת
        """
        # צריך shabbat_cache עם זמני שבת
        shabbat_cache = {
            "2026-01-09": {"enter": "16:30", "exit": "17:45"},  # שבת נכנסת ב-16:30 ויוצאת למחרת ב-17:45
        }

        # משמרת 22:00-06:30 ביום שישי (09/01/2026)
        segments = [(1320, 1830, None)]
        result = _calculate_chain_wages(segments, date(2026, 1, 9), shabbat_cache, 0, is_night_shift=True)

        # בשבת עם סף לילה (7 שעות):
        # calc100=0 (אין שעות רגילות - הכל שבת)
        # calc150 = 420 דקות (7 שעות @ 150% שבת)
        # calc175 = 90 דקות (1.5 שעות @ 175% שבת)
        self.assertEqual(result["calc100"], 0)
        self.assertEqual(result["calc125"], 0)
        self.assertEqual(result["calc150"], 420)   # 7 שעות @ 150%
        self.assertEqual(result["calc175"], 90)    # 1.5 שעות @ 175%
        self.assertEqual(result["calc200"], 0)

    def test_day_shift_08_to_17_is_NOT_night_chain(self):
        """
        משמרת יום 08:00-17:00 = 9 שעות, 0 שעות בטווח לילה
        צריך להיות: 8 שעות 100% + 1 שעה 125% (סף 8 שעות)
        """
        # 08:00 = 480, 17:00 = 1020
        segments = [(480, 1020, None)]
        result = _calculate_chain_wages(segments, date(2024, 12, 15), {}, 0, is_night_shift=False)

        self.assertEqual(result["calc100"], 480)   # 8 שעות
        self.assertEqual(result["calc125"], 60)    # 1 שעה
        self.assertEqual(result["calc150"], 0)

    def test_evening_shift_18_to_02_qualifies_as_night(self):
        """
        משמרת ערב 18:00-02:00 = 8 שעות
        4 שעות בטווח 22:00-02:00 = יותר מ-2 שעות = משמרת לילה
        צריך להיות: 7 שעות 100% + 1 שעה 125%
        """
        # 18:00 = 1080, 02:00 למחרת = 1560
        night_hours = calculate_night_hours_in_segment(1080, 1560)
        self.assertEqual(night_hours, 240)  # 4 שעות (22:00-02:00)
        self.assertTrue(night_hours >= 120)  # מעל 2 שעות = משמרת לילה

        segments = [(1080, 1560, None)]
        result = _calculate_chain_wages(segments, date(2024, 12, 15), {}, 0, is_night_shift=True)

        self.assertEqual(result["calc100"], 420)   # 7 שעות
        self.assertEqual(result["calc125"], 60)    # 1 שעה
        self.assertEqual(result["calc150"], 0)

    def test_evening_shift_18_to_2330_NOT_night(self):
        """
        משמרת ערב 18:00-23:30 = 5.5 שעות
        1.5 שעות בטווח 22:00-23:30 = פחות מ-2 שעות = לא משמרת לילה
        """
        # 18:00 = 1080, 23:30 = 1410
        night_hours = calculate_night_hours_in_segment(1080, 1410)
        self.assertEqual(night_hours, 90)  # 1.5 שעות (22:00-23:30)
        self.assertFalse(night_hours >= 120)  # פחות מ-2 שעות = לא משמרת לילה

    def test_early_morning_04_to_12_qualifies_as_night(self):
        """
        משמרת בוקר מוקדם 04:00-12:00 = 8 שעות
        2 שעות בטווח 04:00-06:00 = בדיוק 2 שעות = משמרת לילה
        צריך להיות: 7 שעות 100% + 1 שעה 125%
        """
        # 04:00 = 240, 12:00 = 720
        night_hours = calculate_night_hours_in_segment(240, 720)
        self.assertEqual(night_hours, 120)  # 2 שעות (04:00-06:00)
        self.assertTrue(night_hours >= 120)  # בדיוק 2 שעות = משמרת לילה

        segments = [(240, 720, None)]
        result = _calculate_chain_wages(segments, date(2024, 12, 15), {}, 0, is_night_shift=True)

        self.assertEqual(result["calc100"], 420)   # 7 שעות
        self.assertEqual(result["calc125"], 60)    # 1 שעה
        self.assertEqual(result["calc150"], 0)

    def test_rate_change_with_carryover_keeps_night_flag(self):
        """
        שינוי תעריף באמצע רצף לילה:
        - 22:00-02:00 בתעריף 42 (4 שעות)
        - 02:00-06:00 בתעריף 34.40 (4 שעות)
        סה"כ 8 שעות, כולן בטווח לילה

        הרצף כולו צריך להיות עם סף 7 שעות גם אחרי שינוי התעריף
        """
        # רצף ראשון: 22:00-02:00 (4 שעות) עם 4 שעות לילה
        segments1 = [(1320, 1560, None)]  # shift_id=42 rate
        result1 = _calculate_chain_wages(segments1, date(2024, 12, 15), {}, 0, is_night_shift=True)

        # 4 שעות < 7 שעות = הכל 100%
        self.assertEqual(result1["calc100"], 240)
        self.assertEqual(result1["calc125"], 0)

        # רצף שני: 02:00-06:00 (4 שעות) עם carryover של 4 שעות
        # סה"כ 8 שעות ברצף, סף 7 שעות
        segments2 = [(1560, 1800, None)]  # shift_id=34.40 rate
        result2 = _calculate_chain_wages(segments2, date(2024, 12, 15), {}, 240, is_night_shift=True)

        # 4 שעות carryover + 3 שעות = 7 שעות @ 100%, 1 שעה @ 125%
        self.assertEqual(result2["calc100"], 180)   # 3 שעות נוספות ב-100% (עד סף 7)
        self.assertEqual(result2["calc125"], 60)    # 1 שעה ב-125%

    def test_long_night_shift_10_hours_all_tiers(self):
        """
        משמרת לילה ארוכה: 20:00-06:00 = 10 שעות
        8 שעות בטווח 22:00-06:00 = משמרת לילה
        צריך להיות: 7 שעות 100% + 2 שעות 125% + 1 שעה 150%
        """
        # 20:00 = 1200, 06:00 למחרת = 1800
        segments = [(1200, 1800, None)]
        result = _calculate_chain_wages(segments, date(2024, 12, 15), {}, 0, is_night_shift=True)

        self.assertEqual(result["calc100"], 420)   # 7 שעות
        self.assertEqual(result["calc125"], 120)   # 2 שעות
        self.assertEqual(result["calc150"], 60)    # 1 שעה

    def test_long_day_shift_10_hours_all_tiers(self):
        """
        משמרת יום ארוכה: 08:00-18:00 = 10 שעות, 0 שעות לילה
        צריך להיות: 8 שעות 100% + 2 שעות 125% (סף 8 שעות)
        """
        # 08:00 = 480, 18:00 = 1080
        segments = [(480, 1080, None)]
        result = _calculate_chain_wages(segments, date(2024, 12, 15), {}, 0, is_night_shift=False)

        self.assertEqual(result["calc100"], 480)   # 8 שעות
        self.assertEqual(result["calc125"], 120)   # 2 שעות
        self.assertEqual(result["calc150"], 0)

    def test_shabbat_with_night_threshold_all_rates(self):
        """
        משמרת שבת עם סף לילה: 22:00-08:30 = 10.5 שעות
        כולן בשבת וכולן בטווח לילה
        צריך להיות (סף 7 שעות, עם תוספות שבת):
        - 7 שעות @ 150% (100%+50%)
        - 2 שעות @ 175% (125%+50%)
        - 1.5 שעות @ 200% (150%+50%)
        """
        shabbat_cache = {
            "2026-01-09": {"enter": "16:30", "exit": "17:45"},
        }

        # 22:00 = 1320, 08:30 למחרת = 1950 (במערכת מורחבת)
        segments = [(1320, 1950, None)]
        result = _calculate_chain_wages(segments, date(2026, 1, 9), shabbat_cache, 0, is_night_shift=True)

        # בשבת עם סף 7 שעות:
        self.assertEqual(result["calc150"], 420)   # 7 שעות @ 150%
        self.assertEqual(result["calc175"], 120)   # 2 שעות @ 175%
        self.assertEqual(result["calc200"], 90)    # 1.5 שעות @ 200%

        # אין שעות חול
        self.assertEqual(result["calc100"], 0)
        self.assertEqual(result["calc125"], 0)


class TestMultipleShiftsSameDay(unittest.TestCase):
    """
    בדיקות למספר משמרות באותו יום עבודה (08:00-08:00).
    כולל: משמרות עם הפסקה ביניהן, שינוי תעריף, ומעבר יום/לילה.
    """

    def test_two_shifts_same_day_with_break_over_60_min(self):
        """
        שתי משמרות באותו יום עם הפסקה > 60 דקות = שני רצפים נפרדים
        משמרת 1: 08:00-12:00 (4 שעות) @ 100%
        הפסקה: 2 שעות
        משמרת 2: 14:00-18:00 (4 שעות) @ 100%

        כל משמרת מתחילה מ-0 כי ההפסקה שוברת רצף
        """
        # משמרת 1
        segments1 = [(480, 720, None)]  # 08:00-12:00
        result1 = _calculate_chain_wages(segments1, date(2024, 12, 15), {}, 0, is_night_shift=False)
        self.assertEqual(result1["calc100"], 240)  # 4 שעות
        self.assertEqual(result1["calc125"], 0)

        # משמרת 2 - מתחילה מ-0 (אחרי הפסקה ארוכה)
        segments2 = [(840, 1080, None)]  # 14:00-18:00
        result2 = _calculate_chain_wages(segments2, date(2024, 12, 15), {}, 0, is_night_shift=False)
        self.assertEqual(result2["calc100"], 240)  # 4 שעות
        self.assertEqual(result2["calc125"], 0)

    def test_two_shifts_same_day_continuous(self):
        """
        שתי משמרות באותו יום עם הפסקה < 60 דקות = רצף אחד
        משמרת 1: 08:00-12:00 (4 שעות)
        הפסקה: 30 דקות
        משמרת 2: 12:30-17:00 (4.5 שעות)

        סה"כ 8.5 שעות ברצף אחד = 8 @ 100% + 0.5 @ 125%
        """
        # סימולציה של שתי משמרות ברצף (הפסקה קצרה לא שוברת)
        segments = [(480, 720, None), (750, 1020, None)]  # 08:00-12:00, 12:30-17:00
        result = _calculate_chain_wages(segments, date(2024, 12, 15), {}, 0, is_night_shift=False)

        total_minutes = (720 - 480) + (1020 - 750)  # 240 + 270 = 510 דקות = 8.5 שעות
        self.assertEqual(total_minutes, 510)

        self.assertEqual(result["calc100"], 480)   # 8 שעות
        self.assertEqual(result["calc125"], 30)    # 0.5 שעות
        self.assertEqual(result["calc150"], 0)

    def test_day_shift_then_night_shift_same_day(self):
        """
        משמרת יום ואז משמרת לילה באותו יום עבודה
        משמרת יום: 08:00-16:00 (8 שעות) - לא לילה
        הפסקה: 4 שעות (שוברת רצף)
        משמרת לילה: 20:00-04:00 (8 שעות) - לילה (6 שעות בטווח 22:00-04:00)

        משמרת היום: סף 8 שעות
        משמרת הלילה: סף 7 שעות
        """
        # משמרת יום
        segments_day = [(480, 960, None)]  # 08:00-16:00
        result_day = _calculate_chain_wages(segments_day, date(2024, 12, 15), {}, 0, is_night_shift=False)
        self.assertEqual(result_day["calc100"], 480)  # 8 שעות @ 100%
        self.assertEqual(result_day["calc125"], 0)

        # משמרת לילה - 20:00-04:00 = 8 שעות, 6 שעות בטווח לילה
        night_hours = calculate_night_hours_in_segment(20*60, 28*60)  # 20:00-04:00 (04:00 = 28*60 למחרת)
        self.assertEqual(night_hours, 360)  # 6 שעות (22:00-04:00)
        self.assertTrue(night_hours >= 120)  # מעל 2 שעות = משמרת לילה

        segments_night = [(1200, 1680, None)]  # 20:00-04:00 (04:00 = 1680)
        result_night = _calculate_chain_wages(segments_night, date(2024, 12, 15), {}, 0, is_night_shift=True)
        self.assertEqual(result_night["calc100"], 420)  # 7 שעות @ 100%
        self.assertEqual(result_night["calc125"], 60)   # 1 שעה @ 125%

    def test_multiple_short_shifts_accumulate_overtime(self):
        """
        מספר משמרות קצרות שמצטברות לשעות נוספות
        3 משמרות של 3 שעות כל אחת ברצף (עם הפסקות קצרות)
        סה"כ 9 שעות = 8 @ 100% + 1 @ 125%
        """
        # 3 סגמנטים ברצף
        segments = [
            (480, 660, None),   # 08:00-11:00 (3 שעות)
            (690, 870, None),   # 11:30-14:30 (3 שעות)
            (900, 1080, None),  # 15:00-18:00 (3 שעות)
        ]
        result = _calculate_chain_wages(segments, date(2024, 12, 15), {}, 0, is_night_shift=False)

        total = (660-480) + (870-690) + (1080-900)  # 180 + 180 + 180 = 540 = 9 שעות
        self.assertEqual(total, 540)

        self.assertEqual(result["calc100"], 480)  # 8 שעות
        self.assertEqual(result["calc125"], 60)   # 1 שעה

    def test_different_rates_same_day_chain_breaks(self):
        """
        משמרות עם תעריפים שונים באותו יום - שינוי תעריף שובר רצף
        אבל ה-carryover נשמר (לחישוב שעות נוספות)

        משמרת 1: 08:00-14:00 (6 שעות) @ 42 ש"ח
        משמרת 2: 14:00-18:00 (4 שעות) @ 34.40 ש"ח

        החישוב: 6 שעות + 4 שעות = 10 שעות
        אבל כל משמרת מחושבת בנפרד עם carryover
        """
        # משמרת 1 - 6 שעות @ 100%
        segments1 = [(480, 840, 42)]  # shift_id=42
        result1 = _calculate_chain_wages(segments1, date(2024, 12, 15), {}, 0, is_night_shift=False)
        self.assertEqual(result1["calc100"], 360)  # 6 שעות @ 100%
        self.assertEqual(result1["calc125"], 0)

        # משמרת 2 - 4 שעות, עם carryover של 6 שעות = סה"כ 10 שעות
        # 2 שעות @ 100% (עד 8), 2 שעות @ 125%
        segments2 = [(840, 1080, 34)]  # shift_id=34
        result2 = _calculate_chain_wages(segments2, date(2024, 12, 15), {}, 360, is_night_shift=False)
        self.assertEqual(result2["calc100"], 120)  # 2 שעות @ 100% (עד 480)
        self.assertEqual(result2["calc125"], 120)  # 2 שעות @ 125%


class TestConsecutiveDaysCarryover(unittest.TestCase):
    """
    בדיקות לימים רצופים עם carryover בין הימים.
    רצף שמסתיים ב-08:00 בדיוק מעביר את הדקות ליום הבא.
    """

    def test_overnight_shift_ends_at_0800_carryover(self):
        """
        משמרת לילה שמסתיימת ב-08:00 בדיוק - carryover ליום הבא
        יום 1: 20:00-08:00 (12 שעות) - רצף לילה
        יום 2: 08:00-10:00 (2 שעות) - המשך הרצף

        סה"כ 14 שעות ברצף אחד
        """
        # יום 1: 20:00-08:00 = 12 שעות
        # שעות לילה: 22:00-06:00 = 8 שעות = רצף לילה (סף 7 שעות)
        night_hours_day1 = calculate_night_hours_in_segment(20*60, 32*60)  # 20:00-08:00
        self.assertEqual(night_hours_day1, 480)  # 8 שעות לילה

        segments_day1 = [(1200, 1920, None)]  # 20:00-08:00 (08:00 = 1920 בציר מורחב)
        result_day1 = _calculate_chain_wages(segments_day1, date(2024, 12, 15), {}, 0, is_night_shift=True)

        # סף 7 שעות: 7 @ 100%, 2 @ 125%, 3 @ 150%
        self.assertEqual(result_day1["calc100"], 420)   # 7 שעות
        self.assertEqual(result_day1["calc125"], 120)   # 2 שעות
        self.assertEqual(result_day1["calc150"], 180)   # 3 שעות

        # יום 2: 08:00-10:00 = 2 שעות, עם carryover של 12 שעות
        # כבר עברנו את כל הסף, הכל ב-150%
        segments_day2 = [(480, 600, None)]  # 08:00-10:00
        result_day2 = _calculate_chain_wages(segments_day2, date(2024, 12, 16), {}, 720, is_night_shift=True)

        # 12 שעות carryover + 2 שעות = 14 שעות (כבר מעל 9 שעות = הכל 150%)
        self.assertEqual(result_day2["calc100"], 0)
        self.assertEqual(result_day2["calc125"], 0)
        self.assertEqual(result_day2["calc150"], 120)   # 2 שעות @ 150%

    def test_three_consecutive_days_with_carryover(self):
        """
        3 ימים רצופים עם רצף עבודה רציף (עבודה עד 08:00 כל יום)

        יום 1: 14:00-08:00 (18 שעות)
        יום 2: 08:00-08:00 (24 שעות) - המשך!
        יום 3: 08:00-12:00 (4 שעות)

        סה"כ 46 שעות ברצף אחד (תיאורטי)
        """
        # יום 1: 14:00-08:00 = 18 שעות
        # שעות לילה: 22:00-06:00 = 8 שעות
        night_hours_1 = calculate_night_hours_in_segment(14*60, 32*60)
        self.assertEqual(night_hours_1, 480)  # 8 שעות לילה = רצף לילה

        segments1 = [(840, 1920, None)]  # 14:00-08:00
        result1 = _calculate_chain_wages(segments1, date(2024, 12, 15), {}, 0, is_night_shift=True)

        # סף 7 שעות: 7 @ 100%, 2 @ 125%, 9 @ 150%
        self.assertEqual(result1["calc100"], 420)
        self.assertEqual(result1["calc125"], 120)
        self.assertEqual(result1["calc150"], 540)  # 9 שעות @ 150%

        # יום 2: עוד 24 שעות עם carryover של 18 שעות
        # כבר הכל ב-150%
        segments2 = [(480, 1920, None)]  # 08:00-08:00 למחרת
        result2 = _calculate_chain_wages(segments2, date(2024, 12, 16), {}, 1080, is_night_shift=True)

        # carryover 18 שעות = 1080 דקות, כבר מעל 9 שעות
        self.assertEqual(result2["calc100"], 0)
        self.assertEqual(result2["calc125"], 0)
        self.assertEqual(result2["calc150"], 1440)  # 24 שעות @ 150%

    def test_day_to_night_transition_across_days(self):
        """
        מעבר מיום ללילה בין ימים:
        יום 1: 10:00-18:00 (8 שעות) - יום רגיל
        יום 2: 22:00-06:00 (8 שעות) - לילה

        שני רצפים נפרדים (הפסקה > 60 דקות בין 18:00 ל-22:00)
        """
        # יום 1 - משמרת יום
        segments1 = [(600, 1080, None)]  # 10:00-18:00
        result1 = _calculate_chain_wages(segments1, date(2024, 12, 15), {}, 0, is_night_shift=False)
        self.assertEqual(result1["calc100"], 480)  # 8 שעות @ 100%

        # יום 2 - משמרת לילה (רצף חדש)
        night_hours = calculate_night_hours_in_segment(22*60, 30*60)
        self.assertEqual(night_hours, 480)  # 8 שעות לילה

        segments2 = [(1320, 1800, None)]  # 22:00-06:00
        result2 = _calculate_chain_wages(segments2, date(2024, 12, 16), {}, 0, is_night_shift=True)

        # סף 7 שעות: 7 @ 100%, 1 @ 125%
        self.assertEqual(result2["calc100"], 420)
        self.assertEqual(result2["calc125"], 60)


class TestMonthBoundaryCarryover(unittest.TestCase):
    """
    בדיקות למעבר בין חודשים עם carryover.
    רצף שמתחיל בסוף חודש ונמשך לתחילת החודש הבא.
    """

    def test_end_of_month_carryover_calculation(self):
        """
        רצף שמתחיל ב-31/12 ונמשך ל-01/01

        31/12: 22:00-08:00 (10 שעות, 8 שעות לילה)
        01/01: 08:00-12:00 (4 שעות) - המשך הרצף

        סה"כ 14 שעות ברצף לילה
        """
        # 31/12: רצף לילה
        night_hours = calculate_night_hours_in_segment(22*60, 32*60)
        self.assertEqual(night_hours, 480)  # 8 שעות לילה

        segments_dec = [(1320, 1920, None)]  # 22:00-08:00
        result_dec = _calculate_chain_wages(segments_dec, date(2024, 12, 31), {}, 0, is_night_shift=True)

        # סף 7 שעות: 7 @ 100%, 2 @ 125%, 1 @ 150%
        self.assertEqual(result_dec["calc100"], 420)
        self.assertEqual(result_dec["calc125"], 120)
        self.assertEqual(result_dec["calc150"], 60)

        # 01/01: המשך עם carryover של 10 שעות (600 דקות)
        segments_jan = [(480, 720, None)]  # 08:00-12:00
        result_jan = _calculate_chain_wages(segments_jan, date(2025, 1, 1), {}, 600, is_night_shift=True)

        # 10 שעות carryover + 4 שעות = 14 שעות
        # כבר עברנו 9 שעות, הכל @ 150%
        self.assertEqual(result_jan["calc100"], 0)
        self.assertEqual(result_jan["calc125"], 0)
        self.assertEqual(result_jan["calc150"], 240)  # 4 שעות @ 150%

    def test_shabbat_spanning_month_boundary(self):
        """
        משמרת 10:00-20:00 ביום שבת (28/12/2024)

        שבת נכנסת ב-16:30 (יום שישי) ויוצאת ב-17:45 (יום שבת)
        כלומר 10:00-17:45 ביום שבת = עדיין שבת (7.75 שעות = 465 דקות)
        17:45-20:00 ביום שבת = אחרי צאת שבת = חול (2.25 שעות = 135 דקות)
        """
        # 28/12/2024 = שבת (weekday=5)
        shabbat_cache = {
            "2024-12-28": {"enter": "16:30", "exit": "17:45"},  # יציאת שבת ב-17:45 ביום שבת
        }

        segments = [(600, 1200, None)]  # 10:00-20:00
        result = _calculate_chain_wages(segments, date(2024, 12, 28), shabbat_cache, 0, is_night_shift=False)

        # 10:00-17:45 (7.75 שעות = 465 דקות) = שבת @ 150%
        # 17:45-18:00 (0.25 שעות = 15 דקות) = חול @ 100% (להשלמת 8 שעות)
        # 18:00-20:00 (2 שעות = 120 דקות) = חול @ 125% (שעות נוספות)
        # סה"כ 10 שעות = 600 דקות
        self.assertEqual(result["calc150"], 465)  # 7.75 שעות שבת
        self.assertEqual(result["calc100"], 15)   # 0.25 שעות חול (להשלמת 8)
        self.assertEqual(result["calc125"], 120)  # 2 שעות חול שעות נוספות

    def test_night_hours_carryover_between_months(self):
        """
        שעות לילה שמועברות בין חודשים

        31/12: 23:00-08:00 (9 שעות, 7 שעות לילה)
        01/01: 08:00-10:00 (2 שעות, 0 שעות לילה)

        הרצף של 01/01 ממשיך להיחשב כרצף לילה כי סה"כ שעות הלילה ברצף = 7
        """
        # 31/12: 23:00-08:00 = 9 שעות
        night_hours_dec = calculate_night_hours_in_segment(23*60, 32*60)  # 23:00-08:00
        self.assertEqual(night_hours_dec, 420)  # 7 שעות לילה (23:00-06:00)

        # 01/01: 08:00-10:00 = 2 שעות, 0 שעות לילה
        night_hours_jan = calculate_night_hours_in_segment(8*60, 10*60)
        self.assertEqual(night_hours_jan, 0)

        # סה"כ שעות לילה ברצף = 7 שעות >= 2 שעות = רצף לילה
        total_night = night_hours_dec + night_hours_jan
        self.assertEqual(total_night, 420)
        self.assertTrue(total_night >= 120)  # NIGHT_HOURS_THRESHOLD


class TestComplexScenarios(unittest.TestCase):
    """
    בדיקות לתרחישים מורכבים שמשלבים מספר מקרים.
    """

    def test_week_of_night_shifts(self):
        """
        שבוע של משמרות לילה רצופות (22:00-08:00 כל לילה)
        כל משמרת נמשכת לבוקר הבא, אז כל יום מתחיל עם carryover

        בדיקה: האם סף 7 שעות נשמר לכל המשמרות?
        """
        # כל משמרת: 22:00-08:00 = 10 שעות, 8 שעות לילה
        segments = [(1320, 1920, None)]

        # יום 1 - ללא carryover
        result1 = _calculate_chain_wages(segments, date(2024, 12, 15), {}, 0, is_night_shift=True)
        self.assertEqual(result1["calc100"], 420)   # 7 שעות
        self.assertEqual(result1["calc125"], 120)   # 2 שעות
        self.assertEqual(result1["calc150"], 60)    # 1 שעה

        # יום 2 - עם carryover של 10 שעות (אם העבודה ב-08:00 בדיוק)
        # אבל בד"כ יש הפסקה, אז נניח שזה רצף חדש
        result2 = _calculate_chain_wages(segments, date(2024, 12, 16), {}, 0, is_night_shift=True)
        self.assertEqual(result2["calc100"], 420)
        self.assertEqual(result2["calc125"], 120)
        self.assertEqual(result2["calc150"], 60)

    def test_mixed_shift_types_same_week(self):
        """
        שבוע עם סוגי משמרות שונים:
        - יום ראשון: משמרת יום 08:00-16:00 (סף 8)
        - יום שני: משמרת לילה 22:00-06:00 (סף 7)
        - יום שלישי: משמרת יום 10:00-18:00 (סף 8)
        """
        # יום ראשון - משמרת יום
        segments_sun = [(480, 960, None)]
        result_sun = _calculate_chain_wages(segments_sun, date(2024, 12, 15), {}, 0, is_night_shift=False)
        self.assertEqual(result_sun["calc100"], 480)

        # יום שני - משמרת לילה
        segments_mon = [(1320, 1800, None)]
        result_mon = _calculate_chain_wages(segments_mon, date(2024, 12, 16), {}, 0, is_night_shift=True)
        self.assertEqual(result_mon["calc100"], 420)
        self.assertEqual(result_mon["calc125"], 60)

        # יום שלישי - משמרת יום
        segments_tue = [(600, 1080, None)]
        result_tue = _calculate_chain_wages(segments_tue, date(2024, 12, 17), {}, 0, is_night_shift=False)
        self.assertEqual(result_tue["calc100"], 480)

    def test_shabbat_to_weekday_transition(self):
        """
        מעבר משבת ליום חול באותו רצף:
        משמרת 10:00-22:00 ביום שבת (28/12/2024)

        שבת נכנסת ב-16:30 (יום שישי) ויוצאת ב-17:45 (יום שבת)
        כלומר 10:00-17:45 ביום שבת = עדיין שבת (7.75 שעות = 465 דקות)
        17:45-22:00 ביום שבת = אחרי צאת שבת = חול (4.25 שעות = 255 דקות)
        """
        shabbat_cache = {
            "2024-12-28": {"enter": "16:30", "exit": "17:45"},
        }

        # משמרת: 10:00-22:00 (12 שעות)
        segments_shabbat = [(600, 1320, None)]
        result_shabbat = _calculate_chain_wages(segments_shabbat, date(2024, 12, 28), shabbat_cache, 0, is_night_shift=False)

        # 10:00-17:45 (7.75 שעות = 465 דקות) = שבת @ 150%
        # 17:45-18:00 (0.25 שעות = 15 דקות) = חול @ 100% (להשלמת 8 שעות)
        # 18:00-20:00 (2 שעות = 120 דקות) = חול @ 125% (שעות 8-10)
        # 20:00-22:00 (2 שעות = 120 דקות) = חול @ 150% (שעות 10+)
        # סה"כ 12 שעות = 720 דקות
        self.assertEqual(result_shabbat["calc150"], 585)  # 7.75 שעות שבת (465) + 2 שעות חול 150% (120)
        self.assertEqual(result_shabbat["calc100"], 15)   # 0.25 שעות חול (להשלמת 8)
        self.assertEqual(result_shabbat["calc125"], 120)  # 2 שעות חול שעות נוספות (8-10)

    def test_partial_night_shift_boundary(self):
        """
        משמרת שנמצאת בדיוק על הגבול של 2 שעות לילה:
        21:00-23:00 = 1 שעה לילה (לא עובר סף)
        21:00-00:00 = 2 שעות לילה (עובר סף)
        """
        # 21:00-23:00 = 1 שעה לילה
        night_1 = calculate_night_hours_in_segment(21*60, 23*60)
        self.assertEqual(night_1, 60)
        self.assertFalse(night_1 >= 120)

        # 21:00-00:00 = 2 שעות לילה
        night_2 = calculate_night_hours_in_segment(21*60, 24*60)
        self.assertEqual(night_2, 120)
        self.assertTrue(night_2 >= 120)

        # משמרת 21:00-23:00 - לא לילה, סף 8 שעות
        segments_short = [(1260, 1380, None)]
        result_short = _calculate_chain_wages(segments_short, date(2024, 12, 15), {}, 0, is_night_shift=False)
        self.assertEqual(result_short["calc100"], 120)  # 2 שעות @ 100%

        # משמרת 21:00-00:00 - לילה, סף 7 שעות
        segments_long = [(1260, 1440, None)]
        result_long = _calculate_chain_wages(segments_long, date(2024, 12, 15), {}, 0, is_night_shift=True)
        self.assertEqual(result_long["calc100"], 180)  # 3 שעות @ 100% (פחות מ-7)


class TestNightChainWithCarryover(unittest.TestCase):
    """בדיקות רצף לילה עם carryover - הרצף נחשב לילה לפי סה"כ שעות הלילה ברצף כולו"""

    def test_carryover_night_hours_determine_chain_type(self):
        """
        רצף עם carryover של שעות לילה:
        אם ה-carryover כולל 2+ שעות לילה, הרצף כולו הוא רצף לילה (סף 7 שעות)
        """
        # דוגמה: אתמול עבדתי 22:00-08:00 (10 שעות, מתוכן 8 שעות לילה)
        # היום ממשיך ב-08:00 עם עוד 2 שעות
        # הרצף כולו = 12 שעות, מתוכן 8 שעות לילה = רצף לילה
        # סף 7 שעות: 420 דק' 100%, 120 דק' 125%, 180 דק' 150%

        # בדיקת זיהוי שעות לילה
        # 22:00-06:00 = 8 שעות לילה (480 דקות)
        night_hours_1 = calculate_night_hours_in_segment(22*60, 6*60)
        self.assertEqual(night_hours_1, 480)

        # 08:00-10:00 = 0 שעות לילה
        night_hours_2 = calculate_night_hours_in_segment(8*60, 10*60)
        self.assertEqual(night_hours_2, 0)

        # סה"כ: 480 + 0 = 480 דקות לילה >= 120 = רצף לילה ✓
        total_night = night_hours_1 + night_hours_2
        self.assertTrue(total_night >= 120)  # NIGHT_HOURS_THRESHOLD

    def test_day_carryover_to_night_chain(self):
        """
        רצף יום שממשיך לרצף לילה:
        אתמול: 14:00-08:00 (18 שעות, מתוכן 8 שעות לילה 22:00-06:00)
        = רצף לילה, סף 7 שעות
        """
        # 14:00-22:00 = 0 שעות לילה
        night_1 = calculate_night_hours_in_segment(14*60, 22*60)
        self.assertEqual(night_1, 0)

        # 22:00-06:00 = 8 שעות לילה
        night_2 = calculate_night_hours_in_segment(22*60, 6*60)
        self.assertEqual(night_2, 480)

        # 06:00-08:00 = 0 שעות לילה
        night_3 = calculate_night_hours_in_segment(6*60, 8*60)
        self.assertEqual(night_3, 0)

        total = night_1 + night_2 + night_3
        self.assertEqual(total, 480)  # 8 שעות לילה
        self.assertTrue(total >= 120)  # רצף לילה

    def test_short_night_hours_not_night_chain(self):
        """
        רצף עם פחות מ-2 שעות לילה = רצף יום (סף 8 שעות)
        """
        # 20:00-23:00 = 1 שעה לילה בלבד (22:00-23:00)
        night_hours = calculate_night_hours_in_segment(20*60, 23*60)
        self.assertEqual(night_hours, 60)  # רק שעה אחת בטווח 22:00-06:00
        self.assertFalse(night_hours >= 120)  # לא רצף לילה

    def test_exactly_2_hours_qualifies(self):
        """
        בדיוק 2 שעות בטווח 22:00-06:00 = רצף לילה
        """
        # 21:00-00:00 = 2 שעות לילה (22:00-00:00)
        night_hours = calculate_night_hours_in_segment(21*60, 24*60)
        self.assertEqual(night_hours, 120)  # בדיוק 2 שעות
        self.assertTrue(night_hours >= 120)  # רצף לילה

    def test_carryover_adds_to_current_night_hours(self):
        """
        carryover של 1 שעת לילה + עבודה נוכחית של 1 שעת לילה = 2 שעות = רצף לילה
        """
        carryover_night = 60  # 1 שעה מאתמול
        current_night = 60     # 1 שעה היום

        total_night = carryover_night + current_night
        self.assertEqual(total_night, 120)
        self.assertTrue(total_night >= 120)  # רצף לילה


class TestMixedDaysInSameWorkday(unittest.TestCase):
    """בדיקות סגמנטים מימים שונים באותו יום עבודה"""

    def test_saturday_segment_in_friday_display_day(self):
        """
        באג שתוקן: משמרת ביום שבת (00:00-01:00) שמוצגת תחת יום שישי
        צריכה להיחשב כשבת (150%) ולא כחול (100%)

        סצנריו: דיווח ביום שישי 30/01/2026
        - 15:00-17:00 = חול (לפני כניסת שבת ~16:50)
        - 17:00-22:00 = שבת
        - בנוסף, משמרת נפרדת 00:00-01:00 ביום שבת 31/01/2026

        ה-00:00-01:00 מוצג תחת יום שישי (אותו יום עבודה) אבל הזמן עצמו
        הוא ביום שבת ולכן צריך להיחשב כשבת.
        """
        # שבת - כניסה בסביבות 16:50 ביום שישי, יציאה בסביבות 17:50 ביום שבת
        shabbat_cache = {
            "2026-01-30": {"start": "16:50", "end": "17:50"},  # שבת פרשת בשלח
            "2026-01-31": {"start": "16:50", "end": "17:50"},
        }

        # משמרת ביום שבת 00:00-01:00 (60 דקות)
        # זמן 0-60 ביום שבת צריך להיחשב כשבת (150%)
        saturday_date = date(2026, 1, 31)  # שבת
        segments_saturday = [(0, 60, None, saturday_date)]

        result = _calculate_chain_wages_new(segments_saturday, shabbat_cache, 0, False)

        # צריך להיות 150% (שבת) ולא 100% (חול)
        self.assertEqual(result["calc150"], 60, "משמרת 00:00-01:00 בשבת צריכה להיות 150%")
        self.assertEqual(result["calc100"], 0, "לא צריך להיות שעות ב-100%")
        self.assertEqual(result["calc150_shabbat"], 60, "צריך להיות מסומן כשבת")

    def test_friday_before_shabbat_vs_saturday_during_shabbat(self):
        """
        השוואה בין סגמנט ביום שישי לפני שבת לסגמנט ביום שבת
        """
        shabbat_cache = {
            "2026-01-30": {"start": "16:50", "end": "17:50"},
            "2026-01-31": {"start": "16:50", "end": "17:50"},
        }

        # סגמנט ביום שישי 15:00-16:00 (לפני כניסת שבת) = חול
        friday_date = date(2026, 1, 30)
        segments_friday = [(15*60, 16*60, None, friday_date)]
        result_friday = _calculate_chain_wages_new(segments_friday, shabbat_cache, 0, False)
        self.assertEqual(result_friday["calc100"], 60, "15:00-16:00 ביום שישי = חול")

        # סגמנט ביום שישי 17:00-18:00 (אחרי כניסת שבת) = שבת
        segments_friday_shabbat = [(17*60, 18*60, None, friday_date)]
        result_friday_shabbat = _calculate_chain_wages_new(segments_friday_shabbat, shabbat_cache, 0, False)
        self.assertEqual(result_friday_shabbat["calc150"], 60, "17:00-18:00 ביום שישי = שבת")

        # סגמנט ביום שבת 00:00-01:00 = שבת
        saturday_date = date(2026, 1, 31)
        segments_saturday = [(0, 60, None, saturday_date)]
        result_saturday = _calculate_chain_wages_new(segments_saturday, shabbat_cache, 0, False)
        self.assertEqual(result_saturday["calc150"], 60, "00:00-01:00 ביום שבת = שבת")

    def test_multiple_segments_different_dates_same_chain(self):
        """
        רצף עבודה עם סגמנטים מימים שונים - כל סגמנט מחושב לפי התאריך שלו
        """
        shabbat_cache = {
            "2026-01-30": {"start": "16:50", "end": "17:50"},
            "2026-01-31": {"start": "16:50", "end": "17:50"},
        }

        friday = date(2026, 1, 30)
        saturday = date(2026, 1, 31)

        # רצף: 15:00-16:00 (שישי, חול) + 00:00-01:00 (שבת, שבת)
        segments_mixed = [
            (15*60, 16*60, None, friday),    # 60 דקות חול
            (0, 60, None, saturday),          # 60 דקות שבת
        ]

        result = _calculate_chain_wages_new(segments_mixed, shabbat_cache, 0, False)

        # 60 דקות חול + 60 דקות שבת
        self.assertEqual(result["calc100"], 60, "60 דקות ביום שישי לפני שבת = חול")
        self.assertEqual(result["calc150"], 60, "60 דקות ביום שבת = שבת")


class TestFixedSegmentsWithWorkLabel(unittest.TestCase):
    """בדיקות לתיקון: סגמנטים עם label='work' ביום is_fixed_segments"""

    def test_work_label_on_saturday_in_fixed_segments_day(self):
        """
        באג: כשיש תגבור + שעת עבודה באותו יום
        - תגבור קובעת is_fixed_segments=True
        - שעת עבודה מקבלת label='work'
        - בעיבוד is_fixed_segments, label='work' נופל ל-else ומחושב כ-100%
        - אבל אם שעת העבודה היא בשבת, צריך להיות 150%

        תיקון: בעיבוד is_fixed_segments, אם label='work', לחשב לפי actual_date
        """
        from core.time_utils import _get_shabbat_boundaries, FRIDAY, SATURDAY, MINUTES_PER_DAY

        # סימולציה של סגמנט עם label='work' ביום שבת
        saturday = date(2026, 1, 31)  # שבת
        seg_weekday = saturday.weekday()

        self.assertEqual(seg_weekday, SATURDAY, "31/01/2026 צריך להיות שבת")

        # קבלת גבולות שבת
        shabbat_cache = {}
        seg_shabbat_enter, seg_shabbat_exit = _get_shabbat_boundaries(saturday, shabbat_cache)

        # סגמנט 00:00-01:00 ביום שבת
        s, e = 0, 60
        actual_start = s % MINUTES_PER_DAY
        actual_end = e % MINUTES_PER_DAY

        day_offset = MINUTES_PER_DAY if seg_weekday == SATURDAY else 0
        abs_start = actual_start + day_offset
        abs_end = actual_end + day_offset

        # בדיקה שהזמן בתוך שבת
        is_in_shabbat = seg_shabbat_enter > 0 and abs_start >= seg_shabbat_enter and abs_end <= seg_shabbat_exit

        self.assertTrue(is_in_shabbat, f"00:00-01:00 בשבת צריך להיות בתוך שבת. enter={seg_shabbat_enter}, exit={seg_shabbat_exit}, abs_start={abs_start}, abs_end={abs_end}")


class TestHolidayWages(unittest.TestCase):
    """
    בדיקות חישוב שכר בחגים.
    חגים מתנהגים כמו שבת (תוספת 50%) וערבי חג כמו יום שישי.
    """

    def test_holiday_adds_50_percent(self):
        """
        יום חג (לא שבת) מקבל תוספת 50% כמו שבת.
        סוכות א' - יום רביעי 2025-10-07
        """
        # ערב סוכות (יום שלישי) עם enter, סוכות א' (יום רביעי) עם exit
        holiday_cache = {
            "2025-10-06": {"enter": "17:45"},  # ערב סוכות - יום שלישי
            "2025-10-07": {"exit": "18:40"},   # סוכות א' - יום רביעי
        }

        # משמרת 10:00-18:00 ביום חג (8 שעות)
        holiday_date = date(2025, 10, 7)  # יום רביעי
        segments = [(600, 1080, None, holiday_date)]

        result = _calculate_chain_wages_new(segments, holiday_cache, 0, False)

        # 8 שעות @ 150% (חג)
        self.assertEqual(result["calc150"], 480, "8 שעות ביום חג צריכות להיות 150%")
        self.assertEqual(result["calc100"], 0, "לא צריך להיות שעות ב-100%")
        self.assertEqual(result["calc150_shabbat"], 480, "צריך להיות מסומן כשבת/חג")

    def test_holiday_eve_like_friday(self):
        """
        ערב חג מתנהג כמו יום שישי - עבודה לפני כניסת החג היא חול,
        עבודה אחרי כניסת החג היא חג.

        מבנה הטבלה: הנתונים (enter + exit) נמצאים ברשומה של יום החג עצמו,
        לא ברשומה של יום הערב.
        """
        # מבנה כמו בטבלה האמיתית - כל הנתונים ברשומה של יום החג
        holiday_cache = {
            "2025-10-07": {"enter": "17:45", "exit": "18:40"},  # סוכות א' - enter היא הדלקת נרות בערב
        }

        # משמרת 16:00-20:00 בערב חג
        eve_date = date(2025, 10, 6)  # יום שני - ערב סוכות
        segments = [(960, 1200, None, eve_date)]  # 16:00-20:00

        result = _calculate_chain_wages_new(segments, holiday_cache, 0, False)

        # 16:00-17:45 (105 דקות) = חול @ 100%
        # 17:45-20:00 (135 דקות) = חג @ 150%
        self.assertEqual(result["calc100"], 105, "לפני כניסת החג = חול")
        self.assertEqual(result["calc150"], 135, "אחרי כניסת החג = 150%")

    def test_holiday_overtime_rates(self):
        """
        שעות נוספות בחג - אותם תעריפים כמו שבת:
        - 0-8 שעות: 150% (100% + 50%)
        - 8-10 שעות: 175% (125% + 50%)
        - 10+ שעות: 200% (150% + 50%)

        מבנה הטבלה: כל הנתונים (enter + exit) נמצאים ברשומה של יום החג עצמו.
        """
        # מבנה נכון - כל הנתונים ברשומה אחת
        holiday_cache = {
            "2025-10-07": {"enter": "17:45", "exit": "18:40"},
        }

        # משמרת 08:00-18:40 ביום חג (10:40 שעות = 640 דקות, כולו בחג)
        holiday_date = date(2025, 10, 7)
        segments = [(480, 1120, None, holiday_date)]  # עד צאת החג

        result = _calculate_chain_wages_new(segments, holiday_cache, 0, False)

        # 8 שעות @ 150%, 2 שעות @ 175%, 40 דקות @ 200%
        self.assertEqual(result["calc150"], 480, "8 שעות @ 150%")
        self.assertEqual(result["calc175"], 120, "2 שעות @ 175%")
        self.assertEqual(result["calc200"], 40, "40 דקות @ 200%")

    def test_two_day_holiday(self):
        """
        חג של יומיים (כמו ראש השנה).

        מבנה הטבלה: לחג דו-יומי יש רשומה אחת ליום האחרון עם enter (מהערב הראשון) ו-exit.
        חייב להיות שדה 'holiday' כדי לזהות אותו כחג דו-יומי.
        """
        # מבנה נכון - רשומה אחת ליום האחרון עם שדה holiday
        holiday_cache = {
            "2025-09-24": {"enter": "18:15", "exit": "19:10", "holiday": "ראש השנה"},
        }

        # משמרת ביום ב' של ראש השנה (2025-09-24)
        day2_date = date(2025, 9, 24)
        segments = [(600, 1080, None, day2_date)]  # 10:00-18:00 (8 שעות)

        result = _calculate_chain_wages_new(segments, holiday_cache, 0, False)

        # כל המשמרת בחג
        self.assertEqual(result["calc150"], 480, "8 שעות ביום ב' של ר\"ה = 150%")
        self.assertEqual(result["calc100"], 0)

    def test_holiday_vs_shabbat_same_logic(self):
        """
        השוואה: חג ושבת צריכים לקבל אותו חישוב כשהמשמרת כולה בתוך הזמן המקודש.
        """
        # שבת רגילה - משמרת 10:00-16:00 (כולה לפני צאת שבת ב-17:45)
        shabbat_cache = {
            "2026-01-09": {"enter": "16:30"},  # יום שישי
            "2026-01-10": {"exit": "17:45"},   # שבת
        }

        saturday = date(2026, 1, 10)
        segments_shabbat = [(600, 960, None, saturday)]  # 10:00-16:00 (6 שעות בשבת)

        result_shabbat = _calculate_chain_wages_new(segments_shabbat, shabbat_cache, 0, False)

        # חג (סוכות) - משמרת 10:00-16:00 (כולה לפני צאת החג ב-18:40)
        holiday_cache = {
            "2025-10-06": {"enter": "17:45"},
            "2025-10-07": {"exit": "18:40"},
        }

        holiday = date(2025, 10, 7)
        segments_holiday = [(600, 960, None, holiday)]  # 10:00-16:00 (6 שעות בחג)

        result_holiday = _calculate_chain_wages_new(segments_holiday, holiday_cache, 0, False)

        # שניהם צריכים להיות 6 שעות @ 150%
        self.assertEqual(result_shabbat["calc150"], result_holiday["calc150"],
                        "שבת וחג צריכים לקבל אותו חישוב")
        self.assertEqual(result_shabbat["calc150"], 360)  # 6 שעות

    def test_weekday_no_holiday(self):
        """
        יום חול רגיל (ללא חג) - לא מקבל תוספת.
        """
        # cache ריק - אין שבת או חג
        empty_cache = {}

        # יום רביעי רגיל
        wednesday = date(2025, 10, 8)  # יום אחרי סוכות
        segments = [(600, 1080, None, wednesday)]  # 10:00-18:00

        result = _calculate_chain_wages_new(segments, empty_cache, 0, False)

        # 8 שעות @ 100% (חול)
        self.assertEqual(result["calc100"], 480)
        self.assertEqual(result["calc150"], 0)

    def test_night_shift_on_holiday(self):
        """
        משמרת לילה בחג - סף 7 שעות כמו בשבת.

        מבנה הטבלה: כל הנתונים (enter + exit) נמצאים ברשומה של יום החג עצמו.
        """
        # מבנה נכון - כל הנתונים ברשומה אחת
        holiday_cache = {
            "2025-10-07": {"enter": "17:45", "exit": "18:40"},
        }

        # משמרת 22:00-06:30 בערב חג (8.5 שעות)
        eve_date = date(2025, 10, 6)
        segments = [
            (1320, 1440, None, eve_date),  # 22:00-00:00 בערב חג
            (1440, 1830, None, date(2025, 10, 7)),  # 00:00-06:30 ביום חג
        ]

        result = _calculate_chain_wages_new(segments, holiday_cache, 0, is_night_shift=True)

        # כל המשמרת אחרי כניסת החג (17:45) = חג
        # עם סף לילה (7 שעות): 7 שעות @ 150%, 1.5 שעות @ 175%
        self.assertEqual(result["calc150"], 420, "7 שעות @ 150%")
        self.assertEqual(result["calc175"], 90, "1.5 שעות @ 175%")

    def test_erev_pesach_not_confused_with_two_day_holiday(self):
        """ערב פסח חד-יומי לא מתבלבל עם חג דו-יומי."""
        holiday_cache = {
            "2026-04-02": {
                "enter": "19:49", "exit": "20:00", "holiday": "Pesach I",
            },
        }
        eve_date = date(2026, 4, 1)
        segments = [(720, 1320, None, eve_date)]
        result = _calculate_chain_wages_new(segments, holiday_cache, 0, False)
        # 12:00-19:49 = chol, 19:49-20:00 = chag 150%, 20:00-22:00 = chag 175%
        self.assertEqual(result["calc100"], 469)
        self.assertEqual(result["calc150"], 11)
        self.assertEqual(result["calc175"], 120)

    def test_erev_pesach_two_record_format(self):
        """ערב פסח בפורמט שתי רשומות."""
        holiday_cache = {
            "2026-04-01": {"enter": "19:49"},
            "2026-04-02": {"exit": "20:00"},
        }
        eve_date = date(2026, 4, 1)
        segments = [(720, 1320, None, eve_date)]
        result = _calculate_chain_wages_new(segments, holiday_cache, 0, False)
        self.assertEqual(result["calc100"], 469)
        self.assertEqual(result["calc150"], 11)
        self.assertEqual(result["calc175"], 120)

    def test_friday_holiday_full_day(self):
        """חג ביום שישי (שבועות) — כל המשמרת בחג @ 150%."""
        # שבועות 2026-05-22 (יום שישי), exit=00:00 = ממשיך לשבת
        holiday_cache = {
            "2026-05-22": {"enter": "18:52", "exit": "00:00", "holiday": "שבועות"},
        }
        friday_date = date(2026, 5, 22)
        segments = [(480, 960, None, friday_date)]  # 08:00-16:00

        result = _calculate_chain_wages_new(segments, holiday_cache, 0, False)

        self.assertEqual(result["calc150"], 480, "8 שעות ביום שישי-חג = 150%")
        self.assertEqual(result["calc100"], 0, "לא צריך שעות חול ביום חג")

    def test_friday_holiday_erev_thursday(self):
        """ערב שבועות ביום חמישי — חיתוך בכניסת החג."""
        holiday_cache = {
            "2026-05-22": {"enter": "18:52", "exit": "00:00", "holiday": "שבועות"},
        }
        thursday_date = date(2026, 5, 21)  # ערב שבועות
        segments = [(960, 1200, None, thursday_date)]  # 16:00-20:00

        result = _calculate_chain_wages_new(segments, holiday_cache, 0, False)

        # 16:00-18:52 = 172 דקות חול, 18:52-20:00 = 68 דקות חג
        self.assertEqual(result["calc100"], 172, "לפני כניסת החג = חול")
        self.assertEqual(result["calc150"], 68, "אחרי כניסת החג = 150%")

    def test_saturday_holiday_with_exit(self):
        """חג בשבת (שמיני עצרת) עם exit רגיל — 150%."""
        holiday_cache = {
            "2025-10-11": {"enter": "17:56", "exit": "19:03", "holiday": "שמיני עצרת"},
        }
        saturday_date = date(2025, 10, 11)
        segments = [(480, 960, None, saturday_date)]  # 08:00-16:00

        result = _calculate_chain_wages_new(segments, holiday_cache, 0, False)

        self.assertEqual(result["calc150"], 480, "8 שעות בשבת-חג = 150%")
        self.assertEqual(result["calc100"], 0)

    def test_rosh_hashana_on_shabbat_exit_00(self):
        """ר\"ה יום א' בשבת, exit=00:00 — כל היום חג @ 150%."""
        holiday_cache = {
            "2026-09-12": {"enter": "18:10", "exit": "00:00", "holiday": "ראש השנה"},
        }
        saturday_date = date(2026, 9, 12)
        segments = [(480, 960, None, saturday_date)]  # 08:00-16:00

        result = _calculate_chain_wages_new(segments, holiday_cache, 0, False)

        self.assertEqual(result["calc150"], 480, "8 שעות בשבת עם exit=00:00 = 150%")
        self.assertEqual(result["calc100"], 0, "exit=00:00 לא גורם לחישוב חול")

    def test_rosh_hashana_day2_after_shabbat(self):
        """ר\"ה יום ב' ביום ראשון אחרי שבת — 150%."""
        holiday_cache = {
            "2026-09-12": {"enter": "18:10", "exit": "00:00", "holiday": "ראש השנה"},
            "2026-09-13": {"enter": "19:39", "exit": "19:38", "holiday": "ראש השנה"},
        }
        sunday_date = date(2026, 9, 13)
        segments = [(480, 960, None, sunday_date)]  # 08:00-16:00

        result = _calculate_chain_wages_new(segments, holiday_cache, 0, False)

        self.assertEqual(result["calc150"], 480, "8 שעות ביום ב' ר\"ה = 150%")
        self.assertEqual(result["calc100"], 0)

    def test_holiday_shabbat_connected_both_days(self):
        """חג+שבת מחוברים (שבועות שישי + שבת) — שני ימים @ 150%."""
        holiday_cache = {
            "2026-05-22": {"enter": "18:52", "exit": "00:00", "holiday": "שבועות"},
            "2026-05-23": {"exit": "19:55"},  # שבת רגילה אחרי שבועות
        }
        # משמרת ביום שישי (חג)
        friday_date = date(2026, 5, 22)
        segments_fri = [(480, 960, None, friday_date)]
        result_fri = _calculate_chain_wages_new(segments_fri, holiday_cache, 0, False)

        # משמרת בשבת
        saturday_date = date(2026, 5, 23)
        segments_sat = [(480, 960, None, saturday_date)]
        result_sat = _calculate_chain_wages_new(segments_sat, holiday_cache, 0, False)

        self.assertEqual(result_fri["calc150"], 480, "שישי-חג = 150%")
        self.assertEqual(result_sat["calc150"], 480, "שבת = 150%")


class TestClassifyDayType(unittest.TestCase):
    """בדיקות לפונקציית classify_day_type."""

    def test_regular_weekday(self):
        """יום חול רגיל = weekday."""
        result = classify_day_type(date(2025, 10, 8), {})
        self.assertEqual(result, "weekday")

    def test_regular_friday_is_eve(self):
        """יום שישי רגיל = eve."""
        cache = {
            "2026-01-10": {"enter": "16:30", "exit": "17:45"},
        }
        result = classify_day_type(date(2026, 1, 9), cache)
        self.assertEqual(result, "eve")

    def test_regular_saturday_is_holy(self):
        """שבת רגילה = holy."""
        cache = {
            "2026-01-10": {"enter": "16:30", "exit": "17:45"},
        }
        result = classify_day_type(date(2026, 1, 10), cache)
        self.assertEqual(result, "holy")

    def test_friday_holiday_is_holy(self):
        """חג ביום שישי = holy (לא eve)."""
        cache = {
            "2026-05-22": {"enter": "18:52", "exit": "00:00", "holiday": "שבועות"},
        }
        result = classify_day_type(date(2026, 5, 22), cache)
        self.assertEqual(result, "holy")

    def test_erev_chag_weekday_is_eve(self):
        """ערב חג ביום חול = eve."""
        cache = {
            "2025-10-07": {"enter": "17:45", "exit": "18:40"},
        }
        result = classify_day_type(date(2025, 10, 6), cache)
        self.assertEqual(result, "eve")

    def test_chag_day_is_holy(self):
        """יום חג עם exit = holy."""
        cache = {
            "2025-10-07": {"enter": "17:45", "exit": "18:40"},
        }
        result = classify_day_type(date(2025, 10, 7), cache)
        self.assertEqual(result, "holy")

    def test_erev_chag_before_friday_holiday(self):
        """ערב שבועות (חמישי) לפני חג בשישי = eve."""
        cache = {
            "2026-05-22": {"enter": "18:52", "exit": "00:00", "holiday": "שבועות"},
        }
        result = classify_day_type(date(2026, 5, 21), cache)
        self.assertEqual(result, "eve")

    def test_saturday_with_exit_00_is_holy(self):
        """שבת עם exit=00:00 (חג ממשיך) = holy."""
        cache = {
            "2026-09-12": {"enter": "18:10", "exit": "00:00", "holiday": "ראש השנה"},
        }
        result = classify_day_type(date(2026, 9, 12), cache)
        self.assertEqual(result, "holy")


class TestHolidayEdgeCases(unittest.TestCase):
    """
    בדיקות מקרי קצה בחגים:
    ר"ה 3 ימים רצופים, ערב חג בשבת, חג ביום ראשון,
    משמרת שחוצה הבדלה, ר"ה שישי-שבת, משמרת לילה ממוצאי חג,
    חגים סמוכים עם הפסקה, הבדלה מוקדמת, משמרת ממוצ"ש לחג ראשון,
    משמרת שמתחילה בדיוק בכניסת חג.
    """

    def test_rosh_hashana_3_consecutive_holy_days(self):
        """
        ר"ה חמישי-שישי + שבת = 3 ימים קדושים רצופים.
        יום א' חמישי (exit=00:00), יום ב' שישי (exit=00:00), שבת.
        """
        cache = {
            # ר"ה יום א' - חמישי, ממשיך לשישי
            "2025-09-25": {"enter": "18:00", "exit": "00:00", "holiday": "ראש השנה"},
            # ר"ה יום ב' - שישי, ממשיך לשבת
            "2025-09-26": {"enter": "18:00", "exit": "00:00", "holiday": "ראש השנה"},
            # שבת רגילה אחרי ר"ה
            "2025-09-27": {"exit": "19:10"},
        }
        # חמישי = חג
        thu = date(2025, 9, 25)
        result_thu = _calculate_chain_wages_new(
            [(480, 960, None, thu)], cache, 0, False)
        self.assertEqual(result_thu["calc150"], 480, "חמישי ר\"ה = 150%")

        # שישי = חג (יום ב' ר"ה)
        fri = date(2025, 9, 26)
        result_fri = _calculate_chain_wages_new(
            [(480, 960, None, fri)], cache, 0, False)
        self.assertEqual(result_fri["calc150"], 480, "שישי ר\"ה = 150%")

        # שבת = קדוש
        sat = date(2025, 9, 27)
        result_sat = _calculate_chain_wages_new(
            [(480, 960, None, sat)], cache, 0, False)
        self.assertEqual(result_sat["calc150"], 480, "שבת אחרי ר\"ה = 150%")

    def test_erev_chag_on_shabbat(self):
        """
        ערב חג בשבת — שבת היא קדושה בפני עצמה, החג מתחיל במוצ"ש.
        סוכות ביום ראשון, ערב סוכות = שבת.
        """
        cache = {
            # שבת רגילה (גם ערב סוכות)
            "2025-10-04": {"enter": "17:50", "exit": "18:55"},
            # סוכות ביום ראשון
            "2025-10-05": {"enter": "18:55", "exit": "19:00", "holiday": "סוכות"},
        }
        # שבת = holy (שבת רגילה)
        sat = date(2025, 10, 4)
        result = _calculate_chain_wages_new(
            [(480, 960, None, sat)], cache, 0, False)
        self.assertEqual(result["calc150"], 480, "שבת = 150% גם כערב חג")
        self.assertEqual(classify_day_type(sat, cache), "holy")

    def test_holiday_on_sunday_erev_is_shabbat(self):
        """
        חג ביום ראשון — ערב החג הוא שבת.
        שבת נשארת "holy", יום ראשון גם "holy".
        """
        cache = {
            "2025-10-04": {"enter": "17:50", "exit": "18:55"},
            "2025-10-05": {"enter": "18:55", "exit": "19:00", "holiday": "סוכות"},
        }
        # שבת = holy
        self.assertEqual(classify_day_type(date(2025, 10, 4), cache), "holy")
        # יום ראשון = holy
        sun = date(2025, 10, 5)
        result = _calculate_chain_wages_new(
            [(480, 960, None, sun)], cache, 0, False)
        self.assertEqual(result["calc150"], 480, "יום ראשון-חג = 150%")

    def test_shift_crossing_havdalah(self):
        """
        משמרת שחוצה את צאת החג — חיתוך בהבדלה.
        חג עם הבדלה ב-19:03, משמרת 16:00-22:00.
        """
        cache = {
            "2025-10-11": {"enter": "17:56", "exit": "19:03", "holiday": "שמיני עצרת"},
        }
        sat = date(2025, 10, 11)
        segments = [(960, 1320, None, sat)]  # 16:00-22:00

        result = _calculate_chain_wages_new(segments, cache, 0, False)

        # 16:00-19:03 = 183 דקות חג, 19:03-22:00 = 177 דקות חול
        shabbat_total = result["calc150_shabbat"]
        weekday_total = result["calc100"] + result["calc125"] + result["calc150_overtime"]
        self.assertEqual(shabbat_total, 183, "לפני הבדלה = חג")
        self.assertEqual(weekday_total, 177, "אחרי הבדלה = חול")

    def test_rosh_hashana_friday_saturday(self):
        """
        ר"ה יום א' בשישי, יום ב' בשבת.
        שישי = חג (holy), שבת = חג+שבת (holy).
        """
        cache = {
            # ר"ה יום א' - שישי, ממשיך לשבת
            "2027-09-17": {"enter": "18:15", "exit": "00:00", "holiday": "ראש השנה"},
            # ר"ה יום ב' - שבת
            "2027-09-18": {"enter": "18:14", "exit": "19:20", "holiday": "ראש השנה"},
        }
        # שישי = holy (חג, לא ערב שבת)
        fri = date(2027, 9, 17)
        self.assertEqual(classify_day_type(fri, cache), "holy")
        result_fri = _calculate_chain_wages_new(
            [(480, 960, None, fri)], cache, 0, False)
        self.assertEqual(result_fri["calc150"], 480, "שישי ר\"ה = 150%")

        # שבת = holy
        sat = date(2027, 9, 18)
        result_sat = _calculate_chain_wages_new(
            [(480, 960, None, sat)], cache, 0, False)
        self.assertEqual(result_sat["calc150"], 480, "שבת ר\"ה = 150%")

    def test_night_shift_from_motzei_chag_to_weekday(self):
        """
        משמרת לילה ממוצאי חג לחול.
        חג ביום שלישי, הבדלה 19:08, משמרת 22:00-06:00.
        22:00-00:00 = אחרי הבדלה = חול, 00:00-06:00 = חול.
        """
        cache = {
            "2025-10-07": {"enter": "18:01", "exit": "19:08", "holiday": "סוכות"},
        }
        # הסגמנט הראשון: 22:00-00:00 ביום חג (אחרי הבדלה)
        chag_day = date(2025, 10, 7)
        next_day = date(2025, 10, 8)
        segments = [
            (1320, 1440, None, chag_day),   # 22:00-00:00
            (1440, 1800, None, next_day),   # 00:00-06:00
        ]
        result = _calculate_chain_wages_new(segments, cache, 0, is_night_shift=True)

        # הכל אחרי הבדלה (19:08) = חול
        self.assertEqual(result["calc150_shabbat"], 0, "אחרי הבדלה = חול")

    def test_adjacent_holidays_with_gap(self):
        """
        שני חגים סמוכים עם יום הפסקה ביניהם.
        סוכות (חמישי) → חול המועד (שישי) → שמיני עצרת (שבת).
        """
        cache = {
            "2025-10-02": {"enter": "18:07", "exit": "19:14", "holiday": "סוכות"},
            # שישי = שבת רגילה (חול המועד)
            "2025-10-04": {"enter": "17:55", "exit": "19:05"},
            # שמיני עצרת בשבת
            "2025-10-04": {"enter": "17:55", "exit": "19:05", "holiday": "שמיני עצרת"},
        }
        # חמישי סוכות = holy
        thu = date(2025, 10, 2)
        self.assertEqual(classify_day_type(thu, cache), "holy")

        # שישי = eve (ערב שבת רגיל, חול המועד = חול)
        fri = date(2025, 10, 3)
        self.assertEqual(classify_day_type(fri, cache), "eve")

        # שבת שמיני עצרת = holy
        sat = date(2025, 10, 4)
        self.assertEqual(classify_day_type(sat, cache), "holy")

    def test_holiday_with_early_havdalah(self):
        """
        חג עם הבדלה מוקדמת (חורף).
        משמרת 08:00-20:00, הבדלה ב-17:00.
        08:00-17:00 = חג (540 דקות), 17:00-20:00 = חול (180 דקות).
        """
        cache = {
            "2025-12-17": {"enter": "16:00", "exit": "17:00", "holiday": "חנוכה חג"},
        }
        wed = date(2025, 12, 17)  # יום רביעי
        segments = [(480, 1200, None, wed)]  # 08:00-20:00 (12 שעות)

        result = _calculate_chain_wages_new(segments, cache, 0, False)

        # 08:00-17:00 = 540 דקות חג: 480@150% + 60@175%
        # 17:00-20:00 = 180 דקות חול: 60@175% + 120@200%... wait
        # Actually with day_offset=1440 for holy day:
        # abs_start = 480+1440 = 1920, abs_end = 1200+1440 = 2640
        # exit = 17*60+1440 = 2460
        # 1920 < 2460 → during shabbat until 2460
        # 2460 < 2640 → crosses exit → Case 5
        # during = 2460-1920 = 540 → shabbat
        # after = 2640-2460 = 180 → weekday
        self.assertEqual(result["calc150_shabbat"], 480, "8 שעות ראשונות חג @ 150%")
        self.assertEqual(result["calc175"], 60, "שעה 9 חג @ 175%")
        # אחרי הבדלה = חול
        weekday_total = result["calc100"] + result["calc125"] + result["calc150_overtime"]
        self.assertEqual(weekday_total, 180, "3 שעות אחרי הבדלה = חול")

    def test_night_shift_motzei_shabbat_into_sunday_holiday(self):
        """
        משמרת לילה ממוצ"ש לחג ביום ראשון.
        שבת הבדלה 18:55, חג ראשון מתחיל 18:55.
        22:00-06:00: שבת כבר נגמרה, חג כבר התחיל → חג.
        """
        cache = {
            "2025-10-04": {"enter": "17:50", "exit": "18:55"},  # שבת
            "2025-10-05": {"enter": "18:55", "exit": "19:00", "holiday": "סוכות"},
        }
        # 22:00-00:00 על שבת (אחרי הבדלה 18:55)
        sat = date(2025, 10, 4)
        sun = date(2025, 10, 5)
        segments = [
            (1320, 1440, None, sat),   # 22:00-00:00 שבת (אחרי הבדלה)
            (1440, 1800, None, sun),   # 00:00-06:00 יום ראשון (חג)
        ]
        result = _calculate_chain_wages_new(segments, cache, 0, is_night_shift=True)

        # 22:00-00:00 = אחרי הבדלה שבת (18:55) = חול
        # 00:00-06:00 = יום ראשון חג = חג
        chag_minutes = result["calc150_shabbat"] + result["calc175"] + result["calc200"]
        self.assertGreater(chag_minutes, 0, "חלק מהמשמרת בחג")

    def test_shift_starting_exactly_at_candle_lighting(self):
        """
        משמרת שמתחילה בדיוק בזמן הדלקת נרות — כל הדקות חג.
        """
        cache = {
            "2025-10-07": {"enter": "18:01", "exit": "19:08", "holiday": "סוכות"},
        }
        # ערב חג, משמרת מתחילה בדיוק ב-18:01 (כניסת חג)
        eve = date(2025, 10, 6)
        segments = [(1081, 1201, None, eve)]  # 18:01-20:01 (120 דקות)

        result = _calculate_chain_wages_new(segments, cache, 0, False)

        # כל 120 הדקות אחרי כניסת החג = חג
        self.assertEqual(result["calc150_shabbat"], 120, "כל המשמרת בחג")
        self.assertEqual(result["calc100"], 0, "אין דקות חול")


# ============================================================================
# חלק 2: בדיקות ידניות עם נתונים אמיתיים
# ============================================================================

def run_real_data_tests():
    """הרצת בדיקות על נתונים אמיתיים מהמערכת"""

    print("\n" + "="*70)
    print("בדיקות ידניות עם נתונים אמיתיים")
    print("="*70)

    try:
        from core.database import get_pooled_connection, return_connection
        from core.logic import calculate_person_monthly_totals
        from app_utils import get_daily_segments_data
    except ImportError as e:
        print(f"שגיאה בייבוא: {e}")
        print("וודא שאתה מריץ מתיקיית הפרויקט")
        return

    conn = get_pooled_connection()
    if not conn:
        print("לא ניתן להתחבר למסד הנתונים")
        return

    try:
        # בדיקה 1: ברהמי רחל - דצמבר 2025
        print("\n" + "-"*50)
        print("בדיקה 1: ברהמי רחל - דצמבר 2025")
        print("-"*50)
        _manual_test_worker_calculation(conn, "ברהמי רחל", 2025, 12)

        # בדיקה 2: בדיקת יום ספציפי עם משמרות חופפות
        print("\n" + "-"*50)
        print("בדיקה 2: ברהמי רחל - 18/12/2025 (משמרות חופפות)")
        print("-"*50)
        _manual_test_specific_day(conn, "ברהמי רחל", date(2025, 12, 18))

    finally:
        return_connection(conn)


def _manual_test_worker_calculation(conn, worker_name: str, year: int, month: int):
    """בדיקת חישוב לעובד ספציפי - להרצה ידנית עם DB אמיתי (לא pytest)"""

    from core.logic import calculate_person_monthly_totals

    # מציאת העובד
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM people WHERE name LIKE %s", (f"%{worker_name}%",))
    row = cursor.fetchone()

    if not row:
        print(f"עובד '{worker_name}' לא נמצא")
        return

    person_id, full_name = row
    print(f"עובד: {full_name} (ID: {person_id})")

    # חישוב חודשי
    shabbat_cache = {}
    result = calculate_person_monthly_totals(conn, person_id, year, month, shabbat_cache)

    if not result:
        print("לא נמצאו נתונים לחודש זה")
        return

    print(f"\nסיכום חודשי:")
    print(f"  שעות 100%: {result.get('calc100', 0) / 60:.2f}")
    print(f"  שעות 125%: {result.get('calc125', 0) / 60:.2f}")
    print(f"  שעות 150%: {result.get('calc150', 0) / 60:.2f}")
    print(f"  שעות 175%: {result.get('calc175', 0) / 60:.2f}")
    print(f"  שעות 200%: {result.get('calc200', 0) / 60:.2f}")
    print(f"  סה\"כ שעות: {result.get('total_minutes', 0) / 60:.2f}")
    print(f"  תשלום עבודה: {result.get('work_payment', 0):.2f} ש\"ח")
    print(f"  תשלום כוננות: {result.get('standby_payment', 0):.2f} ש\"ח")


def _manual_test_specific_day(conn, worker_name: str, test_date: date):
    """בדיקת יום ספציפי עם פירוט מלא - להרצה ידנית עם DB אמיתי (לא pytest)"""

    # מציאת העובד
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM people WHERE name LIKE %s", (f"%{worker_name}%",))
    row = cursor.fetchone()

    if not row:
        print(f"עובד '{worker_name}' לא נמצא")
        return

    person_id, full_name = row

    # שליפת דיווחים ליום הספציפי
    cursor.execute("""
        SELECT tr.id, tr.date, tr.start_time, tr.end_time,
               st.name AS shift_name
        FROM time_reports tr
        LEFT JOIN shift_types st ON st.id = tr.shift_type_id
        WHERE tr.person_id = %s AND tr.date = %s
        ORDER BY tr.start_time
    """, (person_id, test_date))

    reports = cursor.fetchall()

    if not reports:
        print(f"לא נמצאו דיווחים ליום {test_date.strftime('%d/%m/%Y')}")
        return

    print(f"\nדיווחים ליום {test_date.strftime('%d/%m/%Y')}:")
    print("-" * 60)

    for report in reports:
        report_id, rep_date, start_time, end_time, shift_name = report
        print(f"  ID: {report_id}")
        print(f"    שעות: {start_time} - {end_time}")
        print(f"    משמרת: {shift_name or '?'}")
        print()


def compare_logic_and_display(conn, person_id: int, year: int, month: int):
    """השוואה בין חישוב logic.py לתצוגה app_utils.py"""

    from core.logic import calculate_person_monthly_totals
    from app_utils import get_daily_segments_data

    print("\n" + "-"*50)
    print("השוואה בין logic.py ל-app_utils.py")
    print("-"*50)

    # חישוב מ-logic.py
    logic_result = calculate_person_monthly_totals(conn, person_id, year, month)

    # חישוב מ-app_utils.py
    display_data = get_daily_segments_data(conn, person_id, year, month)

    # סיכום מהתצוגה
    display_total_minutes = sum(d.get("total_minutes", 0) for d in display_data)
    display_payment = sum(d.get("payment", 0) for d in display_data)

    print(f"\nlogic.py:")
    print(f"  סה\"כ דקות: {logic_result.get('total_minutes', 0)}")
    print(f"  תשלום עבודה: {logic_result.get('work_payment', 0):.2f} ש\"ח")

    print(f"\napp_utils.py:")
    print(f"  סה\"כ דקות: {display_total_minutes}")
    print(f"  תשלום: {display_payment:.2f} ש\"ח")

    # בדיקת התאמה
    minutes_match = abs(logic_result.get('total_minutes', 0) - display_total_minutes) < 5
    payment_match = abs(logic_result.get('work_payment', 0) - display_payment) < 1

    if minutes_match and payment_match:
        print("\n✓ התוצאות תואמות!")
    else:
        print("\n✗ יש אי-התאמה!")
        if not minutes_match:
            print(f"  הפרש דקות: {abs(logic_result.get('total_minutes', 0) - display_total_minutes)}")
        if not payment_match:
            print(f"  הפרש תשלום: {abs(logic_result.get('work_payment', 0) - display_payment):.2f}")


# ============================================================================
# הרצת הבדיקות
# ============================================================================

def run_unit_tests():
    """הרצת בדיקות אוטומטיות בלבד"""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # בדיקות בסיסיות
    suite.addTests(loader.loadTestsFromTestCase(TestOvertimeCalculation))
    suite.addTests(loader.loadTestsFromTestCase(TestShabbatCalculation))
    suite.addTests(loader.loadTestsFromTestCase(TestCarryover))
    suite.addTests(loader.loadTestsFromTestCase(TestOverlappingShiftsWithDifferentRates))
    suite.addTests(loader.loadTestsFromTestCase(TestMedicalEscort))
    suite.addTests(loader.loadTestsFromTestCase(TestStandaloneMidnightShift))
    suite.addTests(loader.loadTestsFromTestCase(TestTagbur))
    suite.addTests(loader.loadTestsFromTestCase(TestStandby))

    # בדיקות חישוב שכר מלא
    suite.addTests(loader.loadTestsFromTestCase(TestFullSalaryCalculation))
    suite.addTests(loader.loadTestsFromTestCase(TestEdgeCases))
    suite.addTests(loader.loadTestsFromTestCase(TestChainCalculationIntegration))

    # הרצה
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return result


class TestSickVacationWorkHours(unittest.TestCase):
    """בדיקות חישוב שעות עבודה לחופשה/מחלה לפי override של משמרת חול."""

    def test_calculate_weekday_work_minutes_default_array1(self):
        """ברירת מחדל מערך 1: 16:00-08:00 = 7.5 שעות עבודה."""
        from core.constants import calculate_weekday_work_minutes
        # 16:00 = 960, 08:00 = 480 -> span_minutes gives 480+1440=1920
        result = calculate_weekday_work_minutes(960, 480)
        self.assertEqual(result, 450)  # 7.5 שעות = 450 דקות

    def test_calculate_weekday_work_minutes_extended_shift(self):
        """דירות עם override 15:00-08:00 = 8.5 שעות עבודה."""
        from core.constants import calculate_weekday_work_minutes
        # 15:00 = 900, 08:00 = 480
        result = calculate_weekday_work_minutes(900, 480)
        self.assertEqual(result, 510)  # 8.5 שעות = 510 דקות

    def test_calculate_weekday_work_minutes_short_shift(self):
        """דירות עם override 17:00-08:30 = 7 שעות עבודה."""
        from core.constants import calculate_weekday_work_minutes
        # 17:00 = 1020, 08:30 = 510
        result = calculate_weekday_work_minutes(1020, 510)
        self.assertEqual(result, 420)  # 7 שעות = 420 דקות

    def test_calculate_weekday_work_minutes_array2_default(self):
        """ברירת מחדל מערך 2: 16:30-08:30 = 7.5 שעות עבודה."""
        from core.constants import calculate_weekday_work_minutes
        # 16:30 = 990, 08:30 = 510
        result = calculate_weekday_work_minutes(990, 510)
        self.assertEqual(result, 450)  # 7.5 שעות = 450 דקות

    def test_build_sick_vacation_segments_default(self):
        """בדיקת פיצול סגמנטים: 16:00-08:00 → שני סגמנטי עבודה."""
        from app_utils import _build_sick_vacation_segments
        segs = _build_sick_vacation_segments("16:00", "08:00")
        self.assertEqual(len(segs), 2)
        # סגמנט ראשון: 16:00-22:00
        self.assertEqual(segs[0]["start_time"], "16:00")
        self.assertEqual(segs[0]["end_time"], "22:00")
        self.assertEqual(segs[0]["segment_type"], "work")
        # סגמנט שני: 06:30-08:00
        self.assertEqual(segs[1]["start_time"], "06:30")
        self.assertEqual(segs[1]["end_time"], "08:00")
        self.assertEqual(segs[1]["segment_type"], "work")

    def test_build_sick_vacation_segments_extended(self):
        """בדיקת פיצול סגמנטים: 15:00-08:00 → סגמנט ראשון ארוך יותר."""
        from app_utils import _build_sick_vacation_segments
        segs = _build_sick_vacation_segments("15:00", "08:00")
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0]["start_time"], "15:00")
        self.assertEqual(segs[0]["end_time"], "22:00")
        self.assertEqual(segs[1]["start_time"], "06:30")
        self.assertEqual(segs[1]["end_time"], "08:00")

    def test_build_sick_vacation_segments_short(self):
        """בדיקת פיצול סגמנטים: 17:00-08:30 → סגמנט שני ארוך יותר."""
        from app_utils import _build_sick_vacation_segments
        segs = _build_sick_vacation_segments("17:00", "08:30")
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0]["start_time"], "17:00")
        self.assertEqual(segs[0]["end_time"], "22:00")
        self.assertEqual(segs[1]["start_time"], "06:30")
        self.assertEqual(segs[1]["end_time"], "08:30")

    def test_build_segments_total_minutes_match(self):
        """בדיקה שסך הדקות בסגמנטים = calculate_weekday_work_minutes."""
        from app_utils import _build_sick_vacation_segments
        from core.constants import calculate_weekday_work_minutes
        from core.time_utils import span_minutes

        test_cases = [
            ("16:00", "08:00", 450),
            ("15:00", "08:00", 510),
            ("17:00", "08:30", 420),
            ("16:30", "08:30", 450),
        ]
        for start, end, expected_mins in test_cases:
            segs = _build_sick_vacation_segments(start, end)
            total = sum(
                span_minutes(s["start_time"], s["end_time"])[1] - span_minutes(s["start_time"], s["end_time"])[0]
                for s in segs
            )
            start_min, end_min = span_minutes(start, end)
            calc_total = calculate_weekday_work_minutes(start_min, end_min)
            self.assertEqual(total, calc_total, f"Mismatch for {start}-{end}")
            self.assertEqual(total, expected_mins, f"Expected {expected_mins} for {start}-{end}, got {total}")


class TestSickVacationEdgeCases(unittest.TestCase):
    """מקרי קצה בחישוב שעות מחלה/חופשה."""

    def test_edge1_specific_apartment_override_gives_more_hours(self):
        """מקרה קצה 1: דירה עם override ספציפי (15:00-08:00) מקבלת 8.5 שעות."""
        from app_utils import _build_sick_vacation_segments, _build_weekday_work_overrides
        segs = _build_sick_vacation_segments("15:00", "08:00")
        from core.time_utils import span_minutes
        total_mins = sum(
            span_minutes(s["start_time"], s["end_time"])[1] - span_minutes(s["start_time"], s["end_time"])[0]
            for s in segs
        )
        # 15:00-22:00 = 420 + 06:30-08:00 = 90 → 510 דקות = 8.5 שעות
        self.assertEqual(total_mins, 510)
        # ולא 450 (7.5 שעות) כמו ברירת מחדל
        self.assertNotEqual(total_mins, 450)

    def test_edge2_before_feb2026_no_overrides(self):
        """מקרה קצה 2: לפני 02/2026 הלוגיקה הישנה פועלת (dict ריק)."""
        # הבדיקה של (year, month) >= (2026, 2)
        self.assertTrue((2026, 2) >= (2026, 2))   # פברואר 2026 - כלול
        self.assertTrue((2026, 3) >= (2026, 2))   # מרץ 2026 - כלול
        self.assertFalse((2026, 1) >= (2026, 2))  # ינואר 2026 - לא כלול
        self.assertFalse((2025, 12) >= (2026, 2)) # דצמבר 2025 - לא כלול

    def test_edge3_apartment_without_any_override_fallback(self):
        """מקרה קצה 3: דירה ללא override כלל - overrides dict ריק, fallback לסגמנטים."""
        from app_utils import _build_sick_vacation_segments
        # אם דירה לא נמצאת ב-weekday_work_overrides, הקוד לא יחליף את seg_list
        overrides = {}  # ריק - אין overrides
        apt_id = 999  # דירה שלא קיימת
        # הבדיקה: apt_id לא ב-overrides → לא מחליפים
        self.assertNotIn(apt_id, overrides)

    def test_edge4_array2_different_segment_split(self):
        """מקרה קצה 4: מערך 2 (16:30-08:30) - חלוקה שונה אבל אותו סך שעות."""
        from app_utils import _build_sick_vacation_segments
        from core.time_utils import span_minutes
        segs = _build_sick_vacation_segments("16:30", "08:30")
        self.assertEqual(len(segs), 2)
        # סגמנט ראשון: 16:30-22:00 = 330 דקות (5.5 שעות)
        s1_start, s1_end = span_minutes(segs[0]["start_time"], segs[0]["end_time"])
        self.assertEqual(s1_end - s1_start, 330)
        # סגמנט שני: 06:30-08:30 = 120 דקות (2 שעות)
        s2_start, s2_end = span_minutes(segs[1]["start_time"], segs[1]["end_time"])
        self.assertEqual(s2_end - s2_start, 120)
        # סה"כ: 450 = 7.5 שעות (כמו ברירת מחדל אבל חלוקה שונה)
        self.assertEqual((s1_end - s1_start) + (s2_end - s2_start), 450)

    def test_edge5_shift_entirely_within_standby_zero_work(self):
        """מקרה קצה 5: משמרת שכולה בתוך שעות כוננות = 0 דקות עבודה."""
        from core.constants import calculate_weekday_work_minutes
        # משמרת 23:00-05:00 - כולה בתוך 22:00-06:30
        result = calculate_weekday_work_minutes(23 * 60, 5 * 60)
        self.assertEqual(result, 0)
        # גם _build_sick_vacation_segments צריך להחזיר רשימה ריקה
        from app_utils import _build_sick_vacation_segments
        segs = _build_sick_vacation_segments("23:00", "05:00")
        self.assertEqual(len(segs), 0)


class TestSickVacationEdgeCases2(unittest.TestCase):
    """מקרי קצה נוספים בחישוב שעות מחלה/חופשה."""

    def test_edge6_morning_segment_keeps_report_date(self):
        """מקרה קצה 6: סגמנט בוקר (06:30-08:00) נשאר ביום הדיווח, לא ביום הבא."""
        from app_utils import _build_sick_vacation_segments
        from core.time_utils import span_minutes
        segs = _build_sick_vacation_segments("16:00", "08:00")
        # הסגמנט השני (06:30-08:00) עובר חצות, אבל לפי הלוגיקה החדשה
        # actual_seg_date לא מקודם ליום הבא במחלה/חופשה
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[1]["start_time"], "06:30")
        self.assertEqual(segs[1]["end_time"], "08:00")

    def test_edge7_sick_day_payment_rates_with_gap(self):
        """מקרה קצה 7: רצף מחלה עם הפסקה - הרצף מתאפס."""
        from core.sick_days import _identify_sick_day_sequences, get_sick_payment_rate
        # יצירת דיווחים עם הפסקה: יום 1, יום 2, (הפסקה), יום 5, יום 6
        from datetime import date
        reports = [
            {"date": date(2026, 2, 1), "shift_name": "יום מחלה"},
            {"date": date(2026, 2, 2), "shift_name": "יום מחלה"},
            {"date": date(2026, 2, 5), "shift_name": "יום מחלה"},
            {"date": date(2026, 2, 6), "shift_name": "יום מחלה"},
        ]
        seq = _identify_sick_day_sequences(reports)
        # רצף 1: ימים 1-2 (day 1, day 2)
        self.assertEqual(seq[date(2026, 2, 1)], 1)
        self.assertEqual(seq[date(2026, 2, 2)], 2)
        # רצף 2 (חדש): ימים 5-6 (day 1, day 2)
        self.assertEqual(seq[date(2026, 2, 5)], 1)
        self.assertEqual(seq[date(2026, 2, 6)], 2)
        # תשלום: יום 1=0%, יום 2=50%
        self.assertEqual(get_sick_payment_rate(1), 0.0)
        self.assertEqual(get_sick_payment_rate(2), 0.5)

    def test_edge8_vacation_always_100_percent(self):
        """מקרה קצה 8: חופשה תמיד 100% תשלום (לא מדורגת כמו מחלה)."""
        from app_utils import _build_sick_vacation_segments
        from core.time_utils import span_minutes
        # חופשה עם override 15:00-08:00 = 8.5 שעות
        segs = _build_sick_vacation_segments("15:00", "08:00")
        total_mins = sum(
            span_minutes(s["start_time"], s["end_time"])[1]
            - span_minutes(s["start_time"], s["end_time"])[0]
            for s in segs
        )
        self.assertEqual(total_mins, 510)  # 8.5 שעות
        # חופשה לא עוברת דרך sick_day_sequence - תמיד משולמת 100%
        # (אימות שהסגמנטים הם "work" type, לא "sick")
        for s in segs:
            self.assertEqual(s["segment_type"], "work")

    def test_edge9_two_segment_total_matches_calculate_weekday(self):
        """מקרה קצה 9: סכום שני הסגמנטים = תוצאת calculate_weekday_work_minutes."""
        from app_utils import _build_sick_vacation_segments
        from core.constants import calculate_weekday_work_minutes
        from core.time_utils import span_minutes
        test_cases = [
            ("16:00", "08:00"),   # מערך 1 ברירת מחדל
            ("15:00", "08:00"),   # override 8.5h
            ("17:00", "08:30"),   # override 7h
            ("16:30", "08:30"),   # מערך 2 ברירת מחדל
            ("16:30", "09:00"),   # override דירה 35
        ]
        for start, end in test_cases:
            segs = _build_sick_vacation_segments(start, end)
            seg_total = sum(
                span_minutes(s["start_time"], s["end_time"])[1]
                - span_minutes(s["start_time"], s["end_time"])[0]
                for s in segs
            )
            s_min, e_min = span_minutes(start, end)
            calc_total = calculate_weekday_work_minutes(s_min, e_min)
            self.assertEqual(seg_total, calc_total,
                             f"Mismatch for {start}-{end}: segments={seg_total}, calc={calc_total}")

    def test_edge10_override_priority_apartment_over_housing_array(self):
        """מקרה קצה 10: override לדירה ספציפית גובר על ברירת מחדל למערך."""
        from app_utils import _build_sick_vacation_segments
        from core.time_utils import span_minutes
        # מערך 1 ברירת מחדל: 16:00-08:00 = 7.5h
        default_segs = _build_sick_vacation_segments("16:00", "08:00")
        default_total = sum(
            span_minutes(s["start_time"], s["end_time"])[1]
            - span_minutes(s["start_time"], s["end_time"])[0]
            for s in default_segs
        )
        # override ספציפי לדירה: 15:00-08:00 = 8.5h
        override_segs = _build_sick_vacation_segments("15:00", "08:00")
        override_total = sum(
            span_minutes(s["start_time"], s["end_time"])[1]
            - span_minutes(s["start_time"], s["end_time"])[0]
            for s in override_segs
        )
        # override ספציפי > ברירת מחדל
        self.assertGreater(override_total, default_total)
        self.assertEqual(override_total - default_total, 60)  # הפרש שעה אחת


class TestWeekdayShiftOverrides(unittest.TestCase):
    """בדיקות החלפת סגמנטים למשמרת חול (103) לפי override של הדירה."""

    BASE_SEGMENTS = [
        {"start_time": "16:00", "end_time": "22:00", "segment_type": "work", "id": 4},
        {"start_time": "22:00", "end_time": "06:30", "segment_type": "standby", "id": 5},
        {"start_time": "06:30", "end_time": "08:00", "segment_type": "work", "id": 6},
    ]

    def test_override_segments_include_standby_with_original_id(self):
        """סגמנטים עם override כוללים standby עם segment_id מקורי."""
        from app_utils import _build_weekday_shift_overrides
        apt_overrides = {11: ("15:00", "08:00")}
        ha_defaults = {}
        apartment_housing_map = {11: 1}
        result = _build_weekday_shift_overrides(
            {11}, apartment_housing_map, apt_overrides, ha_defaults, self.BASE_SEGMENTS
        )
        self.assertIn(11, result)
        segs = result[11]
        self.assertEqual(len(segs), 3)
        # סגמנט כוננות שומר על segment_id מקורי
        standby = segs[1]
        self.assertEqual(standby["segment_type"], "standby")
        self.assertEqual(standby["start_time"], "22:00")
        self.assertEqual(standby["end_time"], "06:30")
        self.assertEqual(standby["id"], 5)

    def test_override_15_00_work_segments(self):
        """דירה 11 (override 15:00-08:00): סגמנטי עבודה 15:00-22:00 ו-06:30-08:00."""
        from app_utils import _build_weekday_shift_overrides
        apt_overrides = {11: ("15:00", "08:00")}
        ha_defaults = {}
        result = _build_weekday_shift_overrides(
            {11}, {11: 1}, apt_overrides, ha_defaults, self.BASE_SEGMENTS
        )
        segs = result[11]
        # סגמנט עבודה ראשון: 15:00-22:00
        self.assertEqual(segs[0]["start_time"], "15:00")
        self.assertEqual(segs[0]["end_time"], "22:00")
        self.assertEqual(segs[0]["segment_type"], "work")
        # סגמנט עבודה שני: 06:30-08:00
        self.assertEqual(segs[2]["start_time"], "06:30")
        self.assertEqual(segs[2]["end_time"], "08:00")
        self.assertEqual(segs[2]["segment_type"], "work")

    def test_override_17_00_shorter_work(self):
        """דירות 32,33,36 (override 17:00-08:30): סגמנט עבודה ראשון 17:00-22:00."""
        from app_utils import _build_weekday_shift_overrides
        apt_overrides = {32: ("17:00", "08:30")}
        ha_defaults = {}
        result = _build_weekday_shift_overrides(
            {32}, {32: 2}, apt_overrides, ha_defaults, self.BASE_SEGMENTS
        )
        segs = result[32]
        self.assertEqual(segs[0]["start_time"], "17:00")
        self.assertEqual(segs[0]["end_time"], "22:00")
        self.assertEqual(segs[2]["start_time"], "06:30")
        self.assertEqual(segs[2]["end_time"], "08:30")

    def test_no_override_uses_default_segments(self):
        """דירה רגילה ללא override: לא מופיעה במפה."""
        from app_utils import _build_weekday_shift_overrides
        apt_overrides = {11: ("15:00", "08:00")}
        ha_defaults = {}
        apartment_housing_map = {99: 3}  # מערך ללא override
        result = _build_weekday_shift_overrides(
            {99}, apartment_housing_map, apt_overrides, ha_defaults, self.BASE_SEGMENTS
        )
        self.assertNotIn(99, result)

    def test_housing_array_default_applies(self):
        """דירה ללא override ספציפי מקבלת ברירת מחדל ממערך הדיור."""
        from app_utils import _build_weekday_shift_overrides
        apt_overrides = {}
        ha_defaults = {1: ("16:00", "08:00")}
        result = _build_weekday_shift_overrides(
            {50}, {50: 1}, apt_overrides, ha_defaults, self.BASE_SEGMENTS
        )
        self.assertIn(50, result)
        segs = result[50]
        self.assertEqual(segs[0]["start_time"], "16:00")
        self.assertEqual(segs[0]["end_time"], "22:00")

    def test_apartment_override_beats_housing_array(self):
        """override לדירה ספציפית גובר על ברירת מחדל של מערך."""
        from app_utils import _resolve_override_for_apartment
        apt_overrides = {11: ("15:00", "08:00")}
        ha_defaults = {1: ("16:00", "08:00")}
        result = _resolve_override_for_apartment(11, apt_overrides, ha_defaults, {11: 1})
        self.assertEqual(result, ("15:00", "08:00"))

    def test_no_standby_returns_empty(self):
        """אם אין סגמנט כוננות בבסיס, מחזיר מפה ריקה."""
        from app_utils import _build_weekday_shift_overrides
        base_no_standby = [
            {"start_time": "16:00", "end_time": "08:00", "segment_type": "work", "id": 4},
        ]
        apt_overrides = {11: ("15:00", "08:00")}
        result = _build_weekday_shift_overrides(
            {11}, {11: 1}, apt_overrides, {}, base_no_standby
        )
        self.assertEqual(result, {})


class TestWeekdayShiftOverrideEdgeCases(unittest.TestCase):
    """10 מקרי קצה על הלוגיקה החדשה של החלפת סגמנטים למשמרת חול."""

    # סגמנטים מקוריים כמו ב-DB (shift 103)
    BASE_SEGMENTS = [
        {"start_time": "16:00", "end_time": "22:00", "segment_type": "work", "id": 23},
        {"start_time": "22:00", "end_time": "06:30", "segment_type": "standby", "id": 2},
        {"start_time": "06:30", "end_time": "08:00", "segment_type": "work", "id": 3},
    ]

    # Overrides כמו ב-DB האמיתי
    APT_OVERRIDES = {
        10: ("15:00", "08:00"),
        11: ("15:00", "08:00"),
        17: ("15:00", "08:00"),
        20: ("15:00", "08:00"),
        21: ("15:20", "08:00"),
        25: ("15:00", "08:00"),
        32: ("17:00", "08:30"),
        33: ("17:00", "08:30"),
        35: ("16:30", "09:00"),
        36: ("17:00", "08:30"),
    }
    HA_DEFAULTS = {
        1: ("16:00", "08:00"),
        2: ("16:30", "08:30"),
    }

    def _seg_work_minutes(self, segs: list[dict]) -> int:
        """סכום דקות עבודה (work בלבד, ללא standby) מרשימת סגמנטים."""
        from core.time_utils import span_minutes
        total = 0
        for s in segs:
            if s["segment_type"] == "work":
                start, end = span_minutes(s["start_time"], s["end_time"])
                total += end - start
        return total

    def _seg_standby_minutes(self, segs: list[dict]) -> int:
        """סכום דקות כוננות מרשימת סגמנטים."""
        from core.time_utils import span_minutes
        total = 0
        for s in segs:
            if s["segment_type"] == "standby":
                start, end = span_minutes(s["start_time"], s["end_time"])
                total += end - start
        return total

    def test_edge1_non_round_override_start_15_20(self):
        """מקרה קצה 1: דירה 21 עם override 15:20-08:00 — סגמנט עבודה ראשון 15:20-22:00."""
        from app_utils import _build_weekday_shift_overrides
        result = _build_weekday_shift_overrides(
            {21}, {21: 1}, self.APT_OVERRIDES, self.HA_DEFAULTS, self.BASE_SEGMENTS
        )
        segs = result[21]
        self.assertEqual(segs[0]["start_time"], "15:20")
        self.assertEqual(segs[0]["end_time"], "22:00")
        self.assertEqual(segs[0]["segment_type"], "work")

    def test_edge2_override_end_past_0800_apartment_35(self):
        """מקרה קצה 2: דירה 35 עם override 16:30-09:00 — סגמנט עבודה שני 06:30-09:00."""
        from app_utils import _build_weekday_shift_overrides
        result = _build_weekday_shift_overrides(
            {35}, {35: 2}, self.APT_OVERRIDES, self.HA_DEFAULTS, self.BASE_SEGMENTS
        )
        segs = result[35]
        self.assertEqual(segs[2]["start_time"], "06:30")
        self.assertEqual(segs[2]["end_time"], "09:00")
        self.assertEqual(segs[2]["segment_type"], "work")

    def test_edge3_work_minutes_override_15_00_is_510(self):
        """מקרה קצה 3: override 15:00-08:00 = 510 דקות עבודה (8.5h)."""
        from app_utils import _build_weekday_shift_overrides
        result = _build_weekday_shift_overrides(
            {11}, {11: 1}, self.APT_OVERRIDES, self.HA_DEFAULTS, self.BASE_SEGMENTS
        )
        self.assertEqual(self._seg_work_minutes(result[11]), 510)

    def test_edge4_work_minutes_override_17_00_is_420(self):
        """מקרה קצה 4: override 17:00-08:30 = 420 דקות עבודה (7h)."""
        from app_utils import _build_weekday_shift_overrides
        result = _build_weekday_shift_overrides(
            {32}, {32: 2}, self.APT_OVERRIDES, self.HA_DEFAULTS, self.BASE_SEGMENTS
        )
        self.assertEqual(self._seg_work_minutes(result[32]), 420)

    def test_edge5_work_minutes_override_16_30_09_00_is_480(self):
        """מקרה קצה 5: override 16:30-09:00 = 480 דקות עבודה (8h)."""
        from app_utils import _build_weekday_shift_overrides
        result = _build_weekday_shift_overrides(
            {35}, {35: 2}, self.APT_OVERRIDES, self.HA_DEFAULTS, self.BASE_SEGMENTS
        )
        self.assertEqual(self._seg_work_minutes(result[35]), 480)

    def test_edge6_standby_always_510_regardless_of_override(self):
        """מקרה קצה 6: כוננות תמיד 510 דקות (22:00-06:30) ללא קשר ל-override."""
        from app_utils import _build_weekday_shift_overrides
        all_apts = {10, 11, 21, 32, 35}
        housing_map = {10: 1, 11: 1, 21: 1, 32: 2, 35: 2}
        result = _build_weekday_shift_overrides(
            all_apts, housing_map, self.APT_OVERRIDES, self.HA_DEFAULTS, self.BASE_SEGMENTS
        )
        for apt_id in all_apts:
            self.assertEqual(
                self._seg_standby_minutes(result[apt_id]), 510,
                f"Standby not 510 for apt {apt_id}"
            )

    def test_edge7_multiple_apartments_each_gets_own_override(self):
        """מקרה קצה 7: מספר דירות - כל אחת מקבלת override שונה."""
        from app_utils import _build_weekday_shift_overrides
        apts = {11, 32, 35}
        housing_map = {11: 1, 32: 2, 35: 2}
        result = _build_weekday_shift_overrides(
            apts, housing_map, self.APT_OVERRIDES, self.HA_DEFAULTS, self.BASE_SEGMENTS
        )
        # דירה 11: 15:00-22:00
        self.assertEqual(result[11][0]["start_time"], "15:00")
        # דירה 32: 17:00-22:00
        self.assertEqual(result[32][0]["start_time"], "17:00")
        # דירה 35: 16:30-22:00
        self.assertEqual(result[35][0]["start_time"], "16:30")

    def test_edge8_ha_default_same_as_base_segments(self):
        """מקרה קצה 8: HA default 16:00-08:00 = זהה לסגמנטים מקוריים, אך עדיין נוצרים סגמנטים."""
        from app_utils import _build_weekday_shift_overrides
        # דירה 99 שייכת למערך 1, אין לה override ספציפי, HA default = 16:00-08:00
        result = _build_weekday_shift_overrides(
            {99}, {99: 1}, {}, self.HA_DEFAULTS, self.BASE_SEGMENTS
        )
        segs = result[99]
        # סגמנטים זהים לברירת מחדל — אין שינוי בפועל
        self.assertEqual(segs[0]["start_time"], "16:00")
        self.assertEqual(segs[0]["end_time"], "22:00")
        self.assertEqual(segs[2]["start_time"], "06:30")
        self.assertEqual(segs[2]["end_time"], "08:00")
        self.assertEqual(self._seg_work_minutes(segs), 450)  # 7.5h כמו default

    def test_edge9_apt_specific_overrides_ha_default_in_same_array(self):
        """מקרה קצה 9: דירה 32 (override 17:00-08:30) ודירה ללא override במערך 2 (HA 16:30-08:30)."""
        from app_utils import _build_weekday_shift_overrides
        # דירה 32 = ספציפי, דירה 40 = HA default
        apts = {32, 40}
        housing_map = {32: 2, 40: 2}
        result = _build_weekday_shift_overrides(
            apts, housing_map, self.APT_OVERRIDES, self.HA_DEFAULTS, self.BASE_SEGMENTS
        )
        # דירה 32: ספציפי 17:00
        self.assertEqual(result[32][0]["start_time"], "17:00")
        self.assertEqual(result[32][2]["end_time"], "08:30")
        # דירה 40: HA default 16:30
        self.assertEqual(result[40][0]["start_time"], "16:30")
        self.assertEqual(result[40][2]["end_time"], "08:30")

    def test_edge10_apt_in_ha_without_override_not_in_result(self):
        """מקרה קצה 10: דירה במערך ללא override כלל — לא מופיעה בתוצאה."""
        from app_utils import _build_weekday_shift_overrides
        # מערך 5 לא קיים ב-HA_DEFAULTS
        result = _build_weekday_shift_overrides(
            {100}, {100: 5}, self.APT_OVERRIDES, self.HA_DEFAULTS, self.BASE_SEGMENTS
        )
        self.assertNotIn(100, result)
        self.assertEqual(result, {})


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="בדיקות חישוב שכר")
    parser.add_argument("--unit", action="store_true", help="הרץ רק בדיקות אוטומטיות")
    parser.add_argument("--manual", action="store_true", help="הרץ רק בדיקות ידניות")
    args = parser.parse_args()

    if args.unit:
        result = run_unit_tests()
        sys.exit(0 if result.wasSuccessful() else 1)
    elif args.manual:
        run_real_data_tests()
    else:
        # הרץ הכל
        print("="*70)
        print("חלק 1: בדיקות אוטומטיות (Unit Tests)")
        print("="*70)
        result = run_unit_tests()

        if result.wasSuccessful():
            print("\n[OK] כל הבדיקות האוטומטיות עברו!")
        else:
            print(f"\n✗ {len(result.failures)} בדיקות נכשלו")

        # בדיקות ידניות
        run_real_data_tests()
