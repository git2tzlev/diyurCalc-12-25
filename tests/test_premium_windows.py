"""
בדיקות יחידה לשירות premium_windows — חלונות פרימיום מאוחדים.

מכסה:
- _city_matches: לוגיקת סינון ערים (whitelist/blacklist)
- _is_within_window: בדיקת חפיפה לרגע נתון
- get_window_at: בחירת החלון עם rate_pct הגבוה ביותר
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.premium_windows import (
    PremiumWindow,
    _city_matches,
    _is_within_window,
    get_window_at,
    minutes_until_state_change,
)


# תאריכים לדוגמה
PURIM_2026 = date(2026, 3, 3)
INDEPENDENCE_EVE_2026 = date(2026, 4, 21)
INDEPENDENCE_DAY_2026 = date(2026, 4, 22)


def _make_purim_window() -> PremiumWindow:
    """פורים 08:00-22:00 באותו יום, 150%."""
    return PremiumWindow(
        start_date=PURIM_2026, start_min=480,
        end_date=PURIM_2026, end_min=1320,
        rate_pct=150, origin="purim",
        standby_mode="shabbat", source_id=1,
    )


def _make_independence_window() -> PremiumWindow:
    """עצמאות 20:00 ערב עד 20:00 למחרת, 150% (חוצה חצות)."""
    return PremiumWindow(
        start_date=INDEPENDENCE_EVE_2026, start_min=1200,
        end_date=INDEPENDENCE_DAY_2026, end_min=1200,
        rate_pct=150, origin="independence",
        standby_mode="none", source_id=2,
    )


def _make_elections_window(d: date) -> PremiumWindow:
    """בחירות 07:00-22:00 ביום נתון, 200%."""
    return PremiumWindow(
        start_date=d, start_min=420,
        end_date=d, end_min=1320,
        rate_pct=200, origin="elections",
        standby_mode="none", source_id=3,
    )


class TestCityMatches(unittest.TestCase):
    """בדיקות סינון ערים."""

    def test_no_filter_matches_all(self):
        """אין filter ואין exclude → חל על כולם."""
        self.assertTrue(_city_matches(None, None, "תל אביב"))
        self.assertTrue(_city_matches(None, None, "ירושלים"))
        self.assertTrue(_city_matches(None, None, None))
        self.assertTrue(_city_matches(None, None, ""))

    def test_filter_whitelist(self):
        """filter = רק הערים ברשימה."""
        self.assertTrue(_city_matches(["ירושלים"], None, "ירושלים"))
        self.assertFalse(_city_matches(["ירושלים"], None, "תל אביב"))
        self.assertFalse(_city_matches(["ירושלים"], None, None))

    def test_exclude_blacklist(self):
        """exclude = כל הערים חוץ מאלה ברשימה."""
        self.assertTrue(_city_matches(None, ["ירושלים"], "תל אביב"))
        self.assertFalse(_city_matches(None, ["ירושלים"], "ירושלים"))
        self.assertTrue(_city_matches(None, ["ירושלים"], None))

    def test_whitespace_stripped(self):
        """רווחים סביב שם עיר מתבטלים."""
        self.assertTrue(_city_matches(["ירושלים"], None, "  ירושלים  "))


class TestIsWithinWindow(unittest.TestCase):
    """בדיקות חפיפה של רגע לחלון."""

    def test_purim_middle(self):
        """10:00 בפורים → בתוך החלון."""
        w = _make_purim_window()
        self.assertTrue(_is_within_window(w, PURIM_2026, 600))

    def test_purim_start_inclusive(self):
        """08:00 בדיוק = תחילת פורים (כולל)."""
        w = _make_purim_window()
        self.assertTrue(_is_within_window(w, PURIM_2026, 480))

    def test_purim_end_exclusive(self):
        """22:00 בדיוק = סוף פורים (לא כולל)."""
        w = _make_purim_window()
        self.assertFalse(_is_within_window(w, PURIM_2026, 1320))

    def test_purim_before(self):
        """07:59 לפני פורים."""
        w = _make_purim_window()
        self.assertFalse(_is_within_window(w, PURIM_2026, 479))

    def test_purim_wrong_day(self):
        """10:00 ביום אחר → לא בפורים."""
        w = _make_purim_window()
        self.assertFalse(_is_within_window(w, date(2026, 3, 4), 600))

    def test_independence_evening(self):
        """23:00 בערב עצמאות → בתוך החלון (חצות עוד לא עבר)."""
        w = _make_independence_window()
        self.assertTrue(_is_within_window(w, INDEPENDENCE_EVE_2026, 1380))

    def test_independence_after_midnight(self):
        """03:00 ביום העצמאות → בתוך החלון (חצה חצות)."""
        w = _make_independence_window()
        self.assertTrue(_is_within_window(w, INDEPENDENCE_DAY_2026, 180))

    def test_independence_19_59_on_end_day(self):
        """19:59 ביום העצמאות → בתוך החלון (דקה לפני סיום)."""
        w = _make_independence_window()
        self.assertTrue(_is_within_window(w, INDEPENDENCE_DAY_2026, 1199))

    def test_independence_20_00_exclusive(self):
        """20:00 ביום העצמאות = סוף החלון (לא כולל)."""
        w = _make_independence_window()
        self.assertFalse(_is_within_window(w, INDEPENDENCE_DAY_2026, 1200))

    def test_independence_before_erev(self):
        """19:59 בערב עצמאות → לפני החלון."""
        w = _make_independence_window()
        self.assertFalse(_is_within_window(w, INDEPENDENCE_EVE_2026, 1199))

    def test_independence_day_after(self):
        """21:00 יום אחרי העצמאות → לא בתוך החלון."""
        w = _make_independence_window()
        self.assertFalse(_is_within_window(w, date(2026, 4, 23), 1260))


class TestGetWindowAt(unittest.TestCase):
    """בחירת החלון המתאים — max rate wins בחפיפה."""

    def test_no_windows(self):
        """רשימה ריקה → None."""
        self.assertIsNone(get_window_at([], PURIM_2026, 600))

    def test_no_match(self):
        """רגע מחוץ לכל חלון → None."""
        w = _make_purim_window()
        self.assertIsNone(get_window_at([w], date(2026, 3, 10), 600))

    def test_single_match(self):
        """חלון אחד חופף → מוחזר."""
        w = _make_purim_window()
        result = get_window_at([w], PURIM_2026, 600)
        self.assertIsNotNone(result)
        self.assertEqual(result.origin, "purim")

    def test_max_rate_wins_on_overlap(self):
        """שני חלונות חופפים → נבחר זה עם rate_pct הגבוה."""
        # פורים 150% + בחירות 200% באותו יום (תרחיש תאורטי)
        purim = _make_purim_window()
        elections = _make_elections_window(PURIM_2026)
        result = get_window_at([purim, elections], PURIM_2026, 600)
        self.assertIsNotNone(result)
        self.assertEqual(result.rate_pct, 200)
        self.assertEqual(result.origin, "elections")

    def test_non_overlapping_windows_use_correct_one(self):
        """שני חלונות לא חופפים → נבחר זה שבו הרגע."""
        purim = _make_purim_window()
        elections = _make_elections_window(date(2026, 3, 10))
        # רגע בפורים
        self.assertEqual(
            get_window_at([purim, elections], PURIM_2026, 600).origin, "purim"
        )
        # רגע בבחירות
        self.assertEqual(
            get_window_at([purim, elections], date(2026, 3, 10), 600).origin, "elections"
        )


class TestMinutesUntilStateChange(unittest.TestCase):
    """בדיקות מחשב מרחק לגבול חלון (לחיתוך בלוקים במנוע)."""

    def test_no_windows(self):
        """אין חלונות → תמיד max_distance."""
        self.assertEqual(minutes_until_state_change([], PURIM_2026, 600, 500), 500)

    def test_inside_window_until_end(self):
        """בתוך פורים 10:00 → נשארו 720 דקות עד 22:00."""
        w = _make_purim_window()
        # פורים 08:00-22:00 = 480-1320. בשעה 600 (10:00), עד 1320 = 720 דקות.
        self.assertEqual(minutes_until_state_change([w], PURIM_2026, 600, 1000), 720)

    def test_inside_window_limited_by_max(self):
        """max_distance קוטע את המרחק עד סוף החלון."""
        w = _make_purim_window()
        self.assertEqual(minutes_until_state_change([w], PURIM_2026, 600, 100), 100)

    def test_outside_window_before_start(self):
        """07:00 בבוקר פורים → נשארו 60 דקות עד 08:00 (כניסת פורים)."""
        w = _make_purim_window()
        # 07:00 = 420, פורים מתחיל ב-480. מרחק = 60.
        self.assertEqual(minutes_until_state_change([w], PURIM_2026, 420, 1000), 60)

    def test_outside_window_no_upcoming(self):
        """23:00 בפורים (אחרי סוף) → אין חלון קרוב → max_distance."""
        w = _make_purim_window()
        # 23:00 = 1380. פורים כבר עבר.
        self.assertEqual(minutes_until_state_change([w], PURIM_2026, 1380, 500), 500)

    def test_independence_cross_midnight_from_erev(self):
        """בערב עצמאות 22:00 → נשארו 22 שעות עד 20:00 למחרת = 1320 דקות."""
        w = _make_independence_window()
        # 22:00 ב-21/4. סוף החלון ב-20:00 ב-22/4 = 1440 + 1200 = 2640 מנוקד מ-00:00 21/4.
        # 22:00 = 1320. מרחק = 2640 - 1320 = 1320.
        self.assertEqual(
            minutes_until_state_change([w], INDEPENDENCE_EVE_2026, 1320, 10000), 1320
        )

    def test_independence_cross_midnight_from_yom(self):
        """ביום העצמאות 10:00 → נשארו 10 שעות = 600 דקות עד 20:00."""
        w = _make_independence_window()
        # 10:00 ביום 22/4 = 600. סוף ב-20:00 ב-22/4 = 1200.
        self.assertEqual(
            minutes_until_state_change([w], INDEPENDENCE_DAY_2026, 600, 10000), 600
        )


if __name__ == "__main__":
    unittest.main()
