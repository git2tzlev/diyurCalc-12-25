# -*- coding: utf-8 -*-
"""בדיקות אבטחה לתצוגות ייצוא."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from routes.export import (
    _build_blocked_multi_housing_warnings,
    _filter_multi_housing_for_summary,
    _remove_blocked_preview_people,
)
from services import gesher_exporter


class TestGesherPreviewSecurity(unittest.TestCase):
    def test_multi_housing_warning_is_limited_to_visible_people(self):
        multi_housing = {
            10: ["מערך א", "מערך ב"],
            20: ["מערך ג", "מערך ד"],
        }
        summary_data = [{"person_id": 10, "name": "מדריך נראה"}]

        result = _filter_multi_housing_for_summary(multi_housing, summary_data)

        self.assertEqual(result, {10: ["מערך א", "מערך ב"]})

    def test_multi_housing_supports_legacy_id_key(self):
        multi_housing = {10: ["מערך א", "מערך ב"]}
        summary_data = [{"id": 10, "name": "מדריך נראה"}]

        result = _filter_multi_housing_for_summary(multi_housing, summary_data)

        self.assertIn(10, result)

    def test_blocked_multi_housing_warning_uses_visible_preview_data(self):
        preview = [
            {"person_id": 10, "name": "מדריך חסום", "meirav_code": "123", "lines": []},
            {"person_id": 20, "name": "מדריך רגיל", "meirav_code": "456", "lines": []},
        ]
        blocked = {10: ["ASD", "צוהר הלב"], 30: ["ASD", "צוהר הלב"]}

        warnings = _build_blocked_multi_housing_warnings(preview, blocked)

        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["person_id"], 10)
        self.assertIn("ASD, צוהר הלב", warnings[0]["reason"])

    def test_blocked_multi_housing_people_are_removed_from_selectable_preview(self):
        preview = [
            {"person_id": 10, "name": "מדריך חסום"},
            {"person_id": 20, "name": "מדריך רגיל"},
        ]

        result = _remove_blocked_preview_people(preview, {10: ["ASD", "צוהר הלב"]})

        self.assertEqual([person["person_id"] for person in result], [20])


class TestGesherMultiHousingBlockRule(unittest.TestCase):
    def test_rule_applies_only_to_tzohar_halev(self):
        self.assertTrue(gesher_exporter.should_block_multi_housing_for_gesher(1))
        self.assertFalse(gesher_exporter.should_block_multi_housing_for_gesher(2))
        self.assertFalse(gesher_exporter.should_block_multi_housing_for_gesher(None))


if __name__ == "__main__":
    unittest.main()
