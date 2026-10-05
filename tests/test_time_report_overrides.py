from datetime import date

from core.logic import _apply_time_report_overrides


class _FakeCursor:
    def __init__(self):
        self._rows: list[dict] = []

    def execute(self, query: str, params: tuple) -> None:
        if "FROM people" in query:
            self._rows = [{"id": 7, "person_name": "מדריך בדיקה", "is_married": True}]
        elif "FROM apartments" in query:
            self._rows = [{"id": 3, "apartment_name": "דירה", "housing_array_id": 1}]
        else:
            self._rows = []

    def fetchall(self) -> list[dict]:
        return self._rows

    def close(self) -> None:
        pass


class _FakeConn:
    def cursor(self, cursor_factory=None) -> _FakeCursor:
        return _FakeCursor()


def test_restored_deleted_report_gets_person_fields():
    """דיווח שנמחק ומשוחזר מתמונת audit מקבל שם מדריך ומצב משפחתי, כמו בשאילתה הרגילה."""
    snapshot = {"id": 50, "person_id": 7, "apartment_id": 3, "date": date(2026, 9, 1)}

    reports = _apply_time_report_overrides(
        _FakeConn(), [], {50: snapshot}, housing_filter=None,
    )

    assert len(reports) == 1
    assert reports[0]["id"] == 50
    assert reports[0]["person_name"] == "מדריך בדיקה"
    assert reports[0]["is_married"] is True
    assert reports[0]["apartment_name"] == "דירה"
