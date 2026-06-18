# -*- coding: utf-8 -*-
"""בדיקות לתצוגה מקדימה של ייצוא גשר."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import gesher_exporter


class TestGesherExportPreview(unittest.TestCase):
    """מוודא ש-preview מתנהג כמו הייצוא בפועל בנקודות קריטיות."""

    def _summary_data(self):
        return [
            {
                "person_id": 1,
                "name": "מדריך בדיקה",
                "meirav_code": "1234",
                "totals": {
                    "calc100": 480,
                    "actual_work_days": 1,
                },
            }
        ]

    @patch.object(gesher_exporter, "get_minimum_wage", return_value=34.40)
    @patch.object(gesher_exporter, "get_export_options", return_value={"min_amount": 0.01})
    @patch.object(gesher_exporter, "load_export_config_from_db")
    def test_preview_skips_codes_excluded_from_export(
        self,
        mock_load_config,
        _mock_options,
        _mock_minimum_wage,
    ):
        mock_load_config.return_value = {
            "130": ("actual_work_days", "days_with_total_hours", "ימי עבודה"),
            "199": ("actual_work_days", "days_with_total_hours", "סהכ שעות"),
            "360": ("calc100", "hours_100", "שעות רגילות"),
        }

        preview = gesher_exporter.get_export_preview(
            conn=object(),
            year=2026,
            month=4,
            summary_data=self._summary_data(),
        )

        symbols = [line["symbol"] for line in preview[0]["lines"]]
        self.assertEqual(symbols, ["360"])

    @patch.object(gesher_exporter, "get_minimum_wage", return_value=34.40)
    @patch.object(gesher_exporter, "get_export_options", return_value={"min_amount": 0.01})
    @patch.object(gesher_exporter, "load_export_config", return_value={"360": ("calc100", "hours_100")})
    @patch.object(gesher_exporter, "load_export_config_from_db", return_value={})
    def test_preview_supports_legacy_two_value_config_tuple(
        self,
        _mock_db_config,
        _mock_file_config,
        _mock_options,
        _mock_minimum_wage,
    ):
        preview = gesher_exporter.get_export_preview(
            conn=object(),
            year=2026,
            month=4,
            summary_data=self._summary_data(),
        )

        line = preview[0]["lines"][0]
        self.assertEqual(line["symbol"], "360")
        self.assertEqual(line["key"], "calc100")
        self.assertEqual(line["display_name"], "calc100")
        self.assertEqual(line["quantity"], 8.0)
        self.assertEqual(line["payment"], 34.40)

    @patch("core.logic.calculate_monthly_summary")
    @patch.object(gesher_exporter, "get_minimum_wage", return_value=35.40)
    @patch.object(
        gesher_exporter,
        "get_export_options",
        return_value={"export_zero_values": False, "min_amount": 0.01},
    )
    @patch.object(gesher_exporter, "load_export_config", return_value={"254": ("holiday_payment", "money")})
    @patch.object(gesher_exporter, "load_export_config_from_db", return_value={})
    def test_single_person_export_uses_monthly_summary_with_holiday_payment(
        self,
        _mock_db_config,
        _mock_file_config,
        _mock_options,
        _mock_minimum_wage,
        mock_monthly_summary,
    ):
        class _Result:
            def fetchone(self):
                return {"id": 10, "name": "מדריך חג", "meirav_code": "1234", "employer_code": "001"}

        class _Conn:
            def execute(self, _query, _params=None):
                return _Result()

        mock_monthly_summary.return_value = (
            [
                {
                    "person_id": 10,
                    "totals": {"holiday_payment": 247.80},
                }
            ],
            {},
        )

        content, company = gesher_exporter.generate_gesher_file_for_person(
            _Conn(), person_id=10, year=2026, month=5
        )

        self.assertEqual(company, "001")
        self.assertIn("001 26 05", content)
        self.assertIn("001234 254 0000.00 00247.80", content)


if __name__ == "__main__":
    unittest.main()
