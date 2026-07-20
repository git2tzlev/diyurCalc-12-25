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
def test_open_event_is_not_in_approved_export(calculate, load_events):
    load_events.return_value = [_event(status="open")]

    result = build_salary_impact_completion_rows(object(), 2026, 7)

    assert result["rows"] == []
    assert calculate.call_count == 0
