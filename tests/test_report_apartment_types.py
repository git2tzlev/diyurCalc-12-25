# -*- coding: utf-8 -*-
"""בדיקות לקביעת סוג הדירה של דיווח לפי חודש החישוב (סוג בפועל / סוג לתעריף)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app_utils import _apply_report_apartment_types, _register_asd_night_labels
from core.constants import (
    ASD_HOUSING_ARRAY_ID,
    HIGH_FUNCTIONING_APT_TYPE,
    LOW_FUNCTIONING_APT_TYPE,
    NIGHT_SHIFT_ID,
    NURSING_APT_TYPE,
)
from core.shift_hours import calculate_shift_hours

NIGHT_SEGMENTS = {
    NIGHT_SHIFT_ID: [
        {"start_time": "22:00", "end_time": "06:30", "segment_type": "standby"},
        {"start_time": "06:30", "end_time": "08:00", "segment_type": "work"},
    ]
}

MONTH_TYPES = {
    LOW_FUNCTIONING_APT_TYPE: {"id": LOW_FUNCTIONING_APT_TYPE, "name": "תפקוד נמוך", "hourly_wage_supplement": 300},
}


def _report(**overrides) -> dict:
    report = {
        "apartment_id": 38,
        "apartment_type_id": NURSING_APT_TYPE,
        "apartment_type_name": "סיעודית",
        "hourly_wage_supplement": 600,
        "rate_apartment_type_id": None,
        "rate_hourly_wage_supplement": None,
    }
    report.update(overrides)
    return report


class TestApplyReportApartmentTypes(unittest.TestCase):
    def test_current_type_when_no_history(self):
        r = _report()
        _apply_report_apartment_types(r, NURSING_APT_TYPE, {})
        self.assertEqual(r["actual_apartment_type_id"], NURSING_APT_TYPE)
        self.assertEqual(r["apartment_type_id"], NURSING_APT_TYPE)
        self.assertEqual(r["actual_hourly_wage_supplement"], 600)

    def test_historical_type_sets_actual_type_and_supplement(self):
        r = _report()
        _apply_report_apartment_types(r, LOW_FUNCTIONING_APT_TYPE, MONTH_TYPES)
        self.assertEqual(r["actual_apartment_type_id"], LOW_FUNCTIONING_APT_TYPE)
        self.assertEqual(r["apartment_type_id"], LOW_FUNCTIONING_APT_TYPE)
        self.assertEqual(r["apartment_type_name"], "תפקוד נמוך")
        self.assertEqual(r["hourly_wage_supplement"], 300)
        self.assertEqual(r["actual_hourly_wage_supplement"], 300)

    def test_rate_type_overrides_rate_but_keeps_historical_actual(self):
        r = _report(rate_apartment_type_id=1, rate_hourly_wage_supplement=0)
        _apply_report_apartment_types(r, LOW_FUNCTIONING_APT_TYPE, MONTH_TYPES)
        self.assertEqual(r["actual_apartment_type_id"], LOW_FUNCTIONING_APT_TYPE)
        self.assertEqual(r["actual_hourly_wage_supplement"], 300)
        self.assertEqual(r["apartment_type_id"], 1)
        self.assertEqual(r["hourly_wage_supplement"], 0)


class TestNursingBehavesLikeLowFunctioning(unittest.TestCase):
    def _night_hours(self, apt_type: int, marking: str | None = "night_wakefulness") -> tuple[float, float]:
        return calculate_shift_hours(
            "22:00", "08:00", NIGHT_SHIFT_ID, NIGHT_SEGMENTS,
            apartment_type_id=apt_type, housing_array_id=ASD_HOUSING_ARRAY_ID,
            asd_night_marking=marking,
        )

    def test_shift_hours_marked_standby_counts_as_work(self):
        self.assertEqual(self._night_hours(NURSING_APT_TYPE), self._night_hours(LOW_FUNCTIONING_APT_TYPE))
        self.assertEqual(self._night_hours(NURSING_APT_TYPE), (10.0, 0.0))

    def test_shift_hours_unmarked_keeps_standby(self):
        self.assertEqual(self._night_hours(NURSING_APT_TYPE, marking=None), (1.5, 8.5))
        self.assertEqual(self._night_hours(LOW_FUNCTIONING_APT_TYPE, marking=None), (1.5, 8.5))

    def test_high_functioning_keeps_standby(self):
        self.assertEqual(self._night_hours(HIGH_FUNCTIONING_APT_TYPE, marking="sleep_in_living_room"), (1.5, 8.5))

    def test_night_label_is_wakefulness(self):
        entry: dict = {}
        _register_asd_night_labels(entry, {"apartment_name": "צלילים"}, True, NURSING_APT_TYPE)
        self.assertEqual(entry["asd_night_label_by_apt"], {"צלילים": "ערות בלילה"})
        self.assertEqual(entry["asd_night_label_by_apt_type"], {NURSING_APT_TYPE: "ערות בלילה"})


if __name__ == "__main__":
    unittest.main()
