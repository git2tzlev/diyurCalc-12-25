# -*- coding: utf-8 -*-
"""בדיקות לסדר החיפוש של תעריף כוננות היסטורי (core.history._query_standby_rate_for_month)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.history import _query_standby_rate_for_month

SEGMENT = 2
THERAPEUTIC, BERESHIT = 2, 3


class _FakeCursor:
    """מדמה את שתי השאילתות: היסטוריה (עם חודש) ותעריף נוכחי."""

    def __init__(self, current: dict, history: list[tuple]):
        self._current = current  # (segment, type, marital) -> amount
        self._history = history  # (segment, type, marital, year, month, amount)
        self._result = None

    def execute(self, query: str, params: tuple) -> None:
        if "standby_rates_history" in query:
            segment, apt_type, marital, year, _, month = params
            rows = sorted(
                (h for h in self._history
                 if h[:3] == (segment, apt_type, marital) and (h[3], h[4]) > (year, month)),
                key=lambda h: (h[3], h[4]),
            )
            self._result = {"amount": rows[0][5]} if rows else None
        else:
            amount = self._current.get(params)
            self._result = {"amount": amount} if amount is not None else None

    def fetchone(self):
        return self._result

    def close(self) -> None:
        pass


class _FakeConn:
    def __init__(self, current: dict, history: list[tuple]):
        self._cursor = _FakeCursor(current, history)

    def cursor(self, cursor_factory=None):
        return self._cursor


CURRENT = {
    (SEGMENT, None, "single"): 10000,          # כללי - שונה ל-100 מ-10/2026
    (SEGMENT, THERAPEUTIC, "single"): 15600,   # תעריף ספציפי בלי היסטוריה
}
HISTORY = [
    (SEGMENT, None, "single", 2026, 10, 7000),  # עד 09/2026 הכללי היה 70
]


class TestStandbyRateLookupOrder(unittest.TestCase):
    def _rate(self, apt_type, year, month):
        return _query_standby_rate_for_month(_FakeConn(CURRENT, HISTORY), SEGMENT, apt_type, "single", year, month)

    def test_own_rate_beats_general_history(self):
        self.assertEqual(self._rate(THERAPEUTIC, 2026, 9), 15600)

    def test_type_without_own_rate_uses_general_history(self):
        self.assertEqual(self._rate(BERESHIT, 2026, 9), 7000)
        self.assertEqual(self._rate(BERESHIT, 2026, 10), 10000)

    def test_own_history_used_before_change_month(self):
        history = HISTORY + [(SEGMENT, THERAPEUTIC, "single", 2026, 5, 12000)]
        conn = _FakeConn(CURRENT, history)
        self.assertEqual(_query_standby_rate_for_month(conn, SEGMENT, THERAPEUTIC, "single", 2026, 4), 12000)
        self.assertEqual(_query_standby_rate_for_month(conn, SEGMENT, THERAPEUTIC, "single", 2026, 5), 15600)


if __name__ == "__main__":
    unittest.main()
