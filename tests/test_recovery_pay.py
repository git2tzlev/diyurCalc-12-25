from __future__ import annotations

from datetime import date

from openpyxl import Workbook

from core.recovery_pay import (
    _has_payment_month_activity,
    parse_legacy_recovery_xlsx,
    recovery_days_for_period,
    recovery_days_for_seniority,
    recovery_eligible_minutes_from_totals,
    recovery_person_ineligibility_reason,
    seniority_years_for_recovery,
)


def test_recovery_eligible_minutes_do_not_double_count_shabbat_splits():
    totals = {
        "calc100": 60,
        "calc125": 120,
        "calc150": 180,
        "calc150_shabbat": 180,
        "calc150_shabbat_100": 180,
        "calc150_shabbat_50": 180,
        "calc175": 240,
        "calc200": 300,
        "calc_variable": 30,
        "vacation_minutes": 90,
        "sick_minutes": 120,
        "effective_sick_minutes": 60,
        "non_effective_sick_minutes": 60,
        "holiday_payment_hours": 2.5,
    }

    assert recovery_eligible_minutes_from_totals(totals) == 60 + 120 + 180 + 240 + 300 + 30 + 90 + 120 + 150


def test_recovery_eligible_minutes_uses_full_sick_minutes_not_paid_part_only():
    totals = {
        "sick_minutes": 480,
        "effective_sick_minutes": 240,
        "non_effective_sick_minutes": 240,
    }

    assert recovery_eligible_minutes_from_totals(totals) == 480


def test_recovery_person_eligibility_ignores_current_active_flag():
    person = {"housing_array_id": 1, "is_active": False}
    assert recovery_person_ineligibility_reason(person) == ""
    assert recovery_person_ineligibility_reason({**person, "housing_array_id": 2}) == "not_tzohar_halev"


def test_recovery_days_by_seniority_brackets():
    assert recovery_days_for_seniority(0) == 0
    assert recovery_days_for_seniority(1) == 5
    assert recovery_days_for_seniority(2) == 6
    assert recovery_days_for_seniority(3) == 6
    assert recovery_days_for_seniority(4) == 7
    assert recovery_days_for_seniority(11) == 8
    assert recovery_days_for_seniority(16) == 9
    assert recovery_days_for_seniority(20) == 10


def test_seniority_years_for_recovery_counts_july_first_boundary():
    assert seniority_years_for_recovery(date(2025, 6, 30), 2026) == 1
    assert seniority_years_for_recovery(date(2025, 7, 1), 2026) == 1
    assert seniority_years_for_recovery(date(2025, 7, 2), 2026) == 0
    assert seniority_years_for_recovery(date(2024, 7, 1), 2026) == 2
    assert seniority_years_for_recovery(date(2024, 7, 2), 2026) == 1


def test_recovery_days_for_period_prorates_between_seniority_brackets():
    assert recovery_days_for_period(date(2022, 12, 1), 2026) == 6.58
    assert recovery_days_for_period(date(2025, 7, 1), 2026) == 5.0
    assert recovery_days_for_period(date(2025, 7, 2), 2026) == 0.0
    assert recovery_days_for_period(date(2021, 7, 1), 2026) == 7.0


def test_parse_legacy_recovery_xlsx_extracts_only_note_fte(tmp_path):
    path = tmp_path / "legacy.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["Mifal", "Merav", "Zehut", "Semel", "Kamut", "Tarif", "Note", "Hodesh"])
    ws.append(["400", 7098, 207646050, 38, 1, 1768, "הבראה : 6 ימים כפול 0.71 משרה", "01/06/2026"])
    ws.append(["400", 4177, 32168965, 38, 1, 2508, "הבראה : 6 ימים כפול 1 משרה", "01/06/2026"])
    wb.save(path)

    rows = parse_legacy_recovery_xlsx(path)

    assert len(rows) == 2
    assert rows[0].meirav_code == "7098"
    assert rows[0].id_number == "207646050"
    assert rows[0].legacy_fte == 0.71
    assert rows[1].legacy_fte == 1.0


class _FakeCursor:
    def __init__(self, has_activity):
        self.has_activity = has_activity
        self.params = None
        self.closed = False

    def execute(self, _query, params):
        self.params = params

    def fetchone(self):
        return {"has_activity": self.has_activity}

    def close(self):
        self.closed = True


class _FakeConnection:
    def __init__(self, has_activity):
        self.cursor_obj = _FakeCursor(has_activity)

    def cursor(self, cursor_factory=None):
        return self.cursor_obj


def test_has_payment_month_activity_checks_shifts_or_payment_components():
    conn = _FakeConnection(False)

    assert _has_payment_month_activity(conn, 85, 2026, 6) is False
    assert conn.cursor_obj.params[0] == 85
    assert str(conn.cursor_obj.params[1]) == "2026-06-01"
    assert str(conn.cursor_obj.params[2]) == "2026-07-01"
    assert conn.cursor_obj.params[4] == 85
    assert str(conn.cursor_obj.params[5]) == "2026-06-01"
    assert str(conn.cursor_obj.params[6]) == "2026-07-01"

    assert _has_payment_month_activity(_FakeConnection(True), 85, 2026, 6) is True
