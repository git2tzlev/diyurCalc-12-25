from datetime import date, datetime
from unittest.mock import patch

from services.salary_impact import (
    _event_error,
    _reverse_events,
    build_salary_impact_completion_rows,
)


def _event(**overrides):
    value = {
        "id": 1,
        "event_type": "created",
        "source_table": "time_reports",
        "source_id": 10,
        "source_action": "INSERT",
        "person_id": 7,
        "person_name": "מדריך",
        "meirav_code": "123",
        "employer_code": "001",
        "housing_array_id": 1,
        "work_date": date(2026, 6, 10),
        "work_year": 2026,
        "work_month": 6,
        "payment_year": 2026,
        "payment_month": 7,
        "old_data": None,
        "new_data": {"id": 10, "person_id": 7, "date": date(2026, 6, 10)},
        "changed_fields": ["id"],
        "status": "included_in_export",
        "created_at": datetime(2026, 7, 1, 10, 0),
        "validation_error": "",
    }
    value.update(overrides)
    return value


def test_reverse_insert_removes_report_from_previous_state():
    reports, components = _reverse_events([_event()])

    assert reports == {10: None}
    assert components == {}


def test_reverse_update_restores_old_snapshot():
    event = _event(
        source_action="UPDATE",
        old_data={"id": 10, "start_time": "20:00"},
        new_data={"id": 10, "start_time": "22:00"},
    )

    reports, _ = _reverse_events([event])

    assert reports[10]["start_time"] == "20:00"


def test_invalid_event_without_old_delete_snapshot_is_blocked():
    event = _event(source_action="DELETE", old_data=None, new_data=None)

    assert _event_error(event) == "חסרה תמונת נתונים ישנה"


def test_guide_without_meirav_code_is_blocked():
    assert _event_error(_event(meirav_code="")) == "חסר קוד מירב למדריך"
    assert _event_error(_event(meirav_code=None)) == "חסר קוד מירב למדריך"
    assert _event_error(_event(meirav_code="  ")) == "חסר קוד מירב למדריך"
    assert _event_error(_event(meirav_code="3783")) == ""


@patch("services.salary_impact.get_salary_impact_events")
@patch("services.salary_impact._calculate_group_lines")
def test_event_without_meirav_code_is_reported_instead_of_silently_dropped(calculate, load_events):
    event = _event(meirav_code="")
    # כמו ב-get_salary_impact_events האמיתי, שמחשב את השגיאה בזמן הטעינה
    event["validation_error"] = _event_error(event)
    load_events.return_value = [event]

    result = build_salary_impact_completion_rows(object(), 2026, 7)

    assert result["rows"] == []
    assert calculate.call_count == 0
    assert [event["validation_error"] for event in result["invalid_events"]] == [
        "חסר קוד מירב למדריך"
    ]


@patch("services.salary_impact.get_salary_impact_events")
@patch("services.salary_impact._calculate_group_lines")
def test_approved_insert_is_calculated_as_pension_completion(calculate, load_events):
    load_events.return_value = [_event()]
    before = []
    after = [{
        "employee_code": "000123",
        "person_id": 7,
        "person_name": "מדריך",
        "employer_code": "001",
        "symbol": "360",
        "value_type": "money",
        "quantity": 0.0,
        "rate": 100.0,
        "amount": 100.0,
    }]
    calculate.side_effect = [before, after]

    result = build_salary_impact_completion_rows(object(), 2026, 7)

    assert result["rows"][0]["symbol"] == "317"
    assert result["rows"][0]["amount"] == 100.0
    assert calculate.call_count == 2


@patch("services.salary_impact.get_salary_impact_events")
@patch("services.salary_impact._calculate_group_lines")
def test_approved_sick_day_is_calculated_as_retro_sick_symbols(calculate, load_events):
    load_events.return_value = [_event()]
    line = {
        "employee_code": "000123",
        "person_id": 7,
        "person_name": "מדריך",
        "employer_code": "001",
    }
    after = [
        {**line, "symbol": "319", "internal_key": "sick_payment",
         "value_type": "sick_hours_paid", "quantity": 8.0, "rate": 35.4, "amount": 283.2},
        {**line, "symbol": "33", "internal_key": "sick_days_taken",
         "value_type": "days", "quantity": 1.0, "rate": 0.0, "amount": 0.0},
        {**line, "symbol": "698", "internal_key": "sick_days_accrued",
         "value_type": "days", "quantity": 1.25, "rate": 0.0, "amount": 0.0},
    ]
    before = [
        {**line, "symbol": "698", "internal_key": "sick_days_accrued",
         "value_type": "days", "quantity": 1.18, "rate": 0.0, "amount": 0.0},
    ]
    calculate.side_effect = [before, after]

    result = build_salary_impact_completion_rows(object(), 2026, 7)

    assert [(row["symbol"], row["quantity"], row["rate"]) for row in result["rows"]] == [
        ("306", 8.0, 35.4),
        ("410", 0.07, 0.0),
        ("414", 1.0, 0.0),
    ]


@patch("services.salary_impact.get_salary_impact_events")
@patch("services.salary_impact._calculate_group_lines")
def test_open_event_is_not_in_approved_export(calculate, load_events):
    load_events.return_value = [_event(status="open")]

    result = build_salary_impact_completion_rows(object(), 2026, 7)

    assert result["rows"] == []
    assert calculate.call_count == 0
