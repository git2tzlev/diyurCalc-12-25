from io import BytesIO

import pandas as pd

from services.guide_reports_excel_export import build_guide_reports_excel


def test_build_guide_reports_excel_contains_display_rows_and_metadata():
    excel_bytes = build_guide_reports_excel(
        year=2026,
        month=5,
        exported_by="tester",
        selected_housing_array_id=1,
        selected_housing_array_name="מערך",
        guide_reports=[{
            "person": {
                "id": 10,
                "id_number": "123456789",
                "meirav_code": "005832",
                "name": "מדריך",
                "email": "guide@example.com",
                "type": "permanent",
                "housing_array_id": 1,
                "housing_array_name": "מערך",
            },
            "pdf_data": {
                "period_start": "01/05/26",
                "period_end": "31/05/26",
                "generation_time": "10:20:00 03.06.2026",
                "total_work_hours": 7.5,
                "standby_count": 1,
                "total_additions_no_travel": 120,
                "total_salary": 900,
                "shifts_data": [{
                    "date": "01/05/26",
                    "day": "שישי",
                    "apartment": "דירה",
                    "shift_type": "חול",
                    "start_time": "16:00",
                    "end_time": "08:00",
                    "work_hours": 7.5,
                    "standby_hours": 8.5,
                    "note": "הערה | שולם ב-06/2026",
                    "is_payment_period_completion": True,
                }],
                "payments_data": [{"description": "נסיעות", "amount": 50}],
                "completion_payments_data": [],
                "variable_shifts": [{
                    "shift_name": "תגבור",
                    "reason": "-",
                    "hours": 2,
                    "rate": 40,
                    "overtime_payment": 20,
                    "payment": 100,
                }],
                "is_asd_multi_rate": False,
            },
        }],
    )

    sheets = pd.read_excel(BytesIO(excel_bytes), sheet_name=None)

    assert set(sheets) == {"export_info", "guides", "report_rows", "variable_rate_rows"}
    export_info = dict(zip(sheets["export_info"]["key"], sheets["export_info"]["value"]))
    assert export_info["money_unit"] == "NIS decimal"
    assert export_info["guides_count"] == 1
    assert export_info["period_start_iso"] == "2026-05-01"
    assert sheets["guides"].iloc[0]["id_number"] == 123456789
    assert sheets["guides"].iloc[0]["period_end_iso"] == "2026-05-31"
    assert list(sheets["report_rows"]["row_type"]) == ["shift", "payment"]
    assert sheets["report_rows"].iloc[0]["date_iso"] == "2026-05-01"
    assert sheets["report_rows"].iloc[0]["description"] == "הערה | שולם ב-06/2026"
    assert "payment-period-completion-row" in sheets["report_rows"].iloc[0]["css_class"]
    assert sheets["variable_rate_rows"].iloc[0]["shift_name"] == "תגבור"
