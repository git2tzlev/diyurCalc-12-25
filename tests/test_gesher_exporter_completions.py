from services.gesher_exporter import (
    _completion_quantity_and_rate,
    append_completion_rows_to_preview,
    apply_completion_rows_to_summary_data,
    completion_days_total_keys,
    with_completion_export_codes,
)


def test_with_completion_export_codes_adds_virtual_completion_symbols():
    export_codes = {"360": ("calc100", "hours_100", "שעות רגילות")}

    result = with_completion_export_codes(export_codes)

    assert result["360"] == ("calc100", "hours_100", "שעות רגילות")
    assert result["253"] == ("completion_non_pension", "money", "הפרשי השלמות לא לפנסיה")
    assert result["317"] == ("completion_pension", "money", "הפרשי השלמות לפנסיה")
    assert result["306"] == ("completion_sick_pay", "completion_hours", "תשלום מחלה רטרו")
    assert result["332"] == ("completion_vacation_pay", "completion_hours", "תשלום חופשה רטרו")
    assert result["414"] == ("completion_sick_days", "completion_days", "ניצול מחלה רטרו")
    assert result["427"] == ("completion_vacation_days", "completion_days", "ניצול חופשה רטרו")
    assert result["410"] == ("completion_sick_entitlement", "completion_days", "זכות מחלה רטרו")
    assert result["799"] == ("completion_vacation_entitlement", "completion_days", "זכות חופשה רטרו")
    assert "243" not in result


def test_completion_days_total_keys_lists_every_quantity_only_component():
    assert completion_days_total_keys() == {
        "completion_sick_days",
        "completion_vacation_days",
        "completion_sick_entitlement",
        "completion_vacation_entitlement",
    }


def test_completion_quantity_and_rate_per_symbol_type():
    row = {"quantity": 8.0, "rate": 35.4, "amount": 283.2}

    assert _completion_quantity_and_rate({**row, "symbol": "317"}) == (0.0, 283.2)
    assert _completion_quantity_and_rate({**row, "symbol": "306"}) == (8.0, 35.4)
    assert _completion_quantity_and_rate({"symbol": "414", "quantity": 2.0}) == (2.0, 0.0)


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


def test_retro_hours_add_money_while_retro_days_stay_out_of_the_money_total():
    summary_data = [{
        "person_id": 10,
        "name": "מדריך",
        "merav_code": "10",
        "totals": {"rounded_total": 500.0},
    }]
    grand_totals = {"rounded_total": 500.0}
    rows = [
        {
            "person_id": 10, "employee_code": "000010", "symbol": "306",
            "quantity": 8.0, "rate": 35.4, "amount": 283.2,
        },
        {
            "person_id": 10, "employee_code": "000010", "symbol": "414",
            "quantity": 2.0, "rate": 0.0, "amount": 0.0,
        },
    ]

    apply_completion_rows_to_summary_data(summary_data, grand_totals, rows)

    totals = summary_data[0]["totals"]
    assert totals["completion_sick_pay"] == 283.2
    assert totals["completion_sick_pay_quantity"] == 8.0
    assert totals["completion_sick_pay_rate"] == 35.4
    assert totals["completion_sick_days"] == 2.0
    assert totals["completion_retro_money_total"] == 283.2
    assert totals["rounded_total"] == 783.2


def test_mixed_retro_rates_clear_the_displayed_rate():
    totals = {}
    rows = [
        {"person_id": 10, "employee_code": "000010", "symbol": "332",
         "quantity": -4.0, "rate": 34.4, "amount": -137.6},
        {"person_id": 10, "employee_code": "000010", "symbol": "332",
         "quantity": 4.0, "rate": 35.4, "amount": 141.6},
    ]

    apply_completion_rows_to_summary_data(
        [{"person_id": 10, "name": "מדריך", "merav_code": "10", "totals": totals}],
        {},
        rows,
    )

    assert totals["completion_vacation_pay"] == 4.0
    assert totals["completion_vacation_pay_quantity"] == 0.0
    assert totals["completion_vacation_pay_rate"] == 0.0
