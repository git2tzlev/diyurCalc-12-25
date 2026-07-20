from services.gesher_exporter import append_completion_rows_to_preview, with_completion_export_codes


def test_with_completion_export_codes_adds_virtual_completion_symbols():
    export_codes = {"360": ("calc100", "hours_100", "שעות רגילות")}

    result = with_completion_export_codes(export_codes)

    assert result["360"] == ("calc100", "hours_100", "שעות רגילות")
    assert result["253"] == ("completion_non_pension", "money", "הפרשי השלמות לא לפנסיה")
    assert result["317"] == ("completion_pension", "money", "הפרשי השלמות לפנסיה")
    assert result["243"] == ("completion_professional_support", "money", "הפרש תומך מקצועי")


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
    assert result[0]["lines"] == [{
        "symbol": "317",
        "key": "completion_pension",
        "display_name": "הפרשי השלמות לפנסיה",
        "type": "money",
        "quantity": 0.0,
        "payment": 120.5,
        "is_completion_difference": True,
        "source_symbols": "",
    }]
    assert result[1]["person_id"] == 20
    assert result[1]["name"] == "מדריך רק השלמה"
    assert result[1]["lines"][0]["payment"] == -30.0
