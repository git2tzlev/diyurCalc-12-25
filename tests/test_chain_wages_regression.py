"""
בדיקות רגרסיה ל-_calculate_chain_wages — שבת, חג, ופורים.

מוודא שאחרי ה-refactor (premium_windows) התוצאות זהות בדיוק
ללוגיקה הישנה הקשיחה.
"""
import unittest
from datetime import date

from app_utils import _calculate_chain_wages
from core.premium_windows import PremiumWindow


# =============================================================================
# Helpers
# =============================================================================

def _make_purim_window(purim_date: date) -> PremiumWindow:
    """יצירת חלון פורים 08:00-22:00 ליום אחד."""
    return PremiumWindow(
        start_date=purim_date,
        start_min=480,   # 08:00
        end_date=purim_date,
        end_min=1320,    # 22:00
        rate_pct=150,
        origin="purim",
        standby_mode="shabbat",
        source_id=1,
    )


def _make_independence_window(erev_date: date) -> PremiumWindow:
    """חלון יום העצמאות 20:00 ערב → 20:00 למחרת (24 שעות חוצה חצות)."""
    yom_date = date.fromordinal(erev_date.toordinal() + 1)
    return PremiumWindow(
        start_date=erev_date,
        start_min=1200,  # 20:00
        end_date=yom_date,
        end_min=1200,    # 20:00
        rate_pct=150,
        origin="independence",
        standby_mode="none",
        source_id=2,
    )


def _make_elections_window(elections_date: date) -> PremiumWindow:
    """חלון בחירות 07:00-22:00 ביום אחד, 200%."""
    return PremiumWindow(
        start_date=elections_date,
        start_min=420,   # 07:00
        end_date=elections_date,
        end_min=1320,    # 22:00
        rate_pct=200,
        origin="elections",
        standby_mode="none",
        source_id=3,
    )


def _shabbat_cache_for(friday: date, enter_hh_mm: str = "17:30",
                       exit_hh_mm: str = "19:00") -> dict:
    """
    יצירת shabbat_cache מינימלי ליום שישי+שבת.
    הרשומה ב-cache היא תחת מפתח שבת (saturday_str).
    """
    saturday = date.fromordinal(friday.toordinal() + 1)
    saturday_str = saturday.strftime("%Y-%m-%d")
    return {
        saturday_str: {
            "enter": enter_hh_mm,
            "exit": exit_hh_mm,
        }
    }


def _holiday_cache(erev: date, holiday_date: date, enter: str = "17:30",
                   exit_: str = "19:00", holiday_name: str = "חג") -> dict:
    """יצירת shabbat_cache לחג."""
    holiday_str = holiday_date.strftime("%Y-%m-%d")
    erev_str = erev.strftime("%Y-%m-%d")
    cache = {
        holiday_str: {
            "enter": enter,
            "exit": exit_,
            "holiday": holiday_name,
        }
    }
    if erev != holiday_date:
        cache[erev_str] = {"enter": enter}
    return cache


WEEKDAY = date(2026, 3, 10)  # Tuesday, no shabbat/holiday
PURIM_2026 = date(2026, 3, 3)  # Tuesday
FRIDAY_2026 = date(2026, 3, 6)
SATURDAY_2026 = date(2026, 3, 7)


# =============================================================================
# Test: Shabbat — unchanged by refactor
# =============================================================================

class TestShabbatUnchanged(unittest.TestCase):
    """
    שבת מטופלת ע"י _get_shabbat_boundaries + shabbat_cache.
    premium_windows לא צריכים לכלול שבת (מסוננים ב-app_utils).
    הבדיקות מוודאות שהתוצאות זהות ללא premium_windows.
    """

    def setUp(self):
        self.cache = _shabbat_cache_for(FRIDAY_2026, "17:30", "19:00")

    def test_shabbat_full_day_150(self):
        """משמרת 09:00-15:00 בשבת — 6 שעות 150%."""
        result = _calculate_chain_wages(
            [(540, 900, 106, SATURDAY_2026)],
            self.cache, 0, False, premium_windows=[],
        )
        self.assertEqual(result["calc150"], 360)
        self.assertEqual(result["calc150_shabbat"], 360)
        self.assertEqual(result["calc150_shabbat_100"], 360)
        self.assertEqual(result["calc150_shabbat_50"], 360)
        self.assertEqual(result["calc100"], 0)

    def test_shabbat_overtime_175(self):
        """משמרת 10 שעות בשבת — 8ש' 150% + 2ש' 175%."""
        result = _calculate_chain_wages(
            [(480, 1080, 106, SATURDAY_2026)],
            self.cache, 0, False, premium_windows=[],
        )
        self.assertEqual(result["calc150"], 480)
        self.assertEqual(result["calc175"], 120)

    def test_friday_crossing_candle_lighting(self):
        """משמרת 15:00-20:00 ביום שישי, כניסת שבת 17:30.
        150 דקות חול (100%) + 150 דקות שבת (150%).
        """
        result = _calculate_chain_wages(
            [(900, 1200, 103, FRIDAY_2026)],
            self.cache, 0, False, premium_windows=[],
        )
        self.assertEqual(result["calc100"], 150)
        self.assertEqual(result["calc150"], 150)
        self.assertEqual(result["calc150_shabbat"], 150)

    def test_saturday_crossing_havdalah(self):
        """משמרת 18:00-21:00 בשבת, הבדלה 19:00.
        שבת: 18:00-19:00 = 60 דקות 150%.
        חול: 19:00-21:00 = 120 דקות 100%.

        Note: exit = 19:00 ב-shabbat_cache → exit_minutes = 19:00 + 1440 = 2580 (ביחס לחצות ערב שישי).
        current_abs_minute = 1080 (18:00 בשבת) → abs_from_fri = 1080+1440 = 2520. 2520 < 2580 → שבת.
        current_abs_minute = 1140 (19:00 בשבת) → abs_from_fri = 1140+1440 = 2580. 2580 >= 2580 → אחרי שבת.
        """
        result = _calculate_chain_wages(
            [(1080, 1260, 106, SATURDAY_2026)],
            self.cache, 0, False, premium_windows=[],
        )
        self.assertEqual(result["calc150"], 60)
        self.assertEqual(result["calc150_shabbat"], 60)
        self.assertEqual(result["calc100"], 120)

    def test_shabbat_without_premium_windows(self):
        """premium_windows=None — שבת עדיין עובדת."""
        result = _calculate_chain_wages(
            [(540, 900, 106, SATURDAY_2026)],
            self.cache, 0, False, premium_windows=None,
        )
        self.assertEqual(result["calc150"], 360)
        self.assertEqual(result["calc150_shabbat"], 360)

    def test_shabbat_no_double_counting(self):
        """אם premium_windows ריק, calc150_shabbat נספר פעם אחת בלבד."""
        result = _calculate_chain_wages(
            [(540, 900, 106, SATURDAY_2026)],
            self.cache, 0, False, premium_windows=[],
        )
        # calc150_shabbat חייב להיות = calc150 (כולו שבת), לא כפול
        self.assertEqual(result["calc150_shabbat"], result["calc150"])

    def test_weekday_no_shabbat(self):
        """יום חול רגיל — 100% הכל."""
        result = _calculate_chain_wages(
            [(540, 900, 103, WEEKDAY)],
            {}, 0, False, premium_windows=[],
        )
        self.assertEqual(result["calc100"], 360)
        self.assertEqual(result["calc150"], 0)
        self.assertEqual(result["calc150_shabbat"], 0)

    def test_shabbat_overtime_calc150_split(self):
        """שעות נוספות בשבת: calc150_overtime לא נספרת (הכל שבת).
        calc150_shabbat = 480, calc150_overtime = 0."""
        result = _calculate_chain_wages(
            [(480, 1080, 106, SATURDAY_2026)],
            self.cache, 0, False, premium_windows=[],
        )
        self.assertEqual(result["calc150_shabbat"], 480)
        self.assertEqual(result["calc150_overtime"], 0)


# =============================================================================
# Test: Holiday — unchanged by refactor
# =============================================================================

class TestHolidayUnchanged(unittest.TestCase):
    """חגים — אותו מנגנון כמו שבת, דרך shabbat_cache."""

    def test_holiday_midweek_full_150(self):
        """חג באמצע השבוע (למשל סוכות ביום שלישי)."""
        erev = date(2026, 10, 5)   # Monday
        holiday = date(2026, 10, 6)  # Tuesday
        cache = _holiday_cache(erev, holiday, "17:30", "19:00", "סוכות")

        # משמרת 10:00-16:00 ביום החג — כולו בשבת/חג
        result = _calculate_chain_wages(
            [(600, 960, 103, holiday)],
            cache, 0, False, premium_windows=[],
        )
        self.assertEqual(result["calc150"], 360)
        self.assertEqual(result["calc150_shabbat"], 360)
        self.assertEqual(result["calc100"], 0)

    def test_erev_chag_crossing_candle_lighting(self):
        """ערב חג — משמרת חוצה כניסת חג."""
        erev = date(2026, 10, 5)
        holiday = date(2026, 10, 6)
        cache = _holiday_cache(erev, holiday, "17:30", "19:00", "סוכות")

        # 16:00-19:00 בערב חג, כניסה 17:30
        # חול: 16:00-17:30 = 90 min, חג: 17:30-19:00 = 90 min
        result = _calculate_chain_wages(
            [(960, 1140, 103, erev)],
            cache, 0, False, premium_windows=[],
        )
        self.assertEqual(result["calc100"], 90)
        self.assertEqual(result["calc150"], 90)
        self.assertEqual(result["calc150_shabbat"], 90)


# =============================================================================
# Test: Purim via PremiumWindow
# =============================================================================

class TestPurimViaPremiumWindow(unittest.TestCase):
    """
    פורים מטופל כעת דרך PremiumWindow.
    מוודא שהתוצאות זהות ללוגיקה הישנה:
    - 150% מ-08:00 עד 22:00
    - tier stacking כמו שבת (150%/175%/200%)
    - פיצול פנסיה (calc150_shabbat_100 + calc150_shabbat_50)
    """

    def setUp(self):
        self.purim_windows = [_make_purim_window(PURIM_2026)]

    def test_purim_full_in_range(self):
        """09:00-15:00 בפורים — 360 דקות 150%."""
        result = _calculate_chain_wages(
            [(540, 900, 103, PURIM_2026)],
            {}, 0, False, premium_windows=self.purim_windows,
        )
        self.assertEqual(result["calc150"], 360)
        self.assertEqual(result["calc150_shabbat"], 360)
        self.assertEqual(result["calc150_shabbat_100"], 360)
        self.assertEqual(result["calc150_shabbat_50"], 360)
        self.assertEqual(result["calc100"], 0)
        self.assertEqual(result["calc150_purim"], 360)

    def test_purim_crossing_start(self):
        """07:00-12:00 — 60 דקות 100% + 240 דקות 150% פורים."""
        result = _calculate_chain_wages(
            [(420, 720, 103, PURIM_2026)],
            {}, 0, False, premium_windows=self.purim_windows,
        )
        self.assertEqual(result["calc100"], 60)
        self.assertEqual(result["calc150"], 240)
        self.assertEqual(result["calc150_purim"], 240)

    def test_purim_crossing_end(self):
        """20:00-23:00 — 120 דקות 150% + 60 דקות 100%."""
        result = _calculate_chain_wages(
            [(1200, 1380, 103, PURIM_2026)],
            {}, 0, False, premium_windows=self.purim_windows,
        )
        self.assertEqual(result["calc150"], 120)
        self.assertEqual(result["calc100"], 60)
        self.assertEqual(result["calc150_purim"], 120)

    def test_purim_overtime_175(self):
        """08:00-18:00 בפורים — 8ש' ב-150% + 2ש' ב-175%."""
        result = _calculate_chain_wages(
            [(480, 1080, 103, PURIM_2026)],
            {}, 0, False, premium_windows=self.purim_windows,
        )
        self.assertEqual(result["calc150"], 480)
        self.assertEqual(result["calc175"], 120)
        self.assertEqual(result["calc150_purim"], 480)
        self.assertEqual(result["calc175_purim"], 120)

    def test_purim_heavy_overtime_200(self):
        """08:00-20:00 בפורים — 8ש' 150% + 2ש' 175% + 2ש' 200%."""
        result = _calculate_chain_wages(
            [(480, 1200, 103, PURIM_2026)],
            {}, 0, False, premium_windows=self.purim_windows,
        )
        self.assertEqual(result["calc150"], 480)
        self.assertEqual(result["calc175"], 120)
        self.assertEqual(result["calc200"], 120)
        self.assertEqual(result["calc200_purim"], 120)

    def test_purim_not_purim_day(self):
        """יום שאינו פורים — אין premium."""
        result = _calculate_chain_wages(
            [(540, 900, 103, WEEKDAY)],
            {}, 0, False, premium_windows=self.purim_windows,
        )
        self.assertEqual(result["calc100"], 360)
        self.assertEqual(result["calc150"], 0)
        self.assertEqual(result["calc150_purim"], 0)

    def test_purim_before_0800(self):
        """05:00-07:00 בפורים — לפני חלון הפורים, 100%."""
        result = _calculate_chain_wages(
            [(300, 420, 103, PURIM_2026)],
            {}, 0, False, premium_windows=self.purim_windows,
        )
        self.assertEqual(result["calc100"], 120)
        self.assertEqual(result["calc150"], 0)

    def test_purim_after_2200(self):
        """22:00-23:00 בפורים — אחרי חלון הפורים, 100%."""
        result = _calculate_chain_wages(
            [(1320, 1380, 103, PURIM_2026)],
            {}, 0, False, premium_windows=self.purim_windows,
        )
        self.assertEqual(result["calc100"], 60)
        self.assertEqual(result["calc150"], 0)

    def test_purim_full_window_exact(self):
        """08:00-22:00 בדיוק = 840 דקות = 480 ב-150% + 120 ב-175% + 240 ב-200%."""
        result = _calculate_chain_wages(
            [(480, 1320, 103, PURIM_2026)],
            {}, 0, False, premium_windows=self.purim_windows,
        )
        self.assertEqual(result["calc150"], 480)
        self.assertEqual(result["calc175"], 120)
        self.assertEqual(result["calc200"], 240)
        total = result["calc150"] + result["calc175"] + result["calc200"]
        self.assertEqual(total, 840)

    def test_purim_pension_split(self):
        """פיצול פנסיה: calc150_shabbat_100 + calc150_shabbat_50 = calc150."""
        result = _calculate_chain_wages(
            [(540, 900, 103, PURIM_2026)],
            {}, 0, False, premium_windows=self.purim_windows,
        )
        self.assertEqual(result["calc150_shabbat_100"], 360)
        self.assertEqual(result["calc150_shabbat_50"], 360)

    def test_purim_segments_detail_label(self):
        """התוויות בסגמנטים מציגות 'פורים'."""
        result = _calculate_chain_wages(
            [(540, 900, 103, PURIM_2026)],
            {}, 0, False, premium_windows=self.purim_windows,
        )
        labels = [lbl for _, _, lbl, _ in result["segments_detail"]]
        self.assertTrue(any("פורים" in lbl for lbl in labels))


# =============================================================================
# Test: No interference between Shabbat and Premium
# =============================================================================

class TestShabbatPremiumNoInterference(unittest.TestCase):
    """
    מוודא שכאשר premium_windows ריק (או מסונן), שבת עדיין עובדת כרגיל.
    ושכאשר יש premium_windows, הם לא משפיעים על ימי חול רגילים.
    """

    def test_shabbat_with_empty_premium_windows(self):
        """שבת עם premium_windows=[] — תוצאה זהה לבלי premium."""
        cache = _shabbat_cache_for(FRIDAY_2026)
        result_with = _calculate_chain_wages(
            [(540, 900, 106, SATURDAY_2026)],
            cache, 0, False, premium_windows=[],
        )
        result_without = _calculate_chain_wages(
            [(540, 900, 106, SATURDAY_2026)],
            cache, 0, False, premium_windows=None,
        )
        for key in ["calc100", "calc125", "calc150", "calc175", "calc200",
                     "calc150_shabbat", "calc150_overtime",
                     "calc150_shabbat_100", "calc150_shabbat_50"]:
            self.assertEqual(result_with[key], result_without[key],
                             f"Mismatch in {key}: {result_with[key]} vs {result_without[key]}")

    def test_weekday_with_purim_window_wrong_date(self):
        """חלון פורים לא משפיע על יום אחר."""
        windows = [_make_purim_window(PURIM_2026)]
        result = _calculate_chain_wages(
            [(540, 900, 103, WEEKDAY)],
            {}, 0, False, premium_windows=windows,
        )
        self.assertEqual(result["calc100"], 360)
        self.assertEqual(result["calc150"], 0)

    def test_purim_does_not_affect_shabbat_calc(self):
        """פורים לא נופל בשבת (2026) — אין התנגשות."""
        cache = _shabbat_cache_for(FRIDAY_2026)
        windows = [_make_purim_window(PURIM_2026)]

        # משמרת בשבת — רק שבת
        result = _calculate_chain_wages(
            [(540, 900, 106, SATURDAY_2026)],
            cache, 0, False, premium_windows=windows,
        )
        self.assertEqual(result["calc150_shabbat"], 360)
        self.assertEqual(result["calc150_purim"], 0)


# =============================================================================
# Test: Night shift
# =============================================================================

class TestNightShiftPurim(unittest.TestCase):
    """משמרת לילה + פורים."""

    def test_night_shift_purim_evening(self):
        """משמרת לילה 15:00-08:00 בפורים.
        פורים 08:00-22:00.
        15:00-22:00 = 420 דקות פורים.
        22:00-08:00 (= 1320-1920) = 600 דקות חול.

        בפורים (15:00 = 900 min):
        - 900-960 (tier 100%→150% purim): 60 min → calc150 (first 480 of chain)
        Wait, let me recalculate: chain starts at 900 (15:00).
        Tier: minutes 1-480 → 100%, minutes 481-600 → 125%, 601+ → 150%.

        900-1320 (purim): 420 min
          - minute 1-420 of chain → all in 100% tier
          - so shabbat_rate = 150% → calc150 += 420

        1320-1380 (after purim, still in 100% tier): 60 min → calc100
        That's 480 chain minutes done.

        1380-1440 (after purim, 125% tier): 60 min → calc125
        1440-1920 (next day 00:00-08:00, 125% then 150% tier):
          - 1440-1500 (125% tier, 540 chain minute): 60 min → calc125
          - 1500-1920 (150% tier): 420 min → calc150_overtime

        Total chain = 1020 min = 17 hours. Night shift = 7h thresholds.
        Actually with is_night_shift=True, regular_limit=420, overtime_limit=540.

        Let me redo: night shift thresholds: 420 (7h) for 100%→125%, 540 (9h) for 125%→150%.

        900-1320 (purim): 420 min
          - chain minutes 1-420 → 100% tier → shabbat_rate = 150%
          - calc150 += 420, calc150_purim += 420
          At 420 chain minutes: tier change to 125%.

        1320-1380 (after purim): 60 min
          - chain minutes 421-480 → 125% tier → base_rate = 125%
          - calc125 += 60

        1380-1440 (after purim): 60 min
          - chain minutes 481-540 → 125% tier
          - calc125 += 60
          At 540: tier change to 150%.

        1440-1920 (next day): 480 min
          - chain minutes 541-1020 → 150% tier → base_rate = 150%
          - But this is overtime, not shabbat → calc150_overtime += 480
          - calc150 += 480
        """
        windows = [_make_purim_window(PURIM_2026)]
        result = _calculate_chain_wages(
            [(900, 1920, 103, PURIM_2026)],
            {}, 0, True, premium_windows=windows,
        )
        # 420 min purim 150% + 480 min overtime 150% + 120 min 125%
        self.assertEqual(result["calc150_purim"], 420)
        self.assertEqual(result["calc125"], 120)
        total = result["calc100"] + result["calc125"] + result["calc150"] + result["calc175"] + result["calc200"]
        self.assertEqual(total, 1020)


# =============================================================================
# Test: Elections (200% flat)
# =============================================================================

class TestElections(unittest.TestCase):
    """בחירות — 200% flat, ללא tier stacking."""

    def test_elections_full_in_range(self):
        """משמרת 09:00-15:00 ביום בחירות → 360 דקות 200%."""
        elections_date = date(2026, 11, 3)
        windows = [_make_elections_window(elections_date)]
        result = _calculate_chain_wages(
            [(540, 900, 103, elections_date)],
            {}, 0, False, premium_windows=windows,
        )
        self.assertEqual(result["calc200"], 360)
        self.assertEqual(result["calc200_elections"], 360)
        self.assertEqual(result["calc150"], 0)
        self.assertEqual(result["calc100"], 0)

    def test_elections_crossing_boundary(self):
        """06:00-10:00 ביום בחירות (חלון 07:00-22:00).
        60 דקות חול + 180 דקות בחירות 200%."""
        elections_date = date(2026, 11, 3)
        windows = [_make_elections_window(elections_date)]
        result = _calculate_chain_wages(
            [(360, 600, 103, elections_date)],
            {}, 0, False, premium_windows=windows,
        )
        self.assertEqual(result["calc100"], 60)
        self.assertEqual(result["calc200"], 180)
        self.assertEqual(result["calc200_elections"], 180)


# =============================================================================
# Test: Independence Day (150%, cross-midnight)
# =============================================================================

class TestIndependenceDay(unittest.TestCase):
    """יום העצמאות — 150%, 20:00 ערב → 20:00 למחרת."""

    def test_independence_evening(self):
        """21:00-23:00 בערב עצמאות → 120 דקות 150%."""
        erev = date(2026, 4, 21)
        windows = [_make_independence_window(erev)]
        result = _calculate_chain_wages(
            [(1260, 1380, 103, erev)],
            {}, 0, False, premium_windows=windows,
        )
        self.assertEqual(result["calc150"], 120)
        self.assertEqual(result["calc150_independence"], 120)

    def test_independence_before_window(self):
        """18:00-19:30 בערב עצמאות (לפני 20:00) → חול 100%."""
        erev = date(2026, 4, 21)
        windows = [_make_independence_window(erev)]
        result = _calculate_chain_wages(
            [(1080, 1170, 103, erev)],
            {}, 0, False, premium_windows=windows,
        )
        self.assertEqual(result["calc100"], 90)
        self.assertEqual(result["calc150"], 0)


# =============================================================================
# Test: Carryover (minutes_offset)
# =============================================================================

class TestCarryoverWithPurim(unittest.TestCase):
    """המשכיות מיום קודם עם offset."""

    def test_purim_with_offset_starts_at_125(self):
        """480 דקות offset (כבר עברו 8 שעות) + 120 דקות פורים.
        הכל בטייר 125% → shabbat_rate = 175%."""
        windows = [_make_purim_window(PURIM_2026)]
        result = _calculate_chain_wages(
            [(480, 600, 103, PURIM_2026)],
            {}, 480, False, premium_windows=windows,
        )
        self.assertEqual(result["calc175"], 120)
        self.assertEqual(result["calc175_purim"], 120)
        self.assertEqual(result["calc150"], 0)


if __name__ == "__main__":
    unittest.main()
