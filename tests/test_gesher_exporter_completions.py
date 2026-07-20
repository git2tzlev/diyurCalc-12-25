from services.gesher_exporter import (
    append_completion_rows_to_preview,
    apply_completion_rows_to_summary_data,
    with_completion_export_codes,
)


def test_with_completion_export_codes_adds_virtual_completion_symbols():
    export_codes = {"360": ("calc100", "hours_100", "שעות רגילות")}

    result = with_completion_export_codes(export_codes)

    assert result["360"] == ("calc100", "hours_100", "שעות רגילות")
    assert result["253"] == ("completion_non_pension", "money", "הפרשי השלמות לא לפנסיה")
    assert result["317"] == ("completion_pension", "money", "הפרשי השלמות לפנסיה")
    assert "410" not in result
    assert "799" not in result
    assert "243" not in result


def test_append_completion_rows_to_preview_adds_rows_to_existing_or_completion_only_people():
    preview = [{
        "person_id": 10,
        "name": "מדריך קיים",
        "meirav_code": "000010",
        "lines": [],
    }]
    rows = [
        {
            "person_id": 10,
            "employee_code": "000010",
            "person_name": "מדריך קיים",
            "symbol": "317",
            "amount": 120.5,
            "display_name": "הפרשי השלמות לפנסיה",
        },
        {
            "person_id": 20,
            "employee_code": "000020",
            "person_name": "מדריך רק השלמה",
            "symbol": "253",
            "amount": -30.0,
            "display_name": "הפרשי השלמות לא לפנסיה",
        },
    ]

    result = append_completion_rows_to_preview(preview, rows)

    assert len(result) == 2
    assert result[0]["lines"] == [
        {
            "symbol": "317",
            "key": "completion_pension",
            "display_name": "הפרשי השלמות לפנסיה",
            "type": "money",
            "quantity": 0.0,
            "payment": 120.5,
            "is_completion_difference": True,
            "source_symbols": "",
        },
    ]
    assert result[1]["person_id"] == 20
    assert result[1]["name"] == "מדריך רק השלמה"
    assert result[1]["lines"][0]["payment"] == -30.0


def test_apply_completion_rows_to_summary_data_adds_existing_and_completion_only_people():
    summary_data = [{
        "person_id": 10,
        "name": "מדריך קיים",
        "merav_code": "10",
        "totals": {"rounded_total": 500.0},
    }]
    grand_totals = {"rounded_total": 500.0}
    rows = [
        {
            "person_id": 10,
            "employee_code": "000010",
            "person_name": "מדריך קיים",
            "symbol": "317",
            "amount": 120.0,
            "quantity": 0.0,
        },
        {
            "person_id": 20,
            "employee_code": "000020",
            "person_name": "מדריך רק השלמה",
            "symbol": "253",
            "amount": -30.0,
            "quantity": 0.0,
        },
    ]

    apply_completion_rows_to_summary_data(summary_data, grand_totals, rows)

    assert len(summary_data) == 2
    assert summary_data[0]["totals"]["completion_pension"] == 120.0
    assert summary_data[0]["totals"]["completion_retro_money_total"] == 120.0
    assert summary_data[1]["person_id"] == 20
    assert summary_data[1]["totals"]["completion_non_pension"] == -30.0
    assert grand_totals["completion_pension"] == 120.0
    assert grand_totals["completion_non_pension"] == -30.0
