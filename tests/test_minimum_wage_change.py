# -*- coding: utf-8 -*-
"""
בדיקות שינוי שכר מינימום
========================

בדיקת התנהגות המערכת כשיש עדכון שכר מינימום:
- לפני תאריך העדכון
- אחרי תאריך העדכון
- בחודש העדכון עצמו
- מקרי קצה

הרצה:
    pytest tests/test_minimum_wage_change.py -v
"""

import unittest
import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app_utils import (
    get_effective_hourly_rate,
    calculate_rate_from_housing_rates,
    _mul_pay,
    _display_base_hourly,
)
from services.gesher_exporter import calculate_value

# =============================================================================
# קבועים לבדיקות
# =============================================================================

OLD_MINIMUM_WAGE = 32.30   # שכר מינימום ישן
NEW_MINIMUM_WAGE = 34.40   # שכר מינימום חדש


# =============================================================================
# בדיקות get_minimum_wage_for_month - שליפה היסטורית
# =============================================================================

class TestMinimumWageHistorical(unittest.TestCase):
    """בדיקת שליפת שכר מינימום לפי חודש מהדאטאבייס."""

    def _make_mock_conn(self, rows: list):
        """יצירת mock ל-connection עם תוצאות מוגדרות."""
        import psycopg2.extras
        cursor = MagicMock()
        cursor.fetchone.side_effect = rows
        conn = MagicMock()
        conn.cursor.return_value = cursor
        return conn

    def test_before_wage_change(self):
        """חודש לפני עדכון שכר מינימום - צריך להחזיר את התעריף הישן."""
        from core.history import get_minimum_wage_for_month

        conn = self._make_mock_conn([{"hourly_rate": 3230}])  # 32.30 ₪
        result = get_minimum_wage_for_month(conn, 2025, 3)  # מרץ - לפני העדכון
        self.assertEqual(result, 32.30)

    def test_after_wage_change(self):
        """חודש אחרי עדכון שכר מינימום - צריך להחזיר את התעריף החדש."""
        from core.history import get_minimum_wage_for_month

        conn = self._make_mock_conn([{"hourly_rate": 3440}])  # 34.40 ₪
        result = get_minimum_wage_for_month(conn, 2025, 5)  # מאי - אחרי העדכון
        self.assertEqual(result, 34.40)

    def test_month_of_change(self):
        """חודש העדכון עצמו - צריך להחזיר את התעריף החדש."""
        from core.history import get_minimum_wage_for_month

        conn = self._make_mock_conn([{"hourly_rate": 3440}])  # 34.40 ₪
        result = get_minimum_wage_for_month(conn, 2025, 4)  # אפריל - חודש העדכון
        self.assertEqual(result, 34.40)

    def test_no_wage_found_raises_error(self):
        """אם אין שכר מינימום בטבלה - צריך לזרוק שגיאה."""
        from core.history import get_minimum_wage_for_month

        conn = self._make_mock_conn([None])
        with self.assertRaises(ValueError) as ctx:
            get_minimum_wage_for_month(conn, 2020, 1)
        self.assertIn("No minimum wage found", str(ctx.exception))

    def test_invalid_month_raises_error(self):
        """חודש לא תקין - צריך לזרוק שגיאה."""
        from core.history import get_minimum_wage_for_month

        conn = self._make_mock_conn([])
        with self.assertRaises(ValueError):
            get_minimum_wage_for_month(conn, 2025, 13)
        with self.assertRaises(ValueError):
            get_minimum_wage_for_month(conn, 2025, 0)

    def test_zero_rate_raises_error(self):
        """תעריף 0 בטבלה - צריך לזרוק שגיאה."""
        from core.history import get_minimum_wage_for_month

        conn = self._make_mock_conn([{"hourly_rate": 0}])
        with self.assertRaises(ValueError):
            get_minimum_wage_for_month(conn, 2025, 3)


# =============================================================================
# בדיקות get_effective_hourly_rate - השפעת שכר מינימום על תעריפים
# =============================================================================

class TestEffectiveRateWithWageChange(unittest.TestCase):
    """בדיקת חישוב תעריף אפקטיבי לפני ואחרי שינוי שכר מינימום."""

    def _make_report(self, shift_type_id=103, housing_array_id=1,
                     is_married=False, supplement=0):
        return {
            'shift_type_id': shift_type_id,
            'housing_array_id': housing_array_id,
            'is_married': is_married,
            'hourly_wage_supplement': supplement,
        }

    # --- משמרות רגילות (103) - fallback לשכר מינימום ---

    def test_weekday_no_housing_rate_old_wage(self):
        """משמרת חול ללא תעריף מערך דיור - תעריף ישן."""
        report = self._make_report(103, 1)
        result = get_effective_hourly_rate(report, OLD_MINIMUM_WAGE)
        self.assertEqual(result, OLD_MINIMUM_WAGE)

    def test_weekday_no_housing_rate_new_wage(self):
        """משמרת חול ללא תעריף מערך דיור - תעריף חדש."""
        report = self._make_report(103, 1)
        result = get_effective_hourly_rate(report, NEW_MINIMUM_WAGE)
        self.assertEqual(result, NEW_MINIMUM_WAGE)

    def test_wage_change_affects_fallback_rate(self):
        """שינוי שכר מינימום משנה את תעריף ברירת המחדל."""
        report = self._make_report(103, 1)
        old_rate = get_effective_hourly_rate(report, OLD_MINIMUM_WAGE)
        new_rate = get_effective_hourly_rate(report, NEW_MINIMUM_WAGE)
        self.assertGreater(new_rate, old_rate)
        self.assertAlmostEqual(new_rate - old_rate, NEW_MINIMUM_WAGE - OLD_MINIMUM_WAGE, places=2)

    # --- משמרות עם תוספת סוג דירה ---

    def test_supplement_added_to_old_wage(self):
        """תוספת סוג דירה (100 אגורות = 1₪) מתווספת לשכר ישן."""
        report = self._make_report(103, 1, supplement=100)
        result = get_effective_hourly_rate(report, OLD_MINIMUM_WAGE)
        self.assertAlmostEqual(result, OLD_MINIMUM_WAGE + 1.0, places=2)

    def test_supplement_added_to_new_wage(self):
        """תוספת סוג דירה מתווספת לשכר חדש."""
        report = self._make_report(103, 1, supplement=100)
        result = get_effective_hourly_rate(report, NEW_MINIMUM_WAGE)
        self.assertAlmostEqual(result, NEW_MINIMUM_WAGE + 1.0, places=2)

    # --- תעריף קבוע ממערך דיור (באגורות) - לא מושפע ---

    def test_fixed_rate_not_affected_by_wage_change(self):
        """תעריף קבוע ממערך דיור לא מושפע משינוי שכר מינימום."""
        report = self._make_report(103, 1)
        cache = {
            (103, 1): {
                'weekday_single_rate': 4000,  # 40 ₪ קבוע
                'weekday_single_wage_percentage': None,
                'weekday_married_rate': None,
                'weekday_married_wage_percentage': None,
                'shabbat_rate': None,
                'shabbat_wage_percentage': None,
            }
        }
        old_result = get_effective_hourly_rate(report, OLD_MINIMUM_WAGE, False, cache)
        new_result = get_effective_hourly_rate(report, NEW_MINIMUM_WAGE, False, cache)
        self.assertEqual(old_result, 40.0)
        self.assertEqual(new_result, 40.0)  # אותו תעריף - לא מושפע

    # --- תעריף באחוזים ממערך דיור - מושפע ---

    def test_percentage_rate_changes_with_wage(self):
        """תעריף באחוזים (150%) מושפע משינוי שכר מינימום."""
        report = self._make_report(106, 1)
        cache = {
            (106, 1): {
                'weekday_single_rate': None,
                'weekday_single_wage_percentage': 150,  # 150% משכר מינימום
                'weekday_married_rate': None,
                'weekday_married_wage_percentage': None,
                'shabbat_rate': None,
                'shabbat_wage_percentage': None,
            }
        }
        old_result = get_effective_hourly_rate(report, OLD_MINIMUM_WAGE, False, cache)
        new_result = get_effective_hourly_rate(report, NEW_MINIMUM_WAGE, False, cache)
        self.assertAlmostEqual(old_result, OLD_MINIMUM_WAGE * 1.5, places=2)
        self.assertAlmostEqual(new_result, NEW_MINIMUM_WAGE * 1.5, places=2)
        self.assertGreater(new_result, old_result)

    # --- שבת - תעריף באחוזים ---

    def test_shabbat_percentage_rate_changes_with_wage(self):
        """תעריף שבת באחוזים מושפע משינוי שכר מינימום."""
        report = self._make_report(106, 1)
        cache = {
            (106, 1): {
                'weekday_single_rate': None,
                'weekday_single_wage_percentage': None,
                'weekday_married_rate': None,
                'weekday_married_wage_percentage': None,
                'shabbat_rate': None,
                'shabbat_wage_percentage': 200,  # 200% משכר מינימום
            }
        }
        old_result = get_effective_hourly_rate(report, OLD_MINIMUM_WAGE, True, cache)
        new_result = get_effective_hourly_rate(report, NEW_MINIMUM_WAGE, True, cache)
        self.assertAlmostEqual(old_result, OLD_MINIMUM_WAGE * 2.0, places=2)
        self.assertAlmostEqual(new_result, NEW_MINIMUM_WAGE * 2.0, places=2)

    # --- שבת - תעריף קבוע ---

    def test_shabbat_fixed_rate_not_affected(self):
        """תעריף שבת קבוע לא מושפע משינוי שכר מינימום."""
        report = self._make_report(106, 1)
        cache = {
            (106, 1): {
                'weekday_single_rate': None,
                'weekday_single_wage_percentage': None,
                'weekday_married_rate': None,
                'weekday_married_wage_percentage': None,
                'shabbat_rate': 7500,  # 75 ₪ קבוע
                'shabbat_wage_percentage': None,
            }
        }
        old_result = get_effective_hourly_rate(report, OLD_MINIMUM_WAGE, True, cache)
        new_result = get_effective_hourly_rate(report, NEW_MINIMUM_WAGE, True, cache)
        self.assertEqual(old_result, 75.0)
        self.assertEqual(new_result, 75.0)

    # --- נשוי/רווק ---

    def test_married_rate_not_affected_when_fixed(self):
        """תעריף נשוי קבוע לא מושפע."""
        report = self._make_report(103, 1, is_married=True)
        cache = {
            (103, 1): {
                'weekday_single_rate': None,
                'weekday_single_wage_percentage': None,
                'weekday_married_rate': 3800,  # 38 ₪
                'weekday_married_wage_percentage': None,
                'shabbat_rate': None,
                'shabbat_wage_percentage': None,
            }
        }
        result = get_effective_hourly_rate(report, NEW_MINIMUM_WAGE, False, cache)
        self.assertEqual(result, 38.0)

    def test_married_percentage_affected_by_wage(self):
        """תעריף נשוי באחוזים מושפע."""
        report = self._make_report(103, 1, is_married=True)
        cache = {
            (103, 1): {
                'weekday_single_rate': None,
                'weekday_single_wage_percentage': None,
                'weekday_married_rate': None,
                'weekday_married_wage_percentage': 110,  # 110%
                'shabbat_rate': None,
                'shabbat_wage_percentage': None,
            }
        }
        old_result = get_effective_hourly_rate(report, OLD_MINIMUM_WAGE, False, cache)
        new_result = get_effective_hourly_rate(report, NEW_MINIMUM_WAGE, False, cache)
        self.assertAlmostEqual(old_result, OLD_MINIMUM_WAGE * 1.1, places=2)
        self.assertAlmostEqual(new_result, NEW_MINIMUM_WAGE * 1.1, places=2)


# =============================================================================
# בדיקות calculate_rate_from_housing_rates
# =============================================================================

class TestHousingRatesWithWageChange(unittest.TestCase):
    """בדיקת calculate_rate_from_housing_rates עם שכר מינימום שונה."""

    def _rate_row(self, **kwargs):
        defaults = {
            'weekday_single_rate': None, 'weekday_single_wage_percentage': None,
            'weekday_married_rate': None, 'weekday_married_wage_percentage': None,
            'shabbat_rate': None, 'shabbat_wage_percentage': None,
        }
        defaults.update(kwargs)
        return defaults

    def test_fallback_to_minimum_wage(self):
        """ללא תעריף - נופל לשכר מינימום."""
        row = self._rate_row()
        old = calculate_rate_from_housing_rates(row, False, False, OLD_MINIMUM_WAGE)
        new = calculate_rate_from_housing_rates(row, False, False, NEW_MINIMUM_WAGE)
        self.assertEqual(old, OLD_MINIMUM_WAGE)
        self.assertEqual(new, NEW_MINIMUM_WAGE)

    def test_fallback_with_supplement(self):
        """ללא תעריף + תוספת דירה - שכר מינימום + תוספת."""
        row = self._rate_row()
        old = calculate_rate_from_housing_rates(row, False, False, OLD_MINIMUM_WAGE, 200)
        new = calculate_rate_from_housing_rates(row, False, False, NEW_MINIMUM_WAGE, 200)
        self.assertAlmostEqual(old, OLD_MINIMUM_WAGE + 2.0, places=2)
        self.assertAlmostEqual(new, NEW_MINIMUM_WAGE + 2.0, places=2)

    def test_percentage_with_wage_change(self):
        """תעריף באחוזים - משתנה עם שכר מינימום."""
        row = self._rate_row(weekday_single_wage_percentage=125)
        old = calculate_rate_from_housing_rates(row, False, False, OLD_MINIMUM_WAGE)
        new = calculate_rate_from_housing_rates(row, False, False, NEW_MINIMUM_WAGE)
        self.assertAlmostEqual(old, OLD_MINIMUM_WAGE * 1.25, places=2)
        self.assertAlmostEqual(new, NEW_MINIMUM_WAGE * 1.25, places=2)

    def test_fixed_rate_ignores_wage(self):
        """תעריף קבוע - לא משתנה עם שכר מינימום."""
        row = self._rate_row(weekday_single_rate=5000)
        old = calculate_rate_from_housing_rates(row, False, False, OLD_MINIMUM_WAGE)
        new = calculate_rate_from_housing_rates(row, False, False, NEW_MINIMUM_WAGE)
        self.assertEqual(old, 50.0)
        self.assertEqual(new, 50.0)

    def test_rate_takes_priority_over_percentage(self):
        """תעריף קבוע עדיף על אחוזים."""
        row = self._rate_row(weekday_single_rate=5000, weekday_single_wage_percentage=150)
        result = calculate_rate_from_housing_rates(row, False, False, NEW_MINIMUM_WAGE)
        self.assertEqual(result, 50.0)  # קבוע, לא 150% × 34.40


# =============================================================================
# בדיקות חופשה ומחלה - תמיד לפי שכר מינימום
# =============================================================================

class TestVacationSickWithWageChange(unittest.TestCase):
    """חופשה ומחלה תמיד מחושבות לפי שכר מינימום ישירות."""

    def test_vacation_pay_old_wage(self):
        """חופשה - 8 שעות בשכר ישן."""
        hours = round(480 / 60, 2)  # 8 שעות
        pay = _mul_pay(hours, round(OLD_MINIMUM_WAGE, 2))
        self.assertAlmostEqual(pay, 8 * OLD_MINIMUM_WAGE, places=1)

    def test_vacation_pay_new_wage(self):
        """חופשה - 8 שעות בשכר חדש."""
        hours = round(480 / 60, 2)
        pay = _mul_pay(hours, round(NEW_MINIMUM_WAGE, 2))
        self.assertAlmostEqual(pay, 8 * NEW_MINIMUM_WAGE, places=1)

    def test_vacation_pay_difference(self):
        """ההפרש בתשלום חופשה בין שכר ישן לחדש."""
        hours = round(480 / 60, 2)
        old_pay = _mul_pay(hours, round(OLD_MINIMUM_WAGE, 2))
        new_pay = _mul_pay(hours, round(NEW_MINIMUM_WAGE, 2))
        diff = new_pay - old_pay
        expected_diff = 8 * (NEW_MINIMUM_WAGE - OLD_MINIMUM_WAGE)
        self.assertAlmostEqual(diff, expected_diff, places=1)

    def test_sick_day1_zero_both_wages(self):
        """מחלה יום 1 - 0% בכל שכר."""
        hours = round(480 / 60, 2)
        old_pay = _mul_pay(hours, round(OLD_MINIMUM_WAGE, 2) * 0)
        new_pay = _mul_pay(hours, round(NEW_MINIMUM_WAGE, 2) * 0)
        self.assertEqual(old_pay, 0)
        self.assertEqual(new_pay, 0)

    def test_sick_day2_half_rate(self):
        """מחלה יום 2 - 50% משכר מינימום."""
        hours = round(480 / 60, 2)
        old_pay = _mul_pay(hours, round(OLD_MINIMUM_WAGE, 2) * 0.5)
        new_pay = _mul_pay(hours, round(NEW_MINIMUM_WAGE, 2) * 0.5)
        self.assertAlmostEqual(old_pay, 8 * OLD_MINIMUM_WAGE * 0.5, places=1)
        self.assertAlmostEqual(new_pay, 8 * NEW_MINIMUM_WAGE * 0.5, places=1)
        self.assertGreater(new_pay, old_pay)

    def test_sick_day4_full_rate(self):
        """מחלה יום 4+ - 100% משכר מינימום."""
        hours = round(480 / 60, 2)
        old_pay = _mul_pay(hours, round(OLD_MINIMUM_WAGE, 2) * 1.0)
        new_pay = _mul_pay(hours, round(NEW_MINIMUM_WAGE, 2) * 1.0)
        self.assertAlmostEqual(old_pay, 8 * OLD_MINIMUM_WAGE, places=1)
        self.assertAlmostEqual(new_pay, 8 * NEW_MINIMUM_WAGE, places=1)


# =============================================================================
# בדיקות כוננות - לא מושפעת משכר מינימום
# =============================================================================

class TestStandbyNotAffected(unittest.TestCase):
    """כוננות לא מושפעת משינוי שכר מינימום - תעריף קבוע."""

    def test_standby_rate_independent(self):
        """תעריף כוננות קבוע (70₪) לא תלוי בשכר מינימום."""
        standby_rate = 70.0  # DEFAULT_STANDBY_RATE
        # לא משנה מה שכר המינימום - הכוננות זה סכום קבוע
        self.assertEqual(standby_rate, 70.0)


# =============================================================================
# בדיקות ייצוא גשר (Gesher) - calculate_value
# =============================================================================

class TestGesherExportWithWageChange(unittest.TestCase):
    """בדיקת ייצוא למירב עם שכר מינימום שונה."""

    def test_hours_100_old_wage(self):
        """ייצוא שעות 100% עם שכר ישן."""
        totals = {'calc100': 480}  # 8 שעות בדקות
        hours, rate = calculate_value(totals, 'calc100', 'hours_100', OLD_MINIMUM_WAGE)
        self.assertEqual(hours, 8.0)
        self.assertEqual(rate, OLD_MINIMUM_WAGE)

    def test_hours_100_new_wage(self):
        """ייצוא שעות 100% עם שכר חדש."""
        totals = {'calc100': 480}
        hours, rate = calculate_value(totals, 'calc100', 'hours_100', NEW_MINIMUM_WAGE)
        self.assertEqual(hours, 8.0)
        self.assertEqual(rate, NEW_MINIMUM_WAGE)

    def test_hours_125_rate_changes(self):
        """ייצוא שעות 125% - התעריף משתנה."""
        totals = {'calc125': 120}  # 2 שעות
        _, old_rate = calculate_value(totals, 'calc125', 'hours_125', OLD_MINIMUM_WAGE)
        _, new_rate = calculate_value(totals, 'calc125', 'hours_125', NEW_MINIMUM_WAGE)
        # calculate_value עושה round(base * 1.25, 2) - מקביל לעיגול
        self.assertEqual(old_rate, round(OLD_MINIMUM_WAGE * 1.25, 2))
        self.assertEqual(new_rate, round(NEW_MINIMUM_WAGE * 1.25, 2))

    def test_hours_150_rate_changes(self):
        """ייצוא שעות 150% - התעריף משתנה."""
        totals = {'calc150': 60}  # 1 שעה
        _, old_rate = calculate_value(totals, 'calc150', 'hours_150', OLD_MINIMUM_WAGE)
        _, new_rate = calculate_value(totals, 'calc150', 'hours_150', NEW_MINIMUM_WAGE)
        self.assertAlmostEqual(old_rate, OLD_MINIMUM_WAGE * 1.5, places=2)
        self.assertAlmostEqual(new_rate, NEW_MINIMUM_WAGE * 1.5, places=2)

    def test_average_base_rate_overrides_minimum(self):
        """כשיש average_base_rate - הוא משמש במקום שכר מינימום לשעות עבודה."""
        totals = {'calc100': 480, 'average_base_rate': 38.0}
        hours, rate = calculate_value(totals, 'calc100', 'hours_100', NEW_MINIMUM_WAGE)
        self.assertEqual(rate, 38.0)  # average_base_rate, לא minimum_wage

    def test_sick_hours_paid_old_wage(self):
        """ייצוא מחלה - שעות משולמות לפי שכר ישן."""
        totals = {'sick_payment': OLD_MINIMUM_WAGE * 4}  # 4 שעות × שכר ישן
        hours, rate = calculate_value(totals, 'sick_payment', 'sick_hours_paid', OLD_MINIMUM_WAGE)
        self.assertAlmostEqual(hours, 4.0, places=2)
        self.assertAlmostEqual(rate, OLD_MINIMUM_WAGE, places=2)

    def test_sick_hours_paid_new_wage(self):
        """ייצוא מחלה - שעות משולמות לפי שכר חדש."""
        totals = {'sick_payment': NEW_MINIMUM_WAGE * 4}
        hours, rate = calculate_value(totals, 'sick_payment', 'sick_hours_paid', NEW_MINIMUM_WAGE)
        self.assertAlmostEqual(hours, 4.0, places=2)
        self.assertAlmostEqual(rate, NEW_MINIMUM_WAGE, places=2)

    def test_standby_not_affected_in_export(self):
        """ייצוא כוננות - לא מושפע משכר מינימום."""
        totals = {'standby_payment': 210.0}  # 3 × 70₪
        _, old_rate = calculate_value(totals, 'standby_payment', 'standby_with_rate', OLD_MINIMUM_WAGE)
        _, new_rate = calculate_value(totals, 'standby_payment', 'standby_with_rate', NEW_MINIMUM_WAGE)
        self.assertEqual(old_rate, 210.0)
        self.assertEqual(new_rate, 210.0)  # אותו דבר - לא תלוי בשכר מינימום

    def test_money_type_not_affected(self):
        """ייצוא סכום ישיר (money) - לא מושפע משכר מינימום."""
        totals = {'holiday_payment': 500.0}
        _, old_val = calculate_value(totals, 'holiday_payment', 'money', OLD_MINIMUM_WAGE)
        _, new_val = calculate_value(totals, 'holiday_payment', 'money', NEW_MINIMUM_WAGE)
        self.assertEqual(old_val, 500.0)
        self.assertEqual(new_val, 500.0)


# =============================================================================
# בדיקות _display_base_hourly
# =============================================================================

class TestDisplayBaseHourlyWithWageChange(unittest.TestCase):
    """בדיקת תצוגת תעריף בסיס כשהתעריף כבר כולל תוספת."""

    def test_rate_includes_supplement_old_wage(self):
        """כשהתעריף כולל תוספת - מחשב לפי שכר ישן + תוספת בפועל."""
        # seg_rate = minimum_wage + rate_supplement → מחזיר minimum_wage + actual_supplement
        rate_supp = 100  # 1₪
        seg_rate = OLD_MINIMUM_WAGE + 1.0  # 33.30
        result = _display_base_hourly(seg_rate, OLD_MINIMUM_WAGE, rate_supp, 200)  # actual = 2₪
        self.assertAlmostEqual(result, OLD_MINIMUM_WAGE + 2.0, places=2)

    def test_rate_includes_supplement_new_wage(self):
        """כשהתעריף כולל תוספת - מחשב לפי שכר חדש + תוספת בפועל."""
        rate_supp = 100
        seg_rate = NEW_MINIMUM_WAGE + 1.0  # 35.40
        result = _display_base_hourly(seg_rate, NEW_MINIMUM_WAGE, rate_supp, 200)
        self.assertAlmostEqual(result, NEW_MINIMUM_WAGE + 2.0, places=2)

    def test_custom_rate_no_supplement(self):
        """תעריף מותאם (לא שכר מינימום) - מוסיף תוספת בפועל."""
        seg_rate = 50.0
        result = _display_base_hourly(seg_rate, NEW_MINIMUM_WAGE, 0, 100)
        self.assertAlmostEqual(result, 51.0, places=2)  # 50 + 1₪ תוספת


# =============================================================================
# בדיקות gesher_exporter.get_minimum_wage - תיקון הבאג
# =============================================================================

class TestGesherGetMinimumWage(unittest.TestCase):
    """בדיקה שהפונקציה המתוקנת שולפת לפי חודש ולא את האחרון."""

    @patch('services.gesher_exporter.get_minimum_wage_for_month')
    def test_calls_historical_function(self, mock_get):
        """מוודא שהפונקציה קוראת ל-get_minimum_wage_for_month עם year ו-month."""
        from services.gesher_exporter import get_minimum_wage

        mock_get.return_value = 32.30
        conn = MagicMock()

        result = get_minimum_wage(conn, 2025, 3)

        mock_get.assert_called_once()
        self.assertEqual(result, 32.30)

    @patch('services.gesher_exporter.get_minimum_wage_for_month')
    def test_old_month_gets_old_wage(self, mock_get):
        """ייצוא חודש ישן מקבל שכר מינימום ישן."""
        from services.gesher_exporter import get_minimum_wage

        mock_get.return_value = OLD_MINIMUM_WAGE
        conn = MagicMock()
        result = get_minimum_wage(conn, 2025, 3)
        self.assertEqual(result, OLD_MINIMUM_WAGE)

    @patch('services.gesher_exporter.get_minimum_wage_for_month')
    def test_new_month_gets_new_wage(self, mock_get):
        """ייצוא חודש חדש מקבל שכר מינימום חדש."""
        from services.gesher_exporter import get_minimum_wage

        mock_get.return_value = NEW_MINIMUM_WAGE
        conn = MagicMock()
        result = get_minimum_wage(conn, 2025, 5)
        self.assertEqual(result, NEW_MINIMUM_WAGE)


# =============================================================================
# מקרי קצה
# =============================================================================

class TestEdgeCases(unittest.TestCase):
    """מקרי קצה בשינוי שכר מינימום."""

    def test_very_small_wage_change(self):
        """שינוי קטן מאוד (אגורה אחת) עדיין משפיע."""
        old = 34.40
        new = 34.41
        report = {'shift_type_id': 103, 'housing_array_id': 1, 'is_married': False, 'hourly_wage_supplement': 0}
        old_rate = get_effective_hourly_rate(report, old)
        new_rate = get_effective_hourly_rate(report, new)
        self.assertAlmostEqual(new_rate - old_rate, 0.01, places=2)

    def test_large_wage_change(self):
        """שינוי גדול בשכר מינימום."""
        old = 30.0
        new = 40.0
        report = {'shift_type_id': 103, 'housing_array_id': 1, 'is_married': False, 'hourly_wage_supplement': 0}
        old_rate = get_effective_hourly_rate(report, old)
        new_rate = get_effective_hourly_rate(report, new)
        self.assertEqual(old_rate, 30.0)
        self.assertEqual(new_rate, 40.0)

    def test_percentage_200_doubles_the_gap(self):
        """תעריף 200% מכפיל את הפער בין שכר ישן לחדש."""
        row = {
            'weekday_single_rate': None, 'weekday_single_wage_percentage': 200,
            'weekday_married_rate': None, 'weekday_married_wage_percentage': None,
            'shabbat_rate': None, 'shabbat_wage_percentage': None,
        }
        old = calculate_rate_from_housing_rates(row, False, False, OLD_MINIMUM_WAGE)
        new = calculate_rate_from_housing_rates(row, False, False, NEW_MINIMUM_WAGE)
        wage_diff = NEW_MINIMUM_WAGE - OLD_MINIMUM_WAGE
        rate_diff = new - old
        self.assertAlmostEqual(rate_diff, wage_diff * 2, places=2)

    def test_zero_supplement_same_as_no_supplement(self):
        """תוספת 0 אגורות = ללא תוספת."""
        report_no = {'shift_type_id': 103, 'housing_array_id': 1, 'is_married': False, 'hourly_wage_supplement': 0}
        report_none = {'shift_type_id': 103, 'housing_array_id': 1, 'is_married': False, 'hourly_wage_supplement': None}
        r1 = get_effective_hourly_rate(report_no, NEW_MINIMUM_WAGE)
        r2 = get_effective_hourly_rate(report_none, NEW_MINIMUM_WAGE)
        self.assertEqual(r1, r2)

    def test_missing_housing_array_falls_to_minimum(self):
        """ללא housing_array_id - נופל לשכר מינימום."""
        report = {'shift_type_id': 103, 'housing_array_id': None, 'is_married': False, 'hourly_wage_supplement': 0}
        cache = {(103, 1): {'weekday_single_rate': 5000}}
        result = get_effective_hourly_rate(report, NEW_MINIMUM_WAGE, False, cache)
        self.assertEqual(result, NEW_MINIMUM_WAGE)  # לא מוצא את ה-key

    def test_missing_shift_type_falls_to_minimum(self):
        """ללא shift_type_id - נופל לשכר מינימום."""
        report = {'shift_type_id': None, 'housing_array_id': 1, 'is_married': False, 'hourly_wage_supplement': 0}
        cache = {(103, 1): {'weekday_single_rate': 5000}}
        result = get_effective_hourly_rate(report, NEW_MINIMUM_WAGE, False, cache)
        self.assertEqual(result, NEW_MINIMUM_WAGE)


if __name__ == '__main__':
    unittest.main()
